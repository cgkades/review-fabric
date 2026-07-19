from __future__ import annotations

from review_fabric.redaction import redact


def test_redacts_credentials_and_query_secrets() -> None:
    raw = "Authorization: Bearer abcdefghijklmnop\nhttps://x.test/?api_key=abcdefghijk\nAWS_SECRET_ACCESS_KEY=abcdefghijk"
    safe = redact(raw)
    assert "abcdefghijk" not in safe
    assert "[REDACTED]" in safe
