## ADDED Requirements

### Requirement: Logical-role model bindings
The system SHALL load a versioned configuration that maps each logical reviewer role and adjudicator role to a named provider binding. A provider binding MUST declare a transport type, model identifier, and credential reference. Review policy MUST refer only to logical roles and MUST NOT embed provider names, model identifiers, endpoint URLs, or secret references.

#### Scenario: Policy selects a role mapped to a model
- **WHEN** a high-risk policy selects the `security` role and configuration maps `security` to a named provider binding
- **THEN** the execution plan records the logical role and resolved non-secret binding metadata before invocation

#### Scenario: Role has no usable binding
- **WHEN** the selected policy includes a role absent from configuration or bound to an invalid provider configuration
- **THEN** the review fails before any source content is sent to a model provider

### Requirement: Secret-free configuration and artifact redaction
Configuration files, review artifacts, logs, exception messages, and generated reports MUST NOT contain literal API keys, bearer tokens, OAuth refresh tokens, authorization headers, AWS secret access keys, or session cookies. Provider bindings MAY reference a credential by environment-variable name, standard credential-chain name, OS-keychain identifier, secret-manager reference, external command reference, or external OAuth-session identifier.

#### Scenario: API-key binding uses an environment reference
- **WHEN** a provider binding configures native OpenAI access with `credential_source: environment` and `credential_ref: OPENAI_API_KEY`
- **THEN** the system resolves the value at invocation time and persists only `environment` and `OPENAI_API_KEY` as non-secret metadata

#### Scenario: Error output is redacted
- **WHEN** a provider request fails after a credential is resolved
- **THEN** the stored failure record contains no credential value, authorization header, or URL query secret

### Requirement: CLI-first credential lifecycle and resolution order
The system SHALL manage credentials independently of Hermes or any chat-agent runtime. The default credential resolution mechanisms SHALL be:

1. a provider's standard workload/identity chain where one exists (for example AWS's default credential chain for Bedrock IAM);
2. a named operating-system keychain entry for interactive local use;
3. an explicitly named environment variable for CI, containers, or headless execution;
4. an explicit, non-interactive secret-manager reference where a supported adapter is installed; or
5. a supported external OAuth-client/session adapter.

The CLI SHALL provide `auth set`, `auth status`, and `auth remove` commands for keychain-backed API-key profiles. `auth set` MUST read a value from a secure terminal prompt or standard input, MUST NOT accept a secret as a command-line argument, and MUST write only to the OS keychain. The CLI SHALL support dotenv files for direct developer execution: by default it MAY load a repository-root `.env` file and it MUST support an explicit `--env-file` path. Process environment values MUST take precedence over dotenv values. The repository `.gitignore` MUST ignore `.env` and `.env.*` while allowing `.env.example`; runtime validation MUST warn or fail if a selected dotenv file is tracked by Git, has unsafe ownership/permissions, or would be persisted as an artifact. The CLI MUST NOT copy dotenv values into the process environment for child processes unless that child is the configured provider adapter.

#### Scenario: Interactive user stores an API key
- **WHEN** a user runs `review-fabric auth set openai --profile work`
- **THEN** the CLI stores the key in the OS keychain under a named non-secret profile and writes no key value to configuration, shell history, review artifacts, or logs

#### Scenario: CI uses an environment reference
- **WHEN** a binding references `env:OPENAI_API_KEY` and the process environment provides that value
- **THEN** the process uses it only for the provider request and records only the environment-variable name as credential metadata

#### Scenario: Repository dotenv supports direct developer execution
- **WHEN** a direct CLI run finds a Git-ignored, user-owned repository `.env` containing a configured credential variable and the process environment does not define that variable
- **THEN** the resolver may use the dotenv value for the configured provider request without persisting it or exposing it in logs, reports, child environments, or artifacts

#### Scenario: Process environment overrides dotenv
- **WHEN** both the process environment and selected dotenv file provide a value for the same configured credential variable
- **THEN** the resolver uses the process-environment value

#### Scenario: Unsupported or unavailable OS credential store
- **WHEN** a keychain credential source is selected but macOS Keychain, Linux Secret Service, or Windows Credential Manager is unavailable or locked
- **THEN** the CLI reports the unavailable store and suggests a named environment, dotenv, workload-identity, or supported external credential source without falling back to an insecure file backend

#### Scenario: Direct execution has no available credential
- **WHEN** a selected provider binding has no resolvable named keychain entry, workload identity, environment reference, secret-manager reference, or supported OAuth session
- **THEN** the CLI exits before transmitting review source and reports the required non-secret setup action

### Requirement: AWS Bedrock IAM transport
The system SHALL support AWS Bedrock model invocation through the standard AWS credential provider chain, including IAM roles, named profiles, environment credentials, and AWS SSO. A Bedrock IAM binding MUST declare an AWS region and model identifier and MUST NOT require static credentials in the Review Fabric configuration file.

#### Scenario: Bedrock role credentials are discovered
- **WHEN** a binding uses the `bedrock-iam` transport and valid AWS role or profile credentials are available through the configured credential chain
- **THEN** the system invokes the configured Bedrock model in the configured region without reading an AWS secret from project configuration

#### Scenario: Bedrock IAM credentials are unavailable
- **WHEN** a binding uses the `bedrock-iam` transport but no usable AWS credentials can be resolved
- **THEN** the system reports a credential-resolution failure before starting the reviewer task

### Requirement: AWS Bedrock API-key transport
The system SHALL support AWS Bedrock API-key or bearer-token access through the documented OpenAI-compatible Bedrock endpoint. A Bedrock API-key binding MUST require an endpoint base URL or region-derived endpoint, model identifier, and secret reference; it MUST NOT be routed through the IAM transport.

#### Scenario: Bedrock API-key binding uses compatible transport
- **WHEN** a binding selects `bedrock-openai-compatible` with a bearer-token secret reference
- **THEN** the system sends requests through the configured OpenAI-compatible Bedrock endpoint rather than attempting AWS IAM credential resolution

### Requirement: Native API-key provider transports
The system SHALL support native API-key bindings for OpenAI, Anthropic, xAI, Google Gemini, and Azure AI Foundry. A native binding MUST validate provider-specific required non-secret settings before invocation, including model identifier and, where required, Azure endpoint/deployment or Google project/location settings.

#### Scenario: Distinct roles use distinct native providers
- **WHEN** configuration maps `correctness` to OpenAI and `security` to Anthropic
- **THEN** the coordinator invokes each role through its configured provider transport without changing review-policy role selection

#### Scenario: Azure binding omits deployment configuration
- **WHEN** an Azure AI Foundry binding lacks a required endpoint or deployment/model mapping
- **THEN** configuration validation rejects the binding before review execution

### Requirement: Generic OpenAI-compatible transport
The system SHALL support a generic OpenAI-compatible provider binding with configurable HTTPS base URL, model identifier, credential reference, and optional organization/project headers. The system MUST validate that the base URL uses HTTPS unless an explicit local-development override is enabled.

#### Scenario: Compatible gateway is configured
- **WHEN** a role binding selects `openai-compatible` with an HTTPS base URL and API-key environment reference
- **THEN** the role can be invoked without adding a provider-specific adapter to review policy

#### Scenario: Insecure remote endpoint is rejected
- **WHEN** a generic compatible binding specifies an HTTP endpoint that is not an explicitly allowed loopback development endpoint
- **THEN** configuration validation rejects the binding

### Requirement: Optional supported OAuth adapters
The system MAY provide optional OAuth-backed adapters for ChatGPT/Codex, Gemini, Claude Code, and other providers only when an official provider-supported client, CLI, local token store, or documented OAuth mechanism is available. OAuth adapters MUST delegate to that supported mechanism or an explicitly configured external credential helper. They MUST NOT scrape browser cookies, extract tokens from unsupported local storage, imitate undocumented OAuth exchanges, or persist OAuth secrets in Review Fabric configuration or artifacts.

#### Scenario: Supported local client session is available
- **WHEN** a configured OAuth adapter detects an authenticated, supported local provider client session
- **THEN** it may invoke that client through the configured adapter and record only the provider, adapter type, and external-session source

#### Scenario: OAuth support is unavailable
- **WHEN** a selected OAuth adapter cannot find a supported authenticated client session or credential helper
- **THEN** the system fails that binding with an actionable credential-setup error and does not fall back to credential scraping

### Requirement: Provider capability and startup validation
Before source content is transmitted, the system SHALL validate selected bindings for required configuration, supported transport, credential-source availability, and required capabilities such as structured output or configured tool support. The review manifest MUST record provider name, transport type, model identifier, configuration version, and credential-source type, but no secret value.

#### Scenario: Unsupported structured output is detected before review
- **WHEN** a selected reviewer binding cannot satisfy the structured-output requirement of the review protocol
- **THEN** startup validation rejects the run before reviewer invocation

#### Scenario: Manifest records non-secret execution identity
- **WHEN** a review starts with a valid provider binding
- **THEN** its manifest records the logical role, provider, transport, model identifier, and credential-source type without recording secret material
