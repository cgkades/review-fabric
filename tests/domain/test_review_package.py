from hashlib import sha256

import pytest
from pydantic import ValidationError

from review_fabric.domain.models import CommandResult, FrozenPatchEvidence, ReviewPackage
from review_fabric.serialization import canonical_json_bytes


def make_package(**changes: object) -> ReviewPackage:
    values: dict[str, object] = {
        "repository_root": "/workspace/service",
        "base_sha": "a" * 40,
        "head_sha": "b" * 40,
        "patch_digest": "c" * 64,
        "selected_paths": ("src/service.py",),
        "acceptance_criteria": ("Retry writes are idempotent.",),
        "constraints": ("No schema migration.",),
        "command_results": (
            CommandResult(command=("pytest",), exit_code=0, stdout="1 passed", stderr=""),
        ),
    }
    values.update(changes)
    return ReviewPackage(**values)


def test_review_package_has_deterministic_identifier() -> None:
    package = make_package()

    expected = sha256(canonical_json_bytes(package.model_dump(mode="json"))).hexdigest()

    assert package.review_id == expected
    assert make_package().review_id == package.review_id


def test_review_package_identity_changes_with_review_evidence() -> None:
    baseline = make_package()
    changed = make_package(patch_digest="d" * 64)

    assert changed.review_id != baseline.review_id


def test_review_package_is_immutable() -> None:
    package = make_package()

    with pytest.raises(ValidationError):
        package.base_sha = "e" * 40  # type: ignore[misc]


def test_review_package_requires_commit_shas_and_patch_digest() -> None:
    with pytest.raises(ValidationError):
        make_package(base_sha="short")

    with pytest.raises(ValidationError):
        make_package(patch_digest="not-a-sha256")


def test_command_result_requires_a_command_and_nonnegative_exit_code() -> None:
    with pytest.raises(ValidationError):
        CommandResult(command=(), exit_code=0, stdout="", stderr="")

    with pytest.raises(ValidationError):
        CommandResult(command=("pytest",), exit_code=-1, stdout="", stderr="")


def test_frozen_patch_evidence_is_bounded_digest_verified_and_cites_exact_head_lines() -> None:
    patch = (
        "diff --git a/src/service.py b/src/service.py\n"
        "--- a/src/service.py\n"
        "+++ b/src/service.py\n"
        "@@ -1,2 +1,3 @@\n"
        " context = True\n"
        "+timeout = 60\n"
        " return context\n"
    )

    evidence = FrozenPatchEvidence.from_patch(patch)

    assert evidence.digest == sha256(patch.encode()).hexdigest()
    assert evidence.supports_citation(
        {
            "path": "src/service.py",
            "start_line": 1,
            "end_line": 2,
            "excerpt": "context = True\ntimeout = 60",
        }
    )
    assert not evidence.supports_citation(
        {"path": "src/service.py", "start_line": 2, "end_line": 2, "excerpt": "timeout = 10"}
    )
    assert not evidence.supports_citation(
        {"path": "other.py", "start_line": 2, "end_line": 2, "excerpt": "timeout = 60"}
    )


def test_review_package_rejects_patch_evidence_with_mismatched_digest() -> None:
    evidence = FrozenPatchEvidence.from_patch(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+value = 1\n"
    )

    with pytest.raises(ValidationError, match="patch evidence digest"):
        make_package(patch_evidence=evidence)
