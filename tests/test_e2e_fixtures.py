"""Curated deterministic fixture scenarios for terminal review behavior."""

from __future__ import annotations

import json
from pathlib import Path

from review_fabric.domain.findings import EvidenceCitation, Finding, Severity
from review_fabric.domain.models import FrozenPatchEvidence, ReviewPackage
from review_fabric.domain.policy import ReviewerRole, ReviewPlan, ReviewPolicy
from review_fabric.evidence.artifacts import ArtifactStore
from review_fabric.orchestration import execute_plan
from review_fabric.reviewers.base import FakeReviewer, RoleRubric


def package() -> ReviewPackage:
    patch = (
        "diff --git a/src/example.py b/src/example.py\n"
        "+++ b/src/example.py\n"
        "@@ -3,0 +4 @@\n"
        "+write()\n"
    )
    evidence = FrozenPatchEvidence.from_patch(patch)
    return ReviewPackage(
        repository_root="/tmp/curated-fixture",
        base_sha="a" * 40,
        head_sha="b" * 40,
        patch_digest=evidence.digest,
        selected_paths=("src/example.py",),
        acceptance_criteria=("review fixture",),
        constraints=("read-only",),
        patch_evidence=evidence,
    )


def finding(package_id: str, reviewer: str, title: str = "Defect") -> Finding:
    return Finding(
        package_id=package_id,
        reviewer_id=reviewer,
        severity=Severity.CONCERN,
        title=title,
        claim="reachable write can duplicate after retry",
        evidence=(
            EvidenceCitation(path="src/example.py", start_line=4, end_line=4, excerpt="write()"),
        ),
        remediation="make the write idempotent",
        verification="add a timeout-after-commit regression test",
        confidence=0.9,
    )


def run_fixture(tmp_path: Path, reviewers: dict[str, FakeReviewer]) -> list[dict[str, object]]:
    review_package = package()
    store = ArtifactStore.create(tmp_path, review_package, patch="diff --git a/example b/example\n")
    execute_plan(
        review_package,
        ReviewPolicy.default().select_plan(review_package.selected_paths),
        reviewers,
        store,
    )
    return [
        json.loads(line) for line in (store.directory / "events.jsonl").read_text().splitlines()
    ]


def test_correct_change_fixture_ends_in_accept(tmp_path: Path) -> None:
    events = run_fixture(
        tmp_path,
        {"correctness": FakeReviewer(RoleRubric("correctness", "correctness"))},
    )

    assert events[-1]["phase"] == "terminal"
    assert events[-1]["payload"]["outcome"] == "ACCEPT"


def test_demonstrated_defect_fixture_ends_in_change_with_artifacts(tmp_path: Path) -> None:
    review_package = package()
    store = ArtifactStore.create(tmp_path, review_package, patch="diff --git a/example b/example\n")
    reviewer = FakeReviewer(
        RoleRubric("correctness", "correctness"),
        findings=(finding(review_package.review_id, "correctness"),),
    )

    execute_plan(
        review_package,
        ReviewPolicy.default().select_plan(review_package.selected_paths),
        {"correctness": reviewer},
        store,
    )

    events = [
        json.loads(line) for line in (store.directory / "events.jsonl").read_text().splitlines()
    ]
    assert any(event["phase"] == "normalized-findings" for event in events)
    assert any(
        event["phase"] == "decision" and event["payload"]["outcome"] == "CHANGE" for event in events
    )
    assert events[-1]["payload"]["outcome"] == "CHANGE"


def test_duplicate_fixture_groups_both_observations(tmp_path: Path) -> None:
    review_package = package()
    store = ArtifactStore.create(tmp_path, review_package, patch="diff --git a/example b/example\n")
    reviewer = FakeReviewer(
        RoleRubric("correctness", "correctness"),
        findings=(finding(review_package.review_id, "correctness"),),
    )
    testing_reviewer = FakeReviewer(
        RoleRubric("testing", "testing"),
        findings=(finding(review_package.review_id, "testing"),),
    )

    execute_plan(
        review_package,
        ReviewPlan(
            risk_indicators=(),
            roles=(ReviewerRole.CORRECTNESS, ReviewerRole.TESTING),
            max_reviewers=2,
            challenge_limit=0,
            retry_limit=0,
        ),
        {"correctness": reviewer, "testing": testing_reviewer},
        store,
    )

    events = [
        json.loads(line) for line in (store.directory / "events.jsonl").read_text().splitlines()
    ]
    normalized = next(event for event in events if event["phase"] == "normalized-findings")
    assert normalized["payload"]["groups"][0]["finding_count"] == 2


def test_unresolved_architectural_fixture_escalates(tmp_path: Path) -> None:
    events = run_fixture(tmp_path, {})

    assert events[-1]["payload"]["outcome"] == "ESCALATE"


def test_unresolved_architectural_fixture_logs_a_warning_for_escalation(
    tmp_path: Path, caplog
) -> None:
    """Operators must see a log signal for ESCALATE without needing to open artifacts."""
    import logging

    with caplog.at_level(logging.WARNING, logger="review_fabric.orchestration"):
        run_fixture(tmp_path, {})

    assert "outcome=ESCALATE" in caplog.text
