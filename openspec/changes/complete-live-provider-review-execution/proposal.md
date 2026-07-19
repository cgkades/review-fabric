# Complete live provider review execution

## Why
The existing MVP validates protocol schemas and deterministic fake-review execution, but the CLI cannot invoke configured providers. A successful local capture therefore ends in `ESCALATE` for missing reviewers rather than producing a real bounded review.

## What Changes
- Load versioned, secret-free provider configuration through the CLI.
- Add opt-in, bounded live transports and structured reviewer output parsing.
- Connect first pass, targeted challenge, reviewer response, and coordinator adjudication.
- Validate the complete path against low-cost real-provider smoke fixtures and representative repositories.

## Capabilities
- `live-provider-invocation`
- `bounded-review-conversation`
- `operational-validation`

## Impact
Adds outbound provider calls only when an explicit binding/configuration is supplied. No source/Git/GitHub mutation is added.