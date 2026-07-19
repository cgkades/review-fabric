from __future__ import annotations

import pytest

from review_fabric.domain.adjudication import (
    ChallengeResponse,
    DecisionOutcome,
    adjudicate,
    make_dispute,
)
from review_fabric.domain.findings import EvidenceCitation, Finding, Severity
from review_fabric.domain.normalization import normalize_findings


def material() -> Finding:
    return Finding(
        package_id="b" * 64,
        severity=Severity.BLOCKER,
        title="Duplicate",
        claim="retry duplicates write",
        evidence=(EvidenceCitation(path="a.py", start_line=1, end_line=1, excerpt="retry"),),
        remediation="deduplicate",
        verification="regression",
        confidence=1,
        reviewer_id="security",
    )


def test_dispute_and_evidence_limited_challenge() -> None:
    group = normalize_findings((material(),))[0]
    dispute = make_dispute(group, "Does constraint cover retry?")
    assert dispute.question
    decision = adjudicate(
        dispute, ChallengeResponse(claim="constraint proves it", evidence=("contract:a",))
    )
    assert decision.outcome is DecisionOutcome.CHANGE
    with pytest.raises(ValueError, match="evidence"):
        adjudicate(dispute, ChallengeResponse(claim="too cautious"))


def test_unresolved_dispute_escalates_after_one_round() -> None:
    dispute = make_dispute(normalize_findings((material(),))[0], "Question")
    decision = adjudicate(dispute, None)
    assert decision.outcome is DecisionOutcome.ESCALATE
