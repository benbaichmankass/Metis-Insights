# Sprint Log: S-MLOPT-S6

## Date Range
- Start: 2026-06-03
- End: 2026-06-03

## Objective
- Primary goal: the de-Prado-correct **meta-labeling** decision model — given a
  candidate setup + its signal-time features, predict **whether to act**
  (P(profitable)). The decision model the data wall (G4) was blocking: replaces
  `setup-quality-lgbm-v2`, which lost to a per-group-mean baseline at n=80
  (MB-20260527-003) — proven mechanically by the S-MLOPT-S4 gate.
- Success: beats the baseline on a held-out set of **REAL live trades** (never on
  synthetic rows) — the domain-shift discipline the S5 dataset reserved.

## Tier
- **Tier-1** for the trainer/family/splitter (additive, read-only): the
  meta-label model reuses the existing `LightGBMRegressionTrainer`; the
  `live_trades_db` source + `live_holdout` split are dataset/eval tooling. No
  `src/runtime/`, order-path, or live file touched.
- **Tier-3** for the manifest (`ml/configs/setup-candidates-metalabel-v1.yaml`)
  and any promotion past `shadow` — operator-gated. The model ships at
  `research_only`; this sprint **proposes**, the operator promotes. The S6 PR is
  a **draft and is NOT auto-merged** (unlike the Tier-1-only S4/S5).

## Starting Context
- M14 Phase 1.2, directly on top of S-MLOPT-S5 (the `setup_candidates`
  triple-barrier dataset, 15.7k BTC / 6.7k MES candidates). Uses S-MLOPT-S1
  (purged WF-CV) for the leak-free within-distribution check and S-MLOPT-S4
  (`gate-check` oos_edge) for the baseline comparison.
- Key realization from reading the stack: `ClassificationEvaluator` is built to
  score a **regression** predictor's output as a probability (it clamps to
  `[0,1]` for Brier), so meta-labeling needs **no new trainer** — the existing
  `LightGBMRegressionTrainer` on the binary `won` target *is* the de-Prado
  probability model. The novel work is the **real-trade holdout**.

## Files and Systems Inspected
- `ml/trainers/{lightgbm_regression,lightgbm_multiclass,constant_baseline}.py`,
  `ml/evaluators/classification.py` (regression-output-as-probability),
  `ml/experiments/{splitters,runner}.py` (single-split dispatch via `split()`),
  `ml/datasets/families/{setup_candidates,setup_labels,trade_outcomes}.py`
  (real-trade DB read), `ml/datasets/cli.py` (`key=value` builder kwargs),
  `ml/configs/setup-quality-lgbm-v2.yaml` (the stack being retargeted).

## Work Completed
- **`live_holdout` split strategy** (`ml/experiments/splitters.py`) — partitions
  on the `is_live_trade` flag: train = synthetic rows, eval = REAL trades. Raises
  if either population is empty (can't certify domain transfer without both).
  Wired into the single-split `split()` dispatch.
- **Real-trade source in `setup_candidates`** (`live_trades_db` kwarg) — appends
  every REAL closed (non-backtest, non-demo) trade for the symbol: located at the
  bar covering its entry time (`bisect`), emitted in the **same past-only feature
  space** as the synthetic candidates, with its **actual** realized outcome
  (`won` from `pnl`), tagged `barrier_touched: "live"` + `is_live_trade: true`.
  `include_synthetic=false` emits only the real rows. Refactored the shared
  feature extraction into `_feature_fields` so synthetic + real rows are
  feature-identical (the holdout is comparable).
- **Meta-label manifest** `ml/configs/setup-candidates-metalabel-v1.yaml`
  (Tier-3 proposal) — `LightGBMRegressionTrainer` → `won`, `ClassificationEvaluator`,
  `split_strategy: live_holdout`, signal-time features only (outcome columns in
  `forbidden_features`), `target_deployment_stage: research_only`.
- **Tests** (`tests/ml/test_metalabel.py`, additions to
  `tests/ml/test_setup_candidates.py`): live-holdout partition + missing-population
  guards + `split()` dispatch; real-trade append (located + labeled from a stub
  DB) + `include_synthetic=false`; manifest validity (no outcome leak into
  features); **end-to-end runner drive** of `live_holdout` with a real
  trainer+evaluator (constant baseline, no LightGBM dependency).

## Validation Performed
- `tests/ml/test_metalabel.py` + `tests/ml/test_setup_candidates.py` +
  splitters/triple-barrier → 50 passed. Full `tests/ml/` (ex. the 2 pandas-only
  modules) → **501 passed, 1 skipped**. `ruff` clean; manifest loads via
  `TrainingManifest.from_yaml`.
- **No-leakage**: features are past-only for both synthetic and real rows (the
  real trade reuses the same `_feature_fields` at its entry bar); the manifest's
  `forbidden_features` excludes every outcome/label column; the trainer's
  fit-time leakage gate enforces it.
- **Trainer-VM eval** (build synthetic + REAL-trade holdout for BTCUSDT → train
  the meta-label manifest → score on the real holdout vs the majority-class
  baseline): reported below.

## Trainer-VM meta-label eval — ⚠️ honest negative result (the discipline working)
Built `setup_candidates` for BTCUSDT (`market_raw` 1h/v002 → 15,732 synthetic) +
the REAL-trade holdout from the canonical journal
(`data/trade_journal.db`, the `$DATA_DIR` copy — **2320 trades**, not the empty
repo-root `trade_journal.db` a first attempt mis-pointed at; #2697), then trained
the manifest under `live_holdout` (train synthetic → score REAL trades).

| Population | n | win rate |
|---|---|---|
| synthetic candidates (train) | 15,732 | ~0.457 |
| **REAL BTCUSDT closed trades (eval holdout)** | **352** | **0.244** |

Meta-label model on the 352 real trades: **accuracy 0.670**, precision 0.266,
recall 0.198, f1 0.227, brier 0.219. **Majority-class baseline accuracy: 0.756.**

**Verdict — the model does NOT beat the baseline on the real holdout**
(0.670 < 0.756; precision 0.266 only marginally above the 0.244 base rate). So
**no promotion is proposed** — the manifest stays `research_only`. This is the
system working exactly as designed: the leak-free real-trade eval **blocks** a
model that would have looked fine measured only on synthetic data. The honest
reading:
- **Large domain gap.** Synthetic candidate win rate (~0.457) vs the real
  trades' (0.244) — the strategies' actual setups are a very different (and much
  harder, loss-majority) population than CUSUM momentum events. Naive
  synthetic→real transfer is insufficient.
- **Where the edge must come from next** (all already on the roadmap): wire the
  strategies' **actually-logged signals** as the candidate event source (the S5
  follow-up) so the train distribution matches real setups; **S-MLOPT-S7**
  (backtest-augmented real labels) to enlarge + diversify the real population;
  **S-MLOPT-S8** cross-symbol. The `live_holdout` machinery makes each of these
  measurable.
- **The deliverable stands:** the meta-labeling *model + the honest real-trade
  evaluation path* are built, tested, and demonstrably leak-free. The result it
  returns is a true "not yet," not a green light — which is the entire point of
  Phase 0/1 discipline.

## Documentation Updated
- `ROADMAP.md` S-MLOPT-S6 row; `docs/ml/optimization-roadmap.md` Session 1.2
  shipped-block; `docs/architecture/ai-model-platform.md` change-log; this log.

## Risks and Follow-Ups
- **Real holdout is small** (~tens of BTCUSDT closed trades) — honest but
  low-power. S-MLOPT-S7 (backtest-augmented per-trade labels) enlarges the real
  population; cross-symbol transfer (S8) adds MES. The `live_holdout` machinery
  is ready for both.
- **Two protocols, one manifest**: the manifest ships `live_holdout` (the
  domain-transfer headline). Flip `split_strategy` to `purged_walk_forward` for
  the leak-free within-distribution check + the S4 `gate-check` oos_edge — both
  belong in the promotion packet before any operator decision.
- **`r_multiple` for real rows is 0** (real stop distance not reconstructed here)
  — fine for the meta-label (`won`) target; a size-tilt regression on real
  `r_multiple` is a follow-up.
- **Tier-3 gate stands**: the manifest is a proposal; the model is `research_only`;
  promotion past `shadow` is operator-gated.

## Next Recommended Sprint
- **S-MLOPT-S7 (1.3)** — backtest-augmented per-trade labels (enlarges the real
  holdout; closes MB-20260530-001), or **S-MLOPT-S13 (3.1)** per-bar regime
  scoring (Tier-2, the highest-leverage regime-pipeline unblock).

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage / live-path file touched (manifest is a Tier-3 proposal).
- [x] Roadmap status was checked + updated.
- [x] Contradictions were recorded (none new).
- [x] Remaining unknowns were stated clearly.
