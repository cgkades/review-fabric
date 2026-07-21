"""Central secret redaction for all persisted and user-visible records."""

from __future__ import annotations

import re
from typing import Any

# Matches "keyword" only when it is not itself embedded inside a larger alphanumeric
# identifier, while still treating "_"/"-" as valid delimiters (so "aws_secret_access_key"
# and "signing-key" both match on "secret"/"key" respectively, but "monkey" and
# "mypassword123" style false-adjacent-word matches without a delimiter do not expand
# scope beyond the original keyword-boundary behavior).
_NOT_ALNUM_BEFORE = r"(?<![A-Za-z0-9])"
_NOT_ALNUM_AFTER = r"(?![A-Za-z0-9])"
_SECRET_KEYWORD = (
    r"(?:api[_-]?key|access[_-]?key|token|password|passwd|secret|session(?:id)?|key)"
)

_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;\"'&]+"),
    re.compile(r"(?i)(bearer\s+)[^\s,;\"'&]+"),
    # Any other Authorization scheme (Basic, Digest, custom): redact the whole value.
    re.compile(r"(?i)(authorization\s*:\s*)(?!bearer\b)[^\r\n]+"),
    # PEM-encoded key/certificate blocks, which can span many lines; matched and fully
    # replaced before the generic keyword pattern below so the "-----BEGIN"/"-----END"
    # markers are not partially consumed first.
    re.compile(r"-----BEGIN [A-Z0-9 ]+-----[\s\S]*?-----END [A-Z0-9 ]+-----"),
    # keyword = value / keyword: value / keyword: "value" (JSON-quoted), tolerating
    # optional surrounding quotes and underscore/dash-delimited compound identifiers.
    re.compile(
        rf"(?i){_NOT_ALNUM_BEFORE}({_SECRET_KEYWORD}){_NOT_ALNUM_AFTER}"
        r"\s*[\"']?\s*[:=]\s*[\"']?[^\s,;&\"']+"
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
