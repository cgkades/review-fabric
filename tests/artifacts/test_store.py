from __future__ import annotations

import json
from pathlib import Path

from review_fabric.domain.models import ReviewPackage
from review_fabric.evidence.artifacts import ArtifactStore


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
        json.loads(line)
        for line in (store.directory / "events.jsonl").read_text().splitlines()
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
