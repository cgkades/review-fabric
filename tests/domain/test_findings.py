from __future__ import annotations

import pytest
from pydantic import ValidationError

from review_fabric.domain.findings import EvidenceCitation, Finding, Severity


def evidence() -> EvidenceCitation:
    return EvidenceCitation(
        path="src/service.py",
        start_line=18,
        end_line=21,
        excerpt="retry(request)",
    )


def test_finding_accepts_evidence_backed_concern() -> None:
    finding = Finding(
        package_id="a" * 64,
        severity=Severity.CONCERN,
        title="Retries can duplicate writes",
        claim="The retry path has no idempotency key.",
        evidence=(evidence(),),
        remediation="Carry an idempotency key to the durable write boundary.",
        verification="Add a timeout-after-commit regression test.",
        confidence=0.9,
    )

    assert finding.evidence[0].path == "src/service.py"


@pytest.mark.parametrize("severity", [Severity.BLOCKER, Severity.CONCERN])
def test_finding_rejects_material_claim_without_evidence(severity: Severity) -> None:
    with pytest.raises(ValidationError, match="evidence"):
        Finding(
            package_id="a" * 64,
            severity=severity,
            title="Unsupported claim",
            claim="This is material.",
            evidence=(),
            remediation="Do something.",
            verification="Prove it.",
            confidence=0.5,
        )


def test_evidence_citation_rejects_invalid_line_range() -> None:
    with pytest.raises(ValidationError):
        EvidenceCitation(path="src/service.py", start_line=10, end_line=9, excerpt="bad range")
