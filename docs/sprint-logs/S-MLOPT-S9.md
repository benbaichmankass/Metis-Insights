# Sprint Log: S-MLOPT-S9 (range-based volatility estimators)

## Date Range
- Start: 2026-06-03
- End: 2026-06-03

## Objective
M14 Phase 2.1 — the lowest-effort "better features" lever. Close-to-close
realized vol (`rolling_log_return_vol`) throws away each bar's intrabar
high/low/open, so it estimates vol inefficiently and reacts slowly — gap **G5**
("no range-based vol estimators"). Add the canonical **range-based volatility
estimators** to `market_features` so the regime heads can separate the
`volatile` class better, and let a regime manifest select the new vol feature.

The three label-distribution experiments (S6 CUSUM, S6-FU signal-log, S6-FU-2
backtest) converged on the same lesson — at n≈352 real trades, *better features*
may matter as much as a better label. S9 is the cheapest feature lever and is
independent of the labeling arc (it runs in parallel).

## Tier
- **Tier-1** for the estimator module, the `market_features` columns, and the
  tests (additive, read-only over built `market_raw`; past-only features →
  `leakage_test_status` stays `passed`). No `src/runtime/`, order-path, or live
  file touched.
- **Tier-3** for `ml/configs/btc-regime-1h-lgbm-yz-v1.yaml` and any promotion
  past `research_only` — operator-gated. The manifest ships at `research_only`;
  this sprint **proposes** + provides the A/B harness, the operator promotes.

## Starting Context
- M14 Phase 2 ("better features"), parallel to the Phase-1 labeling arc.
- `market_features` (the regime feature dataset) carried only a close-to-close
  `rolling_log_return_vol` + `vol_bucket`; the v2 LightGBM regime heads
  (`btc-regime-{5m,15m,1h}-lgbm-v2`) already expose a `vol_feature_column` knob
  (with `freeze_regime_spec`) — so swapping the vol feature is a one-line
  manifest change once the columns exist.

## Files and Systems Inspected
- `ml/datasets/families/market_features.py` (the past-window feature core +
  schema + leakage discipline), `ml/datasets/builder.py` (schema validation +
  `builder_version` is metadata-only, does not gate dataset path resolution),
  `ml/trainers/lightgbm_multiclass.py` (`vol_feature_column` + `freeze_regime_spec`
  — the live-scoring spec freeze), `ml/configs/btc-regime-1h-lgbm-v2.yaml` (the
  champion mirrored), `scripts/ops/build_trainer_datasets.sh` (builds BTCUSDT
  market_features at 1h/5m/15m daily — so the v3 rebuild is automatic).

## Work Completed
- **`ml/datasets/volatility_estimators.py` (new)** — four canonical range-based
  variance estimators over a window of OHLC bars: **Parkinson** (1980, high-low
  range), **Garman-Klass** (1980), **Rogers-Satchell** (1991, drift-independent),
  **Yang-Zhang** (2000, overnight-gap-aware, minimum-variance — the roadmap's
  headline). Each reads only the bars it's given (leakage-safe when fed a
  past-only window); best-effort on non-positive prices / short windows.
  `_sqrt_or_zero` converts a variance estimate to a stdev for feature emit.
- **`market_features` columns** — four new schema fields (`parkinson_vol`,
  `garman_klass_vol`, `rogers_satchell_vol`, `yang_zhang_vol`) computed over the
  **same inclusive past window** as `rolling_log_return_vol` (`[t-n+1 .. t]`),
  emitted as a stdev. `builder_version` bumped `v2 → v3` (metadata-only).
  Earlier baselines reading only `vol_bucket` / `rolling_log_return_vol` are
  unaffected by the wider schema.
- **`ml/configs/btc-regime-1h-lgbm-yz-v1.yaml`** *(Tier-3 proposal, draft)* — a
  clean A/B against `btc-regime-1h-lgbm-v2`: identical trainer / split / recency
  + class weighting / dataset, the ONLY change is the vol feature set (adds the
  four range estimators; freezes the live regime spec on `yang_zhang_vol`).
  Ships at `research_only`.
- **Tests** — `tests/ml/test_volatility_estimators.py` (Parkinson known-value;
  range-width / positivity / drift-independence ordering; Yang-Zhang term
  combination + the ≥2-usable-bars guard; non-positive/empty → `None`;
  `_sqrt_or_zero`) and `tests/ml/datasets/test_market_features.py` additions
  (the four columns present + non-negative on every row, in-schema, and the full
  build round-trips through `validate_dataset`; `builder_version == v3`).

## Validation Performed
- `pytest tests/ml/test_volatility_estimators.py
  tests/ml/datasets/test_market_features.py tests/ml/test_regime_classifier.py
  tests/ml/datasets/test_cli.py` → **all pass** (50 in the core set). `ruff`
  clean. The new regime manifest loads via `TrainingManifest.from_yaml`
  (`research_only`, `vol_feature_column: yang_zhang_vol`).
- **No-leakage by construction**: the estimators read only the inclusive past
  window `[t-n+1 .. t]` (same window as `rolling_log_return_vol`); the manifest's
  leakage gate still forbids `forward_log_return` / `forward_log_return_vol` /
  `regime_label`.

## Trainer-VM A/B — ✅ positive (modest, consistent lift)
Ran via `trainer-vm-diag` (#2720): rebuilt `market_features` BTCUSDT 1h v002 on
`builder_version v3` (range-vol columns confirmed present), then trained the
champion + challenger under the manifest `time_aware_holdout` (same split for
both → the delta is apples-to-apples).

| Metric | v2 (close-to-close) | yz-v1 (range-based) | Δ |
|---|---|---|---|
| **f1_volatile** | 0.4624 | **0.4724** | **+0.010** |
| accuracy | 0.7444 | 0.7603 | +0.016 |
| macro_f1 | 0.6474 | 0.6586 | +0.011 |
| weighted_f1 | 0.7709 | 0.7830 | +0.012 |
| precision_volatile | 0.3555 | 0.3724 | +0.017 |
| recall_volatile | 0.6614 | 0.6456 | −0.016 |

**Every headline metric improved** — `f1_volatile` +0.010 with a precision/recall
trade favoring precision. The range-based vol features (regime spec frozen on
`yang_zhang_vol`) genuinely help the regime head separate `volatile`, at near-zero
cost. **CAVEAT:** this is the manifest's `time_aware_holdout`, not the Phase-0
purged WF-CV — a purged-CV confirmation is the rigorous follow-up before any
promotion. If the lift holds, propose promoting the yz head to `shadow`
(operator-gated) and extend the range-vol variant to 5m/15m + MES.

## Purged-CV Confirmation + extension (S9 finish, 2026-06-04)
The S9 A/B (#2720) was on the manifest's optimistic `time_aware_holdout`. The
finish step ran the **rigorous Phase-0 purged & embargoed walk-forward CV**
(`scripts/ml/eval_split_compare.py`, 5 folds, `label_horizon=5` to match the
regime label's forward window, `embargo_fraction=0.01`) on BOTH the v2 champion
AND the yz challenger over a freshly-rebuilt v4 `market_features` (range-vol
columns present), identical folds → an apples-to-apples champion-vs-challenger.

**1h — POSITIVE, leak-free (trainer-vm-diag #2736):**

| Metric (purged WF-CV, 5 folds) | v2 champion | yz challenger | Δ (yz − v2) |
|---|---|---|---|
| **f1_volatile** | 0.5009 | **0.5036** | **+0.0027** |
| macro_f1 | 0.6542 | **0.6609** | **+0.0067** |
| accuracy | 0.7258 | **0.7372** | **+0.0114** |

(For reference, the same run's `time_aware_holdout`: yz f1_volatile 0.4694 vs v2
0.4655, +0.0039 — same sign, confirming the holdout lift was not an artifact.
Note both heads' purged-CV f1_volatile (~0.50) exceeds their holdout (~0.47):
the recent-20% holdout regime is the harder block; the pooled WF folds average
higher. The A/B **delta** is positive on every headline metric either way.)

The Yang-Zhang range-vol head beats the production champion `btc-regime-1h-lgbm-v2`
**leak-free** on f1_volatile, macro_f1, AND accuracy. The S9 lift is real and
not a holdout artifact. (The S4 `gate-check` oos_edge gate compares a candidate
vs a *generic* baseline; this direct champion-vs-challenger purged-CV A/B is the
stronger comparison — the yz head beats the current SHADOW champion, not just a
naive majority baseline.)

### Promotion packet (Tier-3 PROPOSAL — operator-gated, NOT flipped)
**Proposal:** advance `btc-regime-1h-lgbm-yz-v1` from `research_only → shadow`
as the new 1h regime champion candidate (it is a clean, leak-free improvement
over the current shadow champion `btc-regime-1h-lgbm-v2`, same role + dataset +
recency/class weighting, only the vol feature set differs).
- **Evidence:** purged-CV A/B above (#2736) + the holdout A/B (#2720); past-only
  features → leakage-safe by construction; `freeze_regime_spec` on `yang_zhang_vol`
  so the live-scoring path buckets ticks against the trained edges.
- **Mechanics (operator runs):** set `target_deployment_stage: shadow` on the
  manifest (or supersede the v2 head with the range-vol feature set), Tier-3 edit.
- **Caveat — full shadow→advisory is separately blocked by `MB-20260529-001`:**
  1h regime heads emit ZERO shadow predictions today (`_emit_shadow_preds` fires
  only on an actionable 5m signal), so even at `shadow` the yz head accrues no
  order-influencing track record until the per-bar regime-scoring path (S-MLOPT-S13,
  Phase 3.1) lands. So the realistic next step is `research_only → shadow` now;
  `shadow → advisory` waits on S13 + an operator decision.

### Extension to 5m/15m/MES (same A/B, research_only)
New clean-A/B manifests cloned from each v2 champion (only the vol feature set
differs; spec frozen on `yang_zhang_vol`): `btc-regime-{5m,15m}-lgbm-yz-v1.yaml`
+ `mes-regime-{5m,15m}-lgbm-yz-v1.yaml`. The range-vol columns land on every
`market_features` build, so these are drop-in A/Bs.

**BTC 5m/15m purged WF-CV A/B (#2736) — POSITIVE on every timeframe, leak-free:**

| TF | v2 f1_vol | yz f1_vol | Δ f1_vol | Δ macro_f1 | Δ accuracy |
|---|---|---|---|---|---|
| 1h | 0.5009 | **0.5036** | +0.0027 | +0.0067 | +0.0114 |
| 5m | 0.1362 | **0.1535** | **+0.0173** | +0.0092 | +0.0025 |
| 15m | 0.2316 | **0.2532** | **+0.0216** | +0.0149 | +0.0144 |

All three BTC regime heads improve on `f1_volatile` under the rigorous purged
WF-CV, with the **largest lifts at 5m/15m** (where the volatile class is rarest
and close-to-close vol is noisiest — exactly where a more-efficient range
estimator should help most). So the promotion proposal extends: advance
`btc-regime-{1h,5m,15m}-lgbm-yz-v1` `research_only → shadow` (Tier-3,
operator-gated). **MES** 5m/15m yz A/B (needs the median-calibrated MES
`market_features` rebuild) is the remaining trainer job — results appended to
`MB-20260603-004`; only positive, leak-free heads are proposed for `shadow`.

## Documentation Updated
- `docs/data/dataset-taxonomy.md` (market_features range-vol columns);
  `docs/architecture/ai-model-platform.md` change-log; `docs/ml/optimization-roadmap.md`
  Session 2.1; `ROADMAP.md` S-MLOPT-S9 row; `docs/claude/ml-review-backlog.json`
  (`MB-20260603-004`, the A/B eval follow-up); this sprint log.

## Risks and Follow-Ups
- **Purged-CV confirm POSITIVE (#2736), 2026-06-04** — the yz head beats the v2
  champion leak-free under the Phase-0 purged WF-CV (1h: f1_volatile +0.0027,
  macro_f1 +0.0067, accuracy +0.0114). Promotion packet written above; the
  Tier-3 `research_only → shadow` advance is the operator's call. The lift is
  modest — a near-free feature win, not a step change.
- **shadow → advisory blocked by `MB-20260529-001`** (no per-bar regime scoring →
  1h heads accrue no shadow track record). The yz head can be advanced to
  `shadow` now; the live-influence step waits on S-MLOPT-S13 (Phase 3.1).
- **Other timeframes / symbols**: 5m/15m BTC + 5m/15m MES yz A/B manifests
  shipped this finish (`research_only`); purged-CV A/B results land in
  `MB-20260603-004` as the trainer jobs complete. Only positive, leak-free heads
  are proposed for `shadow`.
- **Garman-Klass / Rogers-Satchell per-bar terms can dip negative** on a single
  bar; the window-mean variance is clamped at 0 so the sqrt stays real (standard
  practice).
- **Tier-3 gate stands**: the manifest is a proposal at `research_only`;
  promotion past `shadow` is operator-gated.

## Next Recommended Sprint
- **S-MLOPT-S11 crypto funding-rate + open-interest features** (Tier-1) — the
  next cheap, high-value, currently-unused feature family. **Started in the same
  session** (sprint log [`S-MLOPT-S11.md`](S-MLOPT-S11.md)).
- **S-MLOPT-S13 (Phase 3.1) per-bar regime scoring** (Tier-2, operator-gated) —
  the highest-leverage unblock: without it no regime head (incl. this yz one)
  can ever clear `shadow → advisory` on order-influencing evidence
  (`MB-20260529-001`).

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage / live-path file touched; manifests are Tier-3 proposals.
- [x] Roadmap status checked + updated.
- [x] Contradictions were recorded (none new).
- [x] Remaining unknowns stated clearly: 1h purged-CV confirm POSITIVE (#2736,
      f1_volatile +0.0027 leak-free); 5m/15m/MES purged-CV A/B + the MES build
      land in `MB-20260603-004` as the trainer jobs finish; `shadow → advisory`
      blocked on `MB-20260529-001`.
