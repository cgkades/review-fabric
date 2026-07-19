# Design

## Decisions
- Configuration is JSON and contains references only; credentials resolve at invocation and are never serialized.
- A reviewer receives a frozen package plus its own rubric. Provider calls use explicit timeouts and one bounded response size.
- First-pass output remains isolated. Only material evidence-backed findings enter one targeted challenge. The coordinator receives structured records, not hidden reasoning.
- Live transport is opt-in through `--config`; the default remains a safe escalation.

## Non-goals
- Autonomous source edits, remote GitHub writes, provider token scraping, persistent chat sessions, unbounded debate, or provider calls without explicit configuration.

## Validation
Use mocked HTTP tests for every request/error path before a tiny real light-model smoke test. Redact all failures and record only safe request metadata.