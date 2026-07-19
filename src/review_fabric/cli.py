"""Local, read-only command-line entry point for deterministic reviews."""

from __future__ import annotations

import argparse
from pathlib import Path

from review_fabric.domain.models import ReviewPackage
from review_fabric.errors import ReviewFabricError
from review_fabric.evidence.artifacts import ArtifactStore
from review_fabric.evidence.git import collect_git_evidence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="review-fabric", description="Create a local evidence review"
    )
    parser.add_argument("repository", type=Path, help="local Git repository to inspect")
    parser.add_argument("base", help="explicit base Git revision")
    parser.add_argument("head", help="explicit head Git revision")
    parser.add_argument(
        "--constraint",
        action="append",
        default=[],
        help="non-secret review constraint (repeatable)",
    )
    return parser


def run(repository: Path, base: str, head: str, constraints: tuple[str, ...] = ()) -> Path:
    """Build frozen Git evidence, persist it, and execute the deterministic fake path."""
    evidence = collect_git_evidence(repository, base, head)
    package = ReviewPackage(
        repository_root=evidence.repository_root,
        base_sha=evidence.base_sha,
        head_sha=evidence.head_sha,
        patch_digest=evidence.patch_digest,
        selected_paths=evidence.changed_paths,
        acceptance_criteria=(),
        constraints=("read-only", *constraints),
        command_results=(),
    )
    store = ArtifactStore.create(Path(evidence.repository_root), package, patch=evidence.patch)
    store.record_event(
        "package",
        {
            "selected_paths": list(package.selected_paths),
            "reviewer_mode": "deterministic-fake",
        },
    )
    store.record_event("first-pass", {"status": "completed", "finding_count": 0})
    store.record_event("decision", {"outcome": "ACCEPT", "reason": "No fake-review findings"})
    return store.directory


def main(arguments: list[str] | None = None) -> int:
    parsed = build_parser().parse_args(arguments)
    try:
        directory = run(parsed.repository, parsed.base, parsed.head, tuple(parsed.constraint))
    except ReviewFabricError as error:
        print(f"review-fabric: {error}", flush=True)
        return 2
    print(directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
