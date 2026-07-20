"""Deterministic review-plan selection from explicit repository risk indicators."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RiskIndicator(StrEnum):
    IDENTITY_OR_ACCESS = "identity-or-access"
    INFRASTRUCTURE = "infrastructure"
    DEPENDENCY_CHANGE = "dependency-change"
    PUBLIC_INTERFACE = "public-interface"
    DESTRUCTIVE_DATA = "destructive-data"
    RETRY_IDEMPOTENCY = "retry-idempotency"
    CONCURRENCY = "concurrency"
    MIGRATION = "migration"


class ReviewerRole(StrEnum):
    CORRECTNESS = "correctness"
    SECURITY = "security"
    OPERATIONS = "operations"
    TESTING = "testing"


class MissingReviewerBehavior(StrEnum):
    ESCALATE = "escalate"
    INCOMPLETE = "incomplete"


class ReviewPlan(BaseModel):
    model_config = ConfigDict(frozen=True)
    risk_indicators: tuple[RiskIndicator, ...]
    roles: tuple[ReviewerRole, ...] = Field(min_length=1)
    max_reviewers: int = Field(ge=1)
    challenge_limit: int = Field(ge=0, le=1)
    retry_limit: int = Field(ge=0, le=3)
    timeout_seconds: int = Field(default=60, ge=1, le=3600)
    missing_reviewer_behavior: MissingReviewerBehavior = MissingReviewerBehavior.ESCALATE

    @model_validator(mode="after")
    def validate_reviewers(self) -> ReviewPlan:
        if len(set(self.roles)) != len(self.roles):
            raise ValueError("review plan roles must be unique")
        if len(self.roles) > self.max_reviewers:
            raise ValueError("review plan exceeds max_reviewers")
        return self


class ReviewPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)
    max_reviewers: int = Field(default=4, ge=1, le=8)
    retry_limit: int = Field(default=1, ge=0, le=3)
    timeout_seconds: int = Field(default=60, ge=1, le=3600)
    missing_reviewer_behavior: MissingReviewerBehavior = MissingReviewerBehavior.ESCALATE

    @classmethod
    def default(cls) -> ReviewPolicy:
        return cls()

    def select_plan(
        self, changed_paths: tuple[str, ...], declared: tuple[RiskIndicator, ...] = ()
    ) -> ReviewPlan:
        paths = tuple(path.lower() for path in changed_paths)
        rules = {
            RiskIndicator.IDENTITY_OR_ACCESS: (
                "auth",
                "iam",
                "identity",
                "permission",
                "authorization",
            ),
            RiskIndicator.INFRASTRUCTURE: ("infra", "terraform", "helm", "k8s", "kubernetes"),
            RiskIndicator.DEPENDENCY_CHANGE: (
                "pyproject.toml",
                "package-lock",
                "uv.lock",
                "requirements",
            ),
            RiskIndicator.PUBLIC_INTERFACE: ("api/", "public/", "schema"),
            RiskIndicator.DESTRUCTIVE_DATA: ("delete", "drop", "truncate", "purge"),
            RiskIndicator.RETRY_IDEMPOTENCY: ("retry", "idempot"),
            RiskIndicator.CONCURRENCY: ("concurr", "lock", "async", "thread"),
            RiskIndicator.MIGRATION: ("migration", "migrate", "alembic"),
        }
        indicators = tuple(
            indicator
            for indicator, tokens in rules.items()
            if indicator in declared
            or any(any(token in path for token in tokens) for path in paths)
        )
        if not indicators:
            roles = (ReviewerRole.CORRECTNESS,)
        else:
            roles = [ReviewerRole.CORRECTNESS, ReviewerRole.TESTING]
            if RiskIndicator.IDENTITY_OR_ACCESS in indicators:
                roles.append(ReviewerRole.SECURITY)
            if any(
                item in indicators
                for item in (
                    RiskIndicator.INFRASTRUCTURE,
                    RiskIndicator.MIGRATION,
                    RiskIndicator.DESTRUCTIVE_DATA,
                )
            ):
                roles.append(ReviewerRole.OPERATIONS)
            roles = tuple(dict.fromkeys(roles))
        roles = tuple(roles[: self.max_reviewers])
        return ReviewPlan(
            risk_indicators=indicators,
            roles=roles,
            max_reviewers=len(roles),
            challenge_limit=1 if indicators else 0,
            retry_limit=self.retry_limit,
            timeout_seconds=self.timeout_seconds,
            missing_reviewer_behavior=self.missing_reviewer_behavior,
        )
