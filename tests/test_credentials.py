"""Credential-resolution safety tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from review_fabric.configuration import ProviderBinding, Transport
from review_fabric.credentials import load_dotenv, resolve_credential
from review_fabric.errors import PolicyRejectionError


def binding() -> ProviderBinding:
    return ProviderBinding(
        provider="openai",
        transport=Transport.OPENAI,
        model="test-model",
        credential_source="environment",
        credential_ref="OPENAI_API_KEY",
    )


def test_process_environment_wins_over_private_gitignored_dotenv(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("OPENAI_API_KEY=dotenv-value\n")
    dotenv.chmod(0o600)

    assert (
        resolve_credential(
            binding(), repository=tmp_path, environment={"OPENAI_API_KEY": "environment-value"}
        )
        == "environment-value"
    )
    assert resolve_credential(binding(), repository=tmp_path, environment={}) == "dotenv-value"


def test_dotenv_rejects_unsafe_or_tracked_files(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("OPENAI_API_KEY=value\n")
    dotenv.chmod(0o644)
    with pytest.raises(PolicyRejectionError, match="unsafe"):
        load_dotenv(dotenv, tmp_path)

    dotenv.chmod(0o600)
    subprocess.run(("git", "init", "-q"), cwd=tmp_path, check=True)
    subprocess.run(("git", "add", ".env"), cwd=tmp_path, check=True)
    with pytest.raises(PolicyRejectionError, match="tracked"):
        load_dotenv(dotenv, tmp_path)


def test_missing_named_credential_never_returns_a_value(tmp_path: Path) -> None:
    with pytest.raises(PolicyRejectionError, match="OPENAI_API_KEY"):
        resolve_credential(binding(), repository=tmp_path, environment={})
