"""Secret-free provider binding schemas and startup validation."""

from __future__ import annotations

import re
from enum import StrEnum
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    OAUTH = "oauth"


_REFERENCE = re.compile(r"^(?:env:)?[A-Za-z_][A-Za-z0-9_.:-]*$")


class ProviderBinding(BaseModel):
    model_config = ConfigDict(frozen=True)
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
        elif (
            not self.credential_ref
            or not _REFERENCE.fullmatch(self.credential_ref)
            or self.credential_ref.lower().startswith(("sk-", "bearer", "akia", "asia"))
        ):
            raise ValueError("credential_ref must be a named non-secret reference")
        if self.transport is Transport.BEDROCK_IAM and not self.region:
            raise ValueError("bedrock-iam transport requires region")
        if self.transport in {
            Transport.OPENAI_COMPATIBLE,
            Transport.BEDROCK_OPENAI_COMPATIBLE,
            Transport.AZURE_AI_FOUNDRY,
        }:
            if not self.endpoint:
                raise ValueError("transport requires an endpoint")
            parsed = urlparse(self.endpoint)
            query_names = {
                item.partition("=")[0].casefold() for item in parsed.query.split("&") if item
            }
            if query_names & {"api_key", "apikey", "token", "secret", "password", "access_token"}:
                raise ValueError("endpoint must not contain a query credential")
            local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            if not parsed.hostname or (
                parsed.scheme != "https"
                and not (self.allow_local_http and local and parsed.scheme == "http")
            ):
                raise ValueError("endpoint must use HTTPS except explicit loopback development")
        if self.transport is Transport.AZURE_AI_FOUNDRY and not self.deployment:
            raise ValueError("azure-ai-foundry transport requires deployment")
        if self.transport is Transport.GEMINI and (not self.project or not self.location):
            raise ValueError("gemini transport requires project and location")
        return self


class ReviewConfiguration(BaseModel):
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

    def validate_selected_roles(self, roles: tuple[str, ...]) -> None:
        missing = sorted(set(roles) - set(self.roles))
        if missing:
            raise ValueError(f"selected roles have no binding: {', '.join(missing)}")
        unsupported = [role for role in roles if not self.binding_for(role).structured_output]
        if unsupported:
            raise ValueError(f"selected roles lack structured output: {', '.join(unsupported)}")

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
