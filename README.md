# Review Fabric

Review Fabric is an evidence-driven, local code-review protocol. It captures an immutable Git comparison, selects provider-neutral logical reviewer roles by risk, and stores replayable records under the reviewed repository's `.review-fabric/` directory.

## Safety and scope

The MVP is read-only with respect to reviewed source: it never edits source, commits, pushes, merges, or posts provider/GitHub feedback. The only product-created files are local `.review-fabric` artifacts. Artifacts are append-only machine records plus a summary regenerated solely from those records. Secret values are redacted and are never retained in configuration, artifacts, summaries, or error records.

Low-risk changes use at most one reviewer and no challenge. Authorization, destructive-data, retry/idempotency, concurrency, migration, and infrastructure indicators select specialist roles and permit one evidence-limited challenge round. Missing reviewers, invalid output, timeouts, or provider errors produce explicit incomplete/escalated records—not fabricated verdicts.

## Providers and credentials

Policy chooses roles only. Versioned configuration maps roles to bindings with provider, transport, model, and named credential source metadata. Credentials are resolved only at invocation time from workload identity, a named environment variable (which overrides dotenv), a private Git-ignored dotenv file, an OS keychain profile, or a supported external adapter. `.env` files must be private and untracked. Provider clients are injected/mocked by default; no adapter contacts a provider on its own. The optional CrewAI adapter is isolated from core/domain modules.

Run a local package capture with:

```sh
review-fabric /path/to/repository BASE_SHA HEAD_SHA
```

This creates an explicit escalation if no configured reviewers are supplied. Regenerate a report with `review-fabric summary .review-fabric/reviews/REVIEW_ID`.

MVP non-goals: browser-token scraping, credential persistence in project files, automatic remediation, network publishing, and unbounded reviewer debate.
