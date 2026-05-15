# WS7 follow-up — Registry wire + stage / parser hygiene

**Master plan:** [`docs/AI-TRADERS-ROADMAP.md`](../../AI-TRADERS-ROADMAP.md)
**Milestone:** M9 (closes the WS7 deployment-tier gap)
**Sprint id:** **S-AI-WS7-FU-REGISTRY-WIRE** (draft)
**Status:** 📋 NOT STARTED — drafted 2026-05-15 from a live pipeline verification session.

## Why this sprint exists

A 2026-05-15 end-to-end pipeline verification found that **the WS7 shadow harness
has never actually produced a shadow prediction in production**, despite
the WS7 sprints PART-1 through PART-6 being marked done, VWAP being
configured with `shadow_model_ids: ["regime-classifier-baseline-v0"]`,
and the daily training cycle producing valid registry entries on the
trainer VM.

Four distinct bugs combine to break the pipeline silently:

1. **Registry wire is broken.** The live VM strategy factory reads
   `./ml/registry-store/` (relative to its CWD), but the trainer
   publishes the registry to `/data/bot-data/runtime_logs/trainer_mirror/`.
   These two paths are completely disconnected.
   - Verified 2026-05-15: `/home/ubuntu/ict-trading-bot/ml/registry-store/`
     does not exist on the live VM. `ls` returns "No such file or
     directory".
   - Effect: `ModelRegistry(./ml/registry-store)` construction fails →
     factory returns empty predictor list → `with_shadow_preds()` is
     pass-through → `runtime_logs/shadow_predictions.jsonl` is never
     created.
2. **Stage-name mismatch between `ml promote` CLI and shadow factory.**
   The CLI's `_check_stage` allowlist is the WS4 vocabulary
   (`candidate | champion | paper | advisory | live-approved`); the
   WS7 factory's `LIVE_INFLUENCE_STAGES` is the WS7 vocabulary
   (`shadow | advisory | limited_live | live_approved`). The two
   overlap only on `advisory`.
   - Verified 2026-05-15: `python -m ml promote regime-classifier-baseline-v0
     shadow ...` raises `RegistryError: new_status must be one of
     ('candidate', 'champion', 'paper', 'advisory', 'live-approved');
     got 'shadow'`.
   - Effect: the WS5 bootstrap script can never walk a model past
     `advisory` via the CLI. Models that reach `target_deployment_stage:
     shadow` did so via manual JSON edits, not via the promotion path.
3. **WS5 bootstrap parser silently drops `model_id`.** The script's
   `model_id="$(tail -n 200 .../train.out | python -c '... summary.get("model_id")')"`
   pipeline returns empty for every manifest, even though the underlying
   `python -m ml train` exits 0 and registers a run. The stdout that the
   parser scans does not contain `model_id` where expected (or the
   summary is on stderr, or the key has been renamed).
   - Verified 2026-05-15: `bash -x scripts/ops/train_and_register_ws5_baselines.sh`
     for `baseline-regime-classifier.yaml` shows `+ model_id=` (empty)
     after a successful `rc=0` train step.
   - Effect: every manifest under bootstrap emits
     `manifest_failed, phase: parse, detail: could not extract model_id`
     and the promote step is never reached. Bootstrap has never
     successfully promoted anything.
4. **Registry mirror's `registry.jsonl` synthesis missing.** The
   `publish_trainer_mirror.sh` script reads `${REGISTRY_ROOT}/registry.jsonl`
   and pushes the same file. That file does not exist — the registry
   was refactored in S-AI-WS9-FU-2 to per-model JSONs
   (`<model_id>.json`).
   - Verified 2026-05-15: `ls ml/registry-store/registry.jsonl` →
     "No such file or directory" on the trainer VM. `trainer_status.json`
     reports `"registry": {"models": 0, "rows": 0, "stages": {},
     "path_present": false}` even though 7 per-model JSONs exist.
   - Effect: Streamlit dashboard "Models" tab renders the registry as
     empty (Bug 4 alone, independent of Bugs 1-3).

A fifth (smaller) bug: the bootstrap script aborts the entire run on
the first manifest that hits an empty-dataset (rc=78 / `EX_CONFIG`),
even though `run_training_cycle.sh` got the skip-on-empty-dataset
treatment in S-AI-WS9-FU-2. Same fix needs to be back-ported.

## Objective

Make the shadow-prediction pipeline produce actual predictions in
production. Concretely:

- A model registered on the trainer VM at `target_deployment_stage:
  shadow` is loadable by the live VM's strategy factory within one
  trainer publish cycle (≤ 2 min).
- `python -m ml promote <model_id> shadow ...` succeeds without error.
- The WS5 bootstrap script promotes all 7 trainable baselines from
  `research_only` to `shadow` end-to-end without parse failures.
- The Streamlit "Models" tab shows the 7 baseline models (not empty).
- `runtime_logs/shadow_predictions.jsonl` on the live VM begins
  accumulating one record per VWAP tick within five minutes of
  shipping the registry-wire fix.

## Non-negotiables

- **Live-VM YAML stays operator-gated.** This sprint must not touch
  `config/strategies.yaml`, `config/accounts.yaml`, or any live-VM
  systemd unit file as the wiring fix. The bridge must be entirely on
  the trainer side. (Trainer-vm-mode.md § 3.a authorizes Claude on
  `scripts/ops/`, `ml/`, `tests/ml/`; the live VM YAML is Tier-3.)
- **No model-state shape changes.** The per-model JSON layout
  (`{model_id, stage, target_deployment_stage, runs[], history[]}`)
  is stable as of S-AI-WS9-FU-2. Don't repurpose it.
- **Backward compat on the promote ladder.** Old status names
  (`candidate`, `champion`, `paper`, `advisory`, `live-approved`)
  must still be accepted by the CLI (with a deprecation warning)
  so existing scripts and tests don't break. New names
  (`research_only`, `backtest_approved`, `shadow`, `limited_live`,
  `live_approved`) are accepted alongside. The factory continues to
  check the new names only.

## Tasks

### T1. Registry-path bridge (the critical fix)

Two acceptable approaches; pick one in the sprint plan when sized.

**T1.A** — Trainer rsyncs per-model JSONs into the live VM's
strategy-factory path:

- `publish_trainer_mirror.sh` gains a new rsync target:
  `${REGISTRY_ROOT}/*.json` → `live:/home/ubuntu/ict-trading-bot/ml/registry-store/`.
- Same SSH key, same trust contract as the existing dashboard mirror
  push.
- Pro: zero changes to strategy code, env, or live-VM unit files.
- Con: feels hacky (two destinations for the same data, drift risk).

**T1.B** — Strategy factory respects an `ML_REGISTRY_ROOT` env var,
and the live VM systemd unit points it at the existing mirror path:

- New: `ml/shadow/factory.py::DEFAULT_REGISTRY_ROOT` reads from
  `os.environ.get("ML_REGISTRY_ROOT")` with the current path as
  fallback.
- New: trainer also synthesizes a per-model directory under
  `/data/bot-data/runtime_logs/trainer_mirror/` so the factory's
  `ModelRegistry(<mirror>)` construction finds the same per-model
  JSONs.
- Operator action: `ict-trader-live.service` Environment= adds
  `ML_REGISTRY_ROOT=/data/bot-data/runtime_logs/trainer_mirror`.
- Pro: cleaner separation of trainer-write vs live-read territories,
  no rsync into the app dir.
- Con: touches a live-VM unit file → needs an operator-approval
  action via `operator-actions.yml`.

Acceptance: live VM `ModelRegistry(<configured-root>)` construction
succeeds and lists ≥ 1 model.

### T2. Fix `ml promote` stage-name vocabulary

- Audit `ml/registry/model_registry.py::promote` (line ~385 per
  2026-05-15 diag) — replace the WS4 allowlist with the unified WS7
  ladder + a small alias table for the legacy names.
- Aliases (one-way, with deprecation warning): `champion` →
  `live_approved`, `paper` → `shadow`, `live-approved` →
  `live_approved`.
- Add unit tests asserting:
  - All seven WS7 stages accepted directly.
  - All five legacy names accepted and translated.
  - Unknown stage names raise with a useful error.
- Acceptance: `python -m ml promote <id> shadow ...` succeeds on a
  research-only model.

### T3. Fix WS5 bootstrap `model_id` extraction

- Read what `python -m ml train` actually prints (likely a multi-line
  human-readable summary, not a single JSON object). Two possible
  fixes:
  - **T3.A** — make `ml train` emit a structured one-line JSON
    summary at the end of its stdout, with a canonical key set
    including `model_id`. Update the bootstrap parser accordingly.
  - **T3.B** — sidestep stdout entirely: bootstrap reads the
    just-written per-model JSON from `${REGISTRY_ROOT}/` by globbing
    for the file most-recently modified since the train step's
    `train_start` timestamp.
- Prefer T3.A — explicit contract, less filesystem racy.
- Add a `tests/ml/test_train_cli_output.py` regression that asserts
  the JSON summary contract.
- Acceptance: bootstrap for any single manifest emits
  `manifest_trained` with a non-empty `model_id` field.

### T4. Synthesize `registry.jsonl` in publish script

- `publish_trainer_mirror.sh` gains a new step before the
  status-build python block: walk `${REGISTRY_ROOT}/*.json` (excluding
  `registry.jsonl` itself), emit one line per model into a temp file,
  atomically rename to `registry.jsonl`.
- The status-build python then reads from the synthesized jsonl as
  before, so the existing trainer_status payload populates correctly.
- The downstream live-VM consumer (`src/web/api/routers/training_center.py
  ::get_registry`) keeps reading `registry.jsonl` — no change needed
  there.
- Acceptance: `trainer_status.json` after a publish run reports
  `registry: {models: 7, rows: 7, path_present: true, stages: {...}}`.
  Streamlit Models tab renders the 7 baselines.

### T5. Back-port empty-dataset skip to bootstrap

- The WS5 bootstrap's training subprocess wrapper currently treats any
  non-zero rc as `manifest_failed` and (when combined with `set -e`)
  aborts the whole bootstrap run. Mirror the S-AI-WS9-FU-2 logic:
  rc=78 (`EX_CONFIG`, the `EmptyDatasetError` mapping) emits
  `manifest_skipped` and continues to the next manifest.
- Acceptance: a fresh bootstrap with `MANIFESTS=` (default — all 9
  baselines) runs all 9 and emits exactly two `manifest_skipped`
  rows (for `backtest-mean` and `post-trade-review`).

## Cross-task sequencing

T1 is the critical path. T2 + T3 + T5 unblock the
`train_and_register_ws5_baselines.sh` automation. T4 unblocks the
dashboard Models tab.

Suggested PR layout:

1. **PR-1** — T2 + T2 tests (stage-name vocabulary fix).
2. **PR-2** — T4 (registry.jsonl synthesis). Independent of others.
3. **PR-3** — T1 (chosen approach). Critical path; can land after
   PR-1 + PR-2.
4. **PR-4** — T3 + T3 tests (bootstrap parser fix).
5. **PR-5** — T5 (empty-dataset skip back-port). Trivial.
6. **PR-6 (post-merge)** — re-run WS5 bootstrap end-to-end on the
   trainer VM via the diag relay; verify the seven baselines reach
   `shadow` stage in the registry and that VWAP starts logging
   shadow predictions within one publish cycle.

## Acceptance gates (sprint-close)

- All five T-items merged.
- Live-VM `runtime_logs/shadow_predictions.jsonl` exists and contains
  ≥ 1 record per `(model_id, stage)` for the regime classifier within
  five minutes of the publish following the bootstrap.
- `/api/bot/shadow/stats` returns `log_present: true` and a non-empty
  `records` array for `regime-classifier-baseline-v0`.
- `/api/bot/shadow/drift` returns a verdict (not "no data") after
  enough predictions accumulate (≥ 30 in each window — likely 30 min
  of ticks, may need 24-48 h for a meaningful drift verdict).
- Streamlit Models tab shows seven baselines with their stages
  + latest-run metrics.
- A new sprint log `docs/sprint-logs/S-AI-WS7-FU-REGISTRY-WIRE.md`
  records the verified state, the PR list, and the close-out
  acceptance evidence (diag-relay outputs preferred).

## Risks

- **Strategy factory caches** (WS7-PART-6 coordinator cache) may
  hold a stale empty-predictor list for `regime-classifier-baseline-v0`
  even after the registry wires up. Verify cache invalidation behaves
  as expected after the first publish-with-models cycle; if not, file
  a follow-up to add a cache TTL or a registry-mtime-keyed invalidator.
- **Promote CLI vocabulary changes** could break automation that
  passes the old names. The legacy alias table mitigates this but
  needs the deprecation warning to flag callers that should migrate.
- **`registry.jsonl` synthesis** races with concurrent registry
  writes if a training cycle is running simultaneously. The 2-min
  publish timer can collide with the 24-h training cycle window;
  use a small file lock or write-then-rename pattern.

## Out of scope

- Promoting the other six baselines (`execution-quality`,
  `setup-quality`, `setup-quality-audit`, `trade-outcome-global`,
  `trade-outcome-winrate`, `prop-mission-policy`) to a strategy's
  `shadow_model_ids`. That's separate per-strategy wiring work,
  Tier-3 (live-VM YAML edits).
- Fixing the degenerate metrics on `prop-mission-policy-baseline-v0`
  — tracked at `comms/follow_ups.json::FU-20260514-001`, resolution
  requires more closed trades, not code.
- Backfilling shadow predictions for the period 2026-05-10 to
  2026-05-15 — not possible; predictions only get generated at signal
  time.
- WS6 PART-2 (first concrete open-source-model integration). The
  PART-1 framework is done; PART-2 is gated on a real use case.

## Discovery session reference

The bugs above were enumerated in a 2026-05-15 pipeline-verification
session driven by the operator. Diag-relay evidence:

- Trainer registry state: issue #1225 (per-model JSON dump showing
  `regime-classifier-baseline-v0` at `tds=shadow, runs=0`; other six
  at `tds=research_only`).
- Bootstrap parser failure trace: issue #1240 (bash -x for a single
  manifest, `+ model_id=` empty after rc=0 train).
- Stage-name mismatch trace: issue #1241 (`new_status must be one of
  ('candidate', 'champion', 'paper', 'advisory', 'live-approved');
  got 'backtest_approved'`).
- Live VM registry path absence: issue #1243 (`ls: cannot access
  '/home/ubuntu/ict-trading-bot/ml/registry-store/': No such file
  or directory`).
- Trainer publish runs cleanly: issue #1242 (publish_trainer_mirror.sh
  emits `status_built` + `published`, but registry section in the
  payload still empty because of Bug 4).

The session also performed a partial mitigation on the trainer VM:
`python -m ml.registry.reconcile --model-id regime-classifier-baseline-v0`
restored the model's runs from on-disk experiment dirs (1 → 5 runs).
This unblocks T1 + T2 verification once those land but does not
constitute a fix — the next training cycle's promote walk will
re-trip the same bugs.
