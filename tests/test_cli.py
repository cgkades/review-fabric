from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", *arguments), cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_cli_creates_replayable_fake_review_for_explicit_range(tmp_path: Path) -> None:
    repository = tmp_path / "fixture"
    repository.mkdir()
    git(repository, "init", "-q")
    git(repository, "config", "user.email", "test@example.invalid")
    git(repository, "config", "user.name", "Test")
    (repository / "example.py").write_text("value = 1\n")
    git(repository, "add", ".")
    git(repository, "commit", "-qm", "base")
    base = git(repository, "rev-parse", "HEAD")
    (repository / "example.py").write_text("value = 2\n")
    git(repository, "commit", "-am", "change", "-q")
    head = git(repository, "rev-parse", "HEAD")

    completed = subprocess.run(
        (sys.executable, "-m", "review_fabric.cli", str(repository), base, head),
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path.cwd() / "src")},
    )

    assert completed.returncode == 0, completed.stderr
    artifact = Path(completed.stdout.strip())
    assert artifact == repository / ".review-fabric" / "reviews" / artifact.name
    manifest = json.loads((artifact / "manifest.json").read_text())
    assert manifest["package"]["base_sha"] == base
    assert manifest["package"]["head_sha"] == head
    decision = json.loads((artifact / "events.jsonl").read_text().splitlines()[-1])
    assert decision["payload"]["outcome"] == "ACCEPT"

    repeated = subprocess.run(
        (sys.executable, "-m", "review_fabric.cli", str(repository), base, head),
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path.cwd() / "src")},
    )

    assert repeated.returncode == 0, repeated.stderr
    assert Path(repeated.stdout.strip()) == artifact
    assert len((artifact / "events.jsonl").read_text().splitlines()) == 3


def test_cli_rejects_invalid_range_without_creating_artifact(tmp_path: Path) -> None:
    completed = subprocess.run(
        (sys.executable, "-m", "review_fabric.cli", str(tmp_path), "missing", "HEAD"),
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path.cwd() / "src")},
    )

    assert completed.returncode != 0
    assert not (tmp_path / ".review-fabric").exists()
