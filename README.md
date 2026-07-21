# Review Fabric

Review Fabric is an evidence-driven, local code-review protocol. It captures an immutable Git comparison, selects provider-neutral logical reviewer roles by risk, and stores replayable records under the reviewed repository's private `.review-fabric/` directory.

## Safety and scope

The MVP is read-only with respect to reviewed source: it never edits source, commits, pushes, merges, or posts provider/GitHub feedback. The only product-created files are local `.review-fabric` artifacts. Artifacts are append-only machine records plus a summary regenerated solely from those records. Secret values are redacted and are never retained in configuration, artifacts, summaries, or error records.

Low-risk changes use at most one reviewer and no challenge. Authorization, destructive-data, retry/idempotency, concurrency, migration, and infrastructure indicators select specialist roles and permit one evidence-limited challenge round. Missing reviewers, invalid output, timeouts, or provider errors produce explicit incomplete/escalated records—not fabricated verdicts.

## Providers and credentials

Policy chooses roles only. Versioned configuration maps roles to bindings with provider, transport, model, and named credential source metadata. Credentials are resolved only at invocation time from workload identity, a named environment variable (which overrides dotenv), a private Git-ignored dotenv file, or an OS keychain profile. `.env` files must be private and untracked. Use `review-fabric auth set bedrock --profile us-west-2` to store an interactively entered token in the `review-fabric` keychain service, then configure `credential_source: "keychain"` and `credential_ref: "bedrock:us-west-2"`. Provider clients are activated only by an explicit `--config`; no provider contact occurs by default.

Run a local package capture with:

```sh
review-fabric /path/to/repository BASE_SHA HEAD_SHA
```

`--pr` is an equivalent, optional single-token form of the same bounded diff mode —
`review-fabric --pr BASE_SHA..HEAD_SHA /path/to/repository` — for callers that prefer
one revision-range argument over two positionals; it changes nothing about the
review itself, and the two forms must not both be given. This creates an explicit
escalation if no configured reviewers are supplied. Regenerate a report with
`review-fabric summary .review-fabric/reviews/REVIEW_ID`.

To review an entire tracked codebase instead of a bounded diff (e.g. no meaningful
base commit exists, or you want every file assessed rather than just a change), use
`--full`:

```sh
review-fabric --full /path/to/repository [--revision HEAD]
```

`--full` diffs the whole tree against the well-known empty Git tree object — every
tracked file is treated as newly added — and never accepts `base`/`head` positional
arguments (use `--revision` to pick a commit other than `HEAD`). Because a whole
codebase almost always exceeds the bounded per-review patch size, `--full` splits the
evidence into file-aligned chunks and runs one independent, independently-replayable
review per chunk, printing one artifact directory per chunk. Cross-file interaction
awareness is limited to whichever files land in the same chunk — there is no way to
give a reviewer truly whole-repository-at-once context without an unbounded prompt.
A single file whose own diff alone still exceeds the byte cap is never silently
dropped or truncated: it is skipped, clearly reported on stdout/stderr with its exact
path, and the command exits non-zero — every other chunk is still reviewed. Raise the
per-chunk (or per-PR) cap with `--max-patch-bytes` if this happens (e.g. for a large
generated file such as a lockfile) or review that file separately.

MVP non-goals: browser-token scraping, credential persistence in project files, automatic remediation, network publishing, and unbounded reviewer debate.

## Explicit live-provider configuration

Live execution is opt-in: supply a secret-free JSON binding file outside the reviewed repository with `--config`; see
`examples/light-model.review-fabric.json`. The Gemini Developer API and OpenAI-compatible
(including xAI-compatible) transports use a stdlib HTTP client with a 60-second timeout and
64 KiB response cap. Bedrock OpenAI-compatible transports support GPT-OSS; native Bedrock
Converse supports Anthropic inference profiles, including `us.anthropic.claude-sonnet-5` and
`us.anthropic.claude-haiku-4-5-20251001-v1:0`, using an explicit region and a runtime bearer
credential. Converse requests disable Claude thinking so the bounded response budget remains
available for the required structured JSON. Responses may contain one enclosing `json` Markdown
fence, which is removed before otherwise strict JSON/schema/citation validation. Responses must
be a JSON `{"findings": [...]}` object and are validated as findings before recording. Bedrock
IAM, native SDK, and OAuth paths safely escalate until a documented adapter is implemented.

```sh
review-fabric --config /private/path/review-fabric.json /path/to/repository BASE_SHA HEAD_SHA
```

An optional dotenv file must be inside the repository, untracked, and mode `0600`:
`review-fabric --config … --env-file .private.env …`. Neither command prints or persists a
credential. `scripts/live_smoke.py` requires an explicit `--config` and is intentionally not
run by tests or CI.
