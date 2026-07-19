"""Provider-neutral reviewer contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from review_fabric.domain.findings import Finding
from review_fabric.domain.models import ReviewPackage


@dataclass(frozen=True)
class RoleRubric:
    role: str
    rubric: str


class Reviewer(Protocol):
    rubric: RoleRubric

    def review(self, package: ReviewPackage, rubric: RoleRubric) -> tuple[Finding, ...]: ...


@dataclass
class FakeReviewer:
    rubric: RoleRubric
    findings: tuple[Finding, ...] = ()
    received_packages: list[ReviewPackage] = field(default_factory=list)
    received_rubrics: list[RoleRubric] = field(default_factory=list)
    received_peer_outputs: tuple[Finding, ...] = ()

    def review(self, package: ReviewPackage, rubric: RoleRubric) -> tuple[Finding, ...]:
        self.received_packages.append(package)
        self.received_rubrics.append(rubric)
        return self.findings
