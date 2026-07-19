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

    def review(self, package: ReviewPackage) -> tuple[Finding, ...]: ...


@dataclass
class FakeReviewer:
    rubric: RoleRubric
    findings: tuple[Finding, ...] = ()
    received_packages: list[ReviewPackage] = field(default_factory=list)
    received_peer_outputs: tuple[Finding, ...] = ()

    def review(self, package: ReviewPackage) -> tuple[Finding, ...]:
        self.received_packages.append(package)
        return self.findings
