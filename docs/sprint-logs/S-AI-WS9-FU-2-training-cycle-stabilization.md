# Sprint Log: S-AI-WS9-FU-2

> **Training-cycle stabilization** — make the daily trainer-VM cycle
> land all trainable manifests with `overall_rc=0`, with a clear
> per-run version trail in the model registry.

## Date Range
- Start: 2026-05-14
- End:   2026-05-14

## Objective
- **Primary goal:** the trainer VM's daily `run_training_cycle.sh`
  invocation must process every YAML in `ml/configs/` cleanly —
  trainable manifests register a new run, empty-dataset manifests
  skip without raising, no `manifest_failed` events, `overall_rc=0`.
- **Secondary goals:**
  - Preserve per-run training history under a stable `model_id`
    rather than polluting the namespace with timestamp suffixes.
  - Recover the pre-existing on-disk experiment dirs (3+ cycles
    already had runs whose RunRecords never made it into the
    registry).
  - Surface model-quality concerns (degenerate classifiers) through
    the existing `/health-review` follow-up channel so they don't
    silently rot.

## Tier
- **Tier 2** — trainer-side ML infra. No live order code touched.
- **Justification:** the changes touch `ml/registry/*` and the
  experiment runner, both of which are trainer-autonomous per
  `docs/claude/trainer-vm-mode.md` § 9. No `config/strategies.yaml`,
  `config/accounts.yaml`, `src/runtime/orders.py`, or live unit
  files were modified.

## Starting Context
- **Active roadmap items:** S-AI-WS9 (Oracle / HF runtime split).
  `run_training_cycle.sh` shipped in S-AI-WS9-FU; this sprint
  closes the next round of issues surfaced by actually running it
  end-to-end.
- **Prior sprint reference:**
  - [S-AI-WS9.md](S-AI-WS9.md) — two-VM topology + provisioning.
  - [S-AI-WS9-FU-run-cycle.md](S-AI-WS9-FU-run-cycle.md) — the
    cycle wrapper script itself.
- **Known issues at start:**
  - 2026-05-13 cycle reports surfaced only ~1 row per builder
    despite 80 closed trades in the journal (case-sensitivity bug
    in the setup-type matcher).
  - 2-of-9 manifests (`backtest-mean`, `post-trade-review`) raised
    on empty datasets, which the cycle script treated as
    `manifest_failed`.
  - 2-of-9 manifests (`prop-mission-policy-baseline-v0`,
    `regime-classifier-baseline-v0`) hit
    `RegistryError: model_id 'X' already registered` from the
    second cycle onwards because `register()` rejected duplicates.

## Repo State Checked
- **Branch reviewed:** `main` at `7a7bb2a` (pre-sprint) → final
  `63ea457` (post #1139) → `8157d1e` (current HEAD after MSE-1
  Phase 2 #1138).
- **Deployment state reviewed:** trainer VM running
  `ict-trainer.{service,timer}` with daily cadence; live VM
  untouched.
- **Canonical docs reviewed:** `docs/ml/model-registry-policy.md`,
  `docs/claude/trainer-vm-mode.md`, `docs/runbooks/training-vm.md`.

## Files and Systems Inspected
- **Code files inspected:**
  - `ml/registry/model_registry.py` — `RegistryEntry`,
    `ModelRegistry.register`, `_write`, `from_dict`.
  - `ml/experiments/runner.py` — `run_experiment`,
    `EmptyDatasetError` semantics.
  - `ml/datasets/families/*.py` — case-sensitivity in setup-type
    matching (specifically `setup_labels`, `setup_labels_audit`,
    `trade_outcomes`, `execution_quality`).
  - `scripts/ops/run_training_cycle.sh` — manifest-iteration loop +
    rc-handling.
  - `tests/ml/test_model_registry.py` —
    `test_register_rejects_duplicate` (now replaced).
- **Config files inspected:** every manifest under `ml/configs/` to
  confirm `model_id` declarations + dataset family bindings.
- **Deployment files inspected:** `deploy/ict-trainer.service`,
  `deploy/ict-trainer.timer`.
- **Docs inspected:** `docs/ml/model-registry-policy.md`,
  `docs/claude/trainer-vm-mode.md` § Registry, `ROADMAP.md` WS9
  row, the existing `comms/follow_ups.json` open entries.
- **Services or timers inspected:** trainer-vm `ict-trainer.timer`
  (cadence) + diag relay (#1135, #1137, #1141) for read-only
  trainer state inspection.
- **GitHub Actions workflows inspected:** `trainer-vm-diag.yml`
  (used for every read-only check + the post-merge backfill).

## Work Completed
- **#1127 — Case-insensitive setup-type matching + empty-dataset
  skip path.** The builders in `ml/datasets/families/` now
  case-fold both sides of the `setup_type` join; the cycle script
  catches the new `EmptyDatasetError` (rc 78 / `EX_CONFIG`) and
  emits `manifest_skipped` instead of `manifest_failed`. After
  this PR the 2026-05-14 cycle produced 75–78 rows per builder.
- **#1133 — Append-on-duplicate registry with per-run version
  trail.** New `RunRecord` dataclass; `RegistryEntry.runs:
  tuple[RunRecord, ...]` (backward-compat default `()`).
  `register()` rewritten:
  - First call: creates entry with `runs=(first_run,)`.
  - Same `model_id`, new `run_id`: appends new RunRecord,
    refreshes top-level metrics/path/revision, appends a
    `StatusEvent("re-trained at run_id=X")` to history. No raise.
  - Same `(model_id, run_id)`: no-op (retry-safe).
  `run_experiment` now passes the run_id it already computes.
  Updated tests:
  `tests/ml/test_model_registry.py::test_register_appends_run_on_duplicate`
  + `test_register_idempotent_on_same_run_id`.
- **#1139 — On-disk reconciler + prop-mission follow-up.** New
  module `ml/registry/reconcile.py` (strictly additive — walks
  `<experiments_root>/<model_id>/`, rebuilds `runs` from disk,
  preserves existing RunRecords verbatim, never drops a record
  even if its run dir is missing). CLI:
  `python -m ml.registry.reconcile [--registry-root ...]
  [--experiments-root ...] [--model-id MID]+ [--dry-run]`. 8
  unit tests. Same PR adds `FU-20260514-001` to
  `comms/follow_ups.json` flagging
  `prop-mission-policy-baseline-v0`'s degenerate
  accuracy=1.0 / f1=0.0 metrics — the trade journal is too small
  (n_eval=53) for the binary classifier to learn anything non-
  trivial yet.

## Validation Performed
- **Tests run:**
  - `pytest tests/ml/test_model_registry.py
    tests/ml/test_experiments_runner.py
    tests/ml/test_registry_reconcile.py` — 39 passed locally and in
    CI for both #1133 and #1139.
  - `ruff check .` — green on both PRs.
- **End-to-end cycle verification (trainer-vm-diag relay):**
  - #1128 — initial cycle that surfaced the duplicate-registration
    and empty-dataset issues.
  - #1130, #1135 — post-#1127 / post-#1133 cycle confirmations.
  - #1137 — read-only check that disk had every expected run
    directory before backfill.
  - #1141 — post-#1139 backfill verification. Reconciler ran in
    dry-run mode (predictions exact), then for real (actuals
    matched predictions), then idempotently (no further changes).
- **Final registry state (post-#1141):**
  - 5 models at `runs=2` (T16:22 + T16:49 cycles).
  - `prop-mission-policy-baseline-v0` + `regime-classifier-baseline-v0`
    at `runs=3` (T15:53 + T16:22 + T16:49). T15:53 + T16:22 records
    carry `code_revision="unknown"` + `by="registry-reconcile
    (backfilled from disk)"`; T16:49 carries the real revision.
- **Gaps not yet verified:** tomorrow's cycle adding a 4th
  RunRecord under the normal append path (only relevant in
  retrospect; not a release-gate).

## Documentation Updated
- **Rules doc updates:** none.
- **Architecture doc updates:** none structural — the registry's
  external contract (one JSON per `model_id`) is unchanged; only
  internal field set expanded.
- **Trade pipeline doc updates (`docs/TRADE-PIPELINE.md`):** none.
- **Roadmap updates:** `ROADMAP.md` WS9 row updated to note the
  end-to-end cycle stabilization and reference this sprint log.
- **GitHub Actions doc updates:** none.
- **Subsystem doc updates:**
  `docs/ml/model-registry-policy.md` extended with:
  - New top-level "Per-run training history" section (run_id
    semantics, append-on-duplicate contract, idempotency).
  - New "Backfill: `python -m ml.registry.reconcile`" subsection.
  - Sample JSON in "Registry entry shape" updated to include the
    `runs` array.
  - "Versioning" section corrected (used to assert `register()`
    rejects duplicate model_id; now describes per-run vs
    family-level versioning).
  - "Forbidden" + "Update rule" sections extended.
- **Historical docs marked superseded:** none.

## Contradictions or Drift Found
- The pre-sprint `model-registry-policy.md` said new training runs
  with the same `model_id` are *rejected*. That contradicted
  reality even before this sprint, because every daily cycle was
  re-registering the same model_ids — it just hadn't fired yet
  because cycle 2 was today. Doc has been brought into agreement
  with #1133's append-on-duplicate behaviour.

## Risks and Follow-Ups
- **Remaining technical risks:**
  - `prop-mission-policy-baseline-v0` is currently degenerate
    (FU-20260514-001 tracks this; resolution requires more closed
    trades, not a code change).
  - `code_revision="unknown"` on the two backfilled RunRecords per
    model is informational; the experiment dir doesn't persist a
    commit SHA and we accepted the gap rather than fabricating a
    timestamp-derived guess.
- **Remaining product decisions (Tier 3):** none introduced by
  this sprint.
- **Blockers:** none.

## Deferred Items
- HF-side model lifecycle (the second half of WS9) — out of scope
  for this sprint, still queued under the WS9 row.
- Telegram-alerter integration for `concern`-grade cycle outcomes
  (cycle_end with overall_rc != 0). Currently surfaces only via
  diag inspection.

## Next Recommended Sprint
- **Suggested next sprint:** S-AI-WS5-G or equivalent —
  re-evaluate the prop-mission and regime-classifier baselines
  once the trade journal has materially more data (~250+ closed
  trades). Until then the per-run history will accumulate cleanly
  but the models themselves will keep collapsing to the majority
  class.
- **Why next:** the infra is now healthy enough that model-quality
  is the gating axis, not pipeline mechanics.
- **Required verification before starting:** confirm closed-trade
  count from `trade_journal.db::trades` exceeds the threshold and
  that the positive-class label has non-trivial base rate.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from
  summaries. (Read every changed file in the registry, runner,
  and reconciler. Verified test counts. Confirmed CI conclusions
  on #1133 and #1139.)
- [x] Documentation was reviewed and updated as part of the
  sprint. (Registry policy doc + ROADMAP row + this sprint log
  + FU-20260514-001 entry.)
- [x] If this sprint touched any pipeline stage,
  `docs/TRADE-PIPELINE.md` was updated and the dashboard's Trade
  Process tab was visually verified. (Not applicable — trainer
  registry only, not a live pipeline stage.)
- [x] Roadmap status was checked. (WS9 row touched.)
- [x] Contradictions were recorded. (Registry-policy doc claim
  about duplicate rejection brought into agreement with code.)
- [x] Remaining unknowns were stated clearly. (Prop-mission
  degenerate metrics — tracked in FU-20260514-001; tomorrow's
  cycle proving the 4th-run append path — informational only.)
