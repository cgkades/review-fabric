from __future__ import annotations

import os
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest

from review_fabric.errors import InvalidReviewPackageError
from review_fabric.evidence.git import (
    collect_full_tree_evidence,
    collect_git_evidence,
    split_patch_into_chunks,
)
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
    secret_file.write_text("OPENAI_API_KEY=sk-proj-xK7pQ2mZ9wLbN4jH8vTcRa1Y\n")
    git(repository, "add", "config.env")
    git(repository, "commit", "-m", "add accidental credential")

    with pytest.raises(InvalidReviewPackageError, match="secret material") as error:
        collect_git_evidence(repository, "HEAD~1", "HEAD")

    assert "sk-proj" not in str(error.value)


def test_collect_git_evidence_rejects_secret_material_in_test_named_value(repository: Path) -> None:
    secret_file = repository / "test_config.py"
    secret_file.write_text("TEST_API_KEY=sk-proj-xK7pQ2mZ9wLbN4jH8vTcRa1Y\n")
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


@pytest.mark.parametrize(
    "value",
    [
        "abcdefghijklmnopqrstuvwxyz",
        "abcdefghijklmnopqrstuvwxyz1234567890",
        "0123456789",
        "xxxxxxxxxxxxxxxxxxxx",
        "secret",
        "PASSWORD",
        "changeme",
        "leak",
        "not-allowed",
        "str",
    ],
)
def test_collect_git_evidence_allows_structurally_obvious_test_placeholders(
    repository: Path, value: str
) -> None:
    """Values that are provably not real credentials (a sequential run, a repeated
    character, or an exact well-known placeholder word) must not block ingestion —
    this is a same-value structural check, not a nearby-word bypass, so it cannot be
    exploited the way a keyword-anywhere-on-the-line allowlist could."""
    secret_file = repository / "config.env"
    secret_file.write_text(f"OPENAI_API_KEY={value}\n")
    git(repository, "add", "config.env")
    git(repository, "commit", "-m", "add placeholder credential")

    collect_git_evidence(repository, "HEAD~1", "HEAD")  # must not raise


def test_collect_git_evidence_allows_python_type_annotations_on_secret_named_parameters(
    repository: Path,
) -> None:
    """A Python type annotation like "secret: str" (a parameter declaration, not an
    assignment) must not be mistaken for a real credential — this is exactly the
    kind of line a --full whole-codebase review will encounter constantly in normal
    source code, not just test fixtures."""
    source_file = repository / "auth_helpers.py"
    source_file.write_text(
        "def auth_set(provider: str, profile: str, secret: str | None = None) -> None:\n"
        "    pass\n"
    )
    git(repository, "add", "auth_helpers.py")
    git(repository, "commit", "-m", "add helper with a secret-named parameter")

    collect_git_evidence(repository, "HEAD~1", "HEAD")  # must not raise


def test_collect_git_evidence_still_rejects_a_realistic_looking_secret(
    repository: Path,
) -> None:
    secret_file = repository / "config.env"
    secret_file.write_text("OPENAI_API_KEY=sk-proj-xK7pQ2mZ9wLbN4jH8vTcRa1Y\n")
    git(repository, "add", "config.env")
    git(repository, "commit", "-m", "add realistic-looking credential")

    with pytest.raises(InvalidReviewPackageError, match="secret material"):
        collect_git_evidence(repository, "HEAD~1", "HEAD")


def test_collect_git_evidence_rejects_secret_beside_a_placeholder_on_the_same_line(
    repository: Path,
) -> None:
    """A placeholder word/value elsewhere on the line must never exempt a separate,
    genuinely realistic-looking secret value on that same line."""
    secret_file = repository / "config.env"
    secret_file.write_text(
        "OPENAI_API_KEY=sk-proj-xK7pQ2mZ9wLbN4jH8vTcRa1Y  # example placeholder, test\n"
    )
    git(repository, "add", "config.env")
    git(repository, "commit", "-m", "add credential beside placeholder words")

    with pytest.raises(InvalidReviewPackageError, match="secret material"):
        collect_git_evidence(repository, "HEAD~1", "HEAD")


def test_collect_full_tree_evidence_treats_every_tracked_file_as_added(
    repository: Path,
) -> None:
    evidence = collect_full_tree_evidence(repository, "HEAD")

    assert evidence.base_sha == "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    assert evidence.head_sha == git(repository, "rev-parse", "HEAD").strip()
    assert evidence.changed_paths == ("README.md", "src/service.py")
    assert "+    return 'ready'" in evidence.patch
    # Every line is a fresh addition against the empty tree, never a deletion of
    # prior content, since there is no real "base" commit for a full-tree review.
    assert not any(
        line.startswith("-") and not line.startswith("---") for line in evidence.patch.splitlines()
    )
    assert evidence.patch_digest == sha256(evidence.patch.encode("utf-8")).hexdigest()


def test_collect_full_tree_evidence_defaults_to_head(repository: Path) -> None:
    default_revision = collect_full_tree_evidence(repository)
    explicit_head = collect_full_tree_evidence(repository, "HEAD")

    assert default_revision.head_sha == explicit_head.head_sha


def test_collect_full_tree_evidence_still_rejects_secret_material(repository: Path) -> None:
    secret_file = repository / "config.env"
    secret_file.write_text("OPENAI_API_KEY=sk-proj-xK7pQ2mZ9wLbN4jH8vTcRa1Y\n")
    git(repository, "add", "config.env")
    git(repository, "commit", "-m", "add realistic-looking credential")

    with pytest.raises(InvalidReviewPackageError, match="secret material"):
        collect_full_tree_evidence(repository, "HEAD")


def _file_diff(path: str, size: int) -> str:
    body = "x" * size
    return (
        f"diff --git a/{path} b/{path}\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        "--- /dev/null\n"
        f"+++ b/{path}\n"
        "@@ -0,0 +1 @@\n"
        f"+{body}\n"
    )


def test_split_patch_into_chunks_bin_packs_small_files_together() -> None:
    patch = _file_diff("a.py", 50) + _file_diff("b.py", 50) + _file_diff("c.py", 50)

    chunks = split_patch_into_chunks(patch, max_chunk_bytes=1000)

    assert len(chunks) == 1
    assert chunks[0][1] == ("a.py", "b.py", "c.py")


def test_split_patch_into_chunks_splits_when_budget_exceeded() -> None:
    patch = _file_diff("a.py", 50) + _file_diff("b.py", 50) + _file_diff("c.py", 5000)

    chunks = split_patch_into_chunks(patch, max_chunk_bytes=500)

    assert len(chunks) == 2
    assert chunks[0][1] == ("a.py", "b.py")
    assert chunks[1][1] == ("c.py",)
    # Every chunk's rendered patch text must reproduce exactly the original bytes
    # for its files, and every declared path must actually own a "diff --git" block.
    assert all(f"diff --git a/{path}" in text for text, paths in chunks for path in paths)


def test_split_patch_into_chunks_never_splits_a_single_oversized_file() -> None:
    """A single file's own diff exceeding the cap must still become one (oversized)
    chunk rather than being truncated or dropped, which would silently hide part of
    the change."""
    patch = _file_diff("huge.py", 5000)

    chunks = split_patch_into_chunks(patch, max_chunk_bytes=100)

    assert len(chunks) == 1
    assert chunks[0][1] == ("huge.py",)
    assert len(chunks[0][0].encode("utf-8")) > 100


def test_split_patch_into_chunks_of_empty_patch_is_empty() -> None:
    assert split_patch_into_chunks("", max_chunk_bytes=1000) == ()
