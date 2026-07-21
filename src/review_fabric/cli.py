"""Local, read-only command line; provider execution is explicit and never fabricated."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import NoReturn

from review_fabric.configuration import ReviewConfiguration, Transport, load_configuration
from review_fabric.credentials import auth_remove, auth_set, auth_status, resolve_credential
from review_fabric.domain.models import FrozenPatchEvidence, ReviewPackage
from review_fabric.domain.policy import ReviewPolicy, RiskIndicator
from review_fabric.errors import ArtifactAlreadyExistsError, ReviewFabricError
from review_fabric.evidence.artifacts import ArtifactStore
from review_fabric.evidence.git import collect_git_evidence
from review_fabric.orchestration import execute_plan
from review_fabric.reviewers.base import FakeReviewer, RoleRubric
from review_fabric.reviewers.providers import ProviderReviewer

logger = logging.getLogger(__name__)


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
    parser.add_argument("base", nargs="?", help="explicit base Git revision")
    parser.add_argument("head", nargs="?", help="explicit head Git revision")
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
) -> Path:
    evidence = collect_git_evidence(repository, base, head)
    if configuration and configuration_path:
        raise ValueError("configuration object and configuration path are mutually exclusive")
    if configuration_path:
        configuration = load_configuration(
            configuration_path, repository=Path(evidence.repository_root)
        )
    configuration_metadata = None
    if configuration:
        configuration_metadata = {
            "version": configuration.version,
            "bindings": configuration.manifest_bindings(),
        }
    package = ReviewPackage(
        repository_root=evidence.repository_root,
        base_sha=evidence.base_sha,
        head_sha=evidence.head_sha,
        patch_digest=evidence.patch_digest,
        selected_paths=evidence.changed_paths,
        acceptance_criteria=(),
        constraints=(
            "read-only",
            *constraints,
            *(f"risk:{risk.value}" for risk in sorted(set(declared_risks), key=str)),
            *(() if configuration is None else (f"configuration:{configuration.identity}",)),
        ),
        patch_evidence=FrozenPatchEvidence.from_patch(evidence.patch),
    )
    root = Path(evidence.repository_root)
    with ArtifactStore.acquire_package_lock(root, package):
        try:
            store = ArtifactStore.create(
                root,
                package,
                patch=evidence.patch,
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
