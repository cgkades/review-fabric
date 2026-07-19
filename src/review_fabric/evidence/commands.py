"""Explicitly allowlisted command evidence capture."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from review_fabric.domain.models import CommandResult
from review_fabric.errors import DeniedMutationError

_READ_ONLY_COMMANDS = frozenset(
    {
        ("git", "status", "--porcelain"),
        ("mypy", "--version"),
        ("pytest", "--version"),
        ("ruff", "--version"),
    }
)


def _is_allowed(command: tuple[str, ...]) -> bool:
    return command in _READ_ONLY_COMMANDS


def capture_command(repository: Path, command: tuple[str, ...]) -> CommandResult:
    """Run one allowlisted command without invoking a shell."""
    if not command or not _is_allowed(command):
        raise DeniedMutationError("command is not allowlisted for read-only evidence capture")

    executable_command = command
    if command[0] in {"pytest", "ruff", "mypy"}:
        executable_command = (sys.executable, "-m", *command)
    try:
        completed = subprocess.run(
            executable_command,
            cwd=repository,
            capture_output=True,
            check=False,
            encoding="utf-8",
            timeout=30,
        )
    except FileNotFoundError:
        return CommandResult(
            command=command, exit_code=127, stdout="", stderr="command unavailable"
        )
    except subprocess.TimeoutExpired as error:
        return CommandResult(
            command=command,
            exit_code=124,
            stdout=error.stdout or "",
            stderr=error.stderr or "command timed out",
        )
    return CommandResult(
        command=command,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
