"""Local, read-only command line; provider execution is explicit and never fabricated."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import NoReturn

from review_fabric.configuration import ReviewConfiguration, Transport, load_configuration
from review_fabric.credentials import auth_remove, auth_set, auth_status, resolve_credential
from review_fabric.domain.models import (
    DEFAULT_MAX_PATCH_EVIDENCE_BYTES,
    FrozenPatchEvidence,
    ReviewPackage,
)
from review_fabric.domain.policy import ReviewPolicy, RiskIndicator
from review_fabric.errors import ArtifactAlreadyExistsError, ReviewFabricError
from review_fabric.evidence.artifacts import ArtifactStore
from review_fabric.evidence.git import (
    GitEvidence,
    collect_full_tree_evidence,
    collect_git_evidence,
    split_patch_into_chunks,
)
from review_fabric.orchestration import execute_plan
from review_fabric.reviewers.base import FakeReviewer, RoleRubric
from review_fabric.reviewers.providers import ProviderReviewer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FullReviewResult:
    """Outcome of one --full whole-codebase review.

    directories: one artifact directory per successfully reviewed chunk, in order.
    oversized_chunks: any chunk whose own required patch content alone exceeded
        max_patch_bytes and was therefore skipped rather than reviewed — each entry
        is {"index", "total", "paths", "bytes"}. Never silently empty when coverage
        was incomplete: check this before treating a run as having covered every
        tracked file.
    """

    directories: tuple[Path, ...]
    oversized_chunks: tuple[dict[str, object], ...] = ()


class _ArgumentParser(argparse.ArgumentParser):
    """Route argparse usage errors through the same ReviewFabricError path as every
    other expected failure, so main()'s uniform "review-fabric: <message>" prefix
    (and a log scraper grepping for it) never misses a usage error, instead of
    argparse printing its own differently-formatted usage/error block and calling
    sys.exit directly."""

    def error(self, message: str) -> NoReturn:
        raise ReviewFabricError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="review-fabric", description="Create a local evidence review")
    parser.add_argument("repository", type=Path, nargs="?", help="local Git repository to inspect")
    parser.add_argument(
        "base", nargs="?", help="explicit base Git revision (bounded diff mode; omit with --full)"
    )
    parser.add_argument(
        "head", nargs="?", help="explicit head Git revision (bounded diff mode; omit with --full)"
    )
    parser.add_argument(
        "--constraint", action="append", default=[], help="non-secret review constraint"
    )
    parser.add_argument("--env-file", type=Path, help="private, Git-ignored dotenv file")
    parser.add_argument(
        "--config", type=Path, help="explicit secret-free JSON provider configuration"
    )
    parser.add_argument(
        "--risk",
        action="append",
        choices=tuple(indicator.value for indicator in RiskIndicator),
        default=[],
        help="declared risk indicator requiring specialist review",
    )
    parser.add_argument(
        "--pr",
        action="store_true",
        help=(
            "explicit alias confirming bounded diff mode (base/head positional "
            "arguments); no behavior change from the default mode"
        ),
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help=(
            "review the entire tracked codebase at --revision instead of a bounded "
            "base/head diff; base/head positional arguments must be omitted"
        ),
    )
    parser.add_argument(
        "--revision",
        default="HEAD",
        help="revision to snapshot with --full (default: HEAD)",
    )
    parser.add_argument(
        "--max-patch-bytes",
        type=int,
        default=None,
        help=(
            "raise the per-review(or per-chunk, with --full) patch byte cap above "
            "the conservative default; larger values mean fewer, bigger chunks "
            "under --full at the cost of a larger provider prompt"
        ),
    )
    return parser


def _auth_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="review-fabric auth")
    parser.add_argument("action", choices=("set", "status", "remove"))
    parser.add_argument("provider")
    parser.add_argument("--profile", default="default")
    return parser


def run(
    repository: Path,
    base: str,
    head: str,
    constraints: tuple[str, ...] = (),
    declared_risks: tuple[RiskIndicator, ...] = (),
    *,
    configuration: ReviewConfiguration | None = None,
    configuration_path: Path | None = None,
    env_file: Path | None = None,
    max_patch_bytes: int | None = None,
) -> Path:
    evidence = collect_git_evidence(repository, base, head)
    configuration = _resolve_configuration(configuration, configuration_path, evidence)
    configuration_metadata = _configuration_metadata(configuration)
    package = ReviewPackage(
        repository_root=evidence.repository_root,
        base_sha=evidence.base_sha,
        head_sha=evidence.head_sha,
        patch_digest=evidence.patch_digest,
        selected_paths=evidence.changed_paths,
        acceptance_criteria=(),
        constraints=_package_constraints(constraints, declared_risks, configuration),
        patch_evidence=FrozenPatchEvidence.from_patch(evidence.patch, max_bytes=max_patch_bytes),
    )
    root = Path(evidence.repository_root)
    return _review_package(
        repository,
        root,
        package,
        evidence.patch,
        declared_risks=declared_risks,
        configuration=configuration,
        configuration_metadata=configuration_metadata,
        env_file=env_file,
    )


def run_full(
    repository: Path,
    revision: str = "HEAD",
    constraints: tuple[str, ...] = (),
    declared_risks: tuple[RiskIndicator, ...] = (),
    *,
    configuration: ReviewConfiguration | None = None,
    configuration_path: Path | None = None,
    env_file: Path | None = None,
    max_patch_bytes: int | None = None,
) -> FullReviewResult:
    """Review the entire tracked codebase at revision — not a bounded diff.

    A whole codebase almost always exceeds the single-PR patch bound, so the
    evidence is split into file-aligned chunks that each independently respect
    max_patch_bytes; every chunk gets its own bounded, independently-replayable
    artifact.

    Cross-file interaction awareness is limited to whichever files land in the same
    chunk — there is no way to give a reviewer truly whole-repository-at-once context
    without an unbounded prompt. A single file whose own diff alone still exceeds
    max_patch_bytes is never silently truncated or dropped (see
    split_patch_into_chunks); it is skipped and reported back in
    FullReviewResult.oversized_chunks instead, so the caller can see exactly which
    paths were not reviewed and why, and the remaining chunks still proceed.
    """
    evidence = collect_full_tree_evidence(repository, revision)
    configuration = _resolve_configuration(configuration, configuration_path, evidence)
    configuration_metadata = _configuration_metadata(configuration)
    root = Path(evidence.repository_root)
    chunk_bound = max_patch_bytes or DEFAULT_MAX_PATCH_EVIDENCE_BYTES
    chunks = split_patch_into_chunks(evidence.patch, max_chunk_bytes=chunk_bound)
    total = len(chunks)
    directories: list[Path] = []
    oversized: list[dict[str, object]] = []
    for index, (chunk_patch, chunk_paths) in enumerate(chunks, start=1):
        chunk_bytes = len(chunk_patch.encode("utf-8"))
        if chunk_bytes > chunk_bound:
            # A single file's own diff cannot be split further without breaking hunk
            # integrity (see split_patch_into_chunks); rather than raise and abort
            # every remaining chunk, skip just this one and keep going, reporting it
            # clearly so the caller knows exactly what was not reviewed.
            oversized.append(
                {"index": index, "total": total, "paths": chunk_paths, "bytes": chunk_bytes}
            )
            logger.warning(
                "review-fabric: chunk %s/%s skipped, exceeds max-patch-bytes "
                "(%s bytes): paths=%s",
                index,
                total,
                chunk_bytes,
                chunk_paths,
            )
            continue
        package = ReviewPackage(
            repository_root=evidence.repository_root,
            base_sha=evidence.base_sha,
            head_sha=evidence.head_sha,
            patch_digest=sha256(chunk_patch.encode("utf-8")).hexdigest(),
            selected_paths=chunk_paths,
            acceptance_criteria=(),
            constraints=_package_constraints(
                (*constraints, f"full-review-chunk:{index}/{total}"), declared_risks, configuration
            ),
            patch_evidence=FrozenPatchEvidence.from_patch(chunk_patch, max_bytes=max_patch_bytes),
        )
        directories.append(
            _review_package(
                repository,
                root,
                package,
                chunk_patch,
                declared_risks=declared_risks,
                configuration=configuration,
                configuration_metadata=configuration_metadata,
                env_file=env_file,
            )
        )
    return FullReviewResult(directories=tuple(directories), oversized_chunks=tuple(oversized))


def _resolve_configuration(
    configuration: ReviewConfiguration | None,
    configuration_path: Path | None,
    evidence: GitEvidence,
) -> ReviewConfiguration | None:
    if configuration and configuration_path:
        raise ValueError("configuration object and configuration path are mutually exclusive")
    if configuration_path:
        return load_configuration(configuration_path, repository=Path(evidence.repository_root))
    return configuration


def _configuration_metadata(configuration: ReviewConfiguration | None) -> dict[str, object] | None:
    if not configuration:
        return None
    return {"version": configuration.version, "bindings": configuration.manifest_bindings()}


def _package_constraints(
    constraints: tuple[str, ...],
    declared_risks: tuple[RiskIndicator, ...],
    configuration: ReviewConfiguration | None,
) -> tuple[str, ...]:
    return (
        "read-only",
        *constraints,
        *(f"risk:{risk.value}" for risk in sorted(set(declared_risks), key=str)),
        *(() if configuration is None else (f"configuration:{configuration.identity}",)),
    )


def _review_package(
    repository: Path,
    root: Path,
    package: ReviewPackage,
    patch: str,
    *,
    declared_risks: tuple[RiskIndicator, ...] = (),
    configuration: ReviewConfiguration | None,
    configuration_metadata: dict[str, object] | None,
    env_file: Path | None,
) -> Path:
    """Run the full lifecycle (lock, create/resume artifact, plan, reviewers,
    execute_plan) for exactly one already-built ReviewPackage. Shared by a single
    PR/commit-range review and by each bounded chunk of a full-codebase review."""
    with ArtifactStore.acquire_package_lock(root, package):
        try:
            store = ArtifactStore.create(
                root,
                package,
                patch=patch,
                configuration=configuration_metadata,
            )
            created = True
        except ArtifactAlreadyExistsError:
            store = ArtifactStore.open(root, package)
            created = False
        events = store.events()
        if any(event["phase"] == "terminal" for event in events):
            return store.directory
        if not created:
            # A previous process died mid-run. Re-execution could duplicate external
            # provider calls, so close this identity deterministically instead.
            store.record_event("execution-error", {"kind": "incomplete-artifact"})
            store.record_event(
                "terminal", {"outcome": "ESCALATE", "reason": "prior run incomplete"}
            )
            logger.warning(
                "review-fabric: closing incomplete prior run for review_id=%s", package.review_id
            )
            return store.directory
        store.record_event("package", {"selected_paths": list(package.selected_paths)})
        plan = ReviewPolicy.default().select_plan(package.selected_paths, declared=declared_risks)
        reviewers = {}
        try:
            if configuration:
                configuration.validate_selected_roles(tuple(role.value for role in plan.roles))
                store.record_event("configured-bindings", configuration.manifest_bindings())
                for role in plan.roles:
                    binding = configuration.binding_for(role.value)
                    rubric = RoleRubric(role.value, f"Evidence-based {role.value} review")
                    if binding.transport is Transport.FAKE:
                        reviewers[role.value] = FakeReviewer(rubric)
                    else:
                        credential = resolve_credential(
                            binding, repository=repository, env_file=env_file
                        )
                        reviewers[role.value] = ProviderReviewer(
                            binding, credential, rubric, timeout_seconds=plan.timeout_seconds
                        )
        except (ReviewFabricError, OSError, ValueError) as error:
            # Credential/configuration exception text may contain a path or provider
            # detail; artifacts persist a stable category only. The class name alone
            # (never the message) is safe to log for operator triage.
            store.record_event("execution-error", {"kind": "credential-unavailable"})
            store.record_event(
                "terminal", {"outcome": "ESCALATE", "reason": "reviewer setup failed"}
            )
            logger.warning(
                "review-fabric: reviewer setup failed (caused by %s)", type(error).__name__
            )
            return store.directory
        # No configured adapter is silently replaced with an ACCEPTing fake reviewer.
        execute_plan(package, plan, reviewers, store)
    return store.directory


def _run_auth(arguments: list[str]) -> int:
    # Reject unexpected tokens before argparse can echo a supplied secret in its error output.
    if (
        len(arguments) < 2
        or len(arguments) > 4
        or arguments[0] not in {"set", "status", "remove"}
        or arguments[1] not in {"openai", "anthropic", "xai", "gemini", "azure", "bedrock"}
        or (len(arguments) > 2 and (arguments[2] != "--profile" or len(arguments) != 4))
    ):
        raise ReviewFabricError("auth accepts only an action, provider, and optional named profile")
    parsed = _auth_parser().parse_args(arguments)
    if parsed.action == "set":
        auth_set(parsed.provider, parsed.profile)
        print(f"stored keychain profile {parsed.provider}:{parsed.profile}")
    elif parsed.action == "status":
        print("present" if auth_status(parsed.provider, parsed.profile) else "absent")
    else:
        auth_remove(parsed.provider, parsed.profile)
        print(f"removed keychain profile {parsed.provider}:{parsed.profile}")
    return 0


def main(arguments: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if arguments is None else arguments
    try:
        if arguments and arguments[0] == "summary":
            if len(arguments) != 2:
                raise ReviewFabricError("summary requires an artifact directory")
            directory = Path(arguments[1])
            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
            print(ArtifactStore(directory, manifest["review_id"]).regenerate_summary(), end="")
            return 0
        if arguments and arguments[0] == "auth":
            return _run_auth(arguments[1:])
        parsed = build_parser().parse_args(arguments)
        if parsed.pr and parsed.full:
            raise ReviewFabricError("--pr and --full are mutually exclusive")
        if parsed.full:
            if parsed.base or parsed.head:
                raise ReviewFabricError(
                    "--full reviews the whole tree; use --revision instead of base/head"
                )
            if not parsed.repository:
                raise ReviewFabricError("repository is required")
            result = run_full(
                parsed.repository,
                parsed.revision,
                tuple(parsed.constraint),
                tuple(RiskIndicator(risk) for risk in parsed.risk),
                configuration_path=parsed.config,
                env_file=parsed.env_file,
                max_patch_bytes=parsed.max_patch_bytes,
            )
            for directory in result.directories:
                print(directory)
            for chunk in result.oversized_chunks:
                message = (
                    f"review-fabric: chunk {chunk['index']}/{chunk['total']} skipped, "
                    f"exceeds max-patch-bytes ({chunk['bytes']} bytes); raise "
                    f"--max-patch-bytes or review separately: {', '.join(chunk['paths'])}"
                )
                print(message, flush=True)
                logger.error(message)
            return 2 if result.oversized_chunks else 0
        if not (parsed.repository and parsed.base and parsed.head):
            raise ReviewFabricError("repository, base, and head are required")
        print(
            run(
                parsed.repository,
                parsed.base,
                parsed.head,
                tuple(parsed.constraint),
                tuple(RiskIndicator(risk) for risk in parsed.risk),
                configuration_path=parsed.config,
                env_file=parsed.env_file,
                max_patch_bytes=parsed.max_patch_bytes,
            )
        )
        return 0
    except (ReviewFabricError, OSError, ValueError) as error:
        print(f"review-fabric: {error}{_cause_suffix(error)}", flush=True)
        logger.error("review-fabric: %s%s", error, _cause_suffix(error))
        return 2


def _cause_suffix(error: BaseException) -> str:
    """Name (never the message of) a chained cause, to aid triage without leaking
    detail: distinguishing e.g. "caused by ImportError" (a package is not installed)
    from "caused by RuntimeError" (a backend is unavailable) is useful for an operator
    without ever printing the underlying exception's own text, which the codebase
    otherwise deliberately keeps generic/static specifically to avoid leaking secret
    material or provider detail (see errors.ReviewFabricError.to_record)."""
    cause = error.__cause__
    return f" (caused by {type(cause).__name__})" if cause is not None else ""


if __name__ == "__main__":
    raise SystemExit(main())
