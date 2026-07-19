## Context

Review Fabric starts as a local, read-only code-review system. It must improve on a single generalist review without turning a review into an unbounded discussion among correlated LLMs. The first implementation has no existing application code, remote service, or GitHub mutation path.

The implementation must support multiple models/providers eventually, but must not make provider-specific task or memory state the system of record. The initial orchestration experiment will use CrewAI because Flows provide stateful, event-driven execution and parallel task coordination in Python.

## Goals / Non-Goals

**Goals:**

- Produce reproducible review records from a frozen Git revision pair and declared context.
- Preserve independence in the first review round.
- Make deterministic code decide routing, deduplication, evidence completeness, and phase limits.
- Permit LLM judgment only where interpretation is required: review, targeted challenge, and adjudication.
- Persist all phase inputs and outputs locally so a review can be inspected and replayed.
- Support risk-tiered review depth to control cost.

**Non-Goals:**

- Autonomous source changes, commits, merges, or GitHub review/comment publication.
- Persistent cross-review agent memory or open-ended reviewer chat.
- A web UI, queue, server, Unix socket protocol, multi-host execution, or authentication system.
- A claim that an LLM majority vote establishes correctness.
- Full repository indexing or semantic code search beyond the explicit review package in the MVP.

## Decisions

### 1. The domain protocol is provider- and framework-neutral

`review_fabric.domain` will contain Pydantic models and pure functions. It will not import CrewAI or an LLM SDK. Core records include `ReviewPackage`, `Finding`, `Evidence`, `Dispute`, `Decision`, `ReviewPolicy`, and `VerificationRecord`.

**Rationale:** the durable product is the evidence protocol, not an orchestration framework. This permits direct unit tests, replay, and future adapters.

**Alternative considered:** use CrewAI task outputs and memories as the review state. Rejected because output shapes and execution semantics become framework-coupled and difficult to replay.

### 2. A review package is immutable after creation

The package records base SHA, head SHA, patch content or digest, selected repository paths, acceptance criteria, constraints, commands executed, and their captured outputs. Downstream phases reference its immutable `review_id` and input digest.

**Rationale:** reviewers must reason about the same evidence. This prevents silent context drift and makes a verdict auditable.

**Alternative considered:** let each reviewer inspect the live working tree. Rejected for the MVP because local changes and concurrent edits invalidate comparisons.

### 3. Independent review is a fan-out phase

The coordinator selects reviewer roles from the risk policy, sends each the same review package plus only its role rubric, and does not reveal peer findings. Every reviewer returns a structured `Finding[]` list.

**Rationale:** independent first passes preserve diverse hypotheses and prevent anchoring.

**Alternative considered:** one hierarchical CrewAI manager delegates and shares a conversation. Rejected because it encourages agreement theater and makes execution paths nondeterministic.

### 4. Finding admission and routing are deterministic

A finding is admitted only if its severity, claim, evidence, recommended action, and verification proposal validate against schema rules. Pure Python then assigns stable IDs, groups likely duplicates, applies risk thresholds, and opens disputes only for material conflicts or incomplete high-severity claims.

**Rationale:** an LLM should not decide whether its own output meets required evidence shape or how many rounds it receives.

**Alternative considered:** an LLM coordinator handles all triage. Rejected because routing behavior would be unstable and harder to tune empirically.

### 5. Challenges are narrow and bounded

A dispute is a single question created from conflicting or insufficient material findings. A challenge participant may only provide new code, test/reproduction, command-output, or contract evidence. The MVP permits one challenge round. Unresolved conflict becomes `ESCALATE`.

**Rationale:** targeted contradiction testing is useful; full review-to-review debate is usually token-expensive and weakly grounded.

**Alternative considered:** paired reviewers critique every peer review. Rejected because it duplicates the whole evidence package and favors prose over code evidence.

### 6. Adjudication has only three outcomes

The adjudicator outputs `ACCEPT`, `CHANGE`, or `ESCALATE`. A `CHANGE` decision must include the accepted evidence, a bounded required remediation, and a verification condition. A decision cannot invent an unsupported compromise.

**Rationale:** these outcomes distinguish absence of a demonstrated issue, a required engineering action, and a human/product judgment.

### 7. Artifacts are local JSONL plus human-readable Markdown

Each review is written under `.review-fabric/reviews/<review-id>/`. JSON records are machine-readable; `summary.md` is generated from them. Atomic file replacement is used for single-record writes; append-only JSONL is used for event streams.

**Rationale:** local artifacts are simple, diffable, portable, and work without service infrastructure.

**Alternative considered:** database or socket-first state. Rejected until concurrent remote execution is a real need.

### 8. CrewAI is an adapter behind a reviewer interface

A `Reviewer` protocol accepts a `ReviewPackage`, role rubric, and phase input and returns validated domain objects. The initial `CrewAIReviewer` uses a Flow for parallel independent tasks and conditional challenge/adjudication tasks. A deterministic fake reviewer supports tests.

**Rationale:** CrewAI provides fast iteration for orchestration while remaining replaceable.

### 9. Provider configuration separates logical roles, transport, and credentials

A versioned local configuration file maps logical reviewer roles to a named model binding. A binding declares an adapter/transport, model identifier, endpoint settings where applicable, and a credential reference; it never carries a literal secret. The initial provider capability MUST support:

- AWS Bedrock via the standard AWS credential chain (IAM role, profile, access keys, or SSO) and configurable region;
- AWS Bedrock API-key/bearer-token access through its OpenAI-compatible endpoint;
- native OpenAI, Anthropic, xAI, Google Gemini, and Azure AI Foundry API-key adapters;
- a generic OpenAI-compatible endpoint with configurable base URL, API key reference, and model name;
- optional OAuth adapters that obtain or delegate to an already authenticated local ChatGPT/Codex, Gemini, Claude Code, or other provider-supported client session.

OAuth support is explicitly optional because every provider has different terms, token formats, refresh behavior, and client interfaces. It MUST use an external token store or a provider's supported local client/session; Review Fabric MUST NOT scrape browser cookies, copy opaque credentials into config, or emulate an unsupported OAuth flow.

**Rationale:** role selection is a review-policy decision; model/provider binding is deployment configuration; credential resolution is a security boundary. Keeping these separate allows a team to swap models without changing policy and keeps secrets out of Git and review artifacts.

**Alternative considered:** store API keys and provider details inline in each reviewer definition. Rejected because configuration becomes unsafe to commit and role policy becomes coupled to deployment credentials.

### 10. Configuration validates before any source is sent to a provider

At startup, configuration loading resolves only non-secret references and validates that each policy-selected role has a usable binding. Credential resolution happens immediately before invocation through the configured provider's standard mechanism. The system records provider name, transport type, configured model identifier, and credential *source type* (for example `environment`, `aws-default-chain`, or `external-oauth-session`) in the manifest, but never a secret value, token, authorization header, or endpoint query credential.

**Rationale:** fail fast on bad role bindings while minimizing secret lifetime and preventing accidental artifact disclosure.

## Risks / Trade-offs

- **[Correlated model errors]** → Use role-specific rubrics, independent prompts, optional provider diversity, and evidence-weighted adjudication rather than voting.
- **[Token/cost growth with diff size]** → Risk policy limits reviewer count and challenge rounds; package construction selects relevant context and records token estimates.
- **[False precision from schema-valid findings]** → Require line-level/code-path evidence and a reproduction or explicit verification condition for material claims.
- **[CrewAI API churn]** → Keep all CrewAI imports isolated in one adapter module and test core workflow with fake reviewers.
- **[Sensitive source/test output in artifacts]** → MVP stores artifacts locally, documents retention, and does not transmit data except to explicitly configured model providers.
- **[No real defect benchmark]** → Start with curated fixture repositories and label outcomes; do not claim quality improvement without measured results.
- **[Credential leakage]** → Prohibit literal secrets in configuration; redact logs and artifacts; persist only credential-source type and non-sensitive model/provider metadata.
- **[OAuth fragility or provider policy changes]** → Treat OAuth as optional adapters over supported local clients/token stores; maintain API-key and IAM paths as first-class, independently testable transports.
- **[Provider feature skew]** → Validate capabilities at binding load time and surface unsupported model/tool/structured-output combinations before a review starts.

## Migration Plan

1. Add the Python package, schemas, fake reviewer, and local artifact store.
2. Implement package construction from a local Git repository and fixture tests.
3. Implement policy, normalization, and decision state machine with deterministic tests.
4. Add the CrewAI adapter behind the reviewer protocol and run manually against non-sensitive fixture diffs.
5. Add measurement records for cost, finding disposition, and later-confirmed defects.

Rollback is deleting the local package/configuration; no remote systems or source repositories are changed by a review run.

## Open Questions

- Which model providers will be permitted for source transmission, and what redaction policy is needed?
- Which initial reviewer roles provide enough diversity to justify their cost: correctness/reliability, security, tests/compatibility, or production/SRE?
- What fixture corpus and labeling process will establish reviewer precision and recall?
- Should the first CLI accept a Git range only, a patch file only, or both?
- Does CrewAI offer sufficient cancellation, timeout, and tracing controls for the desired local CLI behavior?
- Which OAuth provider/client integrations expose a supported, stable local invocation path suitable for an optional adapter?
- Which credential-source abstractions should be included in MVP: environment variables, AWS default credential chain, OS keychain, external command, and/or secret-manager references?
