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


def test_finding_rejects_unexpected_extra_fields_from_untrusted_provider_output() -> None:
    """Finding is parsed directly from provider/LLM JSON output; an unexpected extra
    key (schema drift, a stray field) must fail loudly rather than be silently
    dropped, so malformed provider output is caught rather than half-trusted."""
    with pytest.raises(ValidationError, match="peer_outputs"):
        Finding(
            package_id="a" * 64,
            severity=Severity.SUGGESTION,
            title="Add a test",
            claim="A branch is untested.",
            evidence=(),
            remediation="Add a regression test.",
            verification="Run pytest.",
            confidence=0.5,
            peer_outputs="leak",  # type: ignore[call-arg]
        )


def test_evidence_citation_rejects_unexpected_extra_fields() -> None:
    with pytest.raises(ValidationError):
        EvidenceCitation(
            path="src/service.py",
            start_line=1,
            end_line=1,
            excerpt="ok",
            extra_field="unexpected",  # type: ignore[call-arg]
        )


def test_evidence_citation_rejects_boolean_line_numbers() -> None:
    """A JSON boolean must not be silently coerced to 1/0 for a line-number field —
    an untrusted provider emitting `"start_line": true` must fail validation, not
    quietly resolve to line 1."""
    with pytest.raises(ValidationError):
        EvidenceCitation(path="src/service.py", start_line=True, end_line=1, excerpt="ok")
    with pytest.raises(ValidationError):
        EvidenceCitation(path="src/service.py", start_line=1, end_line=True, excerpt="ok")
