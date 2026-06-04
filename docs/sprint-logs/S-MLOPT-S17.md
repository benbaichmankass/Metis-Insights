# Sprint Log: S-MLOPT-S17

## Date Range
- Start: 2026-06-04
- End: 2026-06-04

## Objective
- Primary goal: **train/serve feature parity for the regime heads** (Phase 4.2),
  closing `MB-20260604-005` — the gate that blocks promoting ANY regime head
  (the v2/yz vol heads, the S15a trend model, the S15b vol axis) past `shadow`.
- The live regime scoring row was under-featured: it served only
  `{vol_bucket, rolling_log_return_vol}` while the heads train on the full
  `market_features` row (the four range-vol estimators + log-return + its two
  lags + hour/day-of-week). Worse, the **yz heads** froze `vol_bucket_edges` in
  `yang_zhang_vol` units but the live path bucketed close-to-close
  `rolling_log_return_vol` against them → wrong bucket.

## Tier
- **Tier 2** — changes a shared live-path function (`feature_row_for_predictor`,
  consumed by both the signal-time `_emit_shadow_preds` and the per-bar
  `regime_bar_scoring`). **Observe-only**: the shadow path only appends to the
  prediction log; no order/risk reach. Draft PR for one operator OK before
  merge+deploy.

## Starting Context
- S13 (per-bar regime scoring) live; S15a (trend model, research_only) +
  S15b (vol axis, observe-only) merged + deployed this session.
- `MB-20260604-005` (Phase 4.2) is the common unblock: every regime head's
  shadow track record is currently computed under the under-featured row, so it
  can't be the sole basis for a `shadow→advisory` promotion.

## Files and Systems Inspected
- `src/runtime/regime_shadow.py` (the live feature-row builder),
  `src/runtime/regime_bar_scoring.py` + `src/runtime/strategy_signal_builders.py`
  (the two callers), `ml/datasets/families/market_features.py` (the parity
  **oracle** — exact per-bar window `[s..i]`, `s=i-vol_window_n+1`, the four
  estimators sqrt'd, lags, time features, and the `vol_bucket` derivation),
  `ml/trainers/lightgbm_multiclass.py` (the `freeze_regime_spec` edge
  reconstruction — edges are the per-bucket max of `vol_feature_column`),
  `ml/datasets/volatility_estimators.py` (the S9 estimators, reused live),
  `src/runtime/market_data.py` (live candle frame: `[timestamp, o,h,l,c,v]`,
  epoch-ms / datetime timestamps).

## Work Completed
- **`src/runtime/regime_shadow.py` — full parity feature row:**
  - `ohlc_from_candles` (front-truncated, aligned O/H/L/C; duck-typed like
    `closes_from_candles`), `log_return_series`, `range_vol_estimators`
    (the four S9 estimators over the trailing `vol_window_n` bars, with each
    window bar's prior close for Yang-Zhang's overnight term — mirrors the
    builder's `[s..i]` window exactly), `_bar_hour_dow` / `_latest_bar_hour_dow`
    (epoch-ms/sec, datetime, or ISO timestamp → UTC `(hour, weekday)`),
    `build_parity_feature_row` (assembles the superset).
  - **`feature_row_for_predictor` rewrite:** when `candles_df` is present
    (both live callers now pass it), emits the full `market_features` superset
    (`rolling_log_return_vol` + the four range-vol estimators + `log_return` +
    `log_return_lag_1/2` + `hour_of_day`/`dayofweek`) and **buckets
    `vol_bucket` against the value of the estimator named by the head's frozen
    `vol_feature_column`** — so the v2 heads bucket `rolling_log_return_vol` and
    the **yz heads bucket `yang_zhang_vol`** (the fix). Skip-condition parity
    preserved (drop a bar whose `rolling_log_return_vol` is None, like the
    builder). Falls back to the pre-S17 close-only shape when `candles_df` is
    absent (older callers / tests) → backwards-compatible.
  - Side-stream features (funding/microstructure/macro) are **not** emitted —
    no live regime head selects them, and a no-side-stream training build emits
    them as `0.0` anyway. Documented in-module.
- **Callers** `_emit_shadow_preds` (signal-time) + `emit_regime_bar_predictions`
  (per-bar) now pass `candles_df=` so both score on the parity row (per-bar ==
  signal-time stays true).
- The S15b `vol_detector` parity guard (skips yz heads) is **kept** — that
  detector only computes close-to-close vol for the calm/volatile 2-D axis, a
  separate construct from the head scoring path.

## Validation Performed
- **Parity test against the builder oracle** (`tests/runtime/test_regime_shadow_parity.py`):
  stages a synthetic `market_raw`, runs `MarketFeaturesBuilder`, and asserts the
  live row equals the builder's row **bit-for-bit** (rel 1e-9) for
  `rolling_log_return_vol` + the four estimators + `log_return` + both lags +
  `hour_of_day`/`dayofweek`; a second test proves the **yz fix** (an edge placed
  between the live YZ and rolling vols → the bucket follows YZ, not rolling);
  plus the legacy close-only fallback + non-regime passthrough.
- Suite: 176 passed across regime_shadow (+parity), regime_bar_scoring,
  vol_detector, policy, aggregate-intents shadow gate, market_features, and the
  signal-builder wiring tests. Ruff clean.

## Documentation Updated
- `ROADMAP.md` S17 row; `docs/ml/optimization-roadmap.md` Session 4.2;
  `docs/claude/ml-review-backlog.json` (`MB-20260604-005` → resolved-pending-deploy).

## Risks and Follow-Ups
- **Deploy-gated track record:** the shadow track record accrued BEFORE the S17
  deploy was under-featured; promotion reads should use the record accrued
  AFTER it. Re-deploy (`pull-and-deploy`) once merged.
- **Timestamp UTC assumption:** `hour_of_day`/`dayofweek` parity assumes the
  live candle timestamp is the bar's UTC open time (true for Bybit epoch-ms;
  IBKR datetimes are normalised to UTC). A non-UTC connector would shift the
  time features — revisit if a new connector lands.
- **yz `vol_bucket` is an approximation by construction:** the frozen yz edges
  are the per-bucket max of `yang_zhang_vol` where the bucket was *defined* by
  `rolling_log_return_vol` quantiles, so the yz→bucket map is monotonic-ish, not
  exact. This is the best faithful reproduction of the trained `vol_bucket`
  without re-deriving quantiles live; the v2 heads (`vol_feature_column =
  rolling_log_return_vol`) are exact.
- **Now unblocked:** `shadow→advisory` promotion for the strong heads
  (btc-regime-1h, mes-regime-1d) + the S15b vol axis can proceed on the
  post-deploy track record (still operator-gated, Tier-3).

## Next Recommended Sprint
- After re-deploy, let the parity-correct track record accrue, then `/ml-review`
  the strong heads for a `shadow→advisory` promotion proposal. In parallel: the
  S15a trend-model class-weight tuning + head-to-head vs ADX-14.

## Wrap-Up Check
- [x] Code inspected directly (the builder is the parity oracle, matched line-by-line).
- [x] Docs reviewed + updated.
- [x] No order-path / pipeline-stage logic changed (shadow scoring feature row
      only; observe-only).
- [x] Roadmap status checked + updated.
- [x] Contradictions recorded (none new; closes the pre-existing `MB-20260604-005`).
- [x] Remaining unknowns: post-deploy track-record quality; the yz-bucket
      approximation's effect on the yz heads' separation (measure via /ml-review).
