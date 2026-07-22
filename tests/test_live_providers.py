from __future__ import annotations

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError

import pytest

from review_fabric.configuration import ProviderBinding, Transport
from review_fabric.domain.adjudication import Dispute
from review_fabric.domain.models import FrozenPatchEvidence, ReviewPackage
from review_fabric.errors import InvalidReviewerOutputError, PolicyRejectionError
from review_fabric.reviewers.base import RoleRubric
from review_fabric.reviewers.providers import ProviderReviewer, http_post_json


def package() -> ReviewPackage:
    patch = (
        "diff --git a/src/a.py b/src/a.py\n"
        "--- a/src/a.py\n"
        "+++ b/src/a.py\n"
        "@@ -0,0 +1 @@\n"
        "+bad = True\n"
    )
    evidence = FrozenPatchEvidence.from_patch(patch)
    return ReviewPackage(
        repository_root="/repo",
        base_sha="a" * 40,
        head_sha="b" * 40,
        patch_digest=evidence.digest,
        selected_paths=("src/a.py",),
        acceptance_criteria=(),
        constraints=("read-only",),
        patch_evidence=evidence,
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
    assert seen["timeout"] == 60
    assert b"response_mime_type" in seen["data"]
    payload = json.loads(seen["data"])
    assert "untrusted data" in payload["systemInstruction"]["parts"][0]["text"].lower()
    assert "bad = True" in payload["contents"][0]["parts"][0]["text"]
    assert "+1:bad = True" in payload["contents"][0]["parts"][0]["text"]
    assert findings[0].package_id == package().review_id


def test_provider_request_timeout_is_explicit_and_bounded() -> None:
    seen: dict[str, int] = {}

    def opener(_request: object, timeout: int) -> Response:
        seen["timeout"] = timeout
        return Response(b'{"choices":[{"message":{"content":"{\\"findings\\":[]}"}}]}')

    reviewer = ProviderReviewer(
        binding(Transport.OPENAI_COMPATIBLE),
        "secret",
        RoleRubric("correctness", "review"),
        opener=opener,
        timeout_seconds=7,
    )

    assert reviewer.review(package(), reviewer.rubric) == ()
    assert seen["timeout"] == 7


def test_provider_prompt_uses_frozen_patch_and_rejects_fabricated_citations() -> None:
    seen: dict[str, object] = {}

    def opener(request: object, timeout: int) -> Response:
        seen["payload"] = json.loads(request.data)  # type: ignore[attr-defined]
        response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "findings": [
                                    {
                                        "severity": "concern",
                                        "title": "Bad",
                                        "claim": "Bad",
                                        "evidence": [
                                            {
                                                "path": "src/a.py",
                                                "start_line": 1,
                                                "end_line": 1,
                                                "excerpt": "invented",
                                            }
                                        ],
                                        "remediation": "Fix",
                                        "verification": "test",
                                        "confidence": 0.9,
                                    }
                                ]
                            }
                        )
                    }
                }
            ]
        }
        return Response(json.dumps(response).encode())

    reviewer = ProviderReviewer(
        binding(Transport.OPENAI_COMPATIBLE), "secret", RoleRubric("correctness", "review"), opener
    )
    with pytest.raises(InvalidReviewerOutputError, match="citation"):
        reviewer.review(package(), reviewer.rubric)

    system = seen["payload"]["messages"][0]["content"]  # type: ignore[index]
    evidence = seen["payload"]["messages"][1]["content"]  # type: ignore[index]
    assert "bad = True" in evidence
    assert package().repository_root not in evidence
    assert "untrusted data" in system.lower()


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
    with pytest.raises(PolicyRejectionError, match="not yet implemented"):
        ProviderReviewer(unsupported, None, RoleRubric("correctness", "review")).review(
            package(), RoleRubric("correctness", "review")
        )


@pytest.mark.parametrize(
    "transport",
    [Transport.AZURE_AI_FOUNDRY, Transport.OPENAI, Transport.ANTHROPIC, Transport.BEDROCK_IAM],
)
def test_every_not_yet_implemented_transport_fails_with_a_specific_reason(
    transport: Transport,
) -> None:
    """Each schema-valid but not-yet-wired Transport must fail with a distinct,
    honest reason rather than a generic message indistinguishable from a real
    provider outage. (configuration.py rejects these earlier, at config load time;
    this exercises the invocation-time backstop directly.)"""
    kwargs: dict[str, object] = {
        "provider": transport.value,
        "transport": transport,
        "model": "model-id",
        "credential_source": "environment",
        "credential_ref": "API_KEY",
    }
    if transport is Transport.AZURE_AI_FOUNDRY:
        kwargs.update(endpoint="https://azure.example.test", deployment="deployment")
    elif transport is Transport.BEDROCK_IAM:
        kwargs.update(credential_source="aws-chain", credential_ref=None, region="us-west-2")
    binding = ProviderBinding(**kwargs)  # type: ignore[arg-type]

    reviewer = ProviderReviewer(binding, "secret", RoleRubric("correctness", "review"))
    with pytest.raises(PolicyRejectionError, match="not yet implemented"):
        reviewer.review(package(), reviewer.rubric)


def test_oauth_transport_fails_without_client_or_token_scraping() -> None:
    binding = ProviderBinding(
        provider="official-client",
        transport=Transport.OAUTH,
        model="model-id",
        credential_source="external-session",
        credential_ref="official-client-profile",
    )
    reviewer = ProviderReviewer(binding, None, RoleRubric("correctness", "review"))

    with pytest.raises(PolicyRejectionError, match="OAuth adapter unavailable"):
        reviewer.review(package(), reviewer.rubric)


def test_bedrock_converse_content_rejects_response_with_no_text_block() -> None:
    """A response with only non-text blocks must raise InvalidReviewerOutputError, not
    an uncaught StopIteration/AttributeError that would bypass the intended category."""

    def opener(_request: object, _timeout: int) -> Response:
        return Response(
            b'{"output":{"message":{"content":['
            b'{"toolUse":{"name":"x","input":{}}}'
            b"]}}}"
        )

    reviewer = ProviderReviewer(
        ProviderBinding(
            provider="bedrock",
            transport=Transport.BEDROCK_CONVERSE,
            model="anthropic.claude-sonnet-5",
            credential_source="keychain",
            credential_ref="bedrock:us-west-2",
            region="us-west-2",
        ),
        "secret",
        RoleRubric("correctness", "review"),
        opener=opener,
    )
    with pytest.raises(InvalidReviewerOutputError, match="malformed Bedrock Converse"):
        reviewer.review(package(), reviewer.rubric)


def test_bedrock_converse_content_rejects_non_dict_content_items() -> None:
    def opener(_request: object, _timeout: int) -> Response:
        return Response(b'{"output":{"message":{"content":["not-a-dict"]}}}')

    reviewer = ProviderReviewer(
        ProviderBinding(
            provider="bedrock",
            transport=Transport.BEDROCK_CONVERSE,
            model="anthropic.claude-sonnet-5",
            credential_source="keychain",
            credential_ref="bedrock:us-west-2",
            region="us-west-2",
        ),
        "secret",
        RoleRubric("correctness", "review"),
        opener=opener,
    )
    with pytest.raises(InvalidReviewerOutputError, match="malformed Bedrock Converse"):
        reviewer.review(package(), reviewer.rubric)


def test_gemini_content_rejects_non_string_text_field() -> None:
    def opener(_request: object, _timeout: int) -> Response:
        response = {"candidates": [{"content": {"parts": [{"text": None}]}}]}
        return Response(json.dumps(response).encode())

    reviewer = ProviderReviewer(
        binding(Transport.GEMINI), "secret", RoleRubric("correctness", "review"), opener=opener
    )
    with pytest.raises(InvalidReviewerOutputError, match="malformed Gemini"):
        reviewer.review(package(), reviewer.rubric)


def test_openai_reasoning_prefix_is_stripped_before_structured_parse() -> None:
    response = (
        b'{"choices":[{"message":{"content":"<reasoning>internal</reasoning>'
        b'{\\"findings\\":[]}"}}]}'
    )

    reviewer = ProviderReviewer(
        binding(Transport.OPENAI_COMPATIBLE),
        "secret",
        RoleRubric("correctness", "review"),
        opener=lambda *_: Response(response),
    )
    assert reviewer.review(package(), reviewer.rubric) == ()


def test_openai_content_repairs_gpt_oss_nested_opening_brace_before_json_parse() -> None:
    response = (
        b'{"choices":[{"message":{"content":"<reasoning>internal</reasoning>'
        b'{\\"{\\"findings\\":[]}"}}]}'
    )
    reviewer = ProviderReviewer(
        binding(Transport.OPENAI_COMPATIBLE),
        "secret",
        RoleRubric("correctness", "review"),
        opener=lambda *_: Response(response),
    )
    assert reviewer.review(package(), reviewer.rubric) == ()


def test_bedrock_converse_uses_bearer_and_extracts_structured_output() -> None:
    def opener(request: object, timeout: int) -> Response:
        assert (
            request.full_url
            == "https://bedrock-runtime.us-west-2.amazonaws.com/model/anthropic.claude-sonnet-5/converse"
        )  # type: ignore[attr-defined]
        assert request.get_header("Authorization") == "Bearer secret"  # type: ignore[attr-defined]
        body = json.loads(request.data)  # type: ignore[attr-defined]
        assert "untrusted data" in body["system"][0]["text"].lower()
        assert "bad = True" in body["messages"][0]["content"][0]["text"]
        assert body["messages"][0]["content"][0]["text"]
        return Response(
            b'{"output":{"message":{"content":['
            b'{"reasoningContent":{"reasoningText":{"text":"internal"}}},'
            b'{"text":"```json\\n{\\"findings\\":[]}\\n```"}]}}}'
        )

    reviewer = ProviderReviewer(
        ProviderBinding(
            provider="bedrock",
            transport=Transport.BEDROCK_CONVERSE,
            model="anthropic.claude-sonnet-5",
            credential_source="keychain",
            credential_ref="bedrock:us-west-2",
            region="us-west-2",
        ),
        "secret",
        RoleRubric("correctness", "review"),
        opener=opener,
    )
    assert reviewer.review(package(), reviewer.rubric) == ()


def test_bedrock_openai_compatible_uses_bearer_chat_completions() -> None:
    response = b'{"choices":[{"message":{"content":"{\\"findings\\":[]}"}}]}'

    def opener(request: object, timeout: int) -> Response:
        assert (
            request.full_url
            == "https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1/chat/completions"
        )  # type: ignore[attr-defined]
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


def test_bedrock_gpt_oss_does_not_request_incompatible_json_object_mode() -> None:
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
    )
    payload = reviewer._openai_payload(package(), reviewer.rubric)
    assert "response_format" not in payload
    assert payload["reasoning_effort"] == "low"


def test_bedrock_gpt_oss_challenge_uses_compatible_payload_mode() -> None:
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
    )
    dispute = Dispute(
        group_id="group",
        question="Question",
        citations=({"path": "src/a.py", "start_line": 1, "end_line": 1, "excerpt": "bad"},),
    )
    payload = reviewer._openai_challenge_payload(dispute)
    assert "response_format" not in payload
    assert payload["reasoning_effort"] == "low"


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
    system = seen["payload"]["messages"][0]["content"]  # type: ignore[index]
    evidence = seen["payload"]["messages"][1]["content"]  # type: ignore[index]
    assert "src/a.py" in evidence
    assert "reviewer_id" not in evidence
    assert "untrusted data" in system.lower()


def test_bedrock_converse_challenge_uses_bounded_dispute_and_fenced_json() -> None:
    seen: dict[str, object] = {}

    def opener(request: object, timeout: int) -> Response:
        seen["payload"] = json.loads(request.data)  # type: ignore[attr-defined]
        return Response(
            b'{"output":{"message":{"content":[{"text":"```json\\n{\\"disposition\\":\\"confirm\\",\\"evidence\\":[{\\"path\\":\\"src/a.py\\",\\"start_line\\":1,\\"end_line\\":1,\\"excerpt\\":\\"bad\\"}]}\\n```"}]}}}'
        )

    reviewer = ProviderReviewer(
        ProviderBinding(
            provider="bedrock",
            transport=Transport.BEDROCK_CONVERSE,
            model="us.anthropic.claude-sonnet-5",
            credential_source="keychain",
            credential_ref="bedrock:us-west-2",
            region="us-west-2",
        ),
        "secret",
        RoleRubric("correctness", "review"),
        opener,
    )
    dispute = Dispute(
        group_id="group",
        question="Is this evidence sufficient?",
        citations=({"path": "src/a.py", "start_line": 1, "end_line": 1, "excerpt": "bad"},),
    )
    assert reviewer.review_challenge(dispute)["disposition"] == "confirm"
    assert "src/a.py" in seen["payload"]["messages"][0]["content"][0]["text"]  # type: ignore[index]

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
