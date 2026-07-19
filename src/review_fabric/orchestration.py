"""Provider-neutral bounded orchestration and artifact phase recording."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from review_fabric.domain.adjudication import ChallengeResponse, adjudicate, make_dispute
from review_fabric.domain.findings import Finding, Severity
from review_fabric.domain.models import ReviewPackage
from review_fabric.domain.normalization import normalize_findings
from review_fabric.domain.policy import MissingReviewerBehavior, ReviewPlan
from review_fabric.errors import InvalidReviewerOutputError, ReviewFabricError
from review_fabric.evidence.artifacts import ArtifactStore
from review_fabric.reviewers.base import Reviewer


@dataclass(frozen=True)
class FirstPassResult:
    findings: tuple[Finding, ...]
    failures: tuple[dict[str, str], ...] = ()


class ChallengeReviewer(Protocol):
    def review_challenge(self, dispute: object) -> dict[str, object]: ...


def run_first_pass(package: ReviewPackage, reviewers: tuple[Reviewer, ...]) -> FirstPassResult:
    """Invoke reviewers independently; peer outputs never enter these calls."""
    findings: list[Finding] = []
    for reviewer in reviewers:
        reviewer_findings = reviewer.review(package, reviewer.rubric)
        if any(finding.package_id != package.review_id for finding in reviewer_findings):
            raise InvalidReviewerOutputError("reviewer finding references a different package")
        findings.extend(reviewer_findings)
    return FirstPassResult(findings=tuple(findings))


def _failure_kind(error: Exception) -> str:
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, InvalidReviewerOutputError | ValueError):
        return "invalid-output"
    if isinstance(error, ReviewFabricError):
        return (
            "denied-mutation"
            if error.__class__.__name__ == "DeniedMutationError"
            else "provider-error"
        )
    return "provider-error"


def execute_plan(
    package: ReviewPackage,
    plan: ReviewPlan,
    reviewers: Mapping[str, Reviewer],
    store: ArtifactStore,
) -> FirstPassResult:
    """Execute a bounded plan and persist every phase without invented outcomes."""
    store.record_event("plan", plan.model_dump(mode="json"))
    missing = [role.value for role in plan.roles if role.value not in reviewers]
    if missing:
        store.record_event("execution-error", {"kind": "missing-reviewer", "roles": missing})
        outcome = (
            "ESCALATE"
            if plan.missing_reviewer_behavior is MissingReviewerBehavior.ESCALATE
            else "INCOMPLETE"
        )
        store.record_event(
            "terminal", {"outcome": outcome, "reason": "required reviewer unavailable"}
        )
        return FirstPassResult((), ({"kind": "missing-reviewer"},))

    try:
        result = run_first_pass(package, tuple(reviewers[role.value] for role in plan.roles))
    except Exception as error:  # Provider SDKs use provider-specific exception hierarchies.
        kind = _failure_kind(error)
        store.record_event("execution-error", {"kind": kind})
        store.record_event("terminal", {"outcome": "ESCALATE", "reason": "review execution failed"})
        return FirstPassResult((), ({"kind": kind},))

    store.record_event(
        "first-pass",
        {
            "status": "completed",
            "findings": [finding.model_dump(mode="json") for finding in result.findings],
        },
    )
    material = tuple(
        finding
        for finding in result.findings
        if finding.severity in {Severity.BLOCKER, Severity.CONCERN}
    )
    if not material:
        store.record_event("decision", {"outcome": "ACCEPT", "reason": "no material findings"})
        store.record_event(
            "terminal", {"outcome": "ACCEPT", "reason": "all selected reviewers completed"}
        )
        return result

    groups = normalize_findings(material)
    store.record_event(
        "normalized-findings",
        {
            "groups": [
                {
                    "id": group.id,
                    "finding_count": len(group.findings),
                    "findings": [item.model_dump(mode="json") for item in group.findings],
                }
                for group in groups
            ]
        },
    )
    if plan.challenge_limit == 0:
        for group in groups:
            representative = group.findings[0]
            store.record_event(
                "decision",
                {
                    "outcome": "CHANGE",
                    "group_id": group.id,
                    "accepted_evidence": [
                        citation.model_dump(mode="json") for citation in representative.evidence
                    ],
                    "remediation": representative.remediation,
                    "verification": representative.verification,
                },
            )
        store.record_event(
            "terminal", {"outcome": "CHANGE", "reason": "material evidence-backed finding"}
        )
        return result
    escalated = False
    for group in groups[:1]:
        dispute = make_dispute(group, "Is the evidence sufficient to require remediation?")
        store.record_event("challenge", dispute.model_dump(mode="json"))
        reviewer = (
            reviewers[group.findings[0].reviewer_id]
            if group.findings[0].reviewer_id in reviewers
            else reviewers[plan.roles[0].value]
        )
        try:
            response_data = cast(ChallengeReviewer, reviewer).review_challenge(dispute)
            response = ChallengeResponse.model_validate(response_data)
            store.record_event("challenge-response", response.model_dump(mode="json"))
        except Exception as error:
            kind = _failure_kind(error)
            store.record_event("challenge-response", {"status": "unavailable", "kind": kind})
            decision = adjudicate(dispute, None)
            escalated = True
        else:
            decision = adjudicate(dispute, response)
            if decision.outcome.value == "ESCALATE":
                escalated = True
        store.record_event("adjudication", decision.model_dump(mode="json"))
    if len(groups) > 1:
        escalated = True
        store.record_event(
            "adjudication", {"outcome": "ESCALATE", "reason": "challenge limit reached"}
        )
    store.record_event(
        "terminal",
        {
            "outcome": "ESCALATE" if escalated else "CHANGE",
            "reason": "bounded evidence adjudication",
        },
    )
    return result
