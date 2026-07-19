"""One-round, evidence-limited dispute adjudication."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from review_fabric.domain.normalization import FindingGroup


class DecisionOutcome(StrEnum):
    ACCEPT = "ACCEPT"
    CHANGE = "CHANGE"
    ESCALATE = "ESCALATE"


class Dispute(BaseModel):
    model_config = ConfigDict(frozen=True)
    group_id: str
    question: str = Field(min_length=1)
    competing_claims: tuple[str, ...]
    evidence_needed: tuple[str, ...]
    challenge_limit: int = Field(default=1, ge=0, le=1)


class ChallengeResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    claim: str = Field(min_length=1)
    evidence: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_evidence(self) -> ChallengeResponse:
        if not self.evidence:
            raise ValueError("challenge response requires evidence or explicit assumption")
        return self


class Decision(BaseModel):
    model_config = ConfigDict(frozen=True)
    outcome: DecisionOutcome
    group_id: str
    accepted_evidence: tuple[str, ...] = ()
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
    return Dispute(
        group_id=group.id,
        question=question,
        competing_claims=tuple(item.claim for item in group.findings),
        evidence_needed=("precise code, contract, test, command output, or assumption evidence",),
    )


def adjudicate(dispute: Dispute, response: ChallengeResponse | None) -> Decision:
    if response is None:
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
