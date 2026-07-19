from hashlib import sha256

import pytest
from pydantic import ValidationError

from review_fabric.domain.models import CommandResult, ReviewPackage
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
