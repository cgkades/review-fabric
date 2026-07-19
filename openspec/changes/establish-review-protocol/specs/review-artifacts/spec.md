## ADDED Requirements

### Requirement: Replayable local review records
The system SHALL persist each review run under `.review-fabric/reviews/<review-id>/` in machine-readable records and a generated human-readable summary. Persisted records MUST include the package manifest, raw valid reviewer outputs, normalized finding groups, disputes, decisions, execution errors, and verification records when present.

#### Scenario: Completed review has a complete artifact set
- **WHEN** a review reaches a terminal outcome
- **THEN** its artifact directory contains a manifest and enough records to reconstruct the selected reviewers, inputs, evidence, decisions, and terminal status without querying a provider

### Requirement: Append-only phase history
The system SHALL preserve phase records as append-only events with timestamps, schema version, review identifier, and phase name. The generated summary MUST be derivable solely from persisted machine-readable records.

#### Scenario: Summary can be regenerated
- **WHEN** the human-readable summary is deleted while event records remain valid
- **THEN** the system can regenerate an equivalent summary from the event records

### Requirement: Failure capture
The system SHALL record reviewer timeout, schema-validation failure, tool denial, and orchestration error as structured events without fabricating a review conclusion.

#### Scenario: Provider failure does not produce a verdict
- **WHEN** a selected reviewer invocation fails before returning valid output
- **THEN** the artifact records the failure and the coordinator either continues according to policy or emits an explicit incomplete/escalated result