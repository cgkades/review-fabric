from __future__ import annotations

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError

import pytest

from review_fabric.configuration import ProviderBinding, Transport
from review_fabric.domain.adjudication import Dispute
from review_fabric.domain.models import ReviewPackage
from review_fabric.errors import InvalidReviewerOutputError, PolicyRejectionError
from review_fabric.reviewers.base import RoleRubric
from review_fabric.reviewers.providers import ProviderReviewer, http_post_json


def package() -> ReviewPackage:
    return ReviewPackage(
        repository_root="/repo",
        base_sha="a" * 40,
        head_sha="b" * 40,
        patch_digest="c" * 64,
        selected_paths=("src/a.py",),
        acceptance_criteria=(),
        constraints=("read-only",),
        command_results=(),
    )


class Response:
    def __init__(self, payload: bytes) -> None:
        self.payload = io.BytesIO(payload)

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self.payload.read(size)


def binding(transport: Transport = Transport.GEMINI) -> ProviderBinding:
    return ProviderBinding(
        provider="provider",
        transport=transport,
        model="light-model",
        credential_source="environment",
        credential_ref="API_KEY",
        endpoint="https://provider.example.test/v1" if transport is not Transport.GEMINI else None,
    )


def test_gemini_request_is_bounded_and_parses_strict_findings() -> None:
    seen = {}
    response = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps(
                                {
                                    "findings": [
                                        {
                                            "severity": "suggestion",
                                            "title": "Test",
                                            "claim": "Missing test",
                                            "evidence": [],
                                            "remediation": "Add test",
                                            "verification": "pytest",
                                            "confidence": 0.8,
                                        }
                                    ]
                                }
                            )
                        }
                    ]
                }
            }
        ]
    }

    def opener(request: object, timeout: int) -> Response:
        seen["url"], seen["timeout"], seen["data"] = request.full_url, timeout, request.data  # type: ignore[attr-defined]
        assert request.get_header("X-goog-api-key") == "secret"  # type: ignore[attr-defined]
        return Response(json.dumps(response).encode())

    reviewer = ProviderReviewer(
        binding(), "secret", RoleRubric("correctness", "review"), opener=opener
    )
    findings = reviewer.review(package(), reviewer.rubric)
    assert seen["url"].endswith("/v1beta/models/light-model:generateContent")
    assert seen["timeout"] == 10
    assert b"response_mime_type" in seen["data"]
    assert findings[0].package_id == package().review_id


def test_openai_compatible_request_and_malformed_or_network_output_escalate_redacted() -> None:
    def opener(request: object, timeout: int) -> Response:
        assert request.full_url.endswith("/v1/chat/completions")  # type: ignore[attr-defined]
        assert request.get_header("Authorization") == "Bearer secret"  # type: ignore[attr-defined]
        return Response(b'{"choices":[{"message":{"content":"not json"}}]}')

    reviewer = ProviderReviewer(
        binding(Transport.OPENAI_COMPATIBLE),
        "secret",
        RoleRubric("correctness", "review"),
        opener=opener,
    )
    with pytest.raises(InvalidReviewerOutputError, match="malformed") as error:
        reviewer.review(package(), reviewer.rubric)
    assert "secret" not in str(error.value)
    with pytest.raises(TimeoutError, match="timed out"):
        http_post_json(
            "https://provider.example.test",
            {},
            {},
            opener=lambda *_: (_ for _ in ()).throw(TimeoutError()),
        )
    with pytest.raises(PolicyRejectionError, match="unavailable"):
        http_post_json(
            "https://provider.example.test",
            {},
            {},
            opener=lambda *_: (_ for _ in ()).throw(URLError("secret")),
        )


def test_redirect_is_rejected_without_following_or_forwarding_credentials() -> None:
    received_target_requests: list[dict[str, str]] = []

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            if self.path == "/source":
                self.send_response(302)
                self.send_header("Location", "/target")
                self.end_headers()
                return
            received_target_requests.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()

        def log_message(self, _format: str, *_args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/source"
        with pytest.raises(PolicyRejectionError, match="redirect"):
            http_post_json(
                endpoint,
                {"authorization": "Bearer credential", "x-goog-api-key": "credential"},
                {},
                allow_local_http=True,
            )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert received_target_requests == []


def test_response_cap_and_unsupported_transport_are_safe() -> None:
    with pytest.raises(PolicyRejectionError, match="unsafe provider endpoint"):
        http_post_json(
            "http://provider.example.test?api_key=secret", {"x-goog-api-key": "secret"}, {}
        )
    with pytest.raises(PolicyRejectionError, match="response too large"):
        http_post_json(
            "https://provider.example.test",
            {},
            {},
            opener=lambda *_: Response(b"x" * (64 * 1024 + 1)),
        )
    unsupported = ProviderBinding(
        provider="bedrock",
        transport=Transport.BEDROCK_IAM,
        model="m",
        credential_source="aws-chain",
        region="us-west-2",
    )
    with pytest.raises(PolicyRejectionError, match="unsupported"):
        ProviderReviewer(unsupported, None, RoleRubric("correctness", "review")).review(
            package(), RoleRubric("correctness", "review")
        )



def test_bedrock_openai_compatible_uses_bearer_chat_completions() -> None:
    response = b'{"choices":[{"message":{"content":"{\\"findings\\":[]}"}}]}'

    def opener(request: object, timeout: int) -> Response:
        assert request.full_url == "https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1/chat/completions"  # type: ignore[attr-defined]
        assert request.get_header("Authorization") == "Bearer secret"  # type: ignore[attr-defined]
        return Response(response)

    reviewer = ProviderReviewer(
        ProviderBinding(
            provider="bedrock",
            transport=Transport.BEDROCK_OPENAI_COMPATIBLE,
            model="openai.gpt-oss-20b-1:0",
            credential_source="environment",
            credential_ref="BEDROCK_API_KEY",
            endpoint="https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1",
        ),
        "secret",
        RoleRubric("correctness", "review"),
        opener=opener,
    )
    assert reviewer.review(package(), reviewer.rubric) == ()


def test_provider_challenge_sends_only_bounded_dispute_and_strictly_parses_response() -> None:
    seen: dict[str, object] = {}

    def opener(request: object, timeout: int) -> Response:
        seen["payload"] = json.loads(request.data)  # type: ignore[attr-defined]
        response = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"disposition":"confirm","evidence":['
                            '{"path":"src/a.py","start_line":1,"end_line":1,"excerpt":"bad"}'
                            "]}"
                        )
                    }
                }
            ]
        }
        return Response(json.dumps(response).encode())

    reviewer = ProviderReviewer(
        binding(Transport.OPENAI_COMPATIBLE), "secret", RoleRubric("correctness", "review"), opener
    )
    dispute = Dispute(
        group_id="group",
        question="Is this evidence sufficient?",
        citations=({"path": "src/a.py", "start_line": 1, "end_line": 1, "excerpt": "bad"},),
    )
    assert reviewer.review_challenge(dispute) == {
        "disposition": "confirm",
        "evidence": ({"path": "src/a.py", "start_line": 1, "end_line": 1, "excerpt": "bad"},),
    }
    prompt = seen["payload"]["messages"][0]["content"]  # type: ignore[index]
    assert "src/a.py" in prompt
    assert "Package:" not in prompt
    assert "reviewer_id" not in prompt


def test_challenge_response_rejects_extra_or_malformed_fields() -> None:
    reviewer = ProviderReviewer(
        binding(Transport.OPENAI_COMPATIBLE), "secret", RoleRubric("correctness", "review")
    )
    with pytest.raises(InvalidReviewerOutputError, match="malformed challenge"):
        reviewer._parse_challenge_response(
            '{"disposition":"confirm","evidence":[],"peer_outputs":"leak"}',
            Dispute(
                group_id="group",
                question="Is this evidence sufficient?",
                citations=({"path": "src/a.py", "start_line": 1, "end_line": 1, "excerpt": "bad"},),
            ),
        )
