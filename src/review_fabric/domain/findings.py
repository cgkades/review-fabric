"""Structured, evidence-backed reviewer findings."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Severity(StrEnum):
    BLOCKER = "blocker"
    CONCERN = "concern"
    SUGGESTION = "suggestion"


class EvidenceCitation(BaseModel):
    """A precise source citation supporting a reviewer claim."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(min_length=1)
    start_line: int = Field(ge=1, strict=True)
    end_line: int = Field(ge=1, strict=True)
    excerpt: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_line_range(self) -> EvidenceCitation:
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        return self


class Finding(BaseModel):
    """One reviewer claim and the evidence required to admit it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    package_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer_id: str = Field(default="unknown", min_length=1)
    severity: Severity
    title: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    evidence: tuple[EvidenceCitation, ...]
    remediation: str = Field(min_length=1)
    verification: str = Field(min_length=1)
    confidence: float = Field(
        ge=0,
        le=1,
        description=(
            "Reviewer-reported confidence. A BLOCKER/CONCERN finding below "
            "ReviewPlan.minimum_confidence (default 0.5) forces ESCALATE for human "
            "review instead of automatically driving CHANGE; it is never silently "
            "dropped. See orchestration.execute_plan."
        ),
    )

    @model_validator(mode="after")
    def require_evidence_for_material_finding(self) -> Finding:
        if self.severity in {Severity.BLOCKER, Severity.CONCERN} and not self.evidence:
            raise ValueError("material findings require evidence")
        return self
