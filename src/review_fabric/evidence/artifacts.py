"""Append-only, local artifacts for replayable review runs."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from review_fabric.domain.models import ReviewPackage
from review_fabric.errors import InvalidReviewPackageError
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


@dataclass(frozen=True)
class ArtifactStore:
    """A write-once manifest plus append-only records for a single package."""

    directory: Path
    review_id: str

    @classmethod
    def create(cls, root: Path, package: ReviewPackage, *, patch: str) -> ArtifactStore:
        """Create a uniquely named local artifact directory before reviewer execution."""
        directory = root / ".review-fabric" / "reviews" / package.review_id
        directory.mkdir(parents=True, exist_ok=False)
        manifest = {
            "schema_version": _SCHEMA_VERSION,
            "review_id": package.review_id,
            "package": {**package.model_dump(mode="json"), "review_id": package.review_id},
            "patch": patch,
        }
        _write_new(directory / "manifest.json", canonical_json_bytes(manifest) + b"\n")
        _write_new(directory / "events.jsonl", b"")
        store = cls(directory=directory, review_id=package.review_id)
        store.regenerate_summary()
        return store

    def record_event(self, phase: str, payload: dict[str, Any]) -> None:
        """Append one schema-versioned phase event and refresh the derived report."""
        if not phase:
            raise InvalidReviewPackageError("artifact event phase is required")
        event = {
            "schema_version": _SCHEMA_VERSION,
            "review_id": self.review_id,
            "timestamp": _timestamp(),
            "phase": phase,
            "payload": payload,
        }
        with (self.directory / "events.jsonl").open("ab") as events:
            events.write(canonical_json_bytes(event) + b"\n")
            events.flush()
            os.fsync(events.fileno())
        self.regenerate_summary()

    def regenerate_summary(self) -> str:
        """Render the report exclusively from persisted manifest and event records."""
        manifest = json.loads((self.directory / "manifest.json").read_text(encoding="utf-8"))
        events = [
            json.loads(line)
            for line in (self.directory / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        ]
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
            payload = json.dumps(event["payload"], sort_keys=True, separators=(",", ":"))
            lines.append(f"- **{event['phase']}**: `{payload}`")
        summary = "\n".join(lines) + "\n"
        temporary = self.directory / "summary.md.tmp"
        temporary.write_text(summary, encoding="utf-8")
        temporary.replace(self.directory / "summary.md")
        return summary
