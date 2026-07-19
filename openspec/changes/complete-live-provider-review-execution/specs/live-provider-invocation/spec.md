## ADDED Requirements

### Requirement: Explicit live provider invocation
The CLI SHALL invoke a provider only when supplied an explicit secret-free configuration binding and SHALL resolve credentials only at invocation time.

#### Scenario: Configured light-model reviewer
- **WHEN** a valid config binds a selected role to a provider and its named credential is available
- **THEN** the system SHALL make one bounded request and persist only safe metadata and structured output

#### Scenario: Missing credential or malformed response
- **WHEN** a required credential is unavailable or provider output cannot validate as findings
- **THEN** the system SHALL record a redacted escalation and SHALL NOT fabricate an ACCEPT outcome