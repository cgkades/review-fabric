from __future__ import annotations

import json

from review_fabric.domain.findings import EvidenceCitation, Finding, Severity
from review_fabric.domain.models import FrozenPatchEvidence, ReviewPackage
from review_fabric.domain.policy import ReviewerRole, ReviewPlan
from review_fabric.errors import ReviewFabricError
from review_fabric.evidence.artifacts import ArtifactStore
from review_fabric.orchestration import execute_plan
from review_fabric.reviewers.base import FakeReviewer, RoleRubric


def package() -> ReviewPackage:
    patch = "diff --git a/src/a.py b/src/a.py\n+++ b/src/a.py\n@@ -0,0 +1 @@\n+bad\n"
    evidence = FrozenPatchEvidence.from_patch(patch)
    return ReviewPackage(
        repository_root="/repo",
        base_sha="a" * 40,
        head_sha="b" * 40,
        patch_digest=evidence.digest,
        selected_paths=(),
        acceptance_criteria=(),
        constraints=(),
        patch_evidence=evidence,
    )


class ChallengingReviewer(FakeReviewer):
    def review_challenge(self, dispute: object) -> dict[str, object]:
        assert not hasattr(dispute, "findings")
        return {"disposition": "reject", "evidence": []}


def test_one_challenge_response_and_adjudication_are_persisted(tmp_path) -> None:
    item = Finding(
        package_id=package().review_id,
        reviewer_id="correctness",
        severity=Severity.CONCERN,
        title="Bug",
        claim="broken",
        evidence=(EvidenceCitation(path="src/a.py", start_line=1, end_line=1, excerpt="bad"),),
        remediation="fix",
        verification="test",
        confidence=0.9,
    )
    reviewer = ChallengingReviewer(RoleRubric("correctness", "review"), (item,))
    store = ArtifactStore.create(tmp_path, package(), patch="")
    plan = ReviewPlan(
        risk_indicators=(),
        roles=(ReviewerRole.CORRECTNESS,),
        max_reviewers=1,
        challenge_limit=1,
        retry_limit=0,
    )
    execute_plan(package(), plan, {"correctness": reviewer}, store)
    phases = [
        json.loads(line)["phase"]
        for line in (store.directory / "events.jsonl").read_text().splitlines()
    ]
    assert "challenge" in phases and "challenge-response" in phases and "adjudication" in phases
    assert phases.count("challenge") == 1
    events = [
        json.loads(line) for line in (store.directory / "events.jsonl").read_text().splitlines()
    ]
    assert (
        next(event for event in events if event["phase"] == "adjudication")["payload"]["outcome"]
        == "ESCALATE"
    )
    assert events[-1]["payload"]["outcome"] == "ESCALATE"


def test_challenge_failure_and_limit_are_explicit_escalations(tmp_path) -> None:
    first = Finding(
        package_id=package().review_id,
        reviewer_id="correctness",
        severity=Severity.CONCERN,
        title="First",
        claim="broken",
        evidence=(EvidenceCitation(path="src/a.py", start_line=1, end_line=1, excerpt="bad"),),
        remediation="fix",
        verification="test",
        confidence=0.9,
    )
    second = first.model_copy(update={"title": "Second"})
    store = ArtifactStore.create(tmp_path, package(), patch="")
    reviewer = FakeReviewer(RoleRubric("correctness", "review"), (first, second))
    plan = ReviewPlan(
        risk_indicators=(),
        roles=(ReviewerRole.CORRECTNESS,),
        max_reviewers=1,
        challenge_limit=1,
        retry_limit=0,
    )
    execute_plan(package(), plan, {"correctness": reviewer}, store)
    events = [
        json.loads(line) for line in (store.directory / "events.jsonl").read_text().splitlines()
    ]
    assert sum(event["phase"] == "challenge" for event in events) == 1
    assert any(
        event["phase"] == "challenge-response" and event["payload"]["status"] == "unavailable"
        for event in events
    )
    assert any(
        event["phase"] == "adjudication"
        and event["payload"].get("unresolved_question") == "challenge limit reached"
        for event in events
    )
    assert events[-1]["phase"] == "terminal"
    assert events[-1]["payload"]["outcome"] == "ESCALATE"


class FailingReviewer(FakeReviewer):
    def review(self, package: ReviewPackage, rubric: RoleRubric) -> tuple[Finding, ...]:
        raise ReviewFabricError("runtime credential secret-value")


class FlakyReviewer(FakeReviewer):
    calls: int = 0

    def review(self, package: ReviewPackage, rubric: RoleRubric) -> tuple[Finding, ...]:
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError()
        return super().review(package, rubric)


def test_execution_error_is_categorized_without_exception_text(tmp_path) -> None:
    store = ArtifactStore.create(tmp_path, package(), patch="")
    plan = ReviewPlan(
        risk_indicators=(),
        roles=(ReviewerRole.CORRECTNESS,),
        max_reviewers=1,
        challenge_limit=0,
        retry_limit=0,
    )
    execute_plan(
        package(),
        plan,
        {"correctness": FailingReviewer(RoleRubric("correctness", "review"))},
        store,
    )
    events = (store.directory / "events.jsonl").read_text()
    assert "secret-value" not in events
    assert '"kind":"provider-error"' in events
    assert json.loads(events.splitlines()[-1])["payload"]["outcome"] == "ESCALATE"


def test_retry_limit_retries_transient_provider_failure(tmp_path) -> None:
    store = ArtifactStore.create(tmp_path, package(), patch="")
    reviewer = FlakyReviewer(RoleRubric("correctness", "review"))
    plan = ReviewPlan(
        risk_indicators=(),
        roles=(ReviewerRole.CORRECTNESS,),
        max_reviewers=1,
        challenge_limit=0,
        retry_limit=1,
    )

    execute_plan(package(), plan, {"correctness": reviewer}, store)

    events = [
        json.loads(line) for line in (store.directory / "events.jsonl").read_text().splitlines()
    ]
    assert reviewer.calls == 2
    first_pass = next(event for event in events if event["phase"] == "first-pass")
    assert first_pass["payload"]["status"] == "completed"
    assert events[-1]["payload"]["outcome"] == "ACCEPT"


def test_partial_first_pass_results_are_persisted_before_escalation(tmp_path) -> None:
    item = Finding(
        package_id=package().review_id,
        reviewer_id="correctness",
        severity=Severity.SUGGESTION,
        title="Coverage",
        claim="A branch is untested.",
        evidence=(),
        remediation="Add a test.",
        verification="Run pytest.",
        confidence=0.5,
    )
    store = ArtifactStore.create(tmp_path, package(), patch="")
    plan = ReviewPlan(
        risk_indicators=(),
        roles=(ReviewerRole.CORRECTNESS, ReviewerRole.TESTING),
        max_reviewers=2,
        challenge_limit=0,
        retry_limit=0,
    )

    execute_plan(
        package(),
        plan,
        {
            "correctness": FakeReviewer(RoleRubric("correctness", "review"), (item,)),
            "testing": FailingReviewer(RoleRubric("testing", "review")),
        },
        store,
    )

    events = [
        json.loads(line) for line in (store.directory / "events.jsonl").read_text().splitlines()
    ]
    first_pass = next(event for event in events if event["phase"] == "first-pass")
    assert first_pass["payload"]["status"] == "incomplete"
    assert first_pass["payload"]["findings"][0]["title"] == "Coverage"
    assert events[-1]["payload"]["outcome"] == "ESCALATE"


def test_challenge_against_a_reviewer_without_challenge_support_is_explicit(tmp_path) -> None:
    """A structural capability gap must be labeled distinctly, not an incidental AttributeError."""
    item = Finding(
        package_id=package().review_id,
        reviewer_id="correctness",
        severity=Severity.CONCERN,
        title="Bug",
        claim="broken",
        evidence=(EvidenceCitation(path="src/a.py", start_line=1, end_line=1, excerpt="bad"),),
        remediation="fix",
        verification="test",
        confidence=0.9,
    )
    reviewer = FakeReviewer(RoleRubric("correctness", "review"), (item,))
    assert not hasattr(reviewer, "review_challenge")
    store = ArtifactStore.create(tmp_path, package(), patch="")
    plan = ReviewPlan(
        risk_indicators=(),
        roles=(ReviewerRole.CORRECTNESS,),
        max_reviewers=1,
        challenge_limit=1,
        retry_limit=0,
    )

    execute_plan(package(), plan, {"correctness": reviewer}, store)

    events = [
        json.loads(line) for line in (store.directory / "events.jsonl").read_text().splitlines()
    ]
    response = next(event for event in events if event["phase"] == "challenge-response")
    assert response["payload"] == {"status": "unavailable", "kind": "challenge-unsupported"}


def test_low_confidence_material_finding_escalates_instead_of_auto_changing(tmp_path) -> None:
    """A material finding the reviewer itself is not confident about must force
    ESCALATE for a human, not silently drive an automatic CHANGE (or be dropped)."""
    item = Finding(
        package_id=package().review_id,
        reviewer_id="correctness",
        severity=Severity.CONCERN,
        title="Bug",
        claim="broken",
        evidence=(EvidenceCitation(path="src/a.py", start_line=1, end_line=1, excerpt="bad"),),
        remediation="fix",
        verification="test",
        confidence=0.3,
    )
    store = ArtifactStore.create(tmp_path, package(), patch="")
    reviewer = FakeReviewer(RoleRubric("correctness", "review"), (item,))
    plan = ReviewPlan(
        risk_indicators=(),
        roles=(ReviewerRole.CORRECTNESS,),
        max_reviewers=1,
        challenge_limit=0,
        retry_limit=0,
    )

    execute_plan(package(), plan, {"correctness": reviewer}, store)

    events = [
        json.loads(line) for line in (store.directory / "events.jsonl").read_text().splitlines()
    ]
    assert any(event["phase"] == "low-confidence-findings" for event in events)
    assert not any(event["phase"] == "normalized-findings" for event in events)
    assert events[-1]["phase"] == "terminal"
    assert events[-1]["payload"]["outcome"] == "ESCALATE"


def test_confidence_at_or_above_threshold_still_proceeds_to_change(tmp_path) -> None:
    item = Finding(
        package_id=package().review_id,
        reviewer_id="correctness",
        severity=Severity.CONCERN,
        title="Bug",
        claim="broken",
        evidence=(EvidenceCitation(path="src/a.py", start_line=1, end_line=1, excerpt="bad"),),
        remediation="fix",
        verification="test",
        confidence=0.5,
    )
    store = ArtifactStore.create(tmp_path, package(), patch="")
    reviewer = FakeReviewer(RoleRubric("correctness", "review"), (item,))
    plan = ReviewPlan(
        risk_indicators=(),
        roles=(ReviewerRole.CORRECTNESS,),
        max_reviewers=1,
        challenge_limit=0,
        retry_limit=0,
    )

    execute_plan(package(), plan, {"correctness": reviewer}, store)

    events = [
        json.loads(line) for line in (store.directory / "events.jsonl").read_text().splitlines()
    ]
    assert events[-1]["payload"]["outcome"] == "CHANGE"


def test_overlapping_citations_from_two_reviewers_do_not_force_a_spurious_escalation(
    tmp_path,
) -> None:
    """Two reviewers citing the same defect with citations differing only by an
    off-by-one boundary must be treated as one confirmable group, not two, so a
    successful challenge resolves to CHANGE instead of being forced to ESCALATE
    purely because more than one FindingGroup existed."""

    def citation(start: int, end: int) -> EvidenceCitation:
        return EvidenceCitation(
            path="src/a.py",
            start_line=start,
            end_line=end,
            excerpt="\n".join("bad" for _ in range(start, end + 1)),
        )

    patch = (
        "diff --git a/src/a.py b/src/a.py\n"
        "+++ b/src/a.py\n"
        "@@ -0,0 +1,13 @@\n" + "+bad\n" * 13
    )
    evidence = FrozenPatchEvidence.from_patch(patch)
    review_package = package().model_copy(
        update={"patch_digest": evidence.digest, "patch_evidence": evidence}
    )

    first = Finding(
        package_id=review_package.review_id,
        reviewer_id="correctness",
        severity=Severity.CONCERN,
        title="Bug",
        claim="broken",
        evidence=(citation(10, 12),),
        remediation="fix",
        verification="test",
        confidence=0.9,
    )
    second = first.model_copy(update={"reviewer_id": "testing", "evidence": (citation(10, 13),)})

    class ConfirmingReviewer(FakeReviewer):
        def review_challenge(self, dispute: object) -> dict[str, object]:
            return {
                "disposition": "confirm",
                "evidence": [citation.model_dump() for citation in dispute.citations],
            }

    correctness_reviewer = ConfirmingReviewer(RoleRubric("correctness", "review"), (first,))
    testing_reviewer = FakeReviewer(RoleRubric("testing", "review"), (second,))
    store = ArtifactStore.create(tmp_path, review_package, patch=patch)
    plan = ReviewPlan(
        risk_indicators=(),
        roles=(ReviewerRole.CORRECTNESS, ReviewerRole.TESTING),
        max_reviewers=2,
        challenge_limit=1,
        retry_limit=0,
    )

    execute_plan(
        review_package,
        plan,
        {"correctness": correctness_reviewer, "testing": testing_reviewer},
        store,
    )

    events = [
        json.loads(line) for line in (store.directory / "events.jsonl").read_text().splitlines()
    ]
    normalized = next(event for event in events if event["phase"] == "normalized-findings")
    assert len(normalized["payload"]["groups"]) == 1
    assert normalized["payload"]["groups"][0]["finding_count"] == 2
    assert sum(event["phase"] == "challenge" for event in events) == 1
    assert events[-1]["phase"] == "terminal"
    assert events[-1]["payload"]["outcome"] == "CHANGE"
