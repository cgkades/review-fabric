"""Deterministic finding identity and duplicate preservation."""

from __future__ import annotations

from hashlib import sha256
from typing import NamedTuple

from review_fabric.domain.findings import Finding
from review_fabric.serialization import canonical_json_bytes


class FindingGroup(NamedTuple):
    id: str
    findings: tuple[Finding, ...]


def _key(finding: Finding) -> tuple[str, str, int, int, str]:
    citation = finding.evidence[0] if finding.evidence else None
    return (
        finding.package_id,
        citation.path if citation else "",
        citation.start_line if citation else 0,
        citation.end_line if citation else 0,
        finding.title.casefold(),
    )


def normalize_findings(findings: tuple[Finding, ...]) -> tuple[FindingGroup, ...]:
    """Group compatible observations without using vote counts to discard evidence."""
    grouped: dict[tuple[str, str, int, int, str], list[Finding]] = {}
    for finding in findings:
        grouped.setdefault(_key(finding), []).append(finding)
    result = []
    for key in sorted(grouped):
        members = tuple(
            sorted(grouped[key], key=lambda item: (item.reviewer_id, item.title, item.claim))
        )
        group_id = sha256(canonical_json_bytes({"key": key})).hexdigest()
        result.append(FindingGroup(group_id, members))
    return tuple(result)
