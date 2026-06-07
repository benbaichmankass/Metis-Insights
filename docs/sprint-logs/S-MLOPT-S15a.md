# Sprint Log: S-MLOPT-S15a

## Date Range
- Start: 2026-06-07
- End: 2026-06-07

## Objective
- Primary goal: complete the two named S15a follow-ups for the trend-regime
  model `btc-regime-1h-lgbm-v1` (M14 Phase 3.3, `MB-20260601-002`):
  (1) **class-weight tuning** to rescue the starved `transitional` class, and
  (2) a leak-free **head-to-head vs the ADX-14 threshold detector** it would
  replace at the phase-4 regime-router seam.
- Success criterion: the tuned head forecasts the forward trend regime
  *usefully* — i.e. beats the ADX-14 base-rate detector under purged-WF-CV
  without a majority-class collapse. Stays `research_only` regardless (live
  wiring is Tier-3).

## Tier
- **Tier 1** trainer-side experiment (read + analyze on the trainer VM). The
  manifest edit (class_weight) keeps the model `research_only`, so it can never
  influence the order path; live phase-4 wiring remains a separate Tier-3 gate.
  Per the ml/configs gate the manifest change ships via an operator-gated PR.

## Starting Context
- S15a (PR #2780) had shipped the trend label (`trend_regime_label`, Kaufman
  efficiency ratio, market_features builder v7) + the research_only manifest.
  The first purged-CV A/B (#2787) was POSITIVE-but-modest (macro_f1 0.325 vs
  ~0.185 majority; weak on transitional f1 0.163), and the row explicitly
  deferred class-weight tuning + the ADX head-to-head.
- Operator chose this sprint from the "what's next that can be done now" menu.

## Files and Systems Inspected
- `ml/configs/btc-regime-1h-trend-lgbm-v1.yaml` (the manifest + documented eval
  command), `ml/datasets/labeling/trend_regime.py` (the ER label thresholds
  chop_max 0.30 / trend_min 0.55), `ml/trainers/lightgbm_multiclass.py`
  (`class_weight: {label: float}` → per-sample weights, multiplied with the
  recency `sample_weight`), `src/runtime/regime/detector.py`
  (`wilder_adx` + `regime_label`: ADX <20 chop / 20–25 transitional / ≥25
  trending), `scripts/ml/eval_split_compare.py` (holdout vs purged-WF-CV runner).
- Data: `datasets-out/market_features/BTCUSDT/1h/v002` (builder v7, 43,800 rows)
  + `datasets-out/market_raw/BTCUSDT/1h/v002` (OHLC for the ADX baseline).

## Work Completed
- **ADX-14 head-to-head (the rule-based bar to beat), trainer-vm-diag #2925.**
  Computed trailing Wilder ADX-14 on the raw 1h OHLC, mapped via the live
  `regime_label` thresholds, joined to the forward `trend_regime_label` by
  timestamp (43,800 aligned bars). **ADX-14 macro_f1 = 0.3167** (chop f1 0.336 /
  transitional 0.221 / trending 0.393). The untuned LGBM head (macro_f1 0.3243)
  only ties it and actually *loses* to ADX on transitional + trending.
- **Class-weight sweep (purged-WF-CV), trainer-vm-diag #2926.** Six settings via
  ephemeral manifests + `eval_split_compare`. Result table:
  ```
  variant   macro_f1  chop  trans trend rec_tr   (ADX-14 = 0.3167)
  baseline    0.3243 0.458 0.167 0.347 0.124
  invfreq     0.3403 0.393 0.276 0.353 0.286   ← BEST
  w_t2.0      0.3247 0.294 0.343 0.338 0.470
  w_t2.5      0.3030 0.209 0.365 0.335 0.561
  w_t3.0      0.2845 0.160 0.381 0.313 0.645
  w_t2_tr1    0.3148 0.335 0.363 0.246 0.549
  ```
  Mild inverse-frequency weights `{chop:1.0, transitional:1.45, trending:1.14}`
  win: transitional f1 0.167→0.276 (recall →0.286) without collapsing chop →
  **macro_f1 0.3403**, clearing ADX-14 by **+0.0236** and beating it on 2 of 3
  classes. Heavier weights over-correct (chop collapses; macro_f1 < baseline).
- **Baked the winning weights into the manifest** (`class_weight` block) +
  recorded the result in its notes. Stays `research_only`.

## Validation Performed
- Both experiments ran under the same leak-free **purged-WF-CV** the S1 splitter
  enforces (`--n-folds 5 --label-horizon 5 --embargo-fraction 0.01`); the ADX
  baseline is a trailing indicator scored against a future label, so the
  comparison is leakage-safe by construction.
- The label is future-only and excluded from features (the leakage gate already
  forbids `trend_regime_label` / `forward_log_return*` as features).

## Documentation Updated
- `ml/configs/btc-regime-1h-trend-lgbm-v1.yaml` — `class_weight` + result notes.
- `ROADMAP.md` — header refresh (2026-06-07) + S-MLOPT-S15 row S15a result.
- `docs/claude/ml-review-backlog.json` — `MB-20260601-002` evidence entry.

## Risks and Follow-Ups
- **Honest framing:** this is a *modest* win — both detectors sit ~0.32 macro_f1
  on a genuinely hard 3-class forward-regime problem (majority baseline ~0.185).
  The model beats ADX but is not strong in absolute terms.
- **Live phase-4 wiring stays Tier-3** and is NOT proposed here. Gates before it
  could replace ADX as the live trend detector: (a) a shadow track record, and
  (b) backtest/shadow **PnL** justification — macro_f1 alone is insufficient to
  put it on the order path. S15b's 2-D trend×vol observe-only gate accrues that
  evidence.

## Next Recommended Sprint
- S16 (ADWIN drift-triggered retraining) or S18 (champion-challenger promotion
  automation) — both Tier-1, NOT STARTED. Or, once the yz-1h soak completes
  (~6/12), run the shadow→advisory promotion packet under the new regime gate.

## Wrap-Up Check
- [x] Code/data inspected directly on the trainer, not inferred from summaries.
- [x] Docs reviewed + updated (manifest, roadmap, backlog).
- [x] No order-path / live-runtime change — manifest stays research_only.
- [x] Roadmap status checked + updated.
- [x] Honest modest-win framing recorded (not oversold).
