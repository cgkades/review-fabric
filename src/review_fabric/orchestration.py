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


def run_first_pass(
    package: ReviewPackage, reviewers: tuple[Reviewer, ...], *, retry_limit: int = 0
) -> FirstPassResult:
    """Invoke reviewers independently; peer outputs never enter these calls."""
    findings: list[Finding] = []
    failures: list[dict[str, str]] = []
    for reviewer in reviewers:
        for attempt in range(retry_limit + 1):
            try:
                reviewer_findings = reviewer.review(package, reviewer.rubric)
                _validate_reviewer_findings(package, reviewer, reviewer_findings)
            except Exception as error:  # Provider SDKs use provider-specific exception hierarchies.
                if isinstance(error, (InvalidReviewerOutputError, ValueError)) or (
                    attempt == retry_limit
                ):
                    failures.append(
                        {
                            "role": reviewer.rubric.role,
                            "kind": _failure_kind(error),
                            "attempts": str(attempt + 1),
                        }
                    )
                    break
                continue
            findings.extend(reviewer_findings)
            break
    return FirstPassResult(findings=tuple(findings), failures=tuple(failures))


def _validate_reviewer_findings(
    package: ReviewPackage, reviewer: Reviewer, findings: tuple[Finding, ...]
) -> None:
    if any(finding.package_id != package.review_id for finding in findings):
        raise InvalidReviewerOutputError("reviewer finding references a different package")
    if any(finding.reviewer_id != reviewer.rubric.role for finding in findings):
        raise InvalidReviewerOutputError("reviewer finding references a different reviewer")
    evidence = package.patch_evidence
    if any(finding.evidence for finding in findings) and evidence is None:
        raise InvalidReviewerOutputError("reviewer finding requires frozen patch evidence")
    if evidence and any(
        not evidence.supports_citation(citation.model_dump(mode="json"))
        for finding in findings
        for citation in finding.evidence
    ):
        raise InvalidReviewerOutputError(
            "reviewer finding citation is not present in frozen patch evidence"
        )


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

    result = run_first_pass(
        package,
        tuple(reviewers[role.value] for role in plan.roles),
        retry_limit=plan.retry_limit,
    )
    store.record_event(
        "first-pass",
        {
            "status": "completed" if not result.failures else "incomplete",
            "findings": [finding.model_dump(mode="json") for finding in result.findings],
            "failures": list(result.failures),
        },
    )
    if result.failures:
        for failure in result.failures:
            store.record_event("execution-error", failure)
        store.record_event("terminal", {"outcome": "ESCALATE", "reason": "review execution failed"})
        return result
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
    for index, group in enumerate(groups):
        if index >= plan.challenge_limit:
            store.record_event(
                "adjudication",
                {
                    "outcome": "ESCALATE",
                    "group_id": group.id,
                    "unresolved_question": "challenge limit reached",
                },
            )
            escalated = True
            continue
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
    store.record_event(
        "terminal",
        {
            "outcome": "ESCALATE" if escalated else "CHANGE",
            "reason": "bounded evidence adjudication",
        },
    )
    return result
