# Sprint Log: S-MLOPT-S15b

## Date Range
- Start: 2026-06-04
- End: 2026-06-04

## Objective
- Primary goal: **regime-router Phase 3.3 track B** — wire the existing
  volatility-regime classifier as a **second, observe-only** regime axis so the
  router logs a 2-D `trend × vol` would-gate (no enforcement) and accrues
  evidence for a later Tier-3 decision. The companion to S15a (the trend-axis
  model); together they close the taxonomy half of `MB-20260601-002`.
- **Decision — vol-axis taxonomy:** 2-class `calm` / `volatile` (matches the
  existing classifier's `range`/`volatile` 2-class scheme since
  `S-ML-REGIME-CLASSIFIER-FIX`), NOT the 3-bucket vol levels — the operator's
  recommended option.
- **Observe-only / Tier-2:** no order-path effect. Mirrors trend-axis phases
  1+2 (stamp + shadow-gate-log). Phase-3 enforcement stays Tier-3.

## Tier
- **Tier 2** — touches the live signal-builder stamp path + the intent layer's
  shadow-gate logger, but is observe-only by construction (no order/risk reach).
  Draft PR opened for one operator OK before merge+deploy.

## Starting Context
- S13 (per-bar regime scoring) merged + verified live; S14 (HMM) negative;
  S15a (trend-regime model, research_only) shipped — PRs #2778, #2780.
- The 1-D **trend** axis (ADX-14 detector + `regime_policy.yaml`) already runs
  observe-only (phases 1+2): `_stamp_regime_on_meta` tags `regime`/`adx_14`
  onto `signal.meta`; `intent_from_signal` lifts them onto `StrategyIntent`;
  `aggregate_intents._shadow_regime_gate` logs a `regime_shadow_gate` audit row
  for any `off` cell. S15b adds a parallel **vol** axis to each of those three.

## Files and Systems Inspected
- `src/runtime/regime/{detector,policy,__init__}.py`, `src/runtime/regime_shadow.py`
  (the frozen-edge bucketing math), `src/runtime/regime_bar_scoring.py` (how S13
  resolves shadow-stage regime specs from the registry), `src/runtime/intents.py`
  (`StrategyIntent` + `_shadow_regime_gate`), `src/runtime/strategy_signal_builders.py`
  (the 11 `_stamp_regime_on_meta` call sites), `ml/datasets/families/market_features.py`
  + `ml/trainers/lightgbm_multiclass.py` (the `vol_bucket` 2-class collapse +
  the `freeze_regime_spec` edges), `config/regime_policy.yaml`.

## Work Completed
- **`src/runtime/regime/vol_detector.py`** (new) — the vol-axis detector,
  parallel to the ADX `detector.py`. `detect_vol_regime(candles, *, symbol,
  timeframe, specs=None)` → `{vol_regime, rolling_log_return_vol, source}`.
  - Sources the calm/volatile boundary from the **deployed regime head's frozen
    `vol_bucket` edges** (resolved once per process from the registry's
    shadow-stage specs — the same `regime_spec_of` source S13's per-bar path
    uses — cached in `_VOL_SPEC_CACHE`; never hand-copied edge values to drift).
  - Reuses `regime_shadow.{closes_from_candles, rolling_log_return_vol,
    bucket_for_vol}` verbatim so the serve-time vol is computed identically to
    the model's feature row.
  - **2-class collapse:** lowest frozen bucket → `calm`, every higher bucket →
    `volatile` (i.e. `rolling_log_return_vol <= edges[0]`); handles 2- and
    3-bucket specs uniformly (`market_features` doc: `vol_b0 → range`,
    `vol_b1/b2 → volatile`).
  - **Parity guard:** only adopts edges from a head whose frozen
    `vol_feature_column` is `rolling_log_return_vol` (the close-to-close value we
    compute). The **yz** heads (`yang_zhang_vol`) are skipped so their edges
    can't mis-place the boundary — a (symbol, timeframe) served only by yz heads
    stays `unknown` (permissive) rather than wrongly labelled.
  - Never raises; degrades to `vol_regime="unknown"` (permissive) on no spec /
    no candles / any failure — so the live tick is unchanged when unresolvable.
- **2-D policy schema** — `src/runtime/regime/policy.py::would_gate` gains an
  optional `vol_regime=` param. **Default-preserving:** with `vol_regime=None`
  the return is byte-identical to the pre-S15b 1-D shape (no `vol_*` keys). With
  it supplied, the verdict is augmented with observe-only
  `{vol_regime, vol_gated, vol_cell, vol_reason}` from the optional `trend_vol`
  block (`policy["trend_vol"][regime][vol_regime][strategy][side]`), same cell
  semantics + permissive-default rule as the 1-D blocks. The 1-D `gated`
  decision is never altered by the vol axis. New `_evaluate_trend_cell`
  (the unchanged 1-D body) + `_evaluate_vol_cell` helpers.
- **`config/regime_policy.yaml`** — `schema_version 1 → 2`; added a documented
  but **EMPTY** `trend_vol: {}` block. Authoring an `off` cell requires a
  vol-split of the regime-roster matrix (evidence accruing now); left empty so
  the vol axis is fully permissive until the operator authors cells — no
  fabricated trading decisions. Authoring a 2-D cell is, like the 1-D table, a
  Tier-3 decision.
- **`StrategyIntent.vol_regime`** field (Optional, default None) +
  `intent_from_signal` lifts `meta["vol_regime"]` (parallel to `regime`/`adx_14`).
- **`_stamp_regime_on_meta`** now also stamps `vol_regime` +
  `rolling_log_return_vol` + `vol_regime_source` (gated on `symbol`+`timeframe`,
  threaded from all 11 builder call sites).
- **`_shadow_regime_gate`** passes `intent.vol_regime` to `would_gate` and emits
  a `regime_shadow_gate` row when the trend cell OR the vol cell would gate;
  every row now carries both axes (`regime`/`vol_regime` + per-axis cell) so a
  later analysis can split would-gate evidence by volatility. Both axes
  `enforced: false`.

## Validation Performed
- Tests (all pass; ruff clean): `tests/runtime/test_vol_detector.py` (15 — collapse,
  degeneracy guards, routing, never-raises, `resolve_vol_specs` degradation),
  `tests/test_regime_policy.py` (+9 — default-preserving 1-D, 2-D off/on/permissive,
  unknown axes, flat side, empty policy, shipped schema_version 2 + empty
  `trend_vol`), `tests/test_aggregate_intents_regime_shadow.py` (+7 — vol field on
  the intent, vol stamp lifted from meta, vol axis on the gate row, 2-D off fires a
  row independently of the trend axis, no-row when both permissive).
- Full regime/intent suite: 100 passed (vol_detector + policy + shadow-gate +
  shadow + detector + bar-scoring) and the builder suites unaffected
  (multi-strategy intents, symbol scope, delta dispatch, eval stamping, +
  vwap/trend/ict_scalp/mes/mgc/mhg builders): 169 passed.
- Default-preserving verified directly: `would_gate` with no `vol_regime`
  returns exactly `{gated, reason, cell, regime, strategy, side}`.

## Documentation Updated
- `ROADMAP.md` (S15b row), `docs/ml/optimization-roadmap.md` (Session 3.3 track B),
  `docs/claude/ml-review-backlog.json` (`MB-20260601-002` progress).
- `config/regime_policy.yaml` header documents the 2-D axis + the empty
  `trend_vol` block's schema and authoring rule.

## Risks and Follow-Ups
- **Enforcement is NOT wired** (by design). Authoring the 2-D `off` cells waits
  on a vol-split of the regime-roster matrix; the stamped `vol_regime` (on every
  intent + `regime_shadow_gate` row) is the evidence that will inform it. Payoff
  is shadow/backtest PnL over weeks, not an offline metric — no trainer eval
  dispatched.
- **Feature parity (`MB-20260604-005`)** still gates any regime head going live;
  the vol detector deliberately shares the same under-featured serve-time vol as
  the signal-time / per-bar shadow paths (close-to-close vol; the parity guard
  at least keeps the yz-head edge mismatch out).
- **Carried from S15a** (still open): (1) class-weight tuning to rescue
  transitional/trending on the trend model; (2) head-to-head trend-model vs
  ADX-14 forward-predictiveness — both prerequisites before the trend model
  could replace the ADX detector.

## Next Recommended Sprint
- Once ~2–4 weeks of stamped `vol_regime` + `regime_shadow_gate` rows accrue,
  vol-split the regime-roster matrix to find `(trend, vol, strategy, side)`
  cells where volatility flips the edge, and author the first `trend_vol` `off`
  cells (Tier-3). In parallel, the S15a trend-model follow-ups above.

## Wrap-Up Check
- [x] Code inspected directly, not inferred from summaries.
- [x] Docs reviewed + updated.
- [x] No order-path / pipeline-stage logic changed (stamp + shadow-gate-log
      only; observe-only; `gated`/`vol_gated` never reach the order path).
- [x] Roadmap status checked + updated.
- [x] Contradictions recorded (none new; the parity caveat is pre-existing
      `MB-20260604-005`).
- [x] Remaining unknowns: the vol-split matrix evidence (weeks); whether
      `calm`/`volatile` base rates per (symbol, tf) match the classifier's.
