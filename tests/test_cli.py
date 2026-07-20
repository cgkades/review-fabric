from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import review_fabric.cli as cli
from review_fabric.cli import run
from review_fabric.configuration import ProviderBinding, ReviewConfiguration, Transport
from review_fabric.domain.policy import ReviewPolicy
from review_fabric.reviewers.base import FakeReviewer, RoleRubric


def git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", *arguments), cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_cli_creates_replayable_incomplete_review_for_explicit_range(tmp_path: Path) -> None:
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
    assert decision["payload"]["outcome"] == "ESCALATE"

    repeated = subprocess.run(
        (sys.executable, "-m", "review_fabric.cli", str(repository), base, head),
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path.cwd() / "src")},
    )

    assert repeated.returncode == 0, repeated.stderr
    assert Path(repeated.stdout.strip()) == artifact
    assert len((artifact / "events.jsonl").read_text().splitlines()) == 4


def test_cli_records_safe_configured_bindings(tmp_path: Path) -> None:
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
    config = repository / "review-fabric.json"
    config.write_text(
        json.dumps(
            {
                "version": 1,
                "bindings": {
                    "fake": {
                        "provider": "local",
                        "transport": "fake",
                        "model": "fake",
                        "credential_source": "none",
                    }
                },
                "roles": {"correctness": "fake"},
            }
        )
    )

    completed = subprocess.run(
        (
            sys.executable,
            "-m",
            "review_fabric.cli",
            "--config",
            str(config),
            str(repository),
            base,
            head,
        ),
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path.cwd() / "src")},
    )

    assert completed.returncode == 0, completed.stdout
    events = (Path(completed.stdout.strip()) / "events.jsonl").read_text()
    assert '"provider":"local"' in events


def test_cli_binds_provider_timeout_to_selected_review_plan(
    tmp_path: Path, monkeypatch
) -> None:
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
    configuration = ReviewConfiguration(
        version=1,
        bindings={
            "live": ProviderBinding(
                provider="gemini",
                transport=Transport.GEMINI,
                model="light",
                credential_source="environment",
                credential_ref="TEST_KEY",
            )
        },
        roles={"correctness": "live"},
    )
    captured: list[int] = []

    def make_reviewer(
        _binding: ProviderBinding, _credential: str, rubric: RoleRubric, *, timeout_seconds: int
    ) -> FakeReviewer:
        captured.append(timeout_seconds)
        return FakeReviewer(rubric)

    monkeypatch.setattr(
        cli.ReviewPolicy, "default", classmethod(lambda cls: ReviewPolicy(timeout_seconds=7))
    )
    monkeypatch.setattr(cli, "resolve_credential", lambda *_args, **_kwargs: "runtime-only")
    monkeypatch.setattr(cli, "ProviderReviewer", make_reviewer)

    run(repository, base, head, configuration=configuration)

    assert captured == [7]


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


def test_missing_credential_persists_redacted_terminal_artifact(
    tmp_path: Path, monkeypatch
) -> None:
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
    monkeypatch.delenv("MISSING_REVIEW_KEY", raising=False)
    configuration = ReviewConfiguration(
        version=1,
        bindings={
            "live": ProviderBinding(
                provider="gemini",
                transport=Transport.GEMINI,
                model="light",
                credential_source="environment",
                credential_ref="MISSING_REVIEW_KEY",
            )
        },
        roles={"correctness": "live"},
    )

    artifact = run(repository, base, head, configuration=configuration)

    events = (artifact / "events.jsonl").read_text()
    assert '"kind":"credential-unavailable"' in events
    assert "MISSING_REVIEW_KEY" not in events
    assert json.loads(events.splitlines()[-1])["payload"]["outcome"] == "ESCALATE"
    assert run(repository, base, head, configuration=configuration) == artifact


def test_existing_nonterminal_artifact_is_closed_with_same_identity(tmp_path: Path) -> None:
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
    artifact = run(repository, base, head)
    events = (artifact / "events.jsonl").read_text().splitlines()
    (artifact / "events.jsonl").write_text("\n".join(events[:-1]) + "\n")

    assert run(repository, base, head) == artifact
    final = json.loads((artifact / "events.jsonl").read_text().splitlines()[-1])
    assert final["phase"] == "terminal"
    assert final["payload"]["outcome"] == "ESCALATE"
