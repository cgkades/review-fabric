"""Secret-free provider binding schemas and startup validation."""

from __future__ import annotations

import json
import re
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from review_fabric.errors import InvalidConfigurationError
from review_fabric.serialization import canonical_json_bytes


class Transport(StrEnum):
    FAKE = "fake"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    XAI = "xai"
    GEMINI = "gemini"
    AZURE_AI_FOUNDRY = "azure-ai-foundry"
    OPENAI_COMPATIBLE = "openai-compatible"
    BEDROCK_IAM = "bedrock-iam"
    BEDROCK_OPENAI_COMPATIBLE = "bedrock-openai-compatible"
    BEDROCK_CONVERSE = "bedrock-converse"
    OAUTH = "oauth"


_REFERENCE = re.compile(r"^(?:env:)?[A-Za-z_][A-Za-z0-9_.:-]*$")


class ProviderBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    provider: str = Field(min_length=1)
    transport: Transport
    model: str = Field(min_length=1)
    credential_source: str = Field(min_length=1)
    credential_ref: str | None = None
    endpoint: str | None = None
    deployment: str | None = None
    region: str | None = None
    project: str | None = None
    location: str | None = None
    allow_local_http: bool = False
    structured_output: bool = True

    @model_validator(mode="after")
    def validate_safe_binding(self) -> ProviderBinding:
        if self.credential_source == "none":
            if self.credential_ref is not None:
                raise ValueError("credential_ref must be absent when credential_source is none")
        elif self.transport == Transport.BEDROCK_IAM and self.credential_source in {
            "aws-chain",
            "workload",
        }:
            if self.credential_ref is not None:
                raise ValueError(
                    "bedrock IAM uses the standard credential chain, not a secret reference"
                )
        elif self.credential_source in {"aws-chain", "workload"}:
            # Only bedrock-iam implements the AWS credential chain today. Reject this
            # combination explicitly here rather than letting it validate with a
            # placeholder credential_ref and only fail confusingly at invocation time
            # (resolve_credential returns no credential for aws-chain/workload
            # regardless of transport, and every other transport requires a bearer
            # credential).
            raise ValueError(
                f"{self.transport.value} does not support the {self.credential_source} "
                "credential chain; only bedrock-iam does"
            )
        elif (
            not self.credential_ref
            or not _REFERENCE.fullmatch(self.credential_ref)
            or self.credential_ref.lower().startswith(("sk-", "bearer", "akia", "asia"))
        ):
            raise ValueError("credential_ref must be a named non-secret reference")
        if (
            self.transport in {Transport.BEDROCK_IAM, Transport.BEDROCK_CONVERSE}
            and not self.region
        ):
            raise ValueError("Bedrock transport requires region")
        if (
            self.transport
            in {
                Transport.OPENAI_COMPATIBLE,
                Transport.XAI,
                Transport.BEDROCK_OPENAI_COMPATIBLE,
                Transport.AZURE_AI_FOUNDRY,
            }
            and not self.endpoint
        ):
            raise ValueError("transport requires an endpoint")
        # Any configured endpoint can be reached by an HTTP transport. Validate it
        # uniformly so a provider-specific API-key header is never sent to an
        # attacker-controlled URL.
        if self.endpoint:
            parsed = urlparse(self.endpoint)
            if parsed.params or parsed.query or parsed.fragment:
                raise ValueError("endpoint must not contain a query or fragment")
            if parsed.username or parsed.password:
                raise ValueError("endpoint must not contain URL userinfo")
            local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            if not parsed.hostname or (
                parsed.scheme != "https"
                and not (self.allow_local_http and local and parsed.scheme == "http")
            ):
                raise ValueError("endpoint must use HTTPS except explicit loopback development")
        if self.transport is Transport.AZURE_AI_FOUNDRY and not self.deployment:
            raise ValueError("azure-ai-foundry transport requires deployment")
        if self.transport is Transport.GEMINI and (self.project or self.location):
            raise ValueError(
                "Gemini Developer transport does not support Vertex project or location"
            )
        return self


class ReviewConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: Literal[1]
    bindings: dict[str, ProviderBinding]
    roles: dict[str, str]

    @model_validator(mode="after")
    def validate_role_bindings(self) -> ReviewConfiguration:
        missing = sorted(set(self.roles.values()) - set(self.bindings))
        if missing:
            raise ValueError(f"roles reference missing bindings: {', '.join(missing)}")
        return self

    def binding_for(self, role: str) -> ProviderBinding:
        return self.bindings[self.roles[role]]

    def validate_selected_roles(self, roles: tuple[str, ...]) -> None:
        missing = sorted(set(roles) - set(self.roles))
        if missing:
            raise ValueError(f"selected roles have no binding: {', '.join(missing)}")
        unsupported = [role for role in roles if not self.binding_for(role).structured_output]
        if unsupported:
            raise ValueError(f"selected roles lack structured output: {', '.join(unsupported)}")
        supported = {
            Transport.FAKE,
            Transport.GEMINI,
            Transport.XAI,
            Transport.OPENAI_COMPATIBLE,
            Transport.BEDROCK_OPENAI_COMPATIBLE,
            Transport.BEDROCK_CONVERSE,
        }
        unavailable = [
            role for role in roles if self.binding_for(role).transport not in supported
        ]
        if unavailable:
            raise ValueError(
                f"selected roles use unsupported transports: {', '.join(sorted(unavailable))}"
            )

    def manifest_bindings(self) -> dict[str, dict[str, str]]:
        return {
            role: {
                "provider": binding.provider,
                "transport": binding.transport.value,
                "model": binding.model,
                "credential_source": binding.credential_source,
            }
            for role, name in sorted(self.roles.items())
            if (binding := self.bindings[name])
        }

    @property
    def identity(self) -> str:
        """Return a stable secret-free configuration identity for artifact separation."""
        return sha256(canonical_json_bytes(self.model_dump(mode="json"))).hexdigest()


def load_configuration(path: Path, *, repository: Path | None = None) -> ReviewConfiguration:
    """Load JSON configuration containing references, never credential values."""
    if repository:
        resolved = path.resolve()
        root = repository.resolve()
        if resolved == root or root in resolved.parents:
            raise InvalidConfigurationError(
                "provider configuration must be outside the reviewed repository"
            )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InvalidConfigurationError(f"invalid review configuration: {path}") from error
    try:
        return ReviewConfiguration.model_validate(data)
    except ValidationError as error:
        raise InvalidConfigurationError("invalid review configuration") from error
