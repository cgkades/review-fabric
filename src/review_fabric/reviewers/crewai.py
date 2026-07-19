"""Optional CrewAI adapter, isolated so the core never imports CrewAI."""

from __future__ import annotations

from review_fabric.errors import PolicyRejectionError


def build_flow(*args: object, **kwargs: object) -> object:
    """Load CrewAI only when this optional integration is explicitly selected."""
    try:
        from crewai import Flow  # type: ignore[import-not-found]
    except ImportError as error:
        raise PolicyRejectionError("CrewAI adapter selected but CrewAI is not installed") from error
    return Flow(*args, **kwargs)
