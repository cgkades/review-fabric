"""Immutable domain models for review inputs and captured command evidence."""

from __future__ import annotations

import re
from collections.abc import Mapping
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field, model_validator

from review_fabric.serialization import canonical_json_bytes

_MAX_PATCH_EVIDENCE_BYTES = 48 * 1024
_HUNK_HEADER = re.compile(r"^@@ -(?P<old>\d+)(?:,\d+)? \+(?P<new>\d+)(?:,\d+)? @@")


class CommandResult(BaseModel):
    """Captured result of a command run while constructing a review package."""

    model_config = ConfigDict(frozen=True)

    command: tuple[str, ...] = Field(min_length=1)
    exit_code: int = Field(ge=0)
    stdout: str
    stderr: str


class FrozenPatchEvidence(BaseModel):
    """A digest-verified, bounded patch and its only allowable source citations."""

    model_config = ConfigDict(frozen=True)

    patch: str = Field(min_length=1)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def from_patch(cls, patch: str) -> FrozenPatchEvidence:
        return cls(patch=patch, digest=sha256(patch.encode("utf-8")).hexdigest())

    @model_validator(mode="after")
    def validate_integrity_and_bound(self) -> FrozenPatchEvidence:
        encoded = self.patch.encode("utf-8")
        if len(encoded) > _MAX_PATCH_EVIDENCE_BYTES:
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
            and isinstance(end_line, int)
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
    command_results: tuple[CommandResult, ...]
    patch_evidence: FrozenPatchEvidence | None = None

    @model_validator(mode="after")
    def validate_patch_evidence(self) -> ReviewPackage:
        if self.patch_evidence and self.patch_evidence.digest != self.patch_digest:
            raise ValueError("patch evidence digest must match package patch_digest")
        return self

    @property
    def review_id(self) -> str:
        """Return a stable identifier for this exact immutable review input."""
        payload = canonical_json_bytes(self.model_dump(mode="json"))
        return sha256(payload).hexdigest()
