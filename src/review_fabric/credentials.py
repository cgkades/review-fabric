"""Credential references resolved only at provider invocation time."""

from __future__ import annotations

import getpass
import os
import stat
import subprocess
from pathlib import Path

from review_fabric.configuration import ProviderBinding
from review_fabric.errors import PolicyRejectionError


def load_dotenv(path: Path, repository: Path) -> dict[str, str]:
    """Read an ignored, private dotenv file without altering process environment."""
    resolved = path.resolve()
    if repository.resolve() not in resolved.parents:
        raise PolicyRejectionError("dotenv file must be within repository")
    if not resolved.is_file():
        raise PolicyRejectionError("dotenv file does not exist")
    if stat.S_IMODE(resolved.stat().st_mode) & 0o077:
        raise PolicyRejectionError("dotenv file has unsafe permissions")
    tracked = (
        subprocess.run(
            ("git", "ls-files", "--error-unmatch", str(resolved.relative_to(repository.resolve()))),
            cwd=repository,
            capture_output=True,
        ).returncode
        == 0
    )
    if tracked:
        raise PolicyRejectionError("selected dotenv file is tracked by Git")
    values: dict[str, str] = {}
    for line in resolved.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def resolve_credential(
    binding: ProviderBinding,
    *,
    repository: Path | None = None,
    env_file: Path | None = None,
    environment: dict[str, str] | None = None,
) -> str | None:
    """Resolve named environment/dotenv references, never persist returned values."""
    if binding.credential_source in {"none", "aws-chain", "workload"}:
        return None
    reference = (binding.credential_ref or "").removeprefix("env:")
    if binding.credential_source == "keychain":
        try:
            value = _keyring().get_password("review-fabric", reference)  # type: ignore[attr-defined]
        except Exception as error:
            raise keychain_unavailable() from error
        if value:
            return value
        raise PolicyRejectionError(
            f"credential unavailable; configure keychain profile {reference}"
        )
    if binding.credential_source not in {"environment", "env", "dotenv"}:
        raise PolicyRejectionError(
            f"credential source {binding.credential_source} requires an external supported adapter"
        )
    reference = (binding.credential_ref or "").removeprefix("env:")
    process = os.environ if environment is None else environment
    if value := process.get(reference):
        return value
    if repository:
        candidate = env_file or repository / ".env"
        if candidate.exists():
            return load_dotenv(candidate, repository).get(reference)
    raise PolicyRejectionError(
        f"credential unavailable; configure named environment variable {reference}"
    )


def _keyring() -> object:
    try:
        import keyring  # type: ignore[import-not-found]
    except ImportError as error:
        raise keychain_unavailable() from error
    return keyring


def auth_set(provider: str, profile: str, secret: str | None = None) -> None:
    """Store an interactively supplied secret only in the OS keychain."""
    value = secret if secret is not None else getpass.getpass(f"{provider} API key: ")
    if not value:
        raise PolicyRejectionError("credential value was not provided")
    try:
        _keyring().set_password("review-fabric", f"{provider}:{profile}", value)  # type: ignore[attr-defined]
    except Exception as error:
        raise keychain_unavailable() from error


def auth_status(provider: str, profile: str) -> bool:
    try:
        return bool(_keyring().get_password("review-fabric", f"{provider}:{profile}"))  # type: ignore[attr-defined]
    except Exception as error:
        raise keychain_unavailable() from error


def auth_remove(provider: str, profile: str) -> None:
    try:
        _keyring().delete_password("review-fabric", f"{provider}:{profile}")  # type: ignore[attr-defined]
    except Exception as error:
        raise keychain_unavailable() from error


def keychain_unavailable() -> PolicyRejectionError:
    return PolicyRejectionError(
        "OS credential store unavailable; use a named environment, dotenv, workload identity, "
        "or supported external source"
    )
