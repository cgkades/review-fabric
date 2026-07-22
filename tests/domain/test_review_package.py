from hashlib import sha256

import pytest
from pydantic import ValidationError

from review_fabric.domain.models import FrozenPatchEvidence, ReviewPackage
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
    }
    values.update(changes)
    return ReviewPackage(**values)


def test_review_package_has_deterministic_identifier() -> None:
    package = make_package()

    expected = sha256(
        canonical_json_bytes(
            {
                "identity_schema_version": 1,
                "repository_root": package.repository_root,
                "base_sha": package.base_sha,
                "head_sha": package.head_sha,
                "patch_digest": package.patch_digest,
                "selected_paths": list(package.selected_paths),
                "acceptance_criteria": list(package.acceptance_criteria),
                "constraints": list(package.constraints),
            }
        )
    ).hexdigest()

    assert package.review_id == expected
    assert make_package().review_id == package.review_id


def test_review_package_identity_is_unaffected_by_unrelated_field_additions() -> None:
    """review_id must not be a hash of the full model dump: adding a field to
    ReviewPackage in the future (e.g. an optional metadata field with a default)
    must not silently re-address every existing artifact. patch_evidence is the one
    field ReviewPackage already carries that is redundant with patch_digest (its
    digest is validated to always match), so it is a real, present-day case of this:
    attaching it must not change review_id."""
    patch = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+value = 1\n"
    evidence = FrozenPatchEvidence.from_patch(patch)
    without_patch_evidence = make_package(patch_digest=evidence.digest)
    with_patch_evidence = make_package(patch_digest=evidence.digest, patch_evidence=evidence)

    assert with_patch_evidence.review_id == without_patch_evidence.review_id


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
    assert not evidence.supports_citation(
        {"path": "src/service.py", "start_line": True, "end_line": 1, "excerpt": "context = True"}
    )


def test_numbered_patch_prefixes_head_side_lines_and_leaves_everything_else_untouched() -> None:
    """A reviewer model must copy a citation's line number rather than derive it by
    counting through hunk headers itself — smaller models were observed doing that
    arithmetic incorrectly. numbered_patch() is a display-only rendering (never
    stored as evidence) that removes the need to count at all."""
    patch = (
        "diff --git a/src/service.py b/src/service.py\n"
        "--- a/src/service.py\n"
        "+++ b/src/service.py\n"
        "@@ -1,2 +1,3 @@\n"
        " context = True\n"
        "+timeout = 60\n"
        "-old = True\n"
        " return context\n"
    )

    evidence = FrozenPatchEvidence.from_patch(patch)

    assert evidence.numbered_patch() == (
        "diff --git a/src/service.py b/src/service.py\n"
        "--- a/src/service.py\n"
        "+++ b/src/service.py\n"
        "@@ -1,2 +1,3 @@\n"
        " 1:context = True\n"
        "+2:timeout = 60\n"
        "-old = True\n"
        " 3:return context"
    )


def test_frozen_patch_evidence_default_bound_rejects_an_oversized_patch() -> None:
    with pytest.raises(ValueError, match="exceeds byte limit"):
        FrozenPatchEvidence.from_patch("x" * (60 * 1024))


def test_frozen_patch_evidence_max_bytes_override_is_honored() -> None:
    patch = "x" * (60 * 1024)

    evidence = FrozenPatchEvidence.from_patch(patch, max_bytes=100 * 1024)

    assert len(evidence.patch) == len(patch)


def test_frozen_patch_evidence_max_bytes_override_survives_embedding_in_a_package() -> None:
    """Regression: pydantic reruns FrozenPatchEvidence's own model validator when an
    already-built instance is embedded as a field value into ReviewPackage. That
    revalidation pass has no access to the original from_patch(max_bytes=...) call's
    context, so a raised bound must not be silently forgotten and re-checked against
    the conservative default on the second pass."""
    patch = "x" * (60 * 1024)
    evidence = FrozenPatchEvidence.from_patch(patch, max_bytes=100 * 1024)

    package = make_package(patch_digest=evidence.digest, patch_evidence=evidence)

    assert package.patch_evidence is not None
    assert len(package.patch_evidence.patch) == len(patch)


def test_review_package_rejects_patch_evidence_with_mismatched_digest() -> None:
    evidence = FrozenPatchEvidence.from_patch(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+value = 1\n"
    )

    with pytest.raises(ValidationError, match="patch evidence digest"):
        make_package(patch_evidence=evidence)
