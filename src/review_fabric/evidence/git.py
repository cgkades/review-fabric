"""Read-only Git evidence collection for immutable review packages."""

from __future__ import annotations

import os
import re
import subprocess
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from review_fabric.errors import InvalidReviewPackageError


class GitEvidence(BaseModel):
    """Canonical source evidence captured from one explicit Git comparison."""

    model_config = ConfigDict(frozen=True)

    repository_root: str
    base_sha: str
    head_sha: str
    changed_paths: tuple[str, ...]
    patch: str
    patch_digest: str


_SAFE_ENVIRONMENT_NAMES = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC")
_SECRET_PATTERNS = (
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|secret|token|password)\b\s*[:=]\s*[^\s]+"),
)


def _safe_git_environment() -> dict[str, str]:
    environment = {
        name: value for name in _SAFE_ENVIRONMENT_NAMES if (value := os.environ.get(name))
    }
    environment.update(
        {
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
    )
    return environment


def _run_git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        (
            "git",
            "-c",
            "color.ui=false",
            "-c",
            "core.pager=cat",
            "-c",
            "diff.external=",
            *arguments,
        ),
        cwd=repository,
        capture_output=True,
        check=False,
        env=_safe_git_environment(),
    )
    if completed.returncode:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise InvalidReviewPackageError(message or "Git command failed")
    try:
        return completed.stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        raise InvalidReviewPackageError(
            "Git evidence contains a non-UTF-8 path or patch"
        ) from error


_SAFE_TEST_SECRET_VALUES = re.compile(
    r"(?i)\b(?:dotenv|environment|runtime|not-allowed|example|test|fake|dummy)[-_a-z0-9]*\b"
)


def _reject_secret_material(patch: str) -> None:
    added_lines = [
        line[1:]
        for line in patch.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    for line in added_lines:
        if _SAFE_TEST_SECRET_VALUES.search(line):
            continue
        if any(pattern.search(line) for pattern in _SECRET_PATTERNS):
            raise InvalidReviewPackageError("Git patch contains potential secret material")


def _resolve_commit(repository: Path, revision: str, label: str) -> str:
    try:
        return _run_git(
            repository,
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{revision}^{{commit}}",
        ).strip()
    except InvalidReviewPackageError as error:
        raise InvalidReviewPackageError(f"cannot resolve {label} revision: {revision}") from error


def collect_git_evidence(repository: Path, base_revision: str, head_revision: str) -> GitEvidence:
    """Capture canonical, read-only evidence for an explicit local Git range."""
    repository = repository.resolve()
    try:
        top_level = _run_git(repository, "rev-parse", "--show-toplevel").strip()
        repository_root = Path(top_level).resolve()
    except InvalidReviewPackageError as error:
        raise InvalidReviewPackageError(f"not a Git repository: {repository}") from error

    base_sha = _resolve_commit(repository_root, base_revision, "base")
    head_sha = _resolve_commit(repository_root, head_revision, "head")
    patch = _run_git(
        repository_root,
        "diff-tree",
        "--no-commit-id",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--no-renames",
        "--unified=3",
        "-r",
        "--patch",
        base_sha,
        head_sha,
        "--",
    )
    _reject_secret_material(patch)
    changed_paths = tuple(
        sorted(
            filter(
                None,
                _run_git(
                    repository_root,
                    "diff-tree",
                    "--no-commit-id",
                    "--no-color",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--no-renames",
                    "-r",
                    "--name-only",
                    "-z",
                    base_sha,
                    head_sha,
                    "--",
                ).split("\0"),
            )
        )
    )
    return GitEvidence(
        repository_root=str(repository_root),
        base_sha=base_sha,
        head_sha=head_sha,
        changed_paths=changed_paths,
        patch=patch,
        patch_digest=sha256(patch.encode("utf-8")).hexdigest(),
    )
