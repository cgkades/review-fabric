"""Immutable domain models for review inputs and frozen patch evidence."""

from __future__ import annotations

import re
from collections.abc import Mapping
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field, model_validator

from review_fabric.serialization import canonical_json_bytes

DEFAULT_MAX_PATCH_EVIDENCE_BYTES = 48 * 1024
# An absolute, context-independent sanity ceiling enforced unconditionally by the
# model validator, regardless of any caller-supplied max_bytes. Pydantic v2 reruns a
# model's own `@model_validator` when an already-constructed instance is embedded as
# a field value into a parent model (e.g. ReviewPackage(patch_evidence=evidence,
# ...)) — that revalidation pass has no access to the original from_patch() call's
# context, so the *configurable* bound is enforced once, in from_patch() itself
# (plain Python, not a validator), while this fixed ceiling is what the validator
# checks every time, so it can never silently disagree with itself across
# revalidation passes.
_ABSOLUTE_MAX_PATCH_EVIDENCE_BYTES = 64 * 1024 * 1024
_HUNK_HEADER = re.compile(r"^@@ -(?P<old>\d+)(?:,\d+)? \+(?P<new>\d+)(?:,\d+)? @@")
_REVIEW_IDENTITY_SCHEMA_VERSION = 1


class FrozenPatchEvidence(BaseModel):
    """A digest-verified, bounded patch and its only allowable source citations."""

    model_config = ConfigDict(frozen=True)

    patch: str = Field(min_length=1)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def from_patch(cls, patch: str, *, max_bytes: int | None = None) -> FrozenPatchEvidence:
        """Build frozen evidence for one patch, bounded by max_bytes (default
        DEFAULT_MAX_PATCH_EVIDENCE_BYTES). A caller that deliberately needs a larger
        bound (e.g. chunked full-codebase review, where each chunk is still an
        explicit, bounded unit) can raise it per call; the default stays conservative
        for the common single-PR-diff case. This bound is checked here, once, rather
        than in a model validator, so it survives pydantic's later revalidation of
        this already-built instance when it is embedded into ReviewPackage."""
        bound = max_bytes if max_bytes is not None else DEFAULT_MAX_PATCH_EVIDENCE_BYTES
        if len(patch.encode("utf-8")) > bound:
            raise ValueError("patch evidence exceeds byte limit")
        return cls(patch=patch, digest=sha256(patch.encode("utf-8")).hexdigest())

    @model_validator(mode="after")
    def validate_integrity_and_bound(self) -> FrozenPatchEvidence:
        encoded = self.patch.encode("utf-8")
        if len(encoded) > _ABSOLUTE_MAX_PATCH_EVIDENCE_BYTES:
            raise ValueError("patch evidence exceeds byte limit")
        if sha256(encoded).hexdigest() != self.digest:
            raise ValueError("patch evidence digest does not match patch")
        return self

    def supports_citation(self, citation: Mapping[str, object]) -> bool:
        """Return whether a citation exactly reproduces contiguous supplied head lines."""
        path = citation.get("path")
        start_line = citation.get("start_line")
        end_line = citation.get("end_line")
        excerpt = citation.get("excerpt")
        if not (
            isinstance(path, str)
            and isinstance(start_line, int)
            and not isinstance(start_line, bool)
            and isinstance(end_line, int)
            and not isinstance(end_line, bool)
            and isinstance(excerpt, str)
            and start_line >= 1
            and end_line >= start_line
        ):
            return False
        lines = self._head_lines().get(path, {})
        expected = [lines.get(line) for line in range(start_line, end_line + 1)]
        if any(line is None for line in expected):
            return False
        return excerpt == "\n".join(line for line in expected if line is not None)

    def _head_lines(self) -> dict[str, dict[int, str]]:
        files: dict[str, dict[int, str]] = {}
        path: str | None = None
        next_line: int | None = None
        for raw_line in self.patch.splitlines():
            if raw_line.startswith("+++ "):
                candidate = raw_line[4:]
                path = None if candidate == "/dev/null" else candidate.removeprefix("b/")
                continue
            match = _HUNK_HEADER.match(raw_line)
            if match:
                next_line = int(match.group("new"))
                continue
            if path is None or next_line is None or not raw_line:
                continue
            marker, text = raw_line[0], raw_line[1:]
            if marker in {"+", " "}:
                files.setdefault(path, {})[next_line] = text
                next_line += 1
            elif marker == "-":
                continue
            elif marker == "\\":
                continue
            else:
                next_line = None
        return files


class ReviewPackage(BaseModel):
    """The complete immutable evidence input for one review run."""

    model_config = ConfigDict(frozen=True)

    repository_root: str = Field(min_length=1)
    base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    patch_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_paths: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    constraints: tuple[str, ...]
    patch_evidence: FrozenPatchEvidence | None = None

    @model_validator(mode="after")
    def validate_patch_evidence(self) -> ReviewPackage:
        if self.patch_evidence and self.patch_evidence.digest != self.patch_digest:
            raise ValueError("patch evidence digest must match package patch_digest")
        return self

    @property
    def review_id(self) -> str:
        """Return a stable identifier for this exact immutable review input.

        Computed from an explicit, versioned projection of fields — not a full model
        dump — so adding an unrelated field to ReviewPackage in the future does not
        silently change review_id (and therefore the artifact directory) for
        otherwise-identical review inputs. patch_evidence is intentionally excluded:
        its digest is validated to always equal patch_digest, so it carries no
        additional identity information. Deliberately changing which fields
        participate in identity must bump _REVIEW_IDENTITY_SCHEMA_VERSION.
        """
        payload = canonical_json_bytes(
            {
                "identity_schema_version": _REVIEW_IDENTITY_SCHEMA_VERSION,
                "repository_root": self.repository_root,
                "base_sha": self.base_sha,
                "head_sha": self.head_sha,
                "patch_digest": self.patch_digest,
                "selected_paths": list(self.selected_paths),
                "acceptance_criteria": list(self.acceptance_criteria),
                "constraints": list(self.constraints),
            }
        )
        return sha256(payload).hexdigest()
