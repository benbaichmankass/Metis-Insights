# Trend-Follower Go-Live Plan (S-STRAT-IMPROVE-S8, 2026-05-23)

> **Operator-approved (2026-05-23):** take the trend-follower live on the
> real-money account, turn vwap off real-money execution, and drop
> ict_scalp. Straight-to-real-execution chosen (no paper soak) — confirmed
> twice after I flagged the backtest-only/live-trailing-stop risk.
> **Tier-3.** Lands as draft PRs for operator merge; nothing executes
> until merged + `pull-and-deploy`.

## Decisions
- **trend_donchian** (validated: net +22.5R/3yr, robust plateau, fee-
  efficient — `docs/audits/complementary-trend-strategy-2026-05-23.md`)
  goes **live on bybit_2 (real money)**.
- **bybit_2 → `strategies: [trend_donchian]`, mode: live.** This removes
  vwap + ict_scalp from real-money execution in one clean account change.
- **vwap keeps collecting data on the paper accounts** (bybit_1 demo +
  ib_paper) — runs + logs + paper-executes there. **No per-strategy
  log-only gate needed** (avoids the order-path change + Prime-Directive
  "second gate" concern). If real-money-sized vwap logging is later
  wanted, add the gate then (recommended against — ample vwap data exists).
- **ict_scalp** dropped from real money (net ≈ breakeven — doesn't hold
  its weight; operator: "not married to it"). Harmless to leave logging
  on paper.

## Wiring checklist (per `.claude/skills/new-strategy`)
Single draft PR for the wiring (steps 1–4 + 7); separate draft PR for the
accounts.yaml activation (step 6).
1. `src/units/strategies/trend_donchian.py` — `order_package(cfg, candles_df)`
   porting the Donchian breakout + ATR stop from `scripts/backtest_trend.py`;
   `meta` carries trailing params for the monitor.
2. `src/runtime/strategy_signal_builders.py::trend_donchian_signal_builder`
   — fetch 1h candles, call order_package, honour `enabled`.
3. `src/runtime/pipeline.py` — import builder; add to `_STRATEGY_BUILDERS`
   + `STRATEGY_RISK_PCT`.
4. `src/runtime/intent_multiplexer.py::_default_intent_builders` +
   `src/runtime/intents.py::DEFAULT_PRIORITIES` (priority **20** — low, so
   a wiring slip can't override the roster).
5. **Live trailing-stop in `src/runtime/order_monitor.py`** — the
   Chandelier ATR trail (ratchet SL toward highest-high − trail×ATR),
   following turtle_soup's `trail_atr_mult` monitor pattern. **The
   error-prone, real-money-critical piece — implement + test carefully.**
6. `config/strategies.yaml::trend_donchian` block — `enabled: true`,
   `risk_pct` **conservative for the initial live period** (e.g. 0.3),
   donchian 20 / atr_stop 2.5 / trail 3.0 (the robust plateau center),
   `symbols: [BTCUSDT]`.
7. `config/accounts.yaml::bybit_2.strategies: [trend_donchian]` (separate
   draft PR — the activation).
8. Tests: `tests/test_trend_donchian.py` (order_package + trailing) + the
   intent regression suite.

## Safety mitigations (since straight-to-live on real money)
- **Low priority (20)** so a slip can't override anything at runtime.
- **Conservative initial `risk_pct`** (e.g. 0.3, revisit up after live
  proof).
- **Single-symbol invariant respected** — BTCUSDT only (matches the
  backtest + the intent layer's `SUPPORTED_SYMBOLS`).
- **Immediate post-deploy verification:** after `pull-and-deploy` +
  restart, pull the diag relay to confirm trend signals/order packages
  fire and the trailing stop updates as expected on the first live trades;
  watch the per-trade Telegram rejections/fills.
- Operator merges both Tier-3 PRs; activation is the final gated step.

## Status
- [x] Strategy module (`trend_donchian.py`) — order_package + Chandelier
      trailing-stop monitor
- [x] Signal builder + pipeline/intent registration (priority 20)
- [x] Live trailing-stop monitor — ratcheting Chandelier; reads the
      frozen entry-ATR + trail params from the package meta (the monitor
      tick passes `cfg={}`), ratchets the SL only in the favourable
      direction, and never places it on the wrong side of the current
      price (no instant stop-out)
- [x] config/strategies.yaml block (`enabled: true`, risk_pct 0.3,
      donchian 20 / atr_stop 2.5 / trail 3.0, BTCUSDT)
- [x] tests + intent regression (22 new in tests/test_trend_donchian.py;
      204 existing strategy/pipeline/monitor tests still green)
- [x] wiring draft PR
- [ ] accounts.yaml activation draft PR (operator merges)
- [ ] pull-and-deploy + live verification

### Implementation note — risk_pct plumbing
`load_strategies()` does NOT surface the strategies.yaml `risk_pct`
field, so the registry-driven `STRATEGY_RISK_PCT` defaults every
strategy to 1.0 (a pre-existing latent gap affecting turtle_soup /
ict_scalp too). To guarantee the conservative 0.3 for the real-money
launch WITHOUT changing the other live strategies' sizing (which would
be an unapproved Tier-3 change), the trend_donchian signal builder sets
`meta["strategy_risk_pct"] = 0.3` directly from its YAML, and both
multiplexers now preserve a builder-provided value instead of
overwriting it. Net: trend sizes at 0.3 × the account `risk_pct`;
turtle_soup / vwap / ict_scalp sizing is unchanged.
