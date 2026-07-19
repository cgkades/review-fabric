"""Immutable domain models for review inputs and captured command evidence."""

from __future__ import annotations

from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field

from review_fabric.serialization import canonical_json_bytes


class CommandResult(BaseModel):
    """Captured result of a command run while constructing a review package."""

    model_config = ConfigDict(frozen=True)

    command: tuple[str, ...] = Field(min_length=1)
    exit_code: int = Field(ge=0)
    stdout: str
    stderr: str


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

    @property
    def review_id(self) -> str:
        """Return a stable identifier for this exact immutable review input."""
        payload = canonical_json_bytes(self.model_dump(mode="json"))
        return sha256(payload).hexdigest()
