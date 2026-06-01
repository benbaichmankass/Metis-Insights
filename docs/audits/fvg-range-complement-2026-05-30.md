# FVG range / mean-reversion — the missing range member (S-STRAT-IMPROVE)

> **Status:** Tier-3 strategy logic + params. Wired `execution: shadow` on
> bybit_1 (demo) only — DRAFT PR, NOT live. Promotion `shadow → live` and
> bybit_2 (real money) routing are operator-gated.
> **Date:** 2026-05-30. **Symbol:** BTCUSDT. **Timeframe:** 15m.
> **Harness:** `scripts/backtest_fvg_range.py`. **Strategy:**
> `src/units/strategies/fvg_range_15m.py`.

## Why this strategy (the regime gap)

The roster covers every regime except one. The current BTCUSDT members map to:

| Strategy | Regime it trades |
|---|---|
| `trend_donchian` (2h, live) | trending — rides Donchian breakouts |
| `squeeze_breakout_4h` (4h, live) | volatility squeeze → expansion breakout |
| `fade_breakout_4h` (4h, live) | a *failed* breakout (liquidity grab) in chop |
| `turtle_soup` (15m, live) | a sweep + reversal |
| `vwap` (5m, shadow) | mean-reversion to a *drifting* anchor (trend-gated, no net edge) |
| `ict_scalp_5m` (5m, live) | FVG used **directionally** — sweep → displacement → continuation |

The under-served regime is the **clean, persistent HORIZONTAL range**: price
oscillating between *static* support & resistance where the bounce continues
(mean reversion to the range interior). No member trades it:

- `fade_breakout_4h` fades a *breakout attempt* off a Donchian channel — it is
  not a static-S/R bounce, and it enters on the failed-break bar, not at a
  range boundary the market has respected repeatedly.
- `vwap` reverts to a *drifting* VWAP anchor and is trend-gated; it has no
  net-of-fee edge (`docs/audits/strategy-loss-drivers-2026-05-23.md`).
- `ict_scalp_5m` uses an FVG in the **opposite** intent — continuation in the
  breakout direction (momentum), not reversion inside a range.

**Empirical idle confirmation (live diag snapshot 2026-05-30T08:04Z, last tick
08:02:46Z):** all six strategies evaluated to `side: none` across the recent
tick window — the roster produces zero signals in the current chop/range tape,
the exact regime this member is built for. (Snapshot truncated to the last ~20
ticks, so this is a point-in-time idle observation, not full per-strategy PnL.)

## Hypothesis

Inside a confirmed horizontal range (low ADX = chop, sane width, both
boundaries touched repeatedly), an **unfilled Fair Value Gap** sitting in the
lower third (long) / upper third (short) is a high-probability bounce level.
Enter on a wick-rejection at the gap (price wicks INTO the gap and CLOSES back
OUT of it with a matching-direction body — the same confirmation `ict_scalp`
uses, but for reversion not continuation), stop ATR-buffered beyond the gap /
range boundary (a range BREAK invalidates the thesis), target the opposite
boundary (full-range reversion). If this is net-positive net-of-fee in the chop
regime where the trend-followers are flat, it is a genuine diversifier — the
missing range member.

## Method

`scripts/backtest_fvg_range.py` on the 5.2-year qashdev/btc archive
(2021-01-01 → 2026-02-28, 542,667 5m bars resampled to 15m), net of a 7.5 bps
round-trip fee. Entry/exit detection, ADX/ATR formulas, and the FVG helper are
shared verbatim with the live `order_package` (live-parity — see "live-parity"
below). Same readout shape as every other complement audit: win-rate, gross/net
R, long/short split, by-outcome, by-year, month-over-month consistency.

Sweeps over `range_lookback`, `min_touches`, `adx_max`, width bounds, stop
buffer, `min_confidence`, timeout, and exit-style; then a train/OOS
walk-forward and a fee-stress.

## Results

### 1. The let-winners-run lever applies — far boundary, not the midline

The first lesson the program learned (vwap/turtle/ict_scalp die on BTC fees with
tight targets) repeats here. With the **range-midline** target the strategy is
58.6% win-rate but **net −37.7R** — small wins (avg 0.6R) cannot clear the fee
drag. Switching the target to the **opposite boundary** (full-range reversion)
flips it net-positive: bigger winners (avg ~1.5R) at a lower win-rate carry the
fees. `mid` and `tp1r` are both net-negative at the chosen config; `far` is the
exit.

### 2. The `touches >= 4` boundary-confirmation gate is the edge

A range that has been touched only twice per side is barely a range. Requiring
**each boundary touched ≥ 4 times** within the lookback selects genuinely
oscillating, well-established ranges — and that is where the edge concentrates.
At `range_lookback=48`, the full-5y net by touch count (far target, stop 0.25):

| touches | trades | WR | net_R | exp | maxDD | net L / net S |
|---|---|---|---|---|---|---|
| 2 | 291 | 41.2% | +5.2 | +0.018 | 10.7 | −7.0 / +12.3 |
| 3 | 127 | 42.5% | +9.7 | +0.076 | 5.5 | +0.8 / +8.9 |
| **4** | **67** | **50.8%** | **+24.4** | **+0.363** | **3.0** | **+15.9 / +8.5** |

It is a **plateau, not a spike**: `touches=4` is net-positive across
`range_lookback` 40–48 × `adx_max` 18–22 (e.g. lb40/t4/adx20 +15.6R exp 0.421;
lb48/t4/adx22 +22.6R; lb48/t5/adx22 +21.3R). `range_lookback=64` dilutes the
"static range" and goes negative — consistent with the thesis.

### 3. The configuration

`range_lookback=48` (~12h on 15m) · `min_touches=4` · `adx_max=20` (mirrors
`fade_breakout_4h`'s chop gate) · width 1.5–12% of price · `third_frac=0.34` ·
far-boundary target · `atr_stop_buffer=0.25` · `timeout_bars=48`. No
`min_confidence` floor — a sweep found none improves net_R (the regime gates
already do the filtering, same finding as `fade`/`trend`).

**FULL 5y (2021–2026):** 67 trades, WR 50.8%, **net +24.35R, expectancy
+0.363, max-DD 3.0R**, 57% of months positive, **both long (+15.85R) and short
(+8.50R) net-positive** (not a short-only bull-market artifact).

### 4. Walk-forward (unbiased) — passes, and does NOT decay

Train 2021-01..2023-12 / OOS 2024-01..2026-02, chosen config frozen on train:

| window | trades | WR | net_R | exp | maxDD |
|---|---|---|---|---|---|
| TRAIN 2021–2023 | 25 | 44.0% | +2.59 | +0.104 | 3.0 |
| **OOS 2024–2026** | **42** | **54.8%** | **+21.76** | **+0.518** | **3.0** |

OOS is **stronger** than train (exp +0.518 vs +0.104), both sides positive OOS
(L +10.5 / S +11.3) — the opposite of `fade_breakout_4h`'s OOS-halving. OOS
neighbours (lb40/t4, lb48/t4, lb64/t3 @ adx18) are also positive → robust, not a
single lucky cell.

### 5. Fee-robust

Still **+10.45R net at 15 bps round-trip** (2× the modelled 7.5) at the chosen
config — the far-boundary winners absorb double the fee.

## Live-parity

`src/units/strategies/fvg_range_15m.py::order_package` is a verbatim port of the
harness's per-bar entry block, evaluated on the most recent closed bar. Replay
verification: all 67 backtest entries reproduce in `order_package` with
identical direction / entry / SL / TP (`order_package` raised 0 of 67). The
`monitor()` implements only the backtest's time-decay backstop (close after
`timeout_bars`) — no trailing, no break-even, because a premature protective
stop would cut the wick-against-then-revert bounces the edge depends on.

## Caveats (why SHADOW, not live)

1. **Low frequency.** 67 trades over 5.2 years (~13/yr). The expectancy is high
   but the sample is small — exactly the kind of edge that needs live shadow
   confirmation before risking money.
2. **Recent-regime concentration.** The strength is in OOS (2024–2026); train
   (2021–2023) expectancy is a modest +0.10R. The 2024–2026 BTC tape had more
   clean ranges; whether that persists is unknown.
3. These are why it ships `execution: shadow`: it RUNS and LOGS its order
   packages on real ticks (data collection) but never sends a live order.

## Decision

Wire `fvg_range_15m` `enabled: true` / `execution: shadow`, route to **bybit_1
(demo) only**, priority **3** (the new roster floor — a wiring slip cannot let
an unproven strategy override an established member), `risk_pct: 0.3` (low,
moot while shadow). Draft PR; do NOT flip to live. Promotion `shadow → live` and
bybit_2 routing are a later Tier-3, operator-approved step once the live shadow
data confirms the backtest.

## Next steps

1. Land the wiring + this evidence (DRAFT PR), deploy to the demo account, and
   let the live shadow data mature (weeks — low frequency means it needs time).
2. Compare live shadow signals against the backtest (do confirmed 4-touch
   ranges fire as expected; does the far-boundary target get reached).
3. Check correlation of the shadow trade stream against the live trend/fade/
   squeeze members (the diversification payoff is the point — a range member
   should be uncorrelated with the breakout members).
4. If the live shadow data confirms the edge, propose `shadow → live` +
   bybit_2 routing (operator-gated).
