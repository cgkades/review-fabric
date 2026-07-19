from __future__ import annotations

from review_fabric.domain.findings import EvidenceCitation, Finding, Severity
from review_fabric.domain.normalization import normalize_findings

PACKAGE = "a" * 64


def finding(title: str, reviewer: str, line: int = 10) -> Finding:
    return Finding(
        package_id=PACKAGE,
        severity=Severity.CONCERN,
        title=title,
        claim="reachable defect",
        evidence=(
            EvidenceCitation(path="src/a.py", start_line=line, end_line=line, excerpt="write()"),
        ),
        remediation="guard it",
        verification="add regression",
        confidence=0.9,
        reviewer_id=reviewer,
    )


def test_normalization_assigns_stable_ids_and_groups_duplicates() -> None:
    groups = normalize_findings(
        (finding("Duplicate write", "one"), finding("Duplicate write", "two"))
    )
    assert len(groups) == 1
    assert len(groups[0].findings) == 2
    assert groups == normalize_findings(
        (finding("Duplicate write", "one"), finding("Duplicate write", "two"))
    )


def test_supported_minority_finding_is_retained() -> None:
    groups = normalize_findings((finding("Duplicate write", "one"),))
    assert groups[0].findings[0].reviewer_id == "one"
