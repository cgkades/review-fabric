## ADDED Requirements

### Requirement: Risk-tiered review plan
The system SHALL derive a review plan from explicit policy inputs including changed paths, declared risk indicators, and configured limits. The plan MUST specify selected reviewer roles, whether challenge is allowed, maximum challenge rounds, and terminal behavior for missing reviewers.

#### Scenario: Low-risk review uses minimal depth
- **WHEN** a review is classified as low risk by policy and contains no declared high-risk path or operation
- **THEN** the plan selects no more than one first-pass reviewer and disables challenge

#### Scenario: High-risk review enables evidence challenge
- **WHEN** a review includes configured high-risk indicators such as authorization, destructive data handling, retry/idempotency, concurrency, migration, or infrastructure changes
- **THEN** the plan selects at least two relevant specialist roles and permits one challenge round

### Requirement: Deterministic phase limits
The coordinator SHALL enforce configured reviewer-count, timeout, retry, and challenge-round limits. It MUST terminate or escalate rather than starting unbounded review or debate work.

#### Scenario: Challenge limit prevents endless debate
- **WHEN** a dispute remains unresolved after its permitted challenge round
- **THEN** the coordinator emits `ESCALATE` and does not start another challenge round

### Requirement: Provider-neutral reviewer selection
Review policy SHALL select logical roles rather than provider-specific implementations. The selected role MUST be bound to a configured reviewer adapter at execution time.

#### Scenario: Same policy can use a fake adapter
- **WHEN** fixture tests execute a review plan
- **THEN** the plan can bind a deterministic fake reviewer without changing the policy or domain schemas