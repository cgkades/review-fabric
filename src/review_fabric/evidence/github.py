"""Optional GitHub pull request resolution via the gh CLI.

This is the one deliberately network-touching evidence path in review-fabric —
everything else is local-only unless a provider is separately configured with
--config. It is opt-in (only used for --pr) and delegates entirely to whatever
authentication the operator has already configured via `gh auth login`; this
module never scrapes, requests, stores, or otherwise handles a GitHub credential
itself.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from review_fabric.errors import PolicyRejectionError
from review_fabric.evidence.git import _safe_git_environment

_GH_TIMEOUT_SECONDS = 30
_FETCH_TIMEOUT_SECONDS = 60
_REF_NAMESPACE = "refs/review-fabric/pr"
_PR_VIEW_FIELDS = "number,baseRefOid,headRefOid,baseRefName,url"


@dataclass(frozen=True)
class PullRequestEvidence:
    """The exact commits a GitHub pull request currently points at, plus enough
    metadata to record a stable audit constraint."""

    number: str
    base_sha: str
    head_sha: str
    base_ref: str
    url: str


def resolve_pull_request(
    repository: Path, reference: str, *, remote: str = "origin"
) -> PullRequestEvidence:
    """Resolve a GitHub pull request (by number, URL, or branch name — anything
    `gh pr view` accepts) to its exact base/head commits, fetching those commits
    into a private, tool-owned ref namespace if not already present locally.

    Requires the gh CLI to be installed and already authenticated; never falls
    back to an unauthenticated request or any form of credential scraping.
    """
    data = _gh_pr_view(repository, reference)
    _fetch_pull_request_commits(repository, remote, data["number"], data["baseRefName"])
    return PullRequestEvidence(
        number=str(data["number"]),
        base_sha=data["baseRefOid"],
        head_sha=data["headRefOid"],
        base_ref=data["baseRefName"],
        url=data["url"],
    )


def _gh_pr_view(repository: Path, reference: str) -> dict[str, object]:
    try:
        completed = subprocess.run(
            ("gh", "pr", "view", reference, "--json", _PR_VIEW_FIELDS),
            cwd=repository,
            capture_output=True,
            check=False,
            timeout=_GH_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as error:
        raise PolicyRejectionError(
            "gh CLI is not installed; install and run `gh auth login` to use --pr"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise PolicyRejectionError("gh pr view timed out") from error
    if completed.returncode:
        raise PolicyRejectionError(
            f"could not resolve pull request {reference!r} via gh; ensure gh is "
            "authenticated (`gh auth status`) and the pull request exists"
        )
    try:
        data = json.loads(completed.stdout)
        if not isinstance(data, dict):
            raise TypeError("gh pr view output is not a JSON object")
        for key in ("number", "baseRefOid", "headRefOid", "baseRefName", "url"):
            if not data.get(key):
                raise KeyError(key)
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise PolicyRejectionError("gh pr view returned unexpected output") from error
    return data


def _fetch_pull_request_commits(
    repository: Path, remote: str, number: object, base_ref: str
) -> None:
    """Fetch exactly the PR head and its base branch tip into a private, tool-owned
    ref namespace — never touching any ref the operator uses themselves. GitHub
    always exposes refs/pull/<number>/head on the base repository regardless of
    whether the PR originates from a fork."""
    try:
        subprocess.run(
            (
                "git",
                "fetch",
                "--no-tags",
                "--quiet",
                remote,
                f"+refs/pull/{number}/head:{_REF_NAMESPACE}-{number}-head",
                f"+refs/heads/{base_ref}:{_REF_NAMESPACE}-{number}-base",
            ),
            cwd=repository,
            capture_output=True,
            check=True,
            timeout=_FETCH_TIMEOUT_SECONDS,
            env=_safe_git_environment(),
        )
    except subprocess.CalledProcessError as error:
        raise PolicyRejectionError(
            f"could not fetch pull request {number} commits from remote {remote!r}"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise PolicyRejectionError(f"fetching pull request {number} commits timed out") from error
