"""Provider adapters that construct safe request metadata; no network transport is implicit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from review_fabric.configuration import ProviderBinding, Transport
from review_fabric.errors import PolicyRejectionError


@dataclass(frozen=True)
class ProviderRequest:
    endpoint: str | None
    model: str
    headers: dict[str, str]
    transport: str


class ProviderClient(Protocol):
    def invoke(self, request: ProviderRequest, payload: dict[str, object]) -> dict[str, object]: ...


def request_for(binding: ProviderBinding, credential: str | None) -> ProviderRequest:
    """Build request shape for injected/mocked clients only; callers own network I/O."""
    headers: dict[str, str] = {}
    if credential:
        headers["authorization"] = "Bearer [runtime credential]"
    if binding.transport is Transport.BEDROCK_IAM:
        return ProviderRequest(
            endpoint=None, model=binding.model, headers={}, transport=binding.transport.value
        )
    if binding.transport is Transport.OAUTH:
        raise PolicyRejectionError(
            "OAuth adapter unavailable; configure an official supported session/helper"
        )
    return ProviderRequest(
        endpoint=binding.endpoint,
        model=binding.model,
        headers=headers,
        transport=binding.transport.value,
    )


def invoke(
    client: ProviderClient,
    binding: ProviderBinding,
    credential: str | None,
    payload: dict[str, object],
) -> dict[str, object]:
    return client.invoke(request_for(binding, credential), payload)
