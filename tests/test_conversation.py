from __future__ import annotations

import json

from review_fabric.domain.findings import EvidenceCitation, Finding, Severity
from review_fabric.domain.models import ReviewPackage
from review_fabric.domain.policy import ReviewerRole, ReviewPlan
from review_fabric.errors import ReviewFabricError
from review_fabric.evidence.artifacts import ArtifactStore
from review_fabric.orchestration import execute_plan
from review_fabric.reviewers.base import FakeReviewer, RoleRubric


def package() -> ReviewPackage:
    return ReviewPackage(
        repository_root="/repo",
        base_sha="a" * 40,
        head_sha="b" * 40,
        patch_digest="c" * 64,
        selected_paths=(),
        acceptance_criteria=(),
        constraints=(),
        command_results=(),
    )


class ChallengingReviewer(FakeReviewer):
    def review_challenge(self, dispute: object) -> dict[str, object]:
        assert not hasattr(dispute, "findings")
        return {"disposition": "reject", "evidence": []}


def test_one_challenge_response_and_adjudication_are_persisted(tmp_path) -> None:
    item = Finding(
        package_id=package().review_id,
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
        and event["payload"].get("reason") == "challenge limit reached"
        for event in events
    )
    assert events[-1]["phase"] == "terminal"
    assert events[-1]["payload"]["outcome"] == "ESCALATE"


class FailingReviewer(FakeReviewer):
    def review(self, package: ReviewPackage, rubric: RoleRubric) -> tuple[Finding, ...]:
        raise ReviewFabricError("runtime credential secret-value")


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
