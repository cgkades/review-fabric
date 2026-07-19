"""Secret-free provider binding schemas used before adapter invocation."""

from __future__ import annotations

import re
from enum import StrEnum
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Transport(StrEnum):
    FAKE = "fake"
    OPENAI_COMPATIBLE = "openai-compatible"
    BEDROCK_IAM = "bedrock-iam"


_SECRET_VALUE = re.compile(r"(?i)(?:^sk-|^bearer\s+|secret|token=|api[_-]?key=)")
_REFERENCE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]*$")


class ProviderBinding(BaseModel):
    """Non-secret execution identity for one logical reviewer adapter."""

    model_config = ConfigDict(frozen=True)

    provider: str = Field(min_length=1)
    transport: Transport
    model: str = Field(min_length=1)
    credential_source: str = Field(min_length=1)
    credential_ref: str | None = None
    endpoint: str | None = None

    @model_validator(mode="after")
    def validate_safe_binding(self) -> ProviderBinding:
        if self.credential_source == "none":
            if self.credential_ref is not None:
                raise ValueError("credential_ref must be absent when credential_source is none")
        elif not self.credential_ref or not _REFERENCE.fullmatch(self.credential_ref):
            raise ValueError("credential_ref must be a named non-secret reference")
        if self.credential_ref and (
            self.credential_ref.lower().startswith(("sk-", "bearer", "akia", "asia"))
        ):
            raise ValueError("credential_ref must not contain a literal credential")
        if self.transport == Transport.OPENAI_COMPATIBLE:
            if not self.endpoint:
                raise ValueError("openai-compatible transport requires an HTTPS endpoint")
            parsed = urlparse(self.endpoint)
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError("openai-compatible endpoint must use HTTPS")
        return self


class ReviewConfiguration(BaseModel):
    """Versioned mapping from provider-neutral roles to named bindings."""

    model_config = ConfigDict(frozen=True)

    version: int = Field(ge=1)
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

    def manifest_bindings(self) -> dict[str, dict[str, str]]:
        return {
            role: {
                "provider": binding.provider,
                "transport": binding.transport.value,
                "model": binding.model,
                "credential_source": binding.credential_source,
            }
            for role, binding_name in sorted(self.roles.items())
            if (binding := self.bindings[binding_name])
        }
