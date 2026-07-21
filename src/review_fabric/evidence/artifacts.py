"""Append-only, local artifacts for replayable review runs."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from review_fabric.domain.models import ReviewPackage
from review_fabric.errors import ArtifactAlreadyExistsError, InvalidReviewPackageError
from review_fabric.redaction import redact
from review_fabric.serialization import canonical_json_bytes

_SCHEMA_VERSION = 1


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _write_new(path: Path, content: bytes) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise InvalidReviewPackageError(f"artifact already exists: {path}") from error
    with os.fdopen(descriptor, "wb") as artifact:
        artifact.write(content)
        artifact.flush()
        os.fsync(artifact.fileno())


def _write_private(path: Path, content: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as artifact:
            artifact.write(content)
            artifact.flush()
            os.fsync(artifact.fileno())
        os.chmod(temporary_path, 0o600)
        temporary_path.replace(path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


@contextmanager
def _acquire_file_lock(path: Path) -> Iterator[None]:
    if path.is_symlink():
        raise InvalidReviewPackageError("artifact path must not contain symlinks")
    descriptor = os.open(
        path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        if os.name == "nt":
            import msvcrt

            os.write(descriptor, b"0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            while True:
                try:
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class ArtifactStore:
    """A write-once manifest plus append-only records for a single package."""

    directory: Path
    review_id: str

    @classmethod
    def directory_for(cls, root: Path, package: ReviewPackage) -> Path:
        return root / ".review-fabric" / "reviews" / package.review_id

    @staticmethod
    def _reviews_root(root: Path) -> Path:
        artifact_root = root / ".review-fabric"
        reviews_root = artifact_root / "reviews"
        for directory in (artifact_root, reviews_root):
            if directory.is_symlink():
                raise InvalidReviewPackageError("artifact path must not contain symlinks")
            try:
                directory.mkdir(mode=0o700)
            except FileExistsError as error:
                if not directory.is_dir():
                    raise InvalidReviewPackageError(
                        "artifact path component is not a directory"
                    ) from error
                if directory.is_symlink():
                    raise InvalidReviewPackageError(
                        "artifact path must not contain symlinks"
                    ) from error
            os.chmod(directory, 0o700)
        return reviews_root

    @classmethod
    def open(cls, root: Path, package: ReviewPackage) -> ArtifactStore:
        """Open an existing artifact only when it belongs to the exact package."""
        directory = cls._reviews_root(root) / package.review_id
        if directory.is_symlink() or not directory.is_dir():
            raise InvalidReviewPackageError("existing artifact path is unsafe")
        manifest_path = directory / "manifest.json"
        if manifest_path.is_symlink():
            raise InvalidReviewPackageError("existing artifact path is unsafe")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as error:
            raise InvalidReviewPackageError("existing artifact has no valid manifest") from error
        expected_package = {**package.model_dump(mode="json"), "review_id": package.review_id}
        if (
            manifest.get("review_id") != package.review_id
            or manifest.get("package") != expected_package
        ):
            raise InvalidReviewPackageError("existing artifact does not match review package")
        return cls(directory=directory, review_id=package.review_id)

    @classmethod
    def create(
        cls,
        root: Path,
        package: ReviewPackage,
        *,
        patch: str,
        configuration: dict[str, Any] | None = None,
    ) -> ArtifactStore:
        """Create a uniquely named local artifact directory before reviewer execution."""
        reviews_root = cls._reviews_root(root)
        directory = reviews_root / package.review_id
        temporary = Path(tempfile.mkdtemp(prefix=f".{package.review_id}.", dir=reviews_root))
        try:
            package_data = {**package.model_dump(mode="json"), "review_id": package.review_id}
            if redact(package_data) != package_data:
                raise InvalidReviewPackageError("review package contains potential secret material")
            if configuration is not None and redact(configuration) != configuration:
                raise InvalidReviewPackageError(
                    "configuration metadata contains potential secret material"
                )
            manifest = {
                "schema_version": _SCHEMA_VERSION,
                "review_id": package.review_id,
                "package": package_data,
                "patch": redact(patch),
                "configuration": configuration,
            }
            _write_new(temporary / "manifest.json", canonical_json_bytes(manifest) + b"\n")
            _write_new(temporary / "events.jsonl", b"")
            store = cls(directory=temporary, review_id=package.review_id)
            store.regenerate_summary()
            try:
                os.rename(temporary, directory)
            except OSError as error:
                if directory.exists():
                    raise ArtifactAlreadyExistsError(
                        "artifact already exists for review package"
                    ) from error
                raise
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return cls(directory=directory, review_id=package.review_id)

    @classmethod
    @contextmanager
    def acquire_package_lock(cls, root: Path, package: ReviewPackage) -> Iterator[None]:
        """Serialize all lifecycle transitions for one immutable review package."""
        locks = cls._reviews_root(root) / ".locks"
        if locks.is_symlink():
            raise InvalidReviewPackageError("artifact path must not contain symlinks")
        locks.mkdir(mode=0o700, exist_ok=True)
        os.chmod(locks, 0o700)
        with _acquire_file_lock(locks / f"{package.review_id}.lock"):
            yield

    def events(self) -> tuple[dict[str, Any], ...]:
        """Load only structurally valid events for this immutable artifact."""
        events_path = self.directory / "events.jsonl"
        if events_path.is_symlink():
            raise InvalidReviewPackageError("existing artifact path is unsafe")
        try:
            records = tuple(
                json.loads(line)
                for line in events_path.read_text(encoding="utf-8").splitlines()
                if line
            )
        except (OSError, json.JSONDecodeError) as error:
            raise InvalidReviewPackageError("artifact has invalid event records") from error
        if any(
            not isinstance(event, dict)
            or event.get("schema_version") != _SCHEMA_VERSION
            or event.get("review_id") != self.review_id
            or not isinstance(event.get("timestamp"), str)
            or not isinstance(event.get("phase"), str)
            or not isinstance(event.get("payload"), dict)
            for event in records
        ):
            raise InvalidReviewPackageError("artifact has invalid event records")
        return records

    def record_event(self, phase: str, payload: dict[str, Any]) -> None:
        """Append one schema-versioned phase event and incrementally extend the
        derived report — never a full events.jsonl reparse + summary.md rewrite per
        call, so cost per event stays O(1) instead of compounding to O(events) as a
        review accumulates more phases. (Safe without additional locking: every
        caller already holds ArtifactStore.acquire_package_lock for the whole run.)
        """
        if not phase:
            raise InvalidReviewPackageError("artifact event phase is required")
        event = {
            "schema_version": _SCHEMA_VERSION,
            "review_id": self.review_id,
            "timestamp": _timestamp(),
            "phase": phase,
            "payload": redact(payload),
        }
        events_path = self.directory / "events.jsonl"
        if events_path.is_symlink():
            raise InvalidReviewPackageError("existing artifact path is unsafe")
        is_first_event = events_path.stat().st_size == 0
        descriptor = os.open(
            events_path,
            os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0),
        )
        with os.fdopen(descriptor, "ab") as events:
            events.write(canonical_json_bytes(event) + b"\n")
            events.flush()
            os.fsync(events.fileno())
        self._append_summary_event(event, is_first_event=is_first_event)

    def _append_summary_event(self, event: dict[str, Any], *, is_first_event: bool) -> None:
        """Extend summary.md with exactly the new event's rendered line.

        Produces output byte-identical to a from-scratch regenerate_summary() call at
        every step: the very first event replaces the "No phases recorded."
        placeholder instead of appending after it; every subsequent event is a plain
        append. Falls back to a full regenerate_summary() if summary.md is missing or
        doesn't look like what this method expects, so an unexpected on-disk state
        (e.g. a user manually deleted or edited the file) self-heals instead of
        silently producing a wrong report.
        """
        rendered_line = self._render_event_line(event)
        summary_path = self.directory / "summary.md"
        if not is_first_event:
            if not summary_path.exists() or summary_path.is_symlink():
                self.regenerate_summary()
                return
            descriptor = os.open(
                summary_path, os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
            )
            with os.fdopen(descriptor, "ab") as summary:
                summary.write((rendered_line + "\n").encode("utf-8"))
                summary.flush()
                os.fsync(summary.fileno())
            return
        try:
            content = summary_path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            self.regenerate_summary()
            return
        placeholder = "- No phases recorded.\n"
        if summary_path.is_symlink() or placeholder not in content:
            self.regenerate_summary()
            return
        updated = content.replace(placeholder, rendered_line + "\n", 1)
        _write_private(summary_path, updated.encode("utf-8"))

    @staticmethod
    def _render_event_line(event: dict[str, Any]) -> str:
        payload = json.dumps(event["payload"], sort_keys=True, separators=(",", ":"))
        return f"- **{event['phase']}**: `{payload}`"

    def regenerate_summary(self) -> str:
        """Render the report exclusively from persisted manifest and event records.

        This does the full O(events) read + rebuild; record_event() no longer calls
        this on every event (see _append_summary_event), but it remains available for
        on-demand regeneration (the `review-fabric summary` CLI command, or self-heal
        if summary.md is ever missing/unexpected).
        """
        manifest_path = self.directory / "manifest.json"
        if manifest_path.is_symlink():
            raise InvalidReviewPackageError("existing artifact path is unsafe")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        events = self.events()
        lines = [
            "# Review Fabric report",
            "",
            f"- Review ID: `{manifest['review_id']}`",
            f"- Base: `{manifest['package']['base_sha']}`",
            f"- Head: `{manifest['package']['head_sha']}`",
            "",
            "## Phase history",
        ]
        if not events:
            lines.append("- No phases recorded.")
        for event in events:
            lines.append(self._render_event_line(event))
        summary = "\n".join(lines) + "\n"
        _write_private(self.directory / "summary.md", summary.encode("utf-8"))
        return summary
