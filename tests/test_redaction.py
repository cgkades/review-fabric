from __future__ import annotations

from review_fabric.redaction import redact


def test_redacts_credentials_and_query_secrets() -> None:
    raw = "Authorization: Bearer abcdefghijklmnop\nhttps://x.test/?api_key=abcdefghijk\nAWS_SECRET_ACCESS_KEY=abcdefghijk"
    safe = redact(raw)
    assert "abcdefghijk" not in safe
    assert "[REDACTED]" in safe


def test_redacts_json_quoted_secret_values() -> None:
    safe = redact('{"password": "hunter2ProdDbPass!"}')
    assert "hunter2ProdDbPass!" not in safe


def test_redacts_underscore_joined_secret_identifiers() -> None:
    safe = redact("aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
    assert "wJalrXUtnFEMI" not in safe


def test_redacts_generic_key_named_identifiers() -> None:
    for line in ("signing_key: MIIEvQIBADAN", 'PRIVATE_KEY="MIIEvQIBADANsecretvalue"'):
        safe = redact(line)
        assert "MIIEvQIBADAN" not in safe


def test_redacts_non_bearer_authorization_headers() -> None:
    safe = redact("Authorization: Basic dXNlcjpwYXNzd29yZA==")
    assert "dXNlcjpwYXNzd29yZA==" not in safe


def test_redacts_multiline_pem_blocks() -> None:
    raw = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIBogIBAAJBAKsecretbase64\n"
        "-----END RSA PRIVATE KEY-----"
    )
    safe = redact(raw)
    assert "MIIBogIBAAJBAKsecretbase64" not in safe


def test_does_not_redact_unrelated_words_containing_key_or_password() -> None:
    for benign in ("monkey", "turkey", "keyword", "this is a keynote speech"):
        assert redact(benign) == benign
