from __future__ import annotations

import time
from dataclasses import dataclass

from review_fabric.domain.findings import EvidenceCitation, Finding, Severity
from review_fabric.domain.models import FrozenPatchEvidence, ReviewPackage
from review_fabric.errors import DeniedMutationError
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
    )


def test_first_pass_isolates_reviewers_and_returns_valid_findings() -> None:
    first = FakeReviewer(
        RoleRubric(role="correctness", rubric="check correctness"),
        findings=(
            Finding(
                package_id=package().review_id,
                reviewer_id="correctness",
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
    assert first.received_rubrics == [first.rubric]
    assert second.received_rubrics == [second.rubric]
    assert first.received_packages == [package()]
    assert second.received_packages == [package()]


def test_first_pass_records_finding_from_another_package_as_invalid_output() -> None:
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

    result = run_first_pass(package(), (reviewer,))

    assert result.findings == ()
    assert result.failures[0]["kind"] == "invalid-output"


def test_first_pass_rejects_finding_from_another_reviewer() -> None:
    reviewer = FakeReviewer(
        RoleRubric(role="correctness", rubric="check correctness"),
        findings=(
            Finding(
                package_id=package().review_id,
                reviewer_id="security",
                severity=Severity.SUGGESTION,
                title="Forged reviewer",
                claim="The role is fabricated.",
                evidence=(),
                remediation="Reject it.",
                verification="Run pytest.",
                confidence=0.5,
            ),
        ),
    )

    result = run_first_pass(package(), (reviewer,))

    assert result.findings == ()
    assert result.failures[0]["kind"] == "invalid-output"


def test_first_pass_rejects_citation_not_in_frozen_patch() -> None:
    patch = "diff --git a/a.py b/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+safe = True\n"
    evidence = FrozenPatchEvidence.from_patch(patch)
    review_package = package().model_copy(
        update={"patch_digest": evidence.digest, "patch_evidence": evidence}
    )
    reviewer = FakeReviewer(
        RoleRubric(role="correctness", rubric="check correctness"),
        findings=(
            Finding(
                package_id=review_package.review_id,
                reviewer_id="correctness",
                severity=Severity.CONCERN,
                title="Invented evidence",
                claim="This citation was not supplied.",
                evidence=(
                    EvidenceCitation(path="a.py", start_line=1, end_line=1, excerpt="invented"),
                ),
                remediation="Reject it.",
                verification="Run pytest.",
                confidence=0.9,
            ),
        ),
    )

    result = run_first_pass(review_package, (reviewer,))

    assert result.findings == ()
    assert result.failures[0]["kind"] == "invalid-output"


def test_first_pass_does_not_retry_invalid_reviewer_output() -> None:
    class InvalidThenValidReviewer(FakeReviewer):
        calls: int = 0

        def review(self, review_package: ReviewPackage, rubric: RoleRubric) -> tuple[Finding, ...]:
            self.calls += 1
            return (
                Finding(
                    package_id=review_package.review_id,
                    reviewer_id="forged",
                    severity=Severity.SUGGESTION,
                    title="Forged reviewer",
                    claim="The role is fabricated.",
                    evidence=(),
                    remediation="Reject it.",
                    verification="Run pytest.",
                    confidence=0.5,
                ),
            )

    reviewer = InvalidThenValidReviewer(RoleRubric(role="correctness", rubric="check correctness"))

    result = run_first_pass(package(), (reviewer,), retry_limit=1)

    assert reviewer.calls == 1
    assert result.findings == ()
    assert result.failures[0]["kind"] == "invalid-output"


@dataclass
class _SlowReviewer:
    rubric: RoleRubric
    delay_seconds: float

    def review(self, _package: ReviewPackage, _rubric: RoleRubric) -> tuple[Finding, ...]:
        time.sleep(self.delay_seconds)
        return ()


def test_first_pass_runs_reviewers_concurrently_not_sequentially() -> None:
    delay = 0.2
    reviewers = tuple(
        _SlowReviewer(RoleRubric(role=f"role-{index}", rubric="check"), delay_seconds=delay)
        for index in range(4)
    )

    started = time.monotonic()
    run_first_pass(package(), reviewers)
    elapsed = time.monotonic() - started

    # Sequential execution would take at least len(reviewers) * delay (~0.8s); a
    # concurrent implementation bounds total time to roughly one reviewer's delay.
    assert elapsed < delay * len(reviewers)


@dataclass
class _RaisingReviewer:
    rubric: RoleRubric
    error: Exception

    def review(self, _package: ReviewPackage, _rubric: RoleRubric) -> tuple[Finding, ...]:
        raise self.error


def test_denied_mutation_error_is_classified_by_isinstance_not_class_name() -> None:
    """A same-named-but-unrelated exception class must not be misclassified as
    denied-mutation, and a genuine DeniedMutationError subclass must still be."""

    class LookAlikeDeniedMutationError(Exception):
        """Unrelated exception that merely shares the class name."""

    LookAlikeDeniedMutationError.__name__ = "DeniedMutationError"

    class RealSubclass(DeniedMutationError):
        pass

    lookalike_reviewer = _RaisingReviewer(
        RoleRubric(role="a", rubric="check"), error=LookAlikeDeniedMutationError("nope")
    )
    real_reviewer = _RaisingReviewer(
        RoleRubric(role="b", rubric="check"), error=RealSubclass("blocked")
    )

    result = run_first_pass(package(), (lookalike_reviewer, real_reviewer))

    failures_by_role = {failure["role"]: failure["kind"] for failure in result.failures}
    assert failures_by_role["a"] == "provider-error"
    assert failures_by_role["b"] == "denied-mutation"
