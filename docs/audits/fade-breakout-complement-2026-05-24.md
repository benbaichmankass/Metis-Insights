# Failed-breakout fade — validated uncorrelated complement (S-STRAT-IMPROVE-S9)

**Date:** 2026-05-24 · **Status:** wired `execution: shadow` (data-only),
operator-approved · **Harness:** `scripts/backtest_fade.py` · **Data:**
`data/backtest_BTCUSDT_5m.csv` (trainer VM), 2020-03-25 → 2026-05-21,
resampled to 4h · **Fees:** 7.5 bps round-trip unless noted.

## Why this strategy

The strategy-improvement program's North Star is **3-5 complementary
net-positive members + a regime decider**. The first member —
`trend_donchian` (Donchian-breakout trend-follower, live 2h) — wins in
directional regimes. We need members that are net-positive AND
**uncorrelated** with it. Pure z-score mean-reversion failed
(`scripts/backtest_meanrev.py`: payoff too thin net-of-fee). This is the
first that passed.

## Hypothesis

`turtle_soup` (sweep-and-revert) is net-negative. The conjecture: that is
not because fading is wrong, but because turtle_soup takes the fade with
a **tight target**, and on BTC every tight-target strategy (vwap,
ict_scalp) dies on fee drag. The one lever that made the trend-follower
positive was **asymmetric payoff — wide fee-efficient stops + letting
winners run.** So: fade a *failed Donchian breakout* (the literal mirror
of what the trend-follower buys), but exit on a **runner** instead of a
tight target.

## Method

`scripts/backtest_fade.py` runs the *same* failed-breakout entries (a bar
that pierces the prior-bar Donchian channel then closes back inside) under
four exit styles, isolating the payoff variable:

| `--exit-style` | exit |
|---|---|
| `tp1r` | fixed 1R target (tight; ≈ turtle_soup control) |
| `mid` | channel midpoint |
| `far` | far channel band (full-range reversion) |
| `trail` | Chandelier ATR trail, no fixed TP (max runner) |

## Results

### 1. Hypothesis confirmed — payoff asymmetry is the lever

Net R improves monotonically as the target loosens, at **both** timeframes
(full history, donchian 20, ADX gate off):

| exit-style | 2h net R | 4h net R |
|---|---|---|
| tp1r | −128.4 | −66.0 |
| mid | −100.0 | −68.7 |
| far | −79.7 | −16.5 |
| **trail** | −13.5 | **+40.1** |

`tp1r` has the *highest* win rate (~48%) yet the *worst* net — fees eat it.
`trail` has ~30% win rate but wins on payoff. The runner flips the edge.

### 2. ADX chop-gate + 4h is the configuration

Gating entries to chop (ADX < 20 on the prior bar) — the regime a fade
belongs in, and where the trend-follower is flat — sharply improves the 4h
runner. Parameter plateau (4h, trail, ADX<20, full history), net R:

| donchian \ trail | 2.5 | 3.0 | 3.5 |
|---|---|---|---|
| 15 | −2.3 | −0.6 | +26.0 |
| **20** | +18.3 | **+48.9** | **+64.2** |
| 30 | +5.8 | +22.6 | +38.3 |

7/9 cells net-positive; `donchian 20` is the clear optimum; looser trail
consistently better. Chosen config **d20 / trail 3.5**: +64.2R / 6yr,
max-DD 13.9R, net-positive every year, top-month-share 0.24.

### 3. Uncorrelated with the live trend-follower (the point)

Monthly-return correlation vs live `trend_donchian` (2h): **0.035**.

`scripts/ops/portfolio_combine.py`, equal-weight (same total risk as one
strategy), gated fade (d20/t3.0) + trend2h:

| stream | net R | maxDD R | ret/DD |
|---|---|---|---|
| trend2h | +45.0 | 22.9 | 1.97 |
| fade_gated | +48.9 | 14.5 | 3.37 |
| **blend** | +46.9 | **12.4** | **3.80** |

The blend nearly doubles return-per-drawdown and nearly halves max-DD —
the diversification payoff.

### 4. Nested walk-forward (unbiased) — passes

Pick the winner on **train only** (2020-2023), then score it on untouched
**OOS** (2024-2026). Train-winner = **d20 / t3.5** (train +49.7R, exp
0.469) → **OOS +16.2R, exp 0.246** — positive out-of-sample without
peeking. All OOS-positive configs are donchian 20-30 / trail 3.5; the
donchian-15 corner fails OOS.

### 5. Fee-robust

d20/t3.0 full history: +51.9R @5bps → +48.9 @7.5 → +45.9 @10 → **+39.8
@15bps** (double the modelled fee). The edge is not a fee artifact.

## Caveats (why SHADOW, not live)

- **OOS expectancy decays ~half** vs train (0.246 vs 0.469) — more decay
  than the trend-follower showed (~0%).
- **OOS profit is month-concentrated:** strip the single best OOS month
  (2025-05, +16R) and the d20/t3.0 remainder is ≈ −3R; only 42% of OOS
  months are positive. Real but lumpy.

The edge is genuine and uncorrelated, but meaningfully more fragile than
the trend-follower — not yet worth real money.

## Decision

Wired as a live strategy in **`execution: shadow`** (S9 per-strategy gate,
enforced in `Coordinator.multi_account_execute`): it runs and LOGS its
order packages on real ticks (data collection) but **never sends a live
order**. Routed to **`bybit_1` (demo)** only — not the real-money
`bybit_2` — because the execution gate fails *open* on a registry-read
error (`coordinator.py`), so an unvalidated strategy is kept off the
real-money account entirely; demo routing captures identical signal data
(same BTCUSDT feed) at zero risk.

Config: `config/strategies.yaml::fade_breakout_4h` (d20 / atr14 / stop-buf
0.5 / trail 3.5 / ADX<20 / 4h). Code:
`src/units/strategies/fade_breakout_4h.py` (+ builder + intent/pipeline
registration). The `monitor()` Chandelier trail is shared verbatim with
`trend_donchian`.

## Next steps

1. Collect live shadow data on `bybit_1`; compare logged packages against
   the backtest expectancy.
2. Drill the OOS concentration: is the edge event-driven (specific chop
   episodes) or steady? Decide whether the month-concentration is
   acceptable.
3. If live shadow confirms, promote `execution: live` + route to `bybit_2`
   (Tier-3, operator-approved) — and it becomes the 2nd member, unlocking
   the regime decider.

---

## Addendum — time-stop / exit review (2026-05-30)

Operator observation: the live fade "finds good trades but doesn't get out near
the peak and sometimes waits longer than it should." Reviewed the exit on the
6.2yr archive (2020-01..2026-02, BTCUSDT 4h, net-of-fee) via
`scripts/backtest_fade.py` + `fade_exit_analysis` / `fade_timestop_validation`.

### What the exit is, and why the give-back is intentional

The only profit-exit is the **Chandelier trail: peak − 3.5×ATR** (the ~50R TP
is an unreachable sentinel). On winners it keeps ~58% of the peak (avg MFE 6.25R
→ realized 4.04R) — the ~2R give-back the operator sees. **This is optimal, not
a bug.** Tightening the trail to "catch the peak" monotonically destroys net_R
(trail 3.5 → +85.5R; 3.0 → +56.8; 2.0 → +23.5; 1.5 → −0.3) because it chops the
fat-tail runners that ARE the edge. Partial scale-outs do the same (scale ½ @2R
then trail → +38R vs +85.5R pure trail). **Conclusion: leave the 3.5×ATR trail
alone.**

### The one real change — restore the validated 48-bar time stop

The live `monitor()` had **no time stop**, so it ran the `timeout=∞` regime,
while the validated config (and `scripts/backtest_fade.py`) used
`timeout_bars=48` (~8 days at 4h). Effect of adding it back (FULL 6yr):

| time stop | net_R | exp | maxDD |
|---|---|---|---|
| ∞ (**live before**) | 73.5 | 0.417 | 17.2 |
| **48 bars (validated)** | **87.7** | **0.493** | **17.0** |

- **Plateau, not a spike:** 42–60 bars all net 82–88R. Robust.
- **Walk-forward (the honest test):** TRAIN 2020-2023 independently picks
  timeout=48 (net 61.8, plateau 42–54). **OOS 2024-2026 it is NEUTRAL** vs
  no-stop (29.9 vs 30.2R; within noise, marginally lower maxDD). So the +14R
  full-sample lift is **mostly a 2020-2023 effect — not future alpha.**
- **Fee-robust** (+14R edge holds at 12 & 15 bps); **helps across every
  donchian×trail neighbor** tested.
- **By-year:** helps/neutral in 5 of 7 years; marginally hurts 2024 (−0.95R)
  and 2026 (−1.06R).

**Verdict:** ship the 48-bar time stop, but framed honestly — it is a
**live↔backtest parity fix + a risk/operational improvement** (caps the stalled
multi-week holds the operator flagged, frees capital for new signals, slightly
lower maxDD), **NOT an alpha boost** (OOS-neutral). Implemented in
`fade_breakout_4h.monitor()` step 3 + `_DEFAULTS["timeout_bars"]=48` +
`config/strategies.yaml::fade_breakout_4h.timeout_bars`. Tier-3 — operator
approval required before it reaches the live VM. The trail and all entry logic
are unchanged, so the only behavioural delta is "stalled fades now close at ~8
days instead of running until the trail/stop eventually catches them."
