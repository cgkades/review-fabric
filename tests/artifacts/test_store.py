from __future__ import annotations

import json
import stat
from multiprocessing import Event, get_context
from pathlib import Path

import pytest

from review_fabric.domain.models import FrozenPatchEvidence, ReviewPackage
from review_fabric.errors import InvalidReviewPackageError
from review_fabric.evidence.artifacts import ArtifactStore


def _hold_package_lock(root: str, ready: Event, release: Event) -> None:
    with ArtifactStore.acquire_package_lock(Path(root), package()):
        ready.set()
        release.wait(5)


def _acquire_package_lock(root: str, acquired: Event) -> None:
    with ArtifactStore.acquire_package_lock(Path(root), package()):
        acquired.set()


def package() -> ReviewPackage:
    return ReviewPackage(
        repository_root="/tmp/repository",
        base_sha="a" * 40,
        head_sha="b" * 40,
        patch_digest="c" * 64,
        selected_paths=("src/example.py",),
        acceptance_criteria=(),
        constraints=("read-only",),
        command_results=(),
    )


def test_store_writes_immutable_manifest_append_only_events_and_regenerates_summary(
    tmp_path: Path,
) -> None:
    store = ArtifactStore.create(tmp_path, package(), patch="diff --git a/x b/x\n")
    store.record_event("first-pass", {"status": "completed", "finding_count": 0})
    store.record_event("decision", {"outcome": "ACCEPT"})

    manifest = json.loads((store.directory / "manifest.json").read_text())
    events = [
        json.loads(line) for line in (store.directory / "events.jsonl").read_text().splitlines()
    ]
    original = (store.directory / "summary.md").read_text()
    (store.directory / "summary.md").unlink()

    regenerated = store.regenerate_summary()

    assert manifest["schema_version"] == 1
    assert manifest["package"]["review_id"] == package().review_id
    assert manifest["patch"] == "diff --git a/x b/x\n"
    assert [event["phase"] for event in events] == ["first-pass", "decision"]
    assert all(event["review_id"] == package().review_id for event in events)
    assert all(event["schema_version"] == 1 for event in events)
    assert regenerated == original
    assert "ACCEPT" in regenerated


def test_store_preserves_digest_verified_package_and_uses_private_permissions(
    tmp_path: Path,
) -> None:
    patch = "diff --git a/a.py b/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+safe_value = 123\n"
    evidence = FrozenPatchEvidence.from_patch(patch)
    review_package = package().model_copy(
        update={"patch_digest": evidence.digest, "patch_evidence": evidence}
    )

    store = ArtifactStore.create(tmp_path, review_package, patch=patch)
    manifest = json.loads((store.directory / "manifest.json").read_text())

    assert manifest["package"]["patch_evidence"]["patch"] == patch
    assert ReviewPackage.model_validate(manifest["package"]).patch_evidence == evidence
    assert stat.S_IMODE(store.directory.stat().st_mode) & 0o077 == 0
    assert stat.S_IMODE((store.directory / "summary.md").stat().st_mode) & 0o077 == 0


def test_store_rejects_symlinked_artifact_root(tmp_path: Path) -> None:
    target = tmp_path / "outside"
    target.mkdir()
    (tmp_path / ".review-fabric").symlink_to(target, target_is_directory=True)

    with pytest.raises(InvalidReviewPackageError, match="symlinks"):
        ArtifactStore.create(tmp_path, package(), patch="diff --git a/x b/x\n")


def test_store_rejects_unredacted_package_evidence(tmp_path: Path) -> None:
    patch = "diff --git a/a.py b/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+token=secret\n"
    evidence = FrozenPatchEvidence.from_patch(patch)
    review_package = package().model_copy(
        update={"patch_digest": evidence.digest, "patch_evidence": evidence}
    )

    with pytest.raises(InvalidReviewPackageError, match="secret material"):
        ArtifactStore.create(tmp_path, review_package, patch=patch)


def test_store_rejects_configuration_metadata_requiring_redaction(tmp_path: Path) -> None:
    with pytest.raises(InvalidReviewPackageError, match="configuration metadata"):
        ArtifactStore.create(
            tmp_path,
            package(),
            patch="diff --git a/x b/x\n",
            configuration={"model": "sk-abcdefghijklmnopqrstuvwxyz"},
        )


def test_store_rejects_events_missing_protocol_fields(tmp_path: Path) -> None:
    store = ArtifactStore.create(tmp_path, package(), patch="diff --git a/x b/x\n")
    (store.directory / "events.jsonl").write_text(
        json.dumps({"review_id": store.review_id, "phase": "terminal", "payload": {}}) + "\n"
    )

    with pytest.raises(InvalidReviewPackageError, match="invalid event"):
        store.events()


def test_store_package_lock_excludes_concurrent_process(tmp_path: Path) -> None:
    ArtifactStore.create(tmp_path, package(), patch="diff --git a/x b/x\n")
    context = get_context("spawn")
    ready = context.Event()
    release = context.Event()
    acquired = context.Event()
    worker = context.Process(
        target=_hold_package_lock,
        args=(str(tmp_path), ready, release),
    )
    contender = context.Process(target=_acquire_package_lock, args=(str(tmp_path), acquired))
    worker.start()
    try:
        assert ready.wait(5)
        contender.start()
        assert not acquired.wait(0.2)
    finally:
        release.set()
        worker.join(5)
        contender.join(5)
    assert worker.exitcode == 0
    assert contender.exitcode == 0
    assert acquired.is_set()
