from __future__ import annotations

from review_fabric.domain.findings import EvidenceCitation, Finding, Severity
from review_fabric.domain.normalization import normalize_findings

PACKAGE = "a" * 64


def finding(
    title: str, reviewer: str, line: int = 10, *, start: int | None = None, end: int | None = None
) -> Finding:
    start_line = start if start is not None else line
    end_line = end if end is not None else line
    return Finding(
        package_id=PACKAGE,
        severity=Severity.CONCERN,
        title=title,
        claim="reachable defect",
        evidence=(
            EvidenceCitation(
                path="src/a.py", start_line=start_line, end_line=end_line, excerpt="write()"
            ),
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


def test_overlapping_but_not_identical_citations_are_grouped_together() -> None:
    """Citation-boundary noise (e.g. end_line off by one) must not fragment the same
    reported defect into separate groups, which would force a spurious escalation."""
    groups = normalize_findings(
        (
            finding("Duplicate write", "one", start=10, end=12),
            finding("Duplicate write", "two", start=10, end=13),
        )
    )

    assert len(groups) == 1
    assert len(groups[0].findings) == 2


def test_non_overlapping_citations_remain_separate_groups() -> None:
    groups = normalize_findings(
        (
            finding("Duplicate write", "one", start=10, end=12),
            finding("Duplicate write", "two", start=50, end=52),
        )
    )

    assert len(groups) == 2


def test_group_identity_is_independent_of_input_encounter_order() -> None:
    first = finding("Duplicate write", "one", start=10, end=12)
    second = finding("Duplicate write", "two", start=10, end=13)

    forward = normalize_findings((first, second))
    reversed_order = normalize_findings((second, first))

    assert {group.id for group in forward} == {group.id for group in reversed_order}
