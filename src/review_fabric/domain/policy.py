"""Deterministic review-plan selection from explicit repository risk indicators."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RiskIndicator(StrEnum):
    IDENTITY_OR_ACCESS = "identity-or-access"
    INFRASTRUCTURE = "infrastructure"
    DEPENDENCY_CHANGE = "dependency-change"
    PUBLIC_INTERFACE = "public-interface"


class ReviewerRole(StrEnum):
    CORRECTNESS = "correctness"
    SECURITY = "security"
    OPERATIONS = "operations"
    TESTING = "testing"


class ReviewPlan(BaseModel):
    """Bounded, provider-neutral plan selected before source reaches reviewers."""

    model_config = ConfigDict(frozen=True)

    risk_indicators: tuple[RiskIndicator, ...]
    roles: tuple[ReviewerRole, ...] = Field(min_length=1)
    max_reviewers: int = Field(ge=1)
    challenge_limit: int = Field(ge=0, le=1)
    retry_limit: int = Field(ge=0, le=3)


class ReviewPolicy(BaseModel):
    """Fixed routing policy; model/provider selection is deliberately external."""

    model_config = ConfigDict(frozen=True)

    max_reviewers: int = Field(default=4, ge=1, le=8)
    retry_limit: int = Field(default=1, ge=0, le=3)

    @classmethod
    def default(cls) -> ReviewPolicy:
        return cls()

    def select_plan(self, changed_paths: tuple[str, ...]) -> ReviewPlan:
        normalized = tuple(path.lower() for path in changed_paths)
        indicators: list[RiskIndicator] = []
        if any(
            any(token in path for token in ("auth", "iam", "identity", "permission"))
            for path in normalized
        ):
            indicators.append(RiskIndicator.IDENTITY_OR_ACCESS)
        if any(
            any(token in path for token in ("infra", "terraform", "helm", "k8s", "kubernetes"))
            for path in normalized
        ):
            indicators.append(RiskIndicator.INFRASTRUCTURE)
        if any(
            path.endswith(("pyproject.toml", "package-lock.json", "uv.lock", "requirements.txt"))
            for path in normalized
        ):
            indicators.append(RiskIndicator.DEPENDENCY_CHANGE)
        if any(
            any(token in path for token in ("api/", "public/", "schema")) for path in normalized
        ):
            indicators.append(RiskIndicator.PUBLIC_INTERFACE)

        roles: list[ReviewerRole] = [ReviewerRole.CORRECTNESS]
        if RiskIndicator.IDENTITY_OR_ACCESS in indicators:
            roles.append(ReviewerRole.SECURITY)
        if RiskIndicator.INFRASTRUCTURE in indicators:
            roles.append(ReviewerRole.OPERATIONS)
        roles.append(ReviewerRole.TESTING)
        roles = roles[: self.max_reviewers]
        return ReviewPlan(
            risk_indicators=tuple(indicators),
            roles=tuple(roles),
            max_reviewers=len(roles),
            challenge_limit=1 if indicators else 0,
            retry_limit=self.retry_limit,
        )
