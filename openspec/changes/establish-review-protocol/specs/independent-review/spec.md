## ADDED Requirements

### Requirement: Isolated specialist first pass
The system SHALL invoke each selected specialist reviewer with the same immutable review package and that specialist's role rubric. Before all first-pass results are complete, a reviewer MUST NOT receive another reviewer's finding, challenge response, or adjudication.

#### Scenario: Peer findings are withheld during initial review
- **WHEN** two specialist reviewers are selected for a review run
- **THEN** each receives the package and its own rubric without the other specialist's output

### Requirement: Structured findings
A specialist reviewer SHALL return zero or more findings. Each material finding MUST include a stable local identifier, severity, claim, at least one precise evidence reference, recommended action, verification proposal, and confidence. Evidence references MUST identify a source type and a location or command output reference.

#### Scenario: Complete finding is admitted
- **WHEN** a reviewer returns a blocker finding with a code location, claim, remediation, and regression-test proposal
- **THEN** the system validates and admits the finding for normalization

#### Scenario: Unsupported material finding is rejected
- **WHEN** a reviewer returns a blocker or concern without precise evidence or a verification proposal
- **THEN** the system marks the finding invalid and excludes it from adjudication

### Requirement: Read-only review execution
Review execution SHALL be read-only with respect to the reviewed repository. Reviewer tools MUST NOT modify source files, create commits, push branches, merge pull requests, or publish GitHub feedback in the MVP.

#### Scenario: Reviewer attempts a prohibited mutation
- **WHEN** a reviewer requests a source-control or filesystem mutation in the reviewed repository
- **THEN** the execution adapter denies the operation and records the denial in the review artifact