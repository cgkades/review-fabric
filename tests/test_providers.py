"""Provider transport selection tests using injected clients only."""

from __future__ import annotations

import pytest

from review_fabric.configuration import ProviderBinding, Transport
from review_fabric.errors import PolicyRejectionError
from review_fabric.reviewers.providers import request_for


def compatible(transport: Transport) -> ProviderBinding:
    return ProviderBinding(
        provider="gateway",
        transport=transport,
        model="model-id",
        credential_source="environment",
        credential_ref="API_KEY",
        endpoint="https://provider.example.test/v1",
    )


def test_compatible_and_bedrock_api_key_transports_use_configured_endpoint() -> None:
    for transport in (Transport.OPENAI_COMPATIBLE, Transport.BEDROCK_OPENAI_COMPATIBLE):
        request = request_for(compatible(transport), "runtime-only")
        assert request.endpoint == "https://provider.example.test/v1"
        assert request.transport == transport.value
        assert "runtime-only" not in str(request.headers)


def test_bedrock_iam_uses_workload_chain_without_static_credential() -> None:
    binding = ProviderBinding(
        provider="bedrock",
        transport=Transport.BEDROCK_IAM,
        model="anthropic.model",
        credential_source="aws-chain",
        region="us-west-2",
    )
    request = request_for(binding, None)
    assert request.endpoint is None
    assert request.headers == {}


@pytest.mark.parametrize(
    ("transport", "kwargs"),
    [
        (Transport.OPENAI, {}),
        (Transport.ANTHROPIC, {}),
        (Transport.XAI, {"endpoint": "https://xai.example.test/v1"}),
        (Transport.GEMINI, {}),
        (
            Transport.AZURE_AI_FOUNDRY,
            {"endpoint": "https://azure.example.test", "deployment": "deployment"},
        ),
    ],
)
def test_native_bindings_validate_without_network(
    transport: Transport, kwargs: dict[str, str]
) -> None:
    binding = ProviderBinding(
        provider=transport.value,
        transport=transport,
        model="model-id",
        credential_source="environment",
        credential_ref="API_KEY",
        **kwargs,
    )
    assert request_for(binding, "runtime").model == "model-id"


def test_unsupported_oauth_fails_without_client_or_token_scraping() -> None:
    binding = ProviderBinding(
        provider="official-client",
        transport=Transport.OAUTH,
        model="model-id",
        credential_source="external-session",
        credential_ref="official-client-profile",
    )
    with pytest.raises(PolicyRejectionError, match="OAuth adapter unavailable"):
        request_for(binding, None)
