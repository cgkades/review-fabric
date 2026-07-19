## ADDED Requirements

### Requirement: Immutable review package
The system SHALL construct a review package from an explicitly selected local Git base revision and head revision. The package MUST record the repository root, base SHA, head SHA, patch digest, selected context paths, acceptance criteria, declared constraints, and captured automated-check results. After construction, downstream phases MUST receive the package by identifier and MUST NOT mutate it.

#### Scenario: Package pins a Git comparison
- **WHEN** a caller requests a review for a valid base SHA and head SHA
- **THEN** the system writes a package whose base and head resolve to those exact commits and whose patch digest matches the captured diff

#### Scenario: Invalid comparison is rejected
- **WHEN** either requested Git revision cannot be resolved in the repository
- **THEN** the system fails before invoking a reviewer and records a validation error

### Requirement: Package input integrity
The system SHALL calculate a deterministic identifier from the review package's canonical content. Every finding, dispute, decision, and verification record MUST reference that identifier.

#### Scenario: Downstream record references its package
- **WHEN** a reviewer returns a finding for a constructed package
- **THEN** the persisted finding contains the package identifier used by the reviewer

#### Scenario: Changed package content creates a new identity
- **WHEN** a caller changes a package field that affects review evidence or constraints
- **THEN** the system calculates a different package identifier and does not append records to the prior review run