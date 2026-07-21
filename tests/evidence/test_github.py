from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import review_fabric.evidence.github as github_module
from review_fabric.errors import PolicyRejectionError
from review_fabric.evidence.github import PullRequestEvidence, resolve_pull_request


def _gh_view_response(**overrides: object) -> bytes:
    data = {
        "number": 42,
        "baseRefOid": "a" * 40,
        "headRefOid": "b" * 40,
        "baseRefName": "main",
        "url": "https://github.com/example/repo/pull/42",
    }
    data.update(overrides)
    return json.dumps(data).encode()


def test_resolve_pull_request_returns_exact_base_and_head_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(command: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess:
        calls.append(tuple(command))
        if command[0] == "gh":
            return subprocess.CompletedProcess(command, 0, stdout=_gh_view_response(), stderr=b"")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(github_module.subprocess, "run", fake_run)

    result = resolve_pull_request(tmp_path, "42")

    assert result == PullRequestEvidence(
        number="42",
        base_sha="a" * 40,
        head_sha="b" * 40,
        base_ref="main",
        url="https://github.com/example/repo/pull/42",
    )
    gh_call, fetch_call = calls
    assert gh_call[:3] == ("gh", "pr", "view")
    assert gh_call[3] == "42"
    assert fetch_call[:4] == ("git", "fetch", "--no-tags", "--quiet")
    assert fetch_call[4] == "origin"
    assert "refs/pull/42/head" in fetch_call[5]
    assert "refs/review-fabric/pr-42-head" in fetch_call[5]
    assert "refs/heads/main" in fetch_call[6]
    assert "refs/review-fabric/pr-42-base" in fetch_call[6]


def test_resolve_pull_request_passes_through_the_reference_and_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(command: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess:
        calls.append(tuple(command))
        if command[0] == "gh":
            return subprocess.CompletedProcess(command, 0, stdout=_gh_view_response(), stderr=b"")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(github_module.subprocess, "run", fake_run)

    resolve_pull_request(tmp_path, "https://github.com/example/repo/pull/42", remote="upstream")

    gh_call, fetch_call = calls
    assert gh_call[3] == "https://github.com/example/repo/pull/42"
    assert fetch_call[4] == "upstream"


def test_resolve_pull_request_requires_gh_to_be_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError("gh")

    monkeypatch.setattr(github_module.subprocess, "run", fake_run)

    with pytest.raises(PolicyRejectionError, match="gh CLI is not installed"):
        resolve_pull_request(tmp_path, "42")


def test_resolve_pull_request_surfaces_gh_failure_without_leaking_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            command, 1, stdout=b"", stderr=b"secret-token-in-stderr-abc123"
        )

    monkeypatch.setattr(github_module.subprocess, "run", fake_run)

    with pytest.raises(PolicyRejectionError, match="could not resolve pull request") as error:
        resolve_pull_request(tmp_path, "42")
    assert "secret-token-in-stderr-abc123" not in str(error.value)


def test_resolve_pull_request_rejects_malformed_gh_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(command, 0, stdout=b"not json", stderr=b"")

    monkeypatch.setattr(github_module.subprocess, "run", fake_run)

    with pytest.raises(PolicyRejectionError, match="unexpected output"):
        resolve_pull_request(tmp_path, "42")


def test_resolve_pull_request_rejects_gh_output_missing_a_required_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess:
        payload = json.loads(_gh_view_response())
        del payload["headRefOid"]
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(payload).encode(), stderr=b""
        )

    monkeypatch.setattr(github_module.subprocess, "run", fake_run)

    with pytest.raises(PolicyRejectionError, match="unexpected output"):
        resolve_pull_request(tmp_path, "42")


def test_resolve_pull_request_times_out_cleanly_on_gh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess:
        raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout", 30))

    monkeypatch.setattr(github_module.subprocess, "run", fake_run)

    with pytest.raises(PolicyRejectionError, match="timed out"):
        resolve_pull_request(tmp_path, "42")


def test_resolve_pull_request_surfaces_fetch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess:
        if command[0] == "gh":
            return subprocess.CompletedProcess(command, 0, stdout=_gh_view_response(), stderr=b"")
        raise subprocess.CalledProcessError(128, command, stderr=b"fatal: could not fetch")

    monkeypatch.setattr(github_module.subprocess, "run", fake_run)

    with pytest.raises(PolicyRejectionError, match="could not fetch pull request 42"):
        resolve_pull_request(tmp_path, "42")


def test_resolve_pull_request_times_out_cleanly_on_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess:
        if command[0] == "gh":
            return subprocess.CompletedProcess(command, 0, stdout=_gh_view_response(), stderr=b"")
        raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout", 60))

    monkeypatch.setattr(github_module.subprocess, "run", fake_run)

    with pytest.raises(PolicyRejectionError, match="fetching pull request 42.*timed out"):
        resolve_pull_request(tmp_path, "42")
