"""Opt-in, bounded stdlib transports for structured provider reviews."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import ValidationError

from review_fabric.configuration import ProviderBinding, Transport
from review_fabric.domain.adjudication import ChallengeResponse, Dispute
from review_fabric.domain.findings import Finding
from review_fabric.domain.models import ReviewPackage
from review_fabric.errors import InvalidReviewerOutputError, PolicyRejectionError
from review_fabric.reviewers.base import RoleRubric

_TIMEOUT_SECONDS = 10
_MAX_RESPONSE_BYTES = 64 * 1024


@dataclass(frozen=True)
class ProviderRequest:
    endpoint: str | None
    model: str
    headers: dict[str, str]
    transport: str


class ProviderClient(Protocol):
    def invoke(self, request: ProviderRequest, payload: dict[str, object]) -> dict[str, object]: ...


UrlOpener = Callable[[Request, int], object]


class _RejectRedirect(HTTPRedirectHandler):
    """Stop redirects before urllib can make a credential-bearing second request."""

    def redirect_request(
        self, request: Request, fp: object, code: int, message: str, headers: object, newurl: str
    ) -> Request | None:
        return None


def _open_without_redirect(request: Request, timeout: int) -> object:
    return build_opener(_RejectRedirect()).open(request, timeout=timeout)


def request_for(binding: ProviderBinding, credential: str | None) -> ProviderRequest:
    """Build redacted request metadata; the actual credential is invocation-only."""
    headers = {"authorization": "Bearer [runtime credential]"} if credential else {}
    if binding.transport is Transport.BEDROCK_IAM:
        return ProviderRequest(None, binding.model, {}, binding.transport.value)
    if binding.transport is Transport.OAUTH:
        raise PolicyRejectionError(
            "OAuth adapter unavailable; configure an official supported session/helper"
        )
    return ProviderRequest(binding.endpoint, binding.model, headers, binding.transport.value)


def invoke(
    client: ProviderClient,
    binding: ProviderBinding,
    credential: str | None,
    payload: dict[str, object],
) -> dict[str, object]:
    return client.invoke(request_for(binding, credential), payload)


def _validate_outbound_endpoint(endpoint: str, *, allow_local_http: bool) -> None:
    parsed = urlparse(endpoint)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        parsed.username
        or parsed.password
        or parsed.params
        or parsed.query
        or parsed.fragment
        or not parsed.hostname
        or (
            parsed.scheme != "https"
            and not (allow_local_http and local and parsed.scheme == "http")
        )
    ):
        raise PolicyRejectionError("unsafe provider endpoint")


def http_post_json(
    endpoint: str,
    headers: dict[str, str],
    payload: dict[str, object],
    *,
    opener: UrlOpener | None = None,
    allow_local_http: bool = False,
) -> dict[str, object]:
    """POST JSON with a fixed timeout and a hard response cap; never expose server detail."""
    _validate_outbound_endpoint(endpoint, allow_local_http=allow_local_http)
    request = Request(
        endpoint,
        data=json.dumps(payload, separators=(",", ":")).encode(),
        headers={"content-type": "application/json", **headers},
        method="POST",
    )
    try:
        open_request = opener or _open_without_redirect
        with open_request(request, _TIMEOUT_SECONDS) as response:  # type: ignore[union-attr]
            raw = response.read(_MAX_RESPONSE_BYTES + 1)  # type: ignore[union-attr]
    except TimeoutError as error:
        raise TimeoutError("provider request timed out") from error
    except HTTPError as error:
        if 300 <= error.code < 400:
            raise PolicyRejectionError("provider redirect rejected") from error
        raise PolicyRejectionError("provider unavailable") from error
    except (URLError, OSError) as error:
        raise PolicyRejectionError("provider unavailable") from error
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise PolicyRejectionError("provider response too large")
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidReviewerOutputError("provider returned malformed JSON") from error
    if not isinstance(decoded, dict):
        raise InvalidReviewerOutputError("provider returned malformed JSON")
    return decoded


@dataclass(frozen=True)
class ProviderReviewer:
    binding: ProviderBinding
    credential: str | None
    rubric: RoleRubric
    opener: UrlOpener | None = None

    def review(self, package: ReviewPackage, rubric: RoleRubric) -> tuple[Finding, ...]:
        if self.binding.transport is Transport.GEMINI:
            response = http_post_json(
                self._gemini_endpoint(),
                self._gemini_headers(),
                self._gemini_payload(package, rubric),
                opener=self.opener,
                allow_local_http=self.binding.allow_local_http,
            )
            content = self._gemini_content(response)
        elif self.binding.transport in {Transport.OPENAI_COMPATIBLE, Transport.XAI}:
            if not self.binding.endpoint:
                raise PolicyRejectionError("openai-compatible transport requires endpoint")
            response = http_post_json(
                self.binding.endpoint.rstrip("/") + "/chat/completions",
                self._bearer_headers(),
                self._openai_payload(package, rubric),
                opener=self.opener,
                allow_local_http=self.binding.allow_local_http,
            )
            content = self._openai_content(response)
        else:
            raise PolicyRejectionError("unsupported native/OAuth transport")
        try:
            parsed = json.loads(content)
            findings = parsed["findings"]
            if set(parsed) != {"findings"} or not isinstance(findings, list):
                raise ValueError("unexpected response shape")
            return tuple(
                Finding.model_validate(
                    {**item, "package_id": package.review_id, "reviewer_id": rubric.role}
                )
                for item in findings
            )
        except (KeyError, TypeError, json.JSONDecodeError, ValidationError, ValueError) as error:
            raise InvalidReviewerOutputError("provider returned malformed findings JSON") from error

    def review_challenge(self, dispute: Dispute) -> dict[str, object]:
        """Challenge with only the bounded normalized dispute DTO, never a package."""
        if self.binding.transport is Transport.GEMINI:
            response = http_post_json(
                self._gemini_endpoint(),
                self._gemini_headers(),
                self._gemini_challenge_payload(dispute),
                opener=self.opener,
                allow_local_http=self.binding.allow_local_http,
            )
            content = self._gemini_content(response)
        elif self.binding.transport in {Transport.OPENAI_COMPATIBLE, Transport.XAI}:
            if not self.binding.endpoint:
                raise PolicyRejectionError("openai-compatible transport requires endpoint")
            response = http_post_json(
                self.binding.endpoint.rstrip("/") + "/chat/completions",
                self._bearer_headers(),
                self._openai_challenge_payload(dispute),
                opener=self.opener,
                allow_local_http=self.binding.allow_local_http,
            )
            content = self._openai_content(response)
        else:
            raise PolicyRejectionError("unsupported native/OAuth transport")
        return self._parse_challenge_response(content, dispute)

    @staticmethod
    def _parse_challenge_response(content: str, dispute: Dispute) -> dict[str, object]:
        try:
            return (
                ChallengeResponse.model_validate(json.loads(content))
                .validate_for(dispute)
                .model_dump()
            )
        except (TypeError, json.JSONDecodeError, ValidationError, ValueError) as error:
            raise InvalidReviewerOutputError(
                "provider returned malformed challenge JSON"
            ) from error

    def _gemini_endpoint(self) -> str:
        return (self.binding.endpoint or "https://generativelanguage.googleapis.com/v1beta").rstrip(
            "/"
        ) + f"/models/{self.binding.model}:generateContent"

    def _gemini_headers(self) -> dict[str, str]:
        if not self.credential:
            raise PolicyRejectionError("credential unavailable")
        return {"x-goog-api-key": self.credential}

    def _bearer_headers(self) -> dict[str, str]:
        if not self.credential:
            raise PolicyRejectionError("credential unavailable")
        return {"authorization": f"Bearer {self.credential}"}

    @staticmethod
    def _prompt(package: ReviewPackage, rubric: RoleRubric) -> str:
        return (
            f"Role: {rubric.role}\nRubric: {rubric.rubric}\nPackage: "
            + json.dumps(package.model_dump(mode="json"), separators=(",", ":"))
            + '\nReturn only JSON object: {"findings":[...]}'
        )

    def _gemini_payload(self, package: ReviewPackage, rubric: RoleRubric) -> dict[str, object]:
        return {
            "contents": [{"parts": [{"text": self._prompt(package, rubric)}]}],
            "generationConfig": {
                "response_mime_type": "application/json",
                "max_output_tokens": 2048,
            },
        }

    def _openai_payload(self, package: ReviewPackage, rubric: RoleRubric) -> dict[str, object]:
        return {
            "model": self.binding.model,
            "messages": [{"role": "user", "content": self._prompt(package, rubric)}],
            "response_format": {"type": "json_object"},
            "max_tokens": 2048,
        }

    @staticmethod
    def _challenge_prompt(dispute: Dispute) -> str:
        return (
            "Evaluate only this normalized evidence dispute. Return only JSON object: "
            '{"disposition":"confirm|reject|uncertain","evidence":["exact dispute citation"]}. '
            "Use confirm only when evidence is sufficient; evidence must reproduce only supplied "
            "citations. Use reject or uncertain with an empty evidence list.\nDispute: "
            + json.dumps(dispute.model_dump(mode="json"), separators=(",", ":"))
        )

    def _gemini_challenge_payload(self, dispute: Dispute) -> dict[str, object]:
        return {
            "contents": [{"parts": [{"text": self._challenge_prompt(dispute)}]}],
            "generationConfig": {
                "response_mime_type": "application/json",
                "max_output_tokens": 512,
            },
        }

    def _openai_challenge_payload(self, dispute: Dispute) -> dict[str, object]:
        return {
            "model": self.binding.model,
            "messages": [{"role": "user", "content": self._challenge_prompt(dispute)}],
            "response_format": {"type": "json_object"},
            "max_tokens": 512,
        }

    @staticmethod
    def _gemini_content(response: dict[str, object]) -> str:
        try:
            return response["candidates"][0]["content"]["parts"][0]["text"]  # type: ignore[index]
        except (KeyError, IndexError, TypeError) as error:
            raise InvalidReviewerOutputError(
                "provider returned malformed Gemini response"
            ) from error

    @staticmethod
    def _openai_content(response: dict[str, object]) -> str:
        try:
            return response["choices"][0]["message"]["content"]  # type: ignore[index]
        except (KeyError, IndexError, TypeError) as error:
            raise InvalidReviewerOutputError(
                "provider returned malformed OpenAI response"
            ) from error
