## Why

Code-review agents are most useful when their conclusions are independently derived, evidence-backed, and reproducible. A conventional multi-agent chat produces anchoring, duplicate work, and consensus without proof; Review Fabric needs a bounded protocol that turns an immutable code-review input into a durable, auditable decision.

## What Changes

- Define the MVP review protocol: immutable review packages, independent specialist reviews, deterministic finding normalization, conditional disputes, and bounded adjudication.
- Define structured, provider-neutral artifacts and required evidence for every material finding.
- Define deterministic routing and stop conditions so the coordinator does not become an unbounded agent conversation.
- Define a local CLI-first artifact layout and fixture-driven validation strategy.
- Define provider-neutral model and credential configuration: native API providers, OpenAI-compatible endpoints, AWS Bedrock through IAM credentials and through Bedrock API keys, and optional OAuth-backed coding-provider adapters.
- Use CrewAI Flows only as an orchestration adapter; the review protocol and persisted records remain framework-neutral Python domain objects.

## Capabilities

### New Capabilities
- `review-package`: Freeze and validate the code, context, constraints, and tool evidence supplied to a review run.
- `independent-review`: Run isolated specialist reviews and collect structured findings without exposing peer conclusions.
- `finding-adjudication`: Normalize findings, route only material conflicts to a bounded challenge phase, and emit evidence-based decisions.
- `review-artifacts`: Persist review inputs, findings, disputes, decisions, and verification records in a replayable local format.
- `review-policy`: Classify review risk and deterministically select reviewers, phases, and stop conditions.
- `provider-configuration`: Bind logical reviewer roles to configured models and provider adapters while resolving credentials without persisting secrets in review artifacts.

### Modified Capabilities

- None.

## Impact

The initial implementation will be a Python CLI and library. It will introduce Pydantic schemas, a provider-neutral reviewer interface, a CrewAI Flow adapter, provider/credential configuration, local filesystem artifacts, Git evidence collection, and fixture-based tests. It will not write to GitHub, modify reviewed code, merge pull requests, or require a persistent service. Credentials will be supplied by environment variables, standard provider credential chains, or external OAuth token stores; they will not be committed to the repository or persisted in review artifacts.
