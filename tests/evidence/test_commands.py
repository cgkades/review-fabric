from __future__ import annotations

from pathlib import Path

import pytest

from review_fabric.errors import DeniedMutationError
from review_fabric.evidence.commands import capture_command


def test_capture_command_records_an_allowlisted_read_only_command(tmp_path: Path) -> None:
    result = capture_command(tmp_path, ("pytest", "--version"))

    assert result.command == ("pytest", "--version")
    assert result.exit_code == 0
    assert "pytest" in result.stdout
    assert result.stderr == ""


def test_capture_command_does_not_import_modules_from_reviewed_repository(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    (tmp_path / "pytest.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n",
        encoding="utf-8",
    )

    result = capture_command(tmp_path, ("pytest", "--version"))

    assert result.exit_code == 0
    assert not marker.exists()


@pytest.mark.parametrize(
    "command",
    [
        ("git", "commit", "-m", "not allowed"),
        ("git", "push"),
        ("rm", "-rf", "."),
        ("ruff", "format", "."),
        ("ruff", "check", "--fix", "."),
        ("pytest", "--cache-clear"),
        ("git", "diff", "--output=overwritten.patch"),
    ],
)
def test_capture_command_rejects_mutating_or_unapproved_command(
    tmp_path: Path, command: tuple[str, ...]
) -> None:
    with pytest.raises(DeniedMutationError):
        capture_command(tmp_path, command)
