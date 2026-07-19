"""Central secret redaction for all persisted and user-visible records."""

from __future__ import annotations

import re
from typing import Any

_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(bearer\s+)[^\s,;]+"),
    re.compile(
        r"(?i)(api[_-]?key|token|password|secret|session(?:id)?|access_key|aws_secret_access_key)"
        r"\s*[=:]\s*[^\s,;&]+"
    ),
    re.compile(r"(?i)([?&](?:api[_-]?key|token|secret|password|access_token)=)[^&#\s]+"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)


def redact(value: Any) -> Any:
    """Return recursively redacted data, retaining only safe field names and context."""
    if isinstance(value, dict):
        return {str(key): redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if not isinstance(value, str):
        return value
    result = value
    for pattern in _PATTERNS:
        if pattern.groups >= 1:
            result = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", result)
        else:
            result = pattern.sub("[REDACTED]", result)
    return result
