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
