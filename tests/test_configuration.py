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


def test_gemini_vertex_fields_are_rejected_until_a_safe_adapter_exists() -> None:
    with pytest.raises(ValidationError, match="Gemini Developer"):
        ProviderBinding(
            provider="gemini",
            transport=Transport.GEMINI,
            model="model",
            credential_source="environment",
            credential_ref="GEMINI_API_KEY",
            project="project",
            location="us-central1",
        )


def test_configuration_rejects_endpoint_query_credentials() -> None:
    with pytest.raises(ValidationError, match="query or fragment"):
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


def test_configuration_identity_ignores_equivalent_mapping_order() -> None:
    first = ReviewConfiguration.model_validate(
        {
            "version": 1,
            "bindings": {
                "one": {
                    "provider": "one",
                    "transport": "fake",
                    "model": "one",
                    "credential_source": "none",
                },
                "two": {
                    "provider": "two",
                    "transport": "fake",
                    "model": "two",
                    "credential_source": "none",
                },
            },
            "roles": {"correctness": "one", "testing": "two"},
        }
    )
    second = ReviewConfiguration.model_validate(
        {
            "version": 1,
            "bindings": {
                "two": {
                    "provider": "two",
                    "transport": "fake",
                    "model": "two",
                    "credential_source": "none",
                },
                "one": {
                    "provider": "one",
                    "transport": "fake",
                    "model": "one",
                    "credential_source": "none",
                },
            },
            "roles": {"testing": "two", "correctness": "one"},
        }
    )

    assert first.identity == second.identity


@pytest.mark.parametrize(
    "transport",
    [Transport.OPENAI, Transport.ANTHROPIC, Transport.AZURE_AI_FOUNDRY, Transport.BEDROCK_IAM],
)
def test_configuration_rejects_selected_unsupported_transport(transport: Transport) -> None:
    binding = ProviderBinding(
        provider="provider",
        transport=transport,
        model="model",
        credential_source="aws-chain" if transport is Transport.BEDROCK_IAM else "environment",
        credential_ref=None if transport is Transport.BEDROCK_IAM else "API_KEY",
        endpoint=(
            "https://provider.example.test"
            if transport is Transport.AZURE_AI_FOUNDRY
            else None
        ),
        deployment="deployment" if transport is Transport.AZURE_AI_FOUNDRY else None,
        region="us-west-2" if transport is Transport.BEDROCK_IAM else None,
    )
    configuration = ReviewConfiguration(
        version=1, bindings={"provider": binding}, roles={"correctness": "provider"}
    )

    with pytest.raises(ValueError, match="unsupported"):
        configuration.validate_selected_roles(("correctness",))
