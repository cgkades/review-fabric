from __future__ import annotations

import pytest
from pydantic import ValidationError

from review_fabric.configuration import ProviderBinding, ReviewConfiguration, Transport


def test_configuration_uses_non_secret_credential_references_only() -> None:
    configuration = ReviewConfiguration(
        version=1,
        bindings={
            "fake": ProviderBinding(
                provider="local",
                transport=Transport.FAKE,
                model="deterministic",
                credential_source="none",
                credential_ref=None,
            )
        },
        roles={"correctness": "fake"},
    )

    assert configuration.binding_for("correctness").model == "deterministic"
    assert configuration.manifest_bindings() == {
        "correctness": {
            "provider": "local",
            "transport": "fake",
            "model": "deterministic",
            "credential_source": "none",
        }
    }


@pytest.mark.parametrize("credential_ref", ["sk-abcdefghijklmnopqrstuvwxyz", "Bearer top-secret"])
def test_configuration_rejects_literal_secrets(credential_ref: str) -> None:
    with pytest.raises(ValidationError, match="credential"):
        ProviderBinding(
            provider="openai",
            transport=Transport.OPENAI_COMPATIBLE,
            model="model",
            credential_source="environment",
            credential_ref=credential_ref,
            endpoint="https://api.example.test",
        )


def test_configuration_rejects_endpoint_query_credentials() -> None:
    with pytest.raises(ValidationError, match="query credential"):
        ProviderBinding(
            provider="gateway",
            transport=Transport.OPENAI_COMPATIBLE,
            model="model",
            credential_source="environment",
            credential_ref="OPENAI_API_KEY",
            endpoint="https://api.example.test/v1?api_key=not-allowed",
        )


def test_generic_provider_requires_secure_endpoint() -> None:
    with pytest.raises(ValidationError, match="HTTPS"):
        ProviderBinding(
            provider="gateway",
            transport=Transport.OPENAI_COMPATIBLE,
            model="model",
            credential_source="environment",
            credential_ref="OPENAI_API_KEY",
            endpoint="http://api.example.test",
        )
