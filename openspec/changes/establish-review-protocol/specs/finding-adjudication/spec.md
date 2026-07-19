## ADDED Requirements

### Requirement: Deterministic normalization
The system SHALL assign canonical IDs to admitted findings, group likely duplicates using deterministic rules, and preserve every contributing finding and evidence reference. Normalization MUST NOT discard a finding solely because a numerical majority disagrees with it.

#### Scenario: Duplicate observations are grouped
- **WHEN** two reviewers identify the same defect at the same code location with compatible claims
- **THEN** the system emits one finding group that references both original findings

#### Scenario: Supported minority finding is retained
- **WHEN** one reviewer supplies a precise reproduction for a defect and other reviewers report no issue
- **THEN** the system retains the supported finding for adjudication

### Requirement: Bounded dispute routing
The system SHALL create a dispute only for a material finding that is contradicted by credible evidence, has incomplete material evidence, or crosses the configured policy threshold. A dispute MUST pose one explicit question, list competing claims, and enumerate the evidence needed to resolve it. The MVP MUST permit no more than one challenge round per dispute.

#### Scenario: Conflicting material claims open a dispute
- **WHEN** one material finding claims a retry can duplicate a write and another cites a uniqueness constraint as a counterclaim
- **THEN** the system creates one dispute that requests the relevant constraint scope and timeout-after-commit evidence

#### Scenario: Routine suggestion does not open a dispute
- **WHEN** a low-severity maintainability suggestion has no competing evidence
- **THEN** the system does not invoke a challenge reviewer

### Requirement: Evidence-limited challenge
Challenge participants SHALL respond only to the dispute question and MUST add a code citation, executable test or reproduction, captured command output, contract citation, or explicit assumption with impact. A response that adds none of these SHALL not resolve the dispute.

#### Scenario: Unsupported challenge response is insufficient
- **WHEN** a challenge response only states that another reviewer is overly cautious
- **THEN** the system records the response but leaves the dispute unresolved

### Requirement: Bounded adjudication outcomes
The adjudicator SHALL emit exactly one of `ACCEPT`, `CHANGE`, or `ESCALATE` for each material finding group or dispute. A `CHANGE` decision MUST include accepted evidence, bounded remediation, and verification criteria. An `ESCALATE` decision MUST state the unresolved question and the human decision required.

#### Scenario: Demonstrated defect requires a change
- **WHEN** accepted evidence demonstrates a reachable correctness or security defect
- **THEN** adjudication emits `CHANGE` with a specific remediation and regression verification condition

#### Scenario: Architectural choice is escalated
- **WHEN** evidence cannot decide between valid alternatives with different product or operational trade-offs
- **THEN** adjudication emits `ESCALATE` rather than inventing a compromise