# Training Center

> **Status:** Canonical (training-center scope). Adopted in **S-AI-WS4**
> (2026-05-10). Updated in **S-AI-WS4-FU** (2026-05-10):
> Predictor abstraction + split strategies + `compare` subcommand.
> Updated in **S-AI-WS5-B-PART-2 PR 2B** (2026-05-10): multiclass
> predictor + multiclass evaluator + regime classifier baseline.
> Updated in **S-AI-WS5-C** (2026-05-10): `setup_labels` family +
> setup-quality scorer + numeric-mean trainer extension + training
> session workflow.
>
> **Authority:** Subordinate to
> [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md).
>
> **Companion:** [`model-registry-policy.md`](model-registry-policy.md).

## Purpose

Describes the repo-native training factory: directory layout, the
`TrainingManifest` YAML schema, the trainer / evaluator /
experiment-runner contracts, the `Predictor` abstraction (WS4-FU),
the split strategies (WS4-FU), and the umbrella CLI.

## Cadence (S-AI-WS8-PART-2, adopted 2026-05-14; checkpoint/resume + resource
guard added 2026-07-02, BL-20260702-TRAINER-OOM)

One training cycle per day. One shared data sync per cycle that all
manifests reuse. Concretely:

- `ict-trainer.timer` is `OnCalendar=daily, RandomizedDelaySec=1h,
  Persistent=true`. Each firing triggers `ict-trainer.service`, which
  runs `scripts/ops/run_training_cycle.sh` end-to-end.
- The cycle is **a single umbrella pass over every manifest in
  `ml/configs/`**. It is not one timer per model — that would
  re-download the live VM's `trade_journal.db` and rebuild every
  family per manifest, which both wastes I/O and risks intra-day
  inconsistency between models.
- The cycle:
  1. `git fetch && git reset --hard origin/main` — pin to the
     deployed code.
  2. **`sync_trainer_data.sh`** — one rsync of `trade_journal.db`
     and `signal_audit.jsonl` from the live VM. Shared by all
     manifests in this cycle.
  3. **`build_trainer_datasets.sh`** — one rebuild of every dataset
     family on top of that shared snapshot. Shared by all manifests.
  4. **Iterate manifests** — `python -m ml train <manifest>` for each
     `ml/configs/*.yaml` not already `done`/`skipped` in today's
     checkpoint (see below). Each manifest gets its own
     `experiments-runs/<model_id>/<run_id>/` and append-only registry
     row, but they all see the same dataset version.
  5. **Publish to live VM mirror** — `publish_trainer_mirror.sh` at
     cycle start and cycle end so the Streamlit dashboard reflects
     "in progress" and "complete" within seconds of each transition.
- Between cycles the heartbeat publisher
  (`ict-trainer-publish.timer`, every 2 min) keeps
  `trainer_status.json` fresh so dashboard liveness reflects the
  trainer even when no training is running.

Activation is autonomous: Claude fires `trainer-vm-diag-request`
with `cmd: sudo systemctl enable --now ict-trainer.timer`. The
trainer charter authorizes trainer-side systemd changes without
operator approval.

If a single cycle's runtime exceeds ~6 h (currently each manifest
takes 2–10 minutes; 68 manifests currently fit comfortably within a
single cycle), revisit this single-umbrella-cycle design. Until then,
daily-shared-sync is the canonical pattern.

### Checkpoint/resume + catch-up timer (2026-07-02, BL-20260702-TRAINER-OOM)

The trainer VM has no per-manifest memory isolation, and a run that OOMs
mid-cycle used to strand every manifest after the kill point until the next
day's timer fire — a lost training day for ~half the fleet. Two changes close
that gap:

- **Checkpoint file.** `run_training_cycle.sh` writes
  `runtime_logs/trainer/cycle_progress_<UTC-date>.json`, one row per manifest
  (`pending` → `running` → `done`/`skipped`/`failed`), updated before and
  after each `python -m ml train` invocation so a killed process leaves a
  readable partial record. A fresh invocation on the same UTC date loads this
  file and skips anything already `done`/`skipped`, resuming from the first
  non-terminal manifest — so a same-day retry (either the catch-up timer below,
  or a manual re-run) never re-trains what already succeeded.
  `TRAINING_CYCLE_FORCE_RESTART=1` bypasses the checkpoint for a deliberate
  clean restart. A `flock` on `runtime_logs/trainer/.cycle.lock` guards
  against two invocations (primary + catch-up) racing the same checkpoint
  file — the second exits immediately with a `cycle_locked` event.
- **`ict-trainer-catchup.timer`** fires once daily at `OnCalendar=*-*-*
  05:00:00 UTC` (the primary is `daily` + up to 1h random delay, so normally
  long finished by 05:00) and re-invokes the same script. On a clean day
  every manifest is already `done`, so it's a near-no-op
  (`cycle_already_complete`, exits fast); on a day the primary run was
  OOM-killed partway through, it picks up the remaining manifests same-day
  instead of stranding them until tomorrow's primary fire.
- **Resource guard.** `ict-trainer{,-catchup}.service` set `MemoryHigh=3G` /
  `MemoryMax=5G` / `MemorySwapMax=512M` (of the VM's 6GB) with
  `OOMPolicy=continue` — the default systemd `OOMPolicy=stop` kills the whole
  cgroup (and therefore the entire in-progress cycle) on any single manifest's
  OOM; `continue` lets the kernel OOM-kill just the offending subprocess while
  the service (and the loop's existing rc=137-tolerant "one failed manifest
  doesn't abort the cycle" handling) keeps running. Combined with
  checkpoint/resume, one expensive manifest now costs one `failed` row instead
  of the whole day. **NB the accounting:** `MemoryMax` bounds the whole *cgroup*
  (bash + the ONE active `python -m ml` child + page cache), not a long-lived
  "main process" — training already runs each manifest AND each dataset family
  in its own subprocess (`run_training_cycle.sh` / `build_trainer_datasets.sh`),
  so the peak RSS is a single child (the ~215k-row `market_features` build), not
  an orchestrator that needs splitting.
- **Swap containment (2026-07-10, MB-20260709 — supersedes the 07-08 swap
  headroom).** The 2026-07-08 fix grew swap 2G→8G with `MemorySwapMax=infinity`
  so a child could swap past the 5G RAM cap instead of being OOM-killed. That
  **regressed** into an SSH-death: on 2026-07-10 a child paged into the full 8G
  and swap-thrashed the 1-OCPU box until sshd couldn't answer the banner
  exchange, needing an OCI hard reset. Fixed by capping per-cgroup swap
  (`MemorySwapMax=512M`) so a runaway child is **OOM-killed + contained by
  `OOMPolicy=continue`** rather than thrashing the host, and lowering
  `MemoryHigh`→3G so reclaim starts earlier. The 8G swapfile stays as benign
  host headroom (now capped per-cgroup). Do **not** re-raise `MemorySwapMax` to
  infinity. Normal builds peak <5G RAM so the cap is untouched; it only bites a
  genuine runaway. **Stagger** (opt-in) further relieves cumulative pressure:
  `build_trainer_datasets.sh` day-parity-rebuilds the ALT ETH/SOL 5m/15m shards
  on alternating nights (`BUILD_ALL_SHARDS=1` forces all), and
  `run_training_cycle.sh` supports `TRAINING_MANIFEST_ROTATE=1` to alternate the
  manifest fleet by day-parity. The **real peak-RSS reduction** — streaming the
  `market_features` load instead of slurping a list-of-dicts — is the tracked
  follow-up (`MB-20260709-TRAINER-SUBPROC-ISOLATION`); the caps above make the
  box unkillable meanwhile.

> **Log paths.** The dataset-build log is `runtime_logs/trainer/dataset_builds.jsonl`
> and the dataset-audit log is `runtime_logs/trainer/dataset_audit.jsonl` (both
> under the `trainer/` subdir, `BUILD_LOG_PATH`-overridable); the training-cycle
> log is one level up at `runtime_logs/training_cycle.jsonl`.

Both the script logic and the unit/timer definitions live in
`deploy/training-vm-cloud-init.yaml` for re-provisioning; the live trainer VM
picks up the script change on its next self-pull of `main` and the unit
changes via a `trainer-vm-diag-request` (`daemon-reload` + `enable --now
ict-trainer-catchup.timer`) — no operator approval needed, trainer-VM changes
are Claude-autonomous per the VM-authority split.

## Directory layout

```
ml/
  datasets/      # WS3 + WS5-A — dataset framework + builders
  predictors/    # WS4-FU — Predictor ABC + concrete predictors
  trainers/      # WS4 + WS5-A — Trainer ABC + concrete trainers
  evaluators/    # WS4 + WS5-A — Evaluator ABC + concrete evaluators
  experiments/   # WS4 — runner + WS4-FU splitters
  registry/      # WS4 — filesystem registry
  promotion/     # WS4 — documented promotion gates
  configs/       # WS4 + WS5-A + WS4-FU — YAML manifests
  manifest.py    # WS4 — TrainingManifest schema
  cli.py + __main__.py  # WS4 + WS4-FU — `python -m ml`
```

## Training manifest

Schema in [`ml/manifest.py`](../../ml/manifest.py)
(`TrainingManifest`). Fields:

| Field | Notes |
|---|---|
| `manifest_version` | Currently `v1`. |
| `model_id` | Unique identity in the registry. |
| `model_family` | Free-form. |
| `trainer` / `trainer_config` | Fully-qualified trainer + its kwargs. |
| `dataset` | `{family, symbol_scope, timeframe, version}`. |
| `evaluator` / `evaluator_config` | Fully-qualified evaluator + its kwargs. Includes split config (see below). |
| `target_deployment_stage` | One of `candidate` / `shadow` / `advisory` (legacy `research_only`..`live_approved` alias in via `ml.manifest.canonical_stage`). |
| `notes` | Free-form. |

## Predictor abstraction (WS4-FU)

Each trainer pairs itself with a `Predictor` subclass via
`PREDICTOR_CLASS`:

```python
class ConstantPredictionTrainer(Trainer):
    PREDICTOR_CLASS = ConstantPredictor
    def fit(self, rows, config) -> Mapping: ...
```

Evaluators consume predictions via
`Evaluator._resolve_predictor(state)`:

```python
class RegressionEvaluator(Evaluator):
    def score(self, model_state, rows, config):
        predictor = self._resolve_predictor(model_state)
        for row in rows:
            prediction = predictor.predict(row)
            ...
```

Resolution dispatches via `state['trainer']` qualname →
`trainer_cls.PREDICTOR_CLASS(state)`. Concrete predictors live in
[`ml/predictors/`](../../ml/predictors/):

| Predictor | Pairs with |
|---|---|
| `ConstantPredictor` | `ConstantPredictionTrainer` |
| `PerGroupPredictor` | `PerStrategyWinRateTrainer` (configurable `feature_column`; binary `won` target by default; supports continuous `numeric_mean` target via `target_kind` for the WS5-C setup-quality scorer) |
| `PerBucketMulticlassPredictor` | `RegimeClassifierTrainer` (S-AI-WS5-B-PART-2 PR 2B; emits class-label + per-class probabilities; falls back to training-set marginal for unseen buckets) |

### Trainer `target_kind` knob (WS5-C)

`PerStrategyWinRateTrainer` accepts a `target_kind` config flag:

- `binary` (default, WS5-A behavior): coerces `target_column` to
  `{0, 1}` via `bool(value)`; `per_group_rate` is the per-group win
  rate. Backward-compat: existing manifests don't need to change.
- `numeric_mean` (WS5-C): casts `target_column` to `float`;
  `per_group_rate` is the per-group sample mean of the target.
  Used by the setup-quality scorer against `r_multiple`. Pairs
  with `RegressionEvaluator` (not `ClassificationEvaluator`).

The `target_kind` value is recorded in `model_state` so registry
entries are unambiguous about what each baseline learned.

### Trainer `sample_weight` knob (S-MLOPT-S2)

Both LightGBM trainers accept an **opt-in** `trainer_config.sample_weight`
block (absent → no behaviour change, byte-for-byte). It produces a per-row
training weight, mean-normalised to 1.0, that composes with any `class_weight`:

- `half_life_days: N` — **recency decay**: a row `t` days older than the newest
  training row weighs `0.5 ** (t / N)`. Addresses `MB-20260601-001` (a wide
  training window dilutes the recent regime) — keep the sample size but let old
  history count for less.
- `uniqueness: true` — **de Prado average uniqueness** (`AFML` Ch. 4) over label
  spans (`label_horizon` rows). NOTE: near-uniform for fixed-horizon bar labels;
  it only bites once spans vary (Phase 1 triple-barrier).
- `time_column` — where the per-row timestamp is read (default `ts` multiclass /
  `created_at` regression). Missing/unparseable timestamps **fail loud**.

Implemented in [`ml/trainers/sample_weights.py`](../../ml/trainers/sample_weights.py);
the applied block is echoed into `model_state["sample_weight"]`. The
window-length/recency sweep that picks a config is
[`scripts/ml/window_recency_sweep.py`](../../scripts/ml/window_recency_sweep.py).
Adopting a weighted config as a manifest default is Tier-3 (operator-gated).

### Multiclass predictor surface (PR 2B)

[`MulticlassPredictor`](../../ml/predictors/multiclass.py) is a
`Predictor` subclass that adds:

- `predict_label(row) -> str` — discrete class prediction.
- `predict_proba(row) -> Mapping[str, float]` — per-class
  probabilities (sum to 1).

The default `predict(row)` returns the probability of the
predicted class so existing single-float consumers don't break.
`MulticlassClassificationEvaluator` narrows to
`MulticlassPredictor` and raises `TypeError` against any other
predictor.

## Split strategies (WS4-FU)

Dispatched from `evaluator_config.split_strategy`. Default
`holdout` matches the WS4 behavior so existing manifests work
unchanged.

| Strategy | Behavior | Config |
|---|---|---|
| `holdout` | Stable suffix split. | `holdout_fraction: float in (0,1)` (default 0.2) |
| `time_aware_holdout` | Sort by `time_column` (default `created_at`), then suffix split. | `holdout_fraction`, `time_column` |
| `walk_forward` | Rolling-origin folds. Returns the LAST fold for single-split mode. For multi-fold (averaged-across-folds) evaluation, use `purged_walk_forward`. | `n_folds: int >= 2`, `min_train_fraction: float in (0,1)`, `time_column` |
| `purged_walk_forward` | **Multi-fold** purged & embargoed walk-forward CV (de Prado, *AFML* Ch. 7; S-MLOPT-S1). PURGEs training rows whose forward label window overlaps the test block and EMBARGOes an extra buffer; the runner iterates the fold list and pools per-fold metrics. The single-split `split()` form returns the last fold. **Opt-in** — no manifest defaults to it. | `n_folds`, `min_train_fraction`, `time_column`, `label_horizon: int >= 0` (PURGE width in rows, default 1), `embargo_fraction: float in [0,1)` **or** `embargo_n: int` |

Implementation in
[`ml/experiments/splitters.py`](../../ml/experiments/splitters.py): the
two-sided `purge_and_embargo_indices(...)` primitive backs the splitter and is
ready for a later combinatorial purged CV (M14 Phase 0.2). Master plan:
[`docs/ml/optimization-roadmap.md`](optimization-roadmap.md).

## Experiment runner

[`ml/experiments/runner.py::run_experiment(...)`](../../ml/experiments/runner.py).
Steps:

1. Load + validate manifest.
2. Locate dataset under
   `<datasets_root>/<family>/<scope>/<tf>/<version>/data.jsonl`.
3. Split via `splitters.split(rows, evaluator_config)` — **except** for a
   multi-fold `split_strategy` (`purged_walk_forward`), which takes the CV
   branch: `splitters.iter_folds(...)` → fit+score each fold → pool metrics
   (rates sample-weighted by `n_eval`, counts summed; adds `n_folds`) → persist
   a **full-data refit** as the deployable `model_state`.
4. Resolve trainer + evaluator via `importlib`.
5. `trainer.fit(...)` → `model_state` (carries `trainer` qualname).
6. `evaluator.score(state, rows, config)` → `metrics` (uses
   `_resolve_predictor` internally).
7. Write artifacts under `<experiments_root>/<model_id>/<run_id>/`:
   `manifest.json`, `model_state.json`, `metrics.json` (+ `cv_folds.json` for
   the multi-fold CV path).
8. Register in registry as `candidate` (default).

## CLI

```
python -m ml build-dataset ...           # passthrough to ml.datasets
python -m ml validate-dataset <path>     # passthrough
python -m ml list-families               # passthrough
python -m ml train <manifest>            # train + evaluate + register
python -m ml promote <id> <new-status>   # operator-gated transition
python -m ml list-models [--status S]    # registry enumeration
python -m ml list-trainers               # introspection
python -m ml list-evaluators             # introspection
python -m ml compare <id-a> <id-b>       # WS4-FU side-by-side metric diff
```

The `compare` subcommand surfaces shared-metric deltas (`b - a`)
plus per-side-only metric lists, all as JSON for automation.

## Training session workflow

When the operator runs a training session (or when a future
session needs to retrain a baseline against fresh data), follow
this workflow rather than reinventing:

1. **Refresh the labelled feedstock.** The autonomous `/health-review`
   skill produces per-trade `trade_decision_grades[]` for every trade
   since the last review and persists each one — keyed by `trade_id` — to
   `comms/claude_trade_scores.jsonl` (a durable, repo-tracked log; see
   `comms/schema/claude_trade_scores.schema.json`). These grades are the
   labelled training signal that the per-trade baselines (WS5-A outcome
   probability, WS5-C setup quality, future WS5-E post-trade review,
   WS5-F prop mission policy) consume. Run `/health-review` so the fresh
   scores land on `main` before the training run.
2. **Build the dataset(s) the baseline needs.** See
   [`docs/data/dataset-taxonomy.md`](../data/dataset-taxonomy.md)
   for the family roster. Each builder writes a versioned
   artifact under `<output>/<family>/<scope>/<tf>/<version>/`.
   Heavy / network-attached builds (`market_raw` via Bybit) MUST
   run off the live VM with `ICT_OFFVM_BUILD_HOST=1`.
3. **Train + evaluate via a YAML manifest.** Use one of the
   established manifests under `ml/configs/` (listed below), or
   add a new one following the `TrainingManifest` schema. Don't
   skip the manifest — the experiment runner is what writes the
   reproducible artifact triple (`manifest.json`,
   `model_state.json`, `metrics.json`) and registers the run in
   the model registry as a `candidate`.
4. **Compare baselines.** `python -m ml compare <id-a> <id-b>`
   surfaces shared-metric deltas as JSON. Pair every non-trivial
   baseline with a sanity baseline (the trainer-paired global-mean
   variant) so the operator can verify the feature actually carries
   signal.
5. **Promotion is operator-gated.** Even a clean training run
   lands at `target_deployment_stage: candidate`. Promotion to
   `shadow` / `advisory` (the `shadow → advisory` step is the
   operator-approved live-influence switch) requires
   `python -m ml promote --by <name> --reason <text>` and
   operator approval. The registry is append-only; past
   `StatusEvent` entries are NEVER edited.

### Established baseline manifests

Pick the one that matches the task. Add new manifests rather than
editing these in place (operators rely on their `model_id`s as
stable identifiers in `model_registry/`).

| Manifest | Sprint | Trainer | Evaluator | What it learns |
|---|---|---|---|---|
| [`baseline-trade-outcome-winrate.yaml`](../../ml/configs/baseline-trade-outcome-winrate.yaml) | WS5-A | `PerStrategyWinRateTrainer` | `ClassificationEvaluator` | Per-strategy historical win rate against `won` on `trade_outcomes`. |
| [`baseline-trade-outcome-global.yaml`](../../ml/configs/baseline-trade-outcome-global.yaml) | WS4-FU | `ConstantPredictionTrainer` | `ClassificationEvaluator` | Global-mean sanity baseline on `trade_outcomes` (paired sibling to the winrate manifest). |
| [`baseline-regime-classifier.yaml`](../../ml/configs/baseline-regime-classifier.yaml) | WS5-B-PART-2 PR 2B | `RegimeClassifierTrainer` | `MulticlassClassificationEvaluator` | 3-class regime label (trend / range / volatile) on `market_features` with `vol_bucket` as feature. |
| [`baseline-setup-quality.yaml`](../../ml/configs/baseline-setup-quality.yaml) | WS5-C | `PerStrategyWinRateTrainer` (numeric_mean) | `RegressionEvaluator` | Per-`setup_type` mean R-multiple on `setup_labels`. |
| [`baseline-setup-quality-audit.yaml`](../../ml/configs/baseline-setup-quality-audit.yaml) | WS5-C-FU | `PerStrategyWinRateTrainer` (numeric_mean) | `RegressionEvaluator` | Per-`audit_pattern` mean R-multiple on `setup_labels_audit` (audit-joined source — paired comparison against the v1 `setup_type` baseline). |
| [`baseline-execution-quality.yaml`](../../ml/configs/baseline-execution-quality.yaml) | WS5-D | `PerStrategyWinRateTrainer` (numeric_mean) | `RegressionEvaluator` | Per-strategy mean entry slippage (bps, signed; positive = trader paid worse) on `execution_quality` (trades ↔ order_packages join). |
| [`baseline-post-trade-review.yaml`](../../ml/configs/baseline-post-trade-review.yaml) | WS5-E | `PerStrategyWinRateTrainer` (numeric_mean) | `RegressionEvaluator` | Per-`setup` mean reviewer decision-grade score (A=4..F=0) on `review_journal` (parses health-review JSON from answered comms requests). |
| [`baseline-prop-mission-policy.yaml`](../../ml/configs/baseline-prop-mission-policy.yaml) | WS5-F | `PerStrategyWinRateTrainer` (binary) | `ClassificationEvaluator` | Per-strategy acceptance rate (was_taken) on `account_context` (trades JOIN `config/accounts.yaml` for prop-typed accounts; rejected signals are labelled negatives). |

When a baseline lands clean and the operator wants the
"compare-against-marginal" sanity check, ship a paired global-mean
manifest alongside it (the WS5-A winrate + WS4-FU global pair is
the reference example).

## End-to-end demo

```
python -m ml.datasets build trade_outcomes \
  --output-dir ./datasets-out --version v001 \
  --source trade_journal.db -- db_path=/path/to/trade_journal.db

python -m ml train ml/configs/baseline-trade-outcome-winrate.yaml \
  --datasets-root ./datasets-out
python -m ml train ml/configs/baseline-trade-outcome-global.yaml \
  --datasets-root ./datasets-out

python -m ml compare \
  trade-outcome-winrate-baseline-v0 \
  trade-outcome-global-baseline-v0
```

### Regime classifier demo (PR 2B)

```
# 1. Build market_raw bars (CSV adapter shown).
python -m ml.datasets build market_raw \
  --output-dir ./datasets-out --version v001 \
  --source ./bars.csv \
  --symbol-scope BTCUSDT --timeframe 1h \
  -- adapter=csv csv_path=./bars.csv

# 2. Derive market_features.
python -m ml.datasets build market_features \
  --output-dir ./datasets-out --version v001 \
  --source ./datasets-out/market_raw/BTCUSDT/1h/v001 \
  --symbol-scope BTCUSDT --timeframe 1h \
  -- market_raw_path=./datasets-out/market_raw/BTCUSDT/1h/v001 \
     vol_window_n=20 forward_window_m=5

# 3. Train + evaluate the 3-class baseline.
python -m ml train ml/configs/baseline-regime-classifier.yaml \
  --datasets-root ./datasets-out
```

### Setup-quality scorer demo (WS5-C)

```
# 1. Build setup_labels from trade_journal.db.
python -m ml.datasets build setup_labels \
  --output-dir ./datasets-out --version v001 \
  --source trade_journal.db \
  -- db_path=/abs/path/to/trade_journal.db \
     risk_pct=1.0 r_cap=3.0

# 2. Train + evaluate the per-setup_type R-multiple baseline.
python -m ml train ml/configs/baseline-setup-quality.yaml \
  --datasets-root ./datasets-out
```

## Out of scope (deferred)

- Aggregated walk-forward for the plain `walk_forward` strategy. (The
  multi-fold averaged path now exists for `purged_walk_forward` — S-MLOPT-S1.)
- Per-strategy detail metrics artifact alongside scalar metrics.
- Registry concurrent-writer locking.
- `python -m ml.datasets publish` HF subcommand.

## Update rule

Review this doc in the same PR as any change to the manifest
schema, trainer / evaluator / predictor contracts, runner pipeline,
split strategies, registry status state machine / promotion gates,
or the CLI surface.
