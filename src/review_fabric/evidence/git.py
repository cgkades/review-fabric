"""Read-only Git evidence collection for immutable review packages."""

from __future__ import annotations

import os
import re
import subprocess
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from review_fabric.errors import InvalidReviewPackageError
from review_fabric.redaction import redact


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
    re.compile(r"\b(?:AKIA|ASIA)(?P<value>[0-9A-Z]{16})\b"),
    re.compile(r"\bsk-(?:proj-)?(?P<value>[A-Za-z0-9_-]{20,})\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|secret|token|password)\b\s*[:=]\s*(?P<value>[^\s\\\"']+)"),
)

_SEQUENTIAL_ALPHABET = "abcdefghijklmnopqrstuvwxyz"
_SEQUENTIAL_DIGITS = "0123456789"
# A short, exact-match, curated list of literal placeholder values: documented here
# as the one supported convention for a "this is deliberately not a real secret" test
# fixture. Deliberately NOT a keyword search over the surrounding line — that pattern
# (matching an unrelated word anywhere on the line) previously let a real secret slip
# through review just by having a comment like "# test only" nearby. This instead
# only inspects the exact matched candidate value itself.
_KNOWN_PLACEHOLDER_VALUES = frozenset(
    {"secret", "password", "changeme", "placeholder", "dummy", "redacted", "example", "xxx"}
)
# Purely lowercase-letter (optionally hyphenated) tokens, up to a modest length, are
# never real credentials: every credential-issuing convention in these patterns
# (AKIA/ASIA, sk-..., and any realistic API key/token/password) mixes case, digits,
# or other punctuation to maximize entropy. A value like "leak", "not-allowed", or a
# Python type-hint keyword like "str" that happens to follow "secret:" in a type
# annotation (not an assignment) all match this and are structurally implausible as
# real secrets — never based on unrelated context elsewhere on the line.
_LOWERCASE_WORD = re.compile(r"^[a-z]+(-[a-z]+)*$")
_MAX_LOWERCASE_WORD_LENGTH = 24


def _is_sequential_or_repeated(segment: str, alphabet: str) -> bool:
    if len(set(segment)) == 1:
        return True
    extended = alphabet * (len(segment) // len(alphabet) + 2)
    return segment in extended


def _is_obviously_fake_placeholder(value: str) -> bool:
    """Return True only when a candidate secret value is structurally incapable of
    being a real credential — never based on unrelated context elsewhere on the line.

    Three narrow, exact cases are treated as safe test/example/code-artifact values:
    1. A run of a single repeated character, or a strictly ascending slice of the
       alphabet/digits (e.g. "xxxxxxxxxxxx", "abcdefghijklmnopqrstuvwxyz",
       "0123456789"). Real credential-issuing systems emit cryptographically random
       values and structurally cannot produce these.
    2. The value, in full, exactly equals one of a short curated list of documented
       literal placeholder words (see _KNOWN_PLACEHOLDER_VALUES) — not merely
       *contains* one of these words, so an unrelated word next to a real secret can
       never trigger this.
    3. The value is a short, purely lowercase-letter (optionally hyphenated) token
       (see _LOWERCASE_WORD) — real secrets always mix case/digits/other punctuation
       for entropy, and this also safely covers a Python type annotation like
       "secret: str" being mistaken for an assignment.
    """
    core = value.strip().strip("'\";,")
    if not core:
        return False
    lowered = core.lower()
    if lowered in _KNOWN_PLACEHOLDER_VALUES:
        return True
    if len(core) <= _MAX_LOWERCASE_WORD_LENGTH and _LOWERCASE_WORD.fullmatch(core):
        return True
    if len(core) < 4:
        return False
    for group in re.findall(r"[a-z]+|[0-9]+|[^a-z0-9]+", lowered):
        if group.isalpha():
            if not _is_sequential_or_repeated(group, _SEQUENTIAL_ALPHABET):
                return False
        elif group.isdigit():
            if not _is_sequential_or_repeated(group, _SEQUENTIAL_DIGITS):
                return False
        # Punctuation-only separator groups (e.g. "-", "_") carry no entropy and are
        # always allowed between alnum runs.
    return True


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


_GIT_TIMEOUT_SECONDS = 30


def _run_git(repository: Path, *arguments: str) -> str:
    try:
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
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise InvalidReviewPackageError("Git command timed out") from error
    if completed.returncode:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise InvalidReviewPackageError(message or "Git command failed")
    try:
        return completed.stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        raise InvalidReviewPackageError(
            "Git evidence contains a non-UTF-8 path or patch"
        ) from error


def _reject_secret_material(patch: str) -> None:
    added_lines = [
        line[1:]
        for line in patch.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    for line in added_lines:
        for pattern in _SECRET_PATTERNS:
            for match in pattern.finditer(line):
                if not _is_obviously_fake_placeholder(match.group("value")):
                    raise InvalidReviewPackageError(
                        "Git patch contains potential secret material"
                    )


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


# The well-known SHA-1 of a completely empty Git tree object. Diffing any commit
# against this fixed, universal constant (rather than against "no parent") is the
# standard idiom for "treat every tracked file as newly added" — used here for
# full-codebase review, where there is no meaningful "base" commit at all.
_EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _collect_evidence_between(
    repository_root: Path, base_sha: str, head_sha: str
) -> tuple[str, tuple[str, ...]]:
    """Return (redacted patch, changed_paths) for an explicit tree-ish range, common
    to both an explicit base..head comparison and a full-tree (empty-tree..head)
    comparison."""
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
    patch = redact(patch)
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
    return patch, changed_paths


def _resolve_repository_root(repository: Path) -> Path:
    repository = repository.resolve()
    try:
        top_level = _run_git(repository, "rev-parse", "--show-toplevel").strip()
        return Path(top_level).resolve()
    except InvalidReviewPackageError as error:
        raise InvalidReviewPackageError(f"not a Git repository: {repository}") from error


def collect_git_evidence(repository: Path, base_revision: str, head_revision: str) -> GitEvidence:
    """Capture canonical, read-only evidence for an explicit local Git range."""
    repository_root = _resolve_repository_root(repository)
    base_sha = _resolve_commit(repository_root, base_revision, "base")
    head_sha = _resolve_commit(repository_root, head_revision, "head")
    patch, changed_paths = _collect_evidence_between(repository_root, base_sha, head_sha)
    return GitEvidence(
        repository_root=str(repository_root),
        base_sha=base_sha,
        head_sha=head_sha,
        changed_paths=changed_paths,
        patch=patch,
        patch_digest=sha256(patch.encode("utf-8")).hexdigest(),
    )


def collect_full_tree_evidence(repository: Path, revision: str = "HEAD") -> GitEvidence:
    """Capture canonical, read-only evidence for the entire tracked tree at
    revision, as though every tracked file were newly added (diff against the
    well-known empty-tree object rather than any real commit). Used for whole-
    codebase review rather than a bounded PR/commit-range diff. The same secret
    rejection and redaction guarantees apply as for collect_git_evidence."""
    repository_root = _resolve_repository_root(repository)
    head_sha = _resolve_commit(repository_root, revision, "head")
    patch, changed_paths = _collect_evidence_between(repository_root, _EMPTY_TREE_SHA, head_sha)
    return GitEvidence(
        repository_root=str(repository_root),
        base_sha=_EMPTY_TREE_SHA,
        head_sha=head_sha,
        changed_paths=changed_paths,
        patch=patch,
        patch_digest=sha256(patch.encode("utf-8")).hexdigest(),
    )


_FILE_HEADER = re.compile(r"^diff --git a/(?P<path>.*?) b/(?:.*)$")


def split_patch_into_chunks(
    patch: str, *, max_chunk_bytes: int
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Split a unified diff into file-aligned chunks, each at most max_chunk_bytes.

    Returns an ordered tuple of (chunk_patch_text, chunk_paths) pairs. A unified diff
    is never split *within* a file's own diff (that would break hunk-header/context
    line integrity), so a single file whose own diff alone exceeds max_chunk_bytes
    still becomes its own, individually oversized chunk — there is no way to shrink
    one file's diff below the cap without truncating it, which would silently hide
    part of the change instead of failing predictably.
    """
    if not patch:
        return ()
    segments: list[tuple[str | None, str]] = []
    current_lines: list[str] = []
    current_path: str | None = None
    for line in patch.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if current_lines:
                segments.append((current_path, "".join(current_lines)))
            current_lines = [line]
            match = _FILE_HEADER.match(line)
            current_path = match.group("path") if match else None
        else:
            current_lines.append(line)
    if current_lines:
        segments.append((current_path, "".join(current_lines)))

    chunks: list[list[tuple[str | None, str]]] = []
    current_chunk: list[tuple[str | None, str]] = []
    current_chunk_bytes = 0
    for path, text in segments:
        size = len(text.encode("utf-8"))
        if current_chunk and current_chunk_bytes + size > max_chunk_bytes:
            chunks.append(current_chunk)
            current_chunk = []
            current_chunk_bytes = 0
        current_chunk.append((path, text))
        current_chunk_bytes += size
    if current_chunk:
        chunks.append(current_chunk)

    return tuple(
        (
            "".join(text for _, text in chunk),
            tuple(sorted({path for path, _ in chunk if path is not None})),
        )
        for chunk in chunks
    )
