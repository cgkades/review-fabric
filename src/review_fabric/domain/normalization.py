"""Deterministic finding identity and duplicate preservation."""

from __future__ import annotations

from hashlib import sha256
from typing import NamedTuple

from review_fabric.domain.findings import EvidenceCitation, Finding
from review_fabric.serialization import canonical_json_bytes


class FindingGroup(NamedTuple):
    id: str
    findings: tuple[Finding, ...]


def _primary_citation(finding: Finding) -> EvidenceCitation | None:
    return finding.evidence[0] if finding.evidence else None


def _same_defect(current: Finding, member: Finding) -> bool:
    """Return whether two findings plausibly describe the same underlying defect.

    Findings match when they belong to the same package, share a case-insensitively
    identical title, and their primary citations overlap on the same file. Requiring
    an *overlap* rather than an exact (start_line, end_line) match means two reviewers
    citing the same defect with citations differing only by excerpt-boundary noise
    (e.g. end_line off by one) are still recognized as one finding, instead of being
    fragmented into separate groups that would otherwise force a spurious
    multi-group ESCALATE despite both reviewers substantively agreeing.
    """
    if current.package_id != member.package_id:
        return False
    if current.title.casefold() != member.title.casefold():
        return False
    current_citation = _primary_citation(current)
    member_citation = _primary_citation(member)
    if current_citation is None or member_citation is None:
        return current_citation is member_citation
    return (
        current_citation.path == member_citation.path
        and current_citation.start_line <= member_citation.end_line
        and member_citation.start_line <= current_citation.end_line
    )


def _cluster_identity(cluster: list[Finding]) -> tuple[str, str, str, int, int]:
    """Compute a canonical, membership-derived key independent of encounter order."""
    citations = [citation for finding in cluster if (citation := _primary_citation(finding))]
    path = citations[0].path if citations else ""
    start_line = min((citation.start_line for citation in citations), default=0)
    end_line = max((citation.end_line for citation in citations), default=0)
    return (cluster[0].package_id, path, cluster[0].title.casefold(), start_line, end_line)


def normalize_findings(findings: tuple[Finding, ...]) -> tuple[FindingGroup, ...]:
    """Group compatible observations without using vote counts to discard evidence."""
    clusters: list[list[Finding]] = []
    for finding in findings:
        for cluster in clusters:
            if any(_same_defect(finding, member) for member in cluster):
                cluster.append(finding)
                break
        else:
            clusters.append([finding])
    result = []
    for cluster in clusters:
        identity = _cluster_identity(cluster)
        members = tuple(
            sorted(cluster, key=lambda item: (item.reviewer_id, item.title, item.claim))
        )
        group_id = sha256(canonical_json_bytes({"key": identity})).hexdigest()
        result.append(FindingGroup(group_id, members))
    return tuple(sorted(result, key=lambda group: group.id))
