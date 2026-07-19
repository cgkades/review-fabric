## 1. Configuration and CLI activation

- [x] 1.1 Write failing CLI tests for `--config`, selected-role validation, and missing credential escalation.
- [x] 1.2 Implement JSON config loading, `--config` CLI handling, and manifest-safe binding records.
- [x] 1.3 Run the focused CLI/configuration tests.

## 2. Live provider reviewer

- [x] 2.1 Write failing HTTP-client tests for bounded Gemini Developer API request construction, timeout, redacted failures, and strict JSON response extraction.
- [x] 2.2 Implement Gemini light-model transport and `ProviderReviewer` conversion into validated findings.
- [x] 2.3 Write failing OpenAI-compatible transport tests and implement the generic/xAI path.
- [x] 2.4 Add native-provider/Bedrock transports behind the same reviewer interface, or explicit unsupported escalation where no safe documented path is available.
- [x] 2.5 Run provider, credential, and redaction tests.

## 3. Bounded conversation and adjudication

- [x] 3.1 Write failing integration tests that first-pass calls are isolated while targeted challenges receive only normalized evidence.
- [x] 3.2 Implement one challenge, reviewer response, and coordinator decision persistence with no majority-vote rule.
- [x] 3.3 Test timeout, malformed output, unavailable reviewer, and challenge-limit escalation paths.
- [x] 3.4 Reject all provider redirects before a credential-bearing follow-up request, and require evidence-bound challenge dispositions for adjudication.

## 4. Live validation and remediation

- [x] 4.1 Add secret-free light-model configuration examples and operational documentation.
- [x] 4.2 Run a tiny real provider smoke review with strict token/output/time limits; redact and persist result.
- [x] 4.3 Review `review-fabric` and `~/git/github/llm-benchmark`; test and fix confirmed Review Fabric defects.
- [x] 4.4 Run independent code review, `ruff`, full pytest, strict OpenSpec validation, commit, and push.
