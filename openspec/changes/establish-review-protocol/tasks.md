## 1. Project foundation

- [x] 1.1 Create `pyproject.toml` for Python 3.12+, the `src/review_fabric/` package layout, pytest configuration, and development dependencies (`pydantic`, `pytest`, `ruff`). Verify with `python -m pytest` and `ruff check .`.
- [x] 1.2 Add a versioned Pydantic serialization helper that emits canonical JSON for protocol records. Write tests proving identical records produce identical bytes and changed evidence produces changed bytes.
- [x] 1.3 Create package-level error types for invalid package input, invalid reviewer output, policy rejection, and denied mutation attempts. Verify error types are importable and serializable as structured failure events.

## 2. Review package and evidence collection

- [x] 2.1 Write failing tests for `ReviewPackage`, command-result, and constraint schemas in `tests/domain/test_review_package.py`, including immutable fields and required SHA/digest data.
- [x] 2.2 Implement immutable `ReviewPackage` domain models in `src/review_fabric/domain/models.py` and canonical review-ID calculation. Run `pytest tests/domain/test_review_package.py -v`.
- [x] 2.3 Write failing fixture tests for resolving a local Git base/head pair and rejecting an unknown revision in `tests/evidence/test_git.py`.
- [x] 2.4 Implement read-only Git evidence collection in `src/review_fabric/evidence/git.py`: resolve commits, capture a diff, calculate its digest, and record selected context paths. Run `pytest tests/evidence/test_git.py -v`.
- [x] 2.5 Implement captured read-only command evidence in `src/review_fabric/evidence/commands.py`, with explicit allow-listing and a test that denied mutation commands return structured errors.

## 3. Findings and independent reviewer interface

- [x] 3.1 Write failing schema tests for `Finding`, `Evidence`, severity, remediation, confidence, and verification criteria in `tests/domain/test_findings.py`.
- [x] 3.2 Implement finding and evidence domain models plus validation that rejects evidence-free blocker/concern findings. Run `pytest tests/domain/test_findings.py -v`.
- [x] 3.3 Define the provider-neutral `Reviewer` protocol and role-rubric input in `src/review_fabric/reviewers/base.py`; add a deterministic fake reviewer for tests.
- [x] 3.4 Write a test proving first-pass reviewer invocations receive the package and their own role rubric but no peer output.
- [x] 3.5 Implement the first-pass fan-out service using fake reviewers and verify the isolation test passes.

## 4. Policy, normalization, and adjudication state machine

- [x] 4.1 Write fixture tests for low-risk and high-risk review-plan selection in `tests/domain/test_policy.py`.
- [x] 4.2 Implement explicit risk indicators, logical reviewer roles, max-reviewer limits, challenge limits, timeout/retry limits, and terminal behavior in `src/review_fabric/domain/policy.py`. Run `pytest tests/domain/test_policy.py -v`.
- [x] 4.3 Write failing tests for stable finding IDs, duplicate grouping, and retention of an evidence-backed minority finding in `tests/domain/test_normalization.py`.
- [x] 4.4 Implement deterministic normalization and duplicate grouping in `src/review_fabric/domain/normalization.py`. Run `pytest tests/domain/test_normalization.py -v`.
- [x] 4.5 Write failing tests for dispute construction, one-round enforcement, unsupported challenge responses, and the `ACCEPT`/`CHANGE`/`ESCALATE` decision schema in `tests/domain/test_adjudication.py`.
- [x] 4.6 Implement `Dispute`, `Decision`, evidence-limited challenge validation, and terminal routing in `src/review_fabric/domain/adjudication.py`. Run `pytest tests/domain/test_adjudication.py -v`.

## 5. Durable artifacts and summary

- [x] 5.1 Write tests that a review artifact contains a manifest, append-only schema-versioned events, and deterministic summary regeneration in `tests/artifacts/test_store.py`.
- [x] 5.2 Implement `.review-fabric/reviews/<review-id>/` artifact storage and atomic single-record writes in `src/review_fabric/evidence/artifacts.py`. Run `pytest tests/artifacts/test_store.py -v`.
- [x] 5.3 Implement a Markdown summary renderer that uses only persisted machine-readable records. Add a deletion-and-regeneration test.
- [x] 5.4 Implement structured execution failure events for timeout, invalid output, denied mutation, and provider error; verify no failure path fabricates a verdict.

## 6. Provider and credential configuration

- [x] 6.1 Define Pydantic configuration schemas for policies, logical roles, provider bindings, transport settings, and non-secret credential references. Add tests that reject literal secret values in configuration.
- [x] 6.2 Implement CLI-first credential resolution and lifecycle commands (`auth set`, `auth status`, `auth remove`) using the OS keychain for interactive API-key profiles, standard workload chains for IAM, process environment and Git-ignored dotenv files for direct/headless runs, and optional explicit secret-manager references. Define environment-over-dotenv precedence. Test that no secret is accepted through CLI arguments or written to config/artifacts/logs, and that tracked or unsafe dotenv files are rejected.
- [x] 6.3 Implement config loading and startup validation that resolves role-to-binding mappings without resolving secret values. Add tests for missing role bindings, invalid provider fields, and manifest-safe provider metadata.
- [x] 6.4 Implement secret-redaction utilities and tests covering API keys, bearer tokens, authorization headers, AWS secret keys, OAuth tokens, and endpoint query credentials.
- [x] 6.5 Implement AWS Bedrock IAM credential-chain and Bedrock OpenAI-compatible API-key transports behind the provider adapter interface. Cover region selection, unavailable credentials, and transport selection with mocked clients.
- [x] 6.6 Implement and test native OpenAI, Anthropic, xAI, Gemini, and Azure AI Foundry API-key bindings plus the generic OpenAI-compatible HTTPS binding. Validate provider-specific endpoint/deployment requirements without provider network calls.
- [x] 6.7 Define the optional OAuth adapter contract and implement only provider integrations that expose a supported client/session or documented credential helper. Add tests proving unsupported/missing OAuth sessions fail without token scraping or artifact persistence.

## 7. Orchestration adapters and CLI

- [x] 7.1 Implement an orchestration service that executes the selected plan using only the provider-neutral reviewer interface and persists each phase transition. Verify end-to-end behavior with deterministic fixture reviewers.
- [x] 7.2 Add the CrewAI Flow adapter in `src/review_fabric/reviewers/crewai.py`, isolated from domain modules. Verify core tests run without CrewAI installed or configured.
- [x] 7.3 Add a CLI command that accepts a repository path and explicit base/head revisions, validates inputs, creates artifacts, and exits nonzero on invalid package construction. Cover with CLI tests.
- [x] 7.4 Add a CLI command to regenerate a stored review summary and verify output matches the original generated summary.

## 8. Evaluation fixtures and operational documentation

- [x] 8.1 Add curated, non-sensitive Git fixture repositories representing correct changes, a demonstrated defect, a duplicate finding, and an unresolved architectural choice.
- [x] 8.2 Add end-to-end tests asserting each fixture's expected terminal decision and artifact contents using fake reviewers.
- [x] 8.3 Add `README.md` documenting the protocol, risk tiers, read-only boundary, local artifact retention, CrewAI adapter boundary, provider/credential configuration, and explicit MVP non-goals.
- [x] 8.4 Add a contributor guide describing the OpenSpec workflow: propose change, create/modify specs, implement tasks, validate with `npx openspec validate --strict`.
- [x] 8.5 Run `.venv/bin/python -m pytest`, `.venv/bin/ruff check .`, and `npx --yes @fission-ai/openspec@1.6.0 validate establish-review-protocol --strict`; fix all failures before the first implementation commit.
