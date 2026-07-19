"""Credential lifecycle and summary CLI behavior."""

from __future__ import annotations

from pathlib import Path

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
        command_results=(),
    )
    store = ArtifactStore.create(tmp_path, package, patch="diff --git a/x b/x\n")
    store.record_event("terminal", {"outcome": "ACCEPT"})
    expected = (store.directory / "summary.md").read_text()
    (store.directory / "summary.md").unlink()

    assert main(["summary", str(store.directory)]) == 0
    assert capsys.readouterr().out == expected
