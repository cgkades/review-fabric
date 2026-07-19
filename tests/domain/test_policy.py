from __future__ import annotations

from review_fabric.domain.policy import ReviewerRole, ReviewPolicy, RiskIndicator


def test_low_risk_plan_uses_minimal_specialists() -> None:
    plan = ReviewPolicy.default().select_plan(("src/formatting.py", "tests/test_formatting.py"))

    assert plan.risk_indicators == ()
    assert plan.roles == (ReviewerRole.CORRECTNESS,)
    assert plan.challenge_limit == 0


def test_high_risk_paths_add_security_and_operations_with_bounded_challenge() -> None:
    plan = ReviewPolicy.default().select_plan(("infra/terraform/iam.tf", "src/auth/session.py"))

    assert RiskIndicator.IDENTITY_OR_ACCESS in plan.risk_indicators
    assert RiskIndicator.INFRASTRUCTURE in plan.risk_indicators
    assert plan.roles == (
        ReviewerRole.CORRECTNESS,
        ReviewerRole.TESTING,
        ReviewerRole.SECURITY,
        ReviewerRole.OPERATIONS,
    )
    assert plan.challenge_limit == 1
