from __future__ import annotations

import pytest

from review_fabric.domain.findings import Finding, Severity
from review_fabric.domain.models import ReviewPackage
from review_fabric.errors import InvalidReviewerOutputError
from review_fabric.orchestration import run_first_pass
from review_fabric.reviewers.base import FakeReviewer, RoleRubric


def package() -> ReviewPackage:
    return ReviewPackage(
        repository_root="/tmp/repo",
        base_sha="a" * 40,
        head_sha="b" * 40,
        patch_digest="c" * 64,
        selected_paths=(),
        acceptance_criteria=(),
        constraints=(),
        command_results=(),
    )


def test_first_pass_isolates_reviewers_and_returns_valid_findings() -> None:
    first = FakeReviewer(
        RoleRubric(role="correctness", rubric="check correctness"),
        findings=(
            Finding(
                package_id=package().review_id,
                severity=Severity.SUGGESTION,
                title="Add a test",
                claim="A branch is untested.",
                evidence=(),
                remediation="Add a regression test.",
                verification="Run pytest.",
                confidence=0.5,
            ),
        ),
    )
    second = FakeReviewer(RoleRubric(role="security", rubric="check security"))

    result = run_first_pass(package(), (first, second))

    assert result.findings == first.findings
    assert first.received_peer_outputs == ()
    assert second.received_peer_outputs == ()
    assert first.received_packages == [package()]
    assert second.received_packages == [package()]


def test_first_pass_rejects_finding_from_another_package() -> None:
    reviewer = FakeReviewer(
        RoleRubric(role="correctness", rubric="check correctness"),
        findings=(
            Finding(
                package_id="d" * 64,
                severity=Severity.SUGGESTION,
                title="Stale finding",
                claim="It belongs to another package.",
                evidence=(),
                remediation="Discard it.",
                verification="Check package identity.",
                confidence=0.5,
            ),
        ),
    )

    with pytest.raises(InvalidReviewerOutputError, match="package"):
        run_first_pass(package(), (reviewer,))
