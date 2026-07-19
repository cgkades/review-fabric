"""Deterministic first-pass review orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from review_fabric.domain.findings import Finding
from review_fabric.domain.models import ReviewPackage
from review_fabric.errors import InvalidReviewerOutputError
from review_fabric.reviewers.base import Reviewer


@dataclass(frozen=True)
class FirstPassResult:
    findings: tuple[Finding, ...]


def run_first_pass(package: ReviewPackage, reviewers: tuple[Reviewer, ...]) -> FirstPassResult:
    """Invoke each reviewer independently with only the frozen package."""
    findings: list[Finding] = []
    for reviewer in reviewers:
        reviewer_findings = reviewer.review(package)
        if any(finding.package_id != package.review_id for finding in reviewer_findings):
            raise InvalidReviewerOutputError("reviewer finding references a different package")
        findings.extend(reviewer_findings)
    return FirstPassResult(findings=tuple(findings))
