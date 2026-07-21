# Review follow-up notes

This tracks decisions from the full-codebase review and reconciliation with a
parallel commit (`d99ac6a`, "fix(review): harden evidence protocol") that landed on
`origin/main` while this work was in progress. Both efforts targeted overlapping
problems (concurrency, atomic artifact creation, secret redaction) independently;
where they overlapped, the stronger/already-tested mechanism was kept rather than
both, to avoid two competing implementations of the same guarantee.

## Resolved

- **CLI exit code:** confirmed correct as-is — exit code reflects invocation success,
  not review outcome. No change made.
- **`summary.md` performance:** `record_event()` appends just the new event's
  rendered line to `summary.md` directly instead of re-reading `events.jsonl` and
  rebuilding the whole report every time. Safe without extra locking because every
  caller already holds `ArtifactStore.acquire_package_lock` for the whole run. Falls
  back to a full `regenerate_summary()` self-heal if `summary.md` is ever missing or
  unexpected.
- **Confidence threshold:** implemented with a default of **0.5**. A BLOCKER/CONCERN
  finding below `ReviewPlan.minimum_confidence` forces `ESCALATE` for human review —
  never silently dropped or auto-approved. LLM confidence scores are not reliably
  calibrated, so 0.5 (not 0.8) avoids discarding real findings a model rated
  moderately-but-not-highly confident.
- **Endpoint allowlist:** confirmed no allowlist needed — `--config` is explicit,
  operator-supplied input. No change made. (`load_configuration` also now rejects a
  config path inside the reviewed repository, from the parallel commit.)
- **Artifact retention:** confirmed out of scope — handled separately / may be moot
  given non-permanent CI runners. No change made.
- **Dead code removed:** `reviewers/providers.py`'s unused `ProviderClient`/
  `ProviderRequest`/`request_for()`/`invoke()`; `reviewers/crewai.py` (unused, no
  callers, no test coverage); `evidence/commands.py` + `ReviewPackage.command_results`
  + `domain/models.py::CommandResult` (implemented and tested, but never wired into
  `cli.run()` and never consumed by anything downstream even if it were called).
  `_REVIEW_IDENTITY_SCHEMA_VERSION` stays at 1 since removing `command_results` was
  the only identity-relevant field change and this repo's local `.review-fabric/`
  artifacts are gitignored dev fixtures, not depended on externally.

## Superseded by the parallel commit (not reapplied)

- **Concurrent-run locking:** the parallel commit's `ArtifactStore.acquire_package_lock`
  (blocking `fcntl`/`msvcrt` file lock, tested with real multiprocessing) replaced my
  own `acquire_review_lock` (non-blocking, reject-on-contention). Blocking/serializing
  is arguably better UX than immediately erroring the second invocation, and it's
  already merged and tested — I did not add a second, competing locking mechanism.
- **Atomic artifact creation:** the parallel commit's `ArtifactStore.create()` (temp
  dir + `os.rename`, `ArtifactAlreadyExistsError` on race) already solves this.
- **Secret-ingestion allowlist bypass:** the parallel commit removed the
  `_SAFE_TEST_SECRET_VALUES` allowlist from `evidence/git.py` entirely (stronger than
  my scoped-to-the-match fix) and redacts the patch at the source, before the digest
  is ever computed (`evidence/git.py::collect_git_evidence`) — a cleaner fix for the
  digest/redaction-consistency issue than my after-the-fact reconciliation in
  `artifacts.py`, and it also closes a gap I hadn't caught: the *unredacted* patch
  was previously what got sent to LLM providers, not just what got persisted.
- **Corrupt/truncated trailing `events.jsonl` line:** the parallel commit's
  `ArtifactStore.events()` now strictly validates every event's structure and raises
  a clear `InvalidReviewPackageError` on any malformed record (tested). I did not add
  self-healing/repair logic for a torn last line on top of this — it would work
  against that deliberate fail-closed design. An operator gets a clear, if manual,
  recovery path instead of a silent repair.

## Reapplied on top (independent of the parallel commit)

Redaction regex hardening (`redaction.py`), Bedrock Converse/Gemini response-parsing
edge cases, exhaustive Transport dispatch messages, `BEDROCK_CONVERSE`+`aws-chain`
rejection, `git ls-files` `--` separator fix, git subprocess timeout, concurrent
reviewer execution (the parallel commit's retry logic was sequential; wrapped it in a
`ThreadPoolExecutor`), structured logging at failure/terminal points, `isinstance`-based
challenge-capability and `DeniedMutationError` checks (the parallel commit still used
`cast()`/class-name-string checks), `InvalidConfigurationError` for config loading,
`extra="forbid"` + strict-int hardening on `Finding`/`EvidenceCitation`/`Decision`/
`ChallengeCitation`, finding-group clustering by citation overlap, `main([])`
falsy-empty-list fix, uniform argparse error prefix, bounded `keyring` dependency pin,
and versioned `review_id` identity computation.

## Follow-up round: secret-guard false positives + --pr/--full

Running review-fabric on its own diff surfaced real false positives in the secret
guard (`evidence/git.py::_reject_secret_material`), and a feature request for
reviewing an entire codebase, not just a bounded diff.

- **Secret-guard false positives fixed** (all same-value structural checks — never a
  nearby-word bypass, so this cannot reintroduce the original F002 vulnerability):
  - A strictly sequential/repeated-character value (e.g. `abcdefghijklmnopqrstuvwxyz`,
    `xxxxxxxxxxxx`, `0123456789`) is structurally impossible as a real credential.
  - An exact match against a small curated list of literal placeholder words
    (`secret`, `password`, `changeme`, etc.) — not a substring match anywhere on the
    line.
  - A short, purely-lowercase-letters-and-hyphens value (e.g. `leak`, `not-allowed`,
    `str`) — real credentials always mix case/digits/punctuation for entropy, and
    this also fixes a genuine regex flaw where a Python type annotation like
    `secret: str` (a parameter declaration, not an assignment) was mistaken for a
    real secret assignment. That specific flaw will recur constantly in any normal
    Python codebase under `--full`, not just in this repo's tests.
  - Verified: zero remaining false positives scanning this entire repository's
    tracked tree end to end.
- **`--pr`**: implemented as a pure, optional, no-op alias confirming bounded
  base/head diff mode (your call — no new capability).
- **`--full`**: implemented as a genuinely new evidence-collection mode
  (`collect_full_tree_evidence`, diffing against the well-known empty Git tree
  object) plus automatic file-aligned chunking (`split_patch_into_chunks`) so a whole
  codebase's evidence is split into independently-bounded, independently-replayable
  reviews instead of hitting the patch-size cap. Implemented your "1 and 2"
  recommendation together: `--max-patch-bytes` makes the cap configurable, and
  oversized input is auto-chunked by file rather than rejected outright. A single
  file whose own diff alone still exceeds the cap (found in the wild: this repo's
  `uv.lock`) is skipped with a clear, actionable message and a non-zero exit code —
  never silently dropped — while every other chunk still completes.
- **Known, honest limitation:** "checking interactions between files" is real but
  bounded — a reviewer only sees whichever files land together in the same chunk
  (chosen by size, not by import/call relationships). There is no way to give a
  reviewer truly whole-repository-at-once context without an unbounded prompt; I did
  not attempt call-graph-aware chunking (that would need real static analysis, not
  just prompt engineering) — flagging this now rather than overstating what v1 does.
- Found and fixed one subtle bug along the way: pydantic reruns a model's own
  `@model_validator` when an already-built instance is embedded as a field value into
  a parent model, without the original call's validation context — so a
  caller-raised `max_bytes` was being silently forgotten and re-checked against the
  conservative default the moment `FrozenPatchEvidence` was attached to a
  `ReviewPackage`. Fixed by moving the *configurable* bound check into `from_patch()`
  itself (plain Python, checked once) and keeping only a fixed, generous (64 MB)
  absolute ceiling in the model validator, which is safe to recheck unconditionally.

## Follow-up: --pr redesigned to take BASE..HEAD directly

Initial `--pr` was a pure no-op boolean flag (positional base/head did all the work).
You expected `--pr` to actually take the two revisions itself, symmetric with
`--full`/`--revision`. First attempt used `nargs=2` (`--pr BASE HEAD`) — this had a
real, confirmed argparse footgun: an optional argument with `nargs=2` greedily
consumes the next two tokens as plain strings even if one of them is another
registered flag (e.g. `--pr --full /repo` silently became `pr=['--full', '/repo']`,
`full=False`, with `repository` scrambled to the string `"base"` — no error at all).
Redesigned to a single git-style revision-range token instead:
`--pr BASE..HEAD` (e.g. `--pr abc123..def456`). A single-value option can't swallow a
neighboring flag this way — argparse itself now refuses ambiguous input
(`argument --pr: expected one argument`) rather than silently misparsing. The
positional `base`/`head` form still works unchanged; the two forms must not be
combined.

## Follow-up: --pr moved to --diff, real --pr added (fetches an actual GitHub PR)

Per your explicit call: renamed the BASE..HEAD single-token flag from `--pr` to
`--diff` (unchanged behavior otherwise), and built a genuinely new `--pr <ref>` that
resolves and reviews an actual GitHub pull request.

- New `evidence/github.py::resolve_pull_request()`: shells out to `gh pr view <ref>
  --json ...` (accepts a PR number, URL, or branch name — anything `gh pr view`
  accepts) to get the exact current base/head commit SHAs, then `git fetch`es both
  into a private ref namespace (`refs/review-fabric/pr-<number>-{head,base}`) —
  never touching a branch/tag the operator uses. GitHub always exposes
  `refs/pull/<number>/head` on the base repository regardless of whether the PR
  originates from a fork, so this works for fork PRs too.
- Delegates entirely to `gh`'s own already-configured authentication (`gh auth
  login`) — review-fabric never requests, stores, or scrapes a GitHub credential
  itself. This is the one deliberately network-touching evidence path in the tool;
  documented explicitly in the README's safety section as the one exception to
  "never mutates the reviewed repository" (it adds refs, never branches/commits).
- Once resolved, the PR's base/head SHAs feed into the exact same bounded-diff
  pipeline as `--diff`/positional base/head — no separate code path, so it gets the
  same secret-detection, redaction, locking, and artifact guarantees automatically.
  Records a `pr:<number>` constraint in the persisted package for audit purposes.
- `--pr`, `--diff`, `--full`, and positional `base`/`head` are all now mutually
  exclusive (previously only `--full` vs. the diff-like modes was checked).
- gh/git failure messages are deliberately generic (never embed raw subprocess
  stderr), matching the rest of the codebase's redaction discipline — verified with
  a test asserting a simulated leaked token in `gh`'s stderr never appears in the
  raised error.
