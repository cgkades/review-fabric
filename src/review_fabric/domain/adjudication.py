"""One-round, evidence-limited dispute adjudication."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from review_fabric.domain.normalization import FindingGroup


class DecisionOutcome(StrEnum):
    ACCEPT = "ACCEPT"
    CHANGE = "CHANGE"
    ESCALATE = "ESCALATE"


class ChallengeDisposition(StrEnum):
    CONFIRM = "confirm"
    REJECT = "reject"
    UNCERTAIN = "uncertain"


class ChallengeCitation(BaseModel):
    """Small, normalized evidence context permitted in a challenge request."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    path: str = Field(min_length=1, max_length=512)
    start_line: int = Field(ge=1, strict=True)
    end_line: int = Field(ge=1, strict=True)
    excerpt: str = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def validate_line_range(self) -> ChallengeCitation:
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        return self


class Dispute(BaseModel):
    """Bounded DTO: no package, reviewer output, or unnormalized finding fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    group_id: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=512)
    citations: tuple[ChallengeCitation, ...] = Field(min_length=1, max_length=8)
    challenge_limit: int = Field(default=1, ge=0, le=1)


class ChallengeResponse(BaseModel):
    """A disposition plus exact references to evidence already in the dispute."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    disposition: ChallengeDisposition
    evidence: tuple[ChallengeCitation, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def require_evidence(self) -> ChallengeResponse:
        if self.disposition is ChallengeDisposition.CONFIRM and not self.evidence:
            raise ValueError("confirmed challenge response requires cited evidence")
        if self.disposition is not ChallengeDisposition.CONFIRM and self.evidence:
            raise ValueError("nonconfirming challenge response cannot add evidence")
        return self

    def validate_for(self, dispute: Dispute) -> ChallengeResponse:
        permitted = set(dispute.citations)
        if any(citation not in permitted for citation in self.evidence):
            raise ValueError("challenge response evidence must be cited in the dispute")
        return self


class Decision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    outcome: DecisionOutcome
    group_id: str
    accepted_evidence: tuple[ChallengeCitation, ...] = ()
    remediation: str | None = None
    verification: str | None = None
    unresolved_question: str | None = None

    @model_validator(mode="after")
    def complete_outcome(self) -> Decision:
        if self.outcome is DecisionOutcome.CHANGE and (
            not self.remediation or not self.verification
        ):
            raise ValueError("CHANGE requires remediation and verification")
        if self.outcome is DecisionOutcome.ESCALATE and not self.unresolved_question:
            raise ValueError("ESCALATE requires unresolved question")
        return self


def make_dispute(group: FindingGroup, question: str) -> Dispute:
    citations: list[ChallengeCitation] = []
    seen: set[tuple[str, int, int, str]] = set()
    for finding in group.findings:
        for evidence in finding.evidence:
            key = (evidence.path, evidence.start_line, evidence.end_line, evidence.excerpt)
            if key not in seen:
                seen.add(key)
                citations.append(ChallengeCitation.model_validate(evidence.model_dump()))
            if len(citations) == 8:
                break
        if len(citations) == 8:
            break
    return Dispute(group_id=group.id, question=question, citations=tuple(citations))


def adjudicate(dispute: Dispute, response: ChallengeResponse | None) -> Decision:
    if response is None:
        return Decision(
            outcome=DecisionOutcome.ESCALATE,
            group_id=dispute.group_id,
            unresolved_question=dispute.question,
        )
    response.validate_for(dispute)
    if response.disposition is not ChallengeDisposition.CONFIRM:
        return Decision(
            outcome=DecisionOutcome.ESCALATE,
            group_id=dispute.group_id,
            unresolved_question=dispute.question,
        )
    return Decision(
        outcome=DecisionOutcome.CHANGE,
        group_id=dispute.group_id,
        accepted_evidence=response.evidence,
        remediation="Apply the bounded remediation identified by accepted evidence",
        verification="Add or run the cited regression verification",
    )
