from __future__ import annotations

import json
from pathlib import Path

import pytest

from review_fabric.cli import main
from review_fabric.configuration import ProviderBinding, Transport, load_configuration


def test_load_configuration_from_json_file_without_resolving_credential(tmp_path: Path) -> None:
    path = tmp_path / "review-fabric.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "bindings": {
                    "gemini-lite": {
                        "provider": "gemini",
                        "transport": "gemini",
                        "model": "gemini-2.0-flash-lite",
                        "credential_source": "environment",
                        "credential_ref": "GEMINI_API_KEY",
                    }
                },
                "roles": {"correctness": "gemini-lite"},
            }
        )
    )

    configuration = load_configuration(path)

    assert configuration.binding_for("correctness").transport is Transport.GEMINI
    assert configuration.binding_for("correctness").credential_ref == "GEMINI_API_KEY"


@pytest.mark.parametrize(
    "endpoint",
    (
        "http://gemini.example.test/v1",
        "https://key@provider.example.test/v1",
        "https://provider.example.test/v1?api_key=leak",
        "https://provider.example.test/v1?api%5Fkey=leak",
        "https://provider.example.test/v1?mode=normal;api_key=leak",
        "https://provider.example.test/v1;api_key=leak",
    ),
)
def test_gemini_custom_endpoint_cannot_carry_or_receive_api_key_over_unsafe_url(
    endpoint: str,
) -> None:
    with pytest.raises(ValueError, match="endpoint"):
        ProviderBinding(
            provider="gemini",
            transport=Transport.GEMINI,
            model="light-model",
            credential_source="environment",
            credential_ref="GEMINI_API_KEY",
            endpoint=endpoint,
        )


def test_xai_requires_an_explicit_openai_compatible_endpoint() -> None:
    with pytest.raises(ValueError, match="endpoint"):
        ProviderBinding(
            provider="xai",
            transport=Transport.XAI,
            model="grok-light",
            credential_source="environment",
            credential_ref="XAI_API_KEY",
        )


def test_http_endpoint_allows_only_explicit_loopback_development_http() -> None:
    binding = ProviderBinding(
        provider="local",
        transport=Transport.OPENAI_COMPATIBLE,
        model="test",
        credential_source="environment",
        credential_ref="API_KEY",
        endpoint="http://127.0.0.1:8080/v1",
        allow_local_http=True,
    )
    assert binding.endpoint == "http://127.0.0.1:8080/v1"


def test_cli_redacts_rejected_configuration_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "unsafe.json"
    sentinel = "sk-DO-NOT-LOG-THIS"
    config.write_text(
        json.dumps(
            {
                "version": 1,
                "bindings": {
                    "unsafe": {
                        "provider": "gemini",
                        "transport": "gemini",
                        "model": "light-model",
                        "credential_source": "environment",
                        "credential_ref": sentinel,
                    }
                },
                "roles": {"correctness": "unsafe"},
            }
        )
    )

    assert main(["--config", str(config), str(tmp_path), "base", "head"]) == 2
    assert sentinel not in capsys.readouterr().out
