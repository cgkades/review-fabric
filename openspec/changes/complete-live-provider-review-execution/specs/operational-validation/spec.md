## ADDED Requirements

### Requirement: Controlled live evaluation
The system SHALL provide repeatable low-cost smoke validation for configured providers before evaluating a representative repository.

#### Scenario: Light-model smoke review
- **WHEN** a configured provider is selected for smoke validation
- **THEN** the system SHALL use a small fixture, bounded output, and record cost-safe execution metadata

#### Scenario: Self-review remediation
- **WHEN** a live review identifies a reproducible defect in Review Fabric
- **THEN** the defect SHALL be tested, fixed, and re-reviewed before completion