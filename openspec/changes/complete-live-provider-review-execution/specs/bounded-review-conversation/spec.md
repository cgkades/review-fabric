## ADDED Requirements

### Requirement: Evidence-limited reviewer challenge
The system SHALL keep first-pass reviewers isolated and SHALL permit at most one targeted challenge and response for a material evidence-backed finding.

#### Scenario: Material finding receives challenge
- **WHEN** normalization identifies a material finding eligible for challenge
- **THEN** the system SHALL persist the challenge, reviewer response, and evidence-based coordinator decision

#### Scenario: Challenge limit is exhausted
- **WHEN** the configured challenge limit has been reached
- **THEN** the system SHALL route unresolved material findings to ESCALATE rather than start another round