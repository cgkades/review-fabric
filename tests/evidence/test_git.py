from __future__ import annotations

import os
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest

from review_fabric.errors import InvalidReviewPackageError
from review_fabric.evidence.git import collect_git_evidence
from review_fabric.serialization import canonical_json_bytes


def git(repository: Path, *args: str) -> str:
    hooks = repository / ".test-hooks"
    hooks.mkdir(exist_ok=True)
    completed = subprocess.run(
        (
            "git",
            "-c",
            "commit.gpgSign=false",
            "-c",
            f"core.hooksPath={hooks}",
            *args,
        ),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        env={
            "PATH": os.defpath,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        },
    )
    return completed.stdout


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    git(tmp_path, "init", "--initial-branch=main")
    git(tmp_path, "config", "user.name", "Review Fabric Test")
    git(tmp_path, "config", "user.email", "review-fabric@example.invalid")

    service = tmp_path / "src" / "service.py"
    service.parent.mkdir()
    service.write_text("def health() -> str:\n    return 'ok'\n", encoding="utf-8")
    git(tmp_path, "add", "src/service.py")
    git(tmp_path, "commit", "-m", "add health check")

    service.write_text("def health() -> str:\n    return 'ready'\n", encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text("# Example\n", encoding="utf-8")
    git(tmp_path, "add", "src/service.py", "README.md")
    git(tmp_path, "commit", "-m", "change health check")
    return tmp_path


def test_collect_git_evidence_pins_explicit_commits_and_canonical_diff(repository: Path) -> None:
    evidence = collect_git_evidence(repository, "HEAD~1", "HEAD")

    assert evidence.repository_root == str(repository)
    assert evidence.base_sha == git(repository, "rev-parse", "HEAD~1").strip()
    assert evidence.head_sha == git(repository, "rev-parse", "HEAD").strip()
    assert evidence.changed_paths == ("README.md", "src/service.py")
    assert "-    return 'ok'" in evidence.patch
    assert "+    return 'ready'" in evidence.patch
    assert evidence.patch_digest == sha256(evidence.patch.encode("utf-8")).hexdigest()


def test_collect_git_evidence_is_deterministic(repository: Path) -> None:
    first = collect_git_evidence(repository, "HEAD~1", "HEAD")
    second = collect_git_evidence(repository, first.base_sha, first.head_sha)

    assert canonical_json_bytes(first) == canonical_json_bytes(second)


def test_collect_git_evidence_rejects_unknown_revision_before_collecting(repository: Path) -> None:
    with pytest.raises(InvalidReviewPackageError, match="cannot resolve base revision"):
        collect_git_evidence(repository, "does-not-exist", "HEAD")


def test_collect_git_evidence_ignores_ambient_git_color_configuration(repository: Path) -> None:
    baseline = collect_git_evidence(repository, "HEAD~1", "HEAD")
    git(repository, "config", "color.ui", "always")

    configured = collect_git_evidence(repository, "HEAD~1", "HEAD")

    assert configured.patch == baseline.patch
    assert "\x1b" not in configured.patch


def test_collect_git_evidence_does_not_honor_git_trace_environment(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace_file = repository / "collector.trace"
    monkeypatch.setenv("GIT_TRACE", str(trace_file))

    collect_git_evidence(repository, "HEAD~1", "HEAD")

    assert not trace_file.exists()
    monkeypatch.delenv("GIT_TRACE")
    assert git(repository, "status", "--porcelain") == ""


def test_collect_git_evidence_rejects_committed_secret_material(repository: Path) -> None:
    secret_file = repository / "config.env"
    secret_file.write_text("OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz1234567890\n")
    git(repository, "add", "config.env")
    git(repository, "commit", "-m", "add accidental credential")

    with pytest.raises(InvalidReviewPackageError, match="secret material") as error:
        collect_git_evidence(repository, "HEAD~1", "HEAD")

    assert "sk-proj" not in str(error.value)


def test_collect_git_evidence_rejects_secret_material_in_test_named_value(repository: Path) -> None:
    secret_file = repository / "test_config.py"
    secret_file.write_text("TEST_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz1234567890\n")
    git(repository, "add", "test_config.py")
    git(repository, "commit", "-m", "add accidental test credential")

    with pytest.raises(InvalidReviewPackageError, match="secret material"):
        collect_git_evidence(repository, "HEAD~1", "HEAD")


def test_collect_git_evidence_allows_removing_a_preexisting_secret(repository: Path) -> None:
    secret_file = repository / "config.env"
    secret_file.write_text("OPENAI_API_KEY=" + "sk-" + "proj-" + "x" * 30 + "\n")
    git(repository, "add", "config.env")
    git(repository, "commit", "-m", "historical credential")
    git(repository, "rm", "config.env")
    git(repository, "commit", "-m", "remove credential")

    evidence = collect_git_evidence(repository, "HEAD~1", "HEAD")

    assert "config.env" in evidence.changed_paths
    assert "sk-proj-" not in evidence.patch
    assert "[REDACTED]" in evidence.patch


def test_run_git_raises_on_timeout_instead_of_hanging_indefinitely(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import review_fabric.evidence.git as git_module
    from review_fabric.evidence.git import _run_git

    def fake_run(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="git", timeout=30)

    monkeypatch.setattr(git_module.subprocess, "run", fake_run)

    with pytest.raises(InvalidReviewPackageError, match="timed out"):
        _run_git(repository, "status")
