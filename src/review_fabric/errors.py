"""Structured errors emitted by the review protocol."""

from __future__ import annotations


class ReviewFabricError(Exception):
    """Base class for errors that can be persisted as review events."""

    def to_record(self) -> dict[str, str]:
        return {"error_type": type(self).__name__, "message": str(self)}


class InvalidReviewPackageError(ReviewFabricError):
    """Raised when review inputs cannot form a valid immutable package."""


class InvalidReviewerOutputError(ReviewFabricError):
    """Raised when a reviewer response violates the protocol schema."""


class PolicyRejectionError(ReviewFabricError):
    """Raised when deterministic policy forbids a requested execution path."""


class DeniedMutationError(ReviewFabricError):
    """Raised when a read-only review attempts a prohibited mutation."""
