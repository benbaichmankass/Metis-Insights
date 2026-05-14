# ICT Scalp 5m

Deterministic ICT-style scalping strategy. Code lives at
`src/units/strategies/ict_scalp.py`; unit tests at
`tests/test_ict_scalp_5m.py`; YAML config under
`config/strategies.yaml::ict_scalp_5m`.

## What the strategy looks for

A scalp setup fires on the **most recent closed bar** when ALL of:

1. **Liquidity sweep** — a bar in the last `sweep_lookback_bars` bars
   (default 12, ≈ 1h on 5m) pierces a rolling-window swing extreme by
   at least `sweep_buffer_bps` of price (default 5 bps) **and** closes
   back inside the prior range. The reversion gate (close back inside)
   is what separates a sweep from a breakout — without it a regular
   continuation bar would qualify and the downstream reversal never
   comes.
2. **Displacement** — at least one bar between the sweep bar and the
   current bar has a body of size ≥ `displacement_atr_mult` × ATR
   (default 1.0 × ATR(14)), body-to-range ≥
   `min_displacement_body_to_range` (default 0.55), and is in the
   setup direction (bullish body for a long, bearish for a short).
3. **Fair Value Gap (FVG)** — a 3-candle imbalance in the displacement
   leg of size ≥ `min_fvg_size_bps` of price (default 2 bps) and in the
   setup direction. Bullish: `high[i-2] < low[i]`. Bearish: mirror.
   The most recent qualifying FVG is the one used.
4. **Mitigation** — the most recent bar's range overlaps the FVG AND
   its body direction matches the setup (clean entry confirmation).

When all four conditions hold:

* **entry** = close of the most recent bar
* **sl** = sweep extreme ± `atr_sl_buffer_mult` × ATR (default 0.20 ×
  ATR, outside the swept liquidity)
* **tp** = entry ± `tp_at_r` × risk (default 1.5R)
* **confidence** = `0.4 × body_to_range + 0.3 × sweep_depth_atr + 0.3 ×
  fvg_size_norm`, clamped to `[0, 1]`

## Timeframe

Default is **5m**. The unit is timeframe-agnostic — it consumes
`candles_df` and `cfg["timeframe"]`. Switching to 1m is a config
change, **not a code change**:

```yaml
# config/strategies.yaml
strategies:
  ict_scalp_5m:
    timeframe: "1m"
    # Retune lookback windows to span a similar wall-clock window:
    sweep_lookback_bars: 60       # ≈ 1h at 1m
    swing_lookback_bars: 100      # ≈ 100m at 1m
    atr_period: 14
```

Re-backtest the 1m configuration against historical candles before
flipping `enabled: true`. Cadence and noise characteristics at 1m
differ meaningfully from 5m; do not assume the 5m defaults transfer.

## How to backtest

Three ways, in order of operator-friendliness:

### 1. GitHub Actions workflow (recommended pre-live gate)

Open an issue labelled `ict-scalp-backtest-request` with body:

```
strategy: ict_scalp_5m
data: data/backtest_candles.csv      # optional; defaults to repo fixture
timeframe: 5m                         # optional override
```

The `.github/workflows/ict-scalp-backtest.yml` workflow runs the
strategy against the supplied candle CSV inside a clean CI runner and
posts a summary (trade count, win rate, expectancy, max drawdown,
Sharpe) back as an issue comment. **This is the gate that must pass
before flipping `enabled: true` for live trading.**

Workflow can also be triggered manually via `workflow_dispatch` from
the GitHub Actions UI for ad-hoc reruns.

### 2. Local CLI

```bash
python -m scripts.backtest_ict_scalp \
  --data data/backtest_candles.csv \
  --timeframe 5m \
  --json /tmp/ict_scalp_summary.json
```

Reads candles, walks the frame bar-by-bar invoking the unit's
`order_package()` on a rolling window, simulates fills on subsequent
bars against the strategy's own SL/TP, and prints / writes the
summary metrics.

### 3. Unit-level (tests)

```bash
python -m pytest tests/test_ict_scalp_5m.py -v
```

Each test case constructs a synthetic OHLCV frame and asserts the
strategy fires (or doesn't) on it. Fast, deterministic, no exchange.

## How to enable for live trading

After a passing backtest:

1. Flip `enabled: true` in `config/strategies.yaml::ict_scalp_5m`.
2. Open a PR with that change. Per CLAUDE.md, edits to
   `config/strategies.yaml` are Tier-3 (operator-approval-required); do
   not merge from a Claude session.
3. Once merged, the live trader picks it up on the next restart (or
   via `Coordinator.reload_strategy_config()` if the runtime supports
   in-place reload).

While `enabled: false` (the default) the runtime signal builder
(`src/runtime/strategy_signal_builders.py::ict_scalp_signal_builder`)
short-circuits to `side="none"` so live behaviour is unchanged.

## Assumptions and limitations

* **Single-bar mitigation.** v1 only looks at the most recent bar for
  the mitigation gate. Multi-bar consolidation entries are out of
  scope.
* **No HTF bias.** The strategy doesn't consult a higher timeframe
  trend. A future v2 could add an HTF filter analogous to
  `vwap.htf_trend_filter` (currently disabled there too).
* **Session filter off by default.** Crypto is 24/7; the filter exists
  in case the operator wants to scope to London + NY kill-zones
  (07-17 UTC).
* **BTCUSDT-only.** The intent layer (`src/runtime/intents.py::SUPPORTED_SYMBOLS`)
  is single-symbol for now; adding a symbol means wiring per-symbol
  position state, not just adding to this strategy's `symbols` list.
* **Priority below VWAP and Turtle Soup.** Set to 30 in
  `DEFAULT_PRIORITIES` so accidental enablement cannot override the
  established strategies on a tie.
* **No ML.** Purely deterministic gates. Adding a shadow predictor
  follows the same `shadow_model_ids` pattern as turtle_soup/vwap.

## Files

| File | Purpose |
|------|---------|
| `src/units/strategies/ict_scalp.py` | Pure unit; `order_package()` + `monitor()`. |
| `src/runtime/strategy_signal_builders.py::ict_scalp_signal_builder` | Pipeline-side builder; honours `enabled` flag. |
| `src/runtime/pipeline.py` | Registers in `_STRATEGY_BUILDERS`; `STRATEGY=ict_scalp_5m` env override. |
| `src/runtime/intent_multiplexer.py` | Registers in `_default_intent_builders`. |
| `src/runtime/intents.py::DEFAULT_PRIORITIES` | Priority 30 (below vwap=40, turtle_soup=50). |
| `config/strategies.yaml::ict_scalp_5m` | Live config, `enabled: false` until backtest passes. |
| `tests/test_ict_scalp_5m.py` | 27 unit tests covering happy path, no-signal, invalid data, timeframe, session filter, monitor, YAML registration. |
| `scripts/backtest_ict_scalp.py` | Standalone CLI for local backtests. |
| `.github/workflows/ict-scalp-backtest.yml` | CI-side pre-live gate. |
