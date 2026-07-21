"""Provider-neutral bounded orchestration and artifact phase recording."""

from __future__ import annotations

import concurrent.futures
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from review_fabric.domain.adjudication import ChallengeResponse, adjudicate, make_dispute
from review_fabric.domain.findings import Finding, Severity
from review_fabric.domain.models import ReviewPackage
from review_fabric.domain.normalization import normalize_findings
from review_fabric.domain.policy import MissingReviewerBehavior, ReviewPlan
from review_fabric.errors import DeniedMutationError, InvalidReviewerOutputError, ReviewFabricError
from review_fabric.evidence.artifacts import ArtifactStore
from review_fabric.reviewers.base import Reviewer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FirstPassResult:
    findings: tuple[Finding, ...]
    failures: tuple[dict[str, str], ...] = ()


@runtime_checkable
class ChallengeReviewer(Protocol):
    def review_challenge(self, dispute: object) -> dict[str, object]: ...


class _ChallengeUnsupportedError(Exception):
    """Internal sentinel: the selected reviewer has no challenge capability at all."""


def run_first_pass(
    package: ReviewPackage, reviewers: tuple[Reviewer, ...], *, retry_limit: int = 0
) -> FirstPassResult:
    """Invoke reviewers independently; peer outputs never enter these calls.

    Reviewers are invoked concurrently (each is an independent, typically
    network-bound call with its own plan-bounded timeout), so total wall-clock time is
    governed by the slowest single reviewer rather than the sum across every
    configured reviewer. Results are still processed in the original, deterministic
    role order regardless of completion order. Each reviewer is retried up to
    retry_limit additional times on a transient-looking failure; invalid output
    (a protocol violation, never a transient condition) is not retried.
    """
    if not reviewers:
        return FirstPassResult(findings=(), failures=())

    def call(reviewer: Reviewer) -> tuple[tuple[Finding, ...] | None, dict[str, str] | None]:
        last_error: Exception | None = None
        for attempt in range(retry_limit + 1):
            try:
                reviewer_findings = reviewer.review(package, reviewer.rubric)
                _validate_reviewer_findings(package, reviewer, reviewer_findings)
            except Exception as error:  # Provider SDKs use provider-specific hierarchies.
                last_error = error
                if isinstance(error, InvalidReviewerOutputError | ValueError) or (
                    attempt == retry_limit
                ):
                    return None, {
                        "role": reviewer.rubric.role,
                        "kind": _failure_kind(error),
                        "attempts": str(attempt + 1),
                    }
                continue
            return reviewer_findings, None
        # Unreachable in practice (the loop above always returns), but keeps type
        # checkers honest about every path producing a result.
        return None, {
            "role": reviewer.rubric.role,
            "kind": _failure_kind(last_error or RuntimeError("unknown failure")),
            "attempts": str(retry_limit + 1),
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(reviewers)) as executor:
        outcomes = list(executor.map(call, reviewers))

    findings: list[Finding] = []
    failures: list[dict[str, str]] = []
    for reviewer_findings, failure in outcomes:
        if failure is not None:
            failures.append(failure)
            continue
        assert reviewer_findings is not None  # noqa: S101 - narrows the union for mypy
        findings.extend(reviewer_findings)
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
    if isinstance(error, DeniedMutationError):
        return "denied-mutation"
    if isinstance(error, ReviewFabricError):
        return "provider-error"
    return "provider-error"


def _record_terminal(store: ArtifactStore, outcome: str, reason: str) -> None:
    store.record_event("terminal", {"outcome": outcome, "reason": reason})
    level = logging.INFO if outcome in {"ACCEPT", "CHANGE"} else logging.WARNING
    logger.log(level, "review-fabric terminal outcome=%s reason=%s", outcome, reason)


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
        logger.warning("review-fabric execution-error: missing-reviewer roles=%s", missing)
        outcome = (
            "ESCALATE"
            if plan.missing_reviewer_behavior is MissingReviewerBehavior.ESCALATE
            else "INCOMPLETE"
        )
        _record_terminal(store, outcome, "required reviewer unavailable")
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
            logger.warning("review-fabric execution-error: %s", failure)
        _record_terminal(store, "ESCALATE", "review execution failed")
        return result
    material = tuple(
        finding
        for finding in result.findings
        if finding.severity in {Severity.BLOCKER, Severity.CONCERN}
    )
    if not material:
        store.record_event("decision", {"outcome": "ACCEPT", "reason": "no material findings"})
        _record_terminal(store, "ACCEPT", "all selected reviewers completed")
        return result

    low_confidence = tuple(
        finding for finding in material if finding.confidence < plan.minimum_confidence
    )
    if low_confidence:
        # Reviewer confidence is not reliably calibrated, so a below-threshold
        # finding is never silently dropped or auto-approved — it forces ESCALATE so
        # a human decides, exactly like a missing/failed reviewer does, instead of
        # fabricating a CHANGE or ACCEPT verdict the reviewer itself signaled doubt
        # about.
        store.record_event(
            "low-confidence-findings",
            {
                "minimum_confidence": plan.minimum_confidence,
                "findings": [finding.model_dump(mode="json") for finding in low_confidence],
            },
        )
        _record_terminal(
            store, "ESCALATE", "material finding below minimum confidence requires human review"
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
        _record_terminal(store, "CHANGE", "material evidence-backed finding")
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
            if not isinstance(reviewer, ChallengeReviewer):
                # A structural capability gap (e.g. a fake/local reviewer with no
                # challenge support) is a distinct, expected condition, not a
                # provider failure — record it explicitly instead of relying on an
                # incidental AttributeError to fall through to the generic failure
                # bucket below.
                raise _ChallengeUnsupportedError("selected reviewer does not support challenge")
            response_data = reviewer.review_challenge(dispute)
            response = ChallengeResponse.model_validate(response_data)
            store.record_event("challenge-response", response.model_dump(mode="json"))
        except Exception as error:
            kind = (
                "challenge-unsupported"
                if isinstance(error, _ChallengeUnsupportedError)
                else _failure_kind(error)
            )
            store.record_event("challenge-response", {"status": "unavailable", "kind": kind})
            decision = adjudicate(dispute, None)
            escalated = True
        else:
            decision = adjudicate(dispute, response)
            if decision.outcome.value == "ESCALATE":
                escalated = True
        store.record_event("adjudication", decision.model_dump(mode="json"))
    _record_terminal(
        store, "ESCALATE" if escalated else "CHANGE", "bounded evidence adjudication"
    )
    return result
