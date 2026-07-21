"""Credential lifecycle and summary CLI behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from review_fabric.cli import main
from review_fabric.domain.models import ReviewPackage
from review_fabric.evidence.artifacts import ArtifactStore


def test_auth_commands_use_named_profile_without_secret_arguments(monkeypatch) -> None:
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "review_fabric.cli.auth_set",
        lambda provider, profile: calls.append(("set", provider, profile)),
    )
    monkeypatch.setattr(
        "review_fabric.cli.auth_status",
        lambda provider, profile: calls.append(("status", provider, profile)) or True,
    )
    monkeypatch.setattr(
        "review_fabric.cli.auth_remove",
        lambda provider, profile: calls.append(("remove", provider, profile)),
    )

    assert main(["auth", "set", "openai", "--profile", "work"]) == 0
    assert main(["auth", "status", "openai", "--profile", "work"]) == 0
    assert main(["auth", "remove", "openai", "--profile", "work"]) == 0
    assert calls == [
        ("set", "openai", "work"),
        ("status", "openai", "work"),
        ("remove", "openai", "work"),
    ]


def test_summary_command_regenerates_persisted_summary(tmp_path: Path, capsys) -> None:
    package = ReviewPackage(
        repository_root="/tmp/repo",
        base_sha="a" * 40,
        head_sha="b" * 40,
        patch_digest="c" * 64,
        selected_paths=(),
        acceptance_criteria=(),
        constraints=("read-only",),
    )
    store = ArtifactStore.create(tmp_path, package, patch="diff --git a/x b/x\n")
    store.record_event("terminal", {"outcome": "ACCEPT"})
    expected = (store.directory / "summary.md").read_text()
    (store.directory / "summary.md").unlink()

    assert main(["summary", str(store.directory)]) == 0
    assert capsys.readouterr().out == expected


def test_main_with_explicit_empty_argument_list_does_not_fall_back_to_process_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit `main([])` must behave as "no arguments supplied" (usage error),
    never silently substitute the current process's real sys.argv, which the type
    signature (list[str] | None) implies is a distinct, meaningful input from None."""
    monkeypatch.setattr("sys.argv", ["review-fabric", "auth", "status", "openai"])
    calls: list[object] = []
    monkeypatch.setattr(
        "review_fabric.cli.auth_status", lambda *args: calls.append(args) or True
    )

    assert main([]) == 2
    assert calls == []


def test_cli_argparse_usage_errors_use_the_uniform_error_prefix(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An argparse-level usage error (not just manual validation errors) must be
    printed with the same "review-fabric: " prefix as every other expected failure,
    so a log scraper grepping for that prefix never misses it."""
    exit_code = main(["--unrecognized-flag"])

    assert exit_code == 2
    out = capsys.readouterr().out
    assert out.startswith("review-fabric: ")
