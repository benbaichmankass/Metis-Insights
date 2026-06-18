# Cross-asset strategies — technical scope / gap analysis (2026-06-18)

> **Scope doc, not a design.** Answers the operator's question: *"what is lacking
> right now to build strategies that trade asset A using other assets' data, how
> does data intake work and what has to change — so we know what we're getting
> into."* Grounded in a codebase investigation (file:line refs below). No build
> proposed here beyond the phased scope.

## TL;DR

The bot is **single-symbol at the strategy layer** but **already multi-asset and
leakage-safe in the ML layer**. Cross-asset strategies need **wiring, not new math**:
the hard problems (time-alignment across venues, no-lookahead joins) are *already
solved* in `ml/datasets/adapters/` and just need to be instantiated at runtime +
backtest. Feasibility: **high.** Hardest real problem: different trading hours
(crypto 24/7 vs futures/equities RTH) — a configuration problem, not research.

## 1. The current contract at each layer

| Layer | File | Today's contract |
|---|---|---|
| **Strategy unit** | `src/units/strategies/_base.py` (+ `trend_donchian.py`, `htf_pullback_trend_2h.py`, `ict_scalp.py`) | `order_package(cfg, candles_df)` / `monitor(cfg, candles_df, open_pkg)` — **ONE symbol's** OHLCV frame. No multi-symbol input. |
| **Live fetch** | `src/runtime/market_data.py` (`connector_for_symbol`, `fetch_candles`) + `strategy_signal_builders.py` | one `fetch_candles(symbol, tf)` per strategy per tick; symbol routed by `config/instruments.yaml`. `src/main.py::_resolve_tick_symbols` unions every account's symbols. |
| **Backtest** | `scripts/backtest_*.py::_load_candles` | ONE `--data` CSV, one in-memory DataFrame, single-symbol bar loop. |
| **ML features** | `ml/datasets/adapters/*` + `ml/datasets/families/market_features.py` | **already multi-source**: per-symbol `market_raw` rows joined `merge_asof` with lagged macro/funding side-streams. |

## 2. The one precedent that already does cross-asset (reuse this)

- `ml/datasets/adapters/yfinance_macro.py` + `ml/datasets/macro_features.py` — fetch
  VIX/DXY/rates at **daily cadence**, **lag one day** (a day-D feature is stamped at
  D+1 00:00 so an intraday bar never sees same-day close), join via `merge_asof`.
  Consumed by `mes-regime-5m-lgbm-macro-v1` (M14 S12). **This is the leakage-safe
  cross-asset template** — the exact discipline cross-asset strategies need.
- `ml/datasets/adapters/bybit_funding_oi.py` — multi-symbol funding/OI fetcher,
  as-of join, any symbol.
- `src/runtime/regime_bar_scoring.py` — proves **cost-controlled multi-symbol
  fetching works live** on the 2-core VM: group by `(symbol, timeframe)` (one fetch
  per group, not per consumer), wall-clock fetch gate, per-process client cache,
  per-tick budget. The cross-asset live fetcher copies this pattern.

## 3. What must change, layer by layer (the scope)

**A. Strategy unit contract (Tier-1, backward-compatible).** Add an optional
`context` kwarg: `order_package(cfg, candles_df, context=None)` where `context` is a
dict of *as-of, pre-lagged* cross-asset reads (`{"btc_regime": ..., "vix_z": ...}`).
Existing strategies ignore it → zero behavior change. A cross-asset strategy opts in
by reading `context` + declaring its inputs in config. (Mirrors how `modelScores`
already ride alongside the package.)

**B. Live context fetcher (Tier-2, new — `src/runtime/cross_asset_data.py`).** A
sibling of `regime_bar_scoring.py` that fetches each strategy's declared side-streams
**once per `(source,timeframe)` group**, applies the configured **lag**, caches, and
hands the signal builder a ready `context` dict. The builders pass it into
`order_package`. Cost is bounded by the proven grouping+gate+budget pattern.

**C. Backtest multi-asset loader (Tier-1, opt-in).** A `load_multi_asset_data(primary,
aux={...})` that time-aligns multiple CSVs with **`merge_asof`** (as-of, past-only)
and yields aligned bars; the harness passes the aux context into the unit exactly as
live does (so backtest == live). Single-symbol path unchanged.

**D. Live wrappers over the ML adapters (Tier-1, reuse).** Thin live-cache wrappers
around `bybit_funding_oi.py` / `yfinance_macro.py` so the runtime reads the *same*
lagged side-streams the trainer builds — guaranteeing the live feature == the
backtested/trained feature.

**E. Config (Tier-3) + boot validation (Tier-1).** `config/strategies.yaml` grows a
`cross_asset_inputs: {source, lag_bars, required}` block per opting-in strategy;
`validate_startup` refuses a config whose declared inputs aren't resolvable.

## 4. What is REUSED vs NEW

- **Reuse as-is:** the ML adapter pattern + leakage discipline (macro 1-day lag),
  the funding/OI + macro adapters, the `regime_bar_scoring` cost-control pattern,
  the entire gate/tier/robustness stack, `merge_asof`.
- **New (all small, additive):** the `context` kwarg, `cross_asset_data.py` (live
  fetch, ~mirrors regime scoring), the multi-asset backtest loader, the config
  block + validation. No ML-layer redesign; no new data sources required.

## 5. The hard problems (honest)

1. **Cross-venue trading hours** — a 5m MES bar at 09:35 ET has no synchronous 5m
   crypto bar in the same session; equities/futures are RTH, crypto is 24/7.
   *Mitigation:* config picks the cross-asset cadence (e.g. "1h BTC regime even on
   5m MES"); `merge_asof` forward-fills the last *closed* bar with a
   `max_staleness` guard; the strategy declares its tolerance. A config problem.
2. **Leakage** is the #1 trap — cross-asset features MUST be lagged (never the
   contemporaneous other-asset bar). *Mitigation:* enforce lag in the fetcher +
   adapter (already done in macro_features); purged-WF-CV catches accidents.
3. **Lead-lag is usually shared beta, not alpha** — correlated assets "leading" each
   other is mostly common-factor exposure that decays OOS. *Mitigation:* prefer the
   **beta-residual** feature ("A's move beyond what BTC explains") over raw
   "B up → A up"; require an out-of-pool holdout.
4. **Live fetch cost** on the 2-core money box. *Mitigation:* the proven
   group+gate+budget pattern; a per-tick `CROSS_ASSET_FETCH_BUDGET_S`.

## 6. Recommended order (do the cheap research answer first)

Per the cross-asset-features design discussion: **answer "do cross-asset predictors
add edge?" in the ML/feature layer first** (extend `market_features` with a lagged
`cross_asset` adapter → A/B a model with-vs-without via `gate-check` — this is exactly
the feature-ablation hook in the research-framework design). Only if a feature proves
predictive out-of-pool do we invest in the live strategy-unit + backtest wiring
(A–E above) to run it as a rule-based or advisory cross-asset strategy.

So: **research-framework (feature-ablation) → ML cross-asset feature probe → (if it
proves out) the live/backtest wiring scoped here.** The wiring is ~5 small PRs
(§3 A–E); none requires new research.
