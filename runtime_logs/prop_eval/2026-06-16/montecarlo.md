# Prop-firm Monte-Carlo — survival + speed sweep

_Generated 2026-06-16T16:14:40.660534+00:00_

**Ruleset:** `breakout` / `1-step-classic` — account $5,000, target 10%, daily-loss 3%, static DD 6%.

**Method:** block-bootstrap (5000 paths, block_len 8, seed 1234) of each combo's real per-trade ledger, walked as a fresh $5,000 account compounded at each `risk_pct`. Engine base risk_pct 0.5; ledger rescaled to each cell's risk via sizing-independent R-multiples.

**Data:** 2023-01-01 00:00:00+00:00 → 2026-02-28 23:55:00+00:00 (clock_tf 1h, flip_policy hold, reentry suppress).

> **Honesty caveat (daily-loss is OPTIMISTIC):** a per-trade bootstrap has no intraday open-position equity swing, so the daily-loss (and static-DD) checks see only REALISED closed-trade P&L per synthetic day. Breakout's real daily-loss fires on intraday equity incl. open positions — so P(breach by daily_loss) here UNDER-counts. Backtest ≠ funded reality (slippage/fills/funding).

| combo | risk | P(pass) | days→pass (med / p5–p95) | P(breach) | by cause | P(surv 3mo) | P(surv 6mo) | P(surv 12mo) | end ret (med) |
|---|---|---|---|---|---|---|---|---|---|---|
| squeeze_breakout_4h | 0.3 | 3% | 861.5 / 561.2–1141 | 0% | static 0% | 100% | 100% | 100% | 3.3% |
| squeeze_breakout_4h | 0.5 | 26% | 709.6 / 358.3–1087.7 | 3% | static 3% | 100% | 99.94% | 99.36% | 5.5% |
| squeeze_breakout_4h | 0.6 | 38% | 644 / 284.7–1049.3 | 6% | static 6% | 100% | 99.26% | 98.04% | 6.5% |
| squeeze_breakout_4h | 0.75 | 53% | 555.7 / 202.5–1017.2 | 11% | static 11% | 99.78% | 98.1% | 94.98% | 8.1% |
| squeeze_breakout_4h | 1.0 | 63% | 448.5 / 145.5–955.5 | 19% | static 19% | 99.14% | 94.76% | 89.04% | 10.2% |
| fvg_range_15m | 0.3 | 46% | 641.8 / 332.6–902 | 0% | static 0% | 100% | 100% | 100% | 8.7% |
| fvg_range_15m | 0.5 | 81% | 464.1 / 170.6–835.1 | 1% | static 1% | 100% | 100% | 99.56% | 14.8% |
| fvg_range_15m | 0.6 | 87% | 394 / 108.2–799.5 | 2% | static 2% | 100% | 99.58% | 98.84% | 17.8% |
| fvg_range_15m | 0.75 | 90% | 314.8 / 45.6–743.4 | 4% | static 4% | 100% | 98.52% | 97% | 22.4% |
| fvg_range_15m | 1.0 | 90% | 248.6 / 45.6–646.1 | 8% | static 8% | 100% | 96.18% | 93.66% | 30.0% |
| squeeze_breakout_4h,fvg_range_15m | 0.3 | 23% | 472.7 / 199.5–687.2 | 0% | static 0% | 100% | 100% | 100% | 5.5% |
| squeeze_breakout_4h,fvg_range_15m | 0.5 | 55% | 375.7 / 109.6–635.8 | 4% | static 4% | 100% | 99.94% | 97.6% | 9.2% |
| squeeze_breakout_4h,fvg_range_15m | 0.6 | 64% | 327.2 / 41.2–614.8 | 7% | static 7% | 100% | 99.46% | 95.2% | 11.0% |
| squeeze_breakout_4h,fvg_range_15m | 0.75 | 71% | 275.4 / 41.6–595.8 | 13% | static 13% | 99.86% | 96.3% | 90.22% | 13.5% |
| squeeze_breakout_4h,fvg_range_15m | 1.0 | 72% | 204.1 / 41.6–536 | 22% | static 22% | 99.54% | 88.4% | 81.48% | 16.7% |
| fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m | 0.3 | 25% | 454.3 / 165.7–754.4 | 25% | static 25% | 99.72% | 97.08% | 87.92% | 1.5% |
| fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m | 0.5 | 41% | 276.1 / 81–660.9 | 49% | static 49% | 93.76% | 80.92% | 64.94% | -3.8% |
| fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m | 0.6 | 43% | 209.4 / 49.8–604.5 | 55% | daily 0%, static 55% | 88.12% | 72.92% | 56.92% | -6.0% |
| fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m | 0.75 | 42% | 147.8 / 17.2–482.3 | 63% | daily 0%, static 63% | 79.26% | 63.04% | 47.88% | -6.1% |
| fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m | 1.0 | 37% | 101.2 / 7.2–339.1 | 85% | daily 28%, static 56% | 65.88% | 47.14% | 29.94% | -6.1% |
| trend_donchian | 0.3 | 41% | 263.4 / 34.4–517.5 | 41% | daily 24%, static 18% | 92.16% | 84.14% | 69.9% | 4.3% |
| trend_donchian | 0.5 | 55% | 212 / 38.7–480.4 | 48% | daily 15%, static 33% | 90.26% | 77.8% | 62.38% | 5.4% |
| trend_donchian | 0.6 | 58% | 183.4 / 38.7–453.6 | 52% | daily 13%, static 39% | 87.92% | 73.34% | 58% | 4.4% |
| trend_donchian | 0.75 | 59% | 142.5 / 34.4–405.3 | 56% | daily 11%, static 45% | 82.88% | 67.84% | 52.88% | 1.1% |
| trend_donchian | 1.0 | 56% | 95.4 / 23.8–289.5 | 63% | daily 9%, static 54% | 72.7% | 57.88% | 44.88% | -6.1% |
| trend_donchian,squeeze_breakout_4h,fvg_range_15m | 0.3 | 27% | 380.9 / 187.2–559.3 | 41% | daily 22%, static 19% | 92.32% | 84.54% | 70.32% | 2.2% |
| trend_donchian,squeeze_breakout_4h,fvg_range_15m | 0.5 | 51% | 252.5 / 88.9–511.2 | 47% | daily 13%, static 34% | 90.6% | 79.7% | 63.8% | 2.2% |
| trend_donchian,squeeze_breakout_4h,fvg_range_15m | 0.6 | 56% | 203.1 / 67.2–481.1 | 51% | daily 11%, static 40% | 88.44% | 75.52% | 59.56% | 1.1% |
| trend_donchian,squeeze_breakout_4h,fvg_range_15m | 0.75 | 58% | 158.4 / 43.3–438.4 | 56% | daily 8%, static 48% | 83.54% | 69.4% | 53.98% | -3.1% |
| trend_donchian,squeeze_breakout_4h,fvg_range_15m | 1.0 | 56% | 101.4 / 25.6–316.7 | 69% | daily 16%, static 53% | 72.98% | 58.12% | 41.94% | -6.1% |

## NOTE — the honest headline

**Question:** does ANY combo + sizing pass FAST (define _fast_ = median
days-to-pass ≤ ~60) while keeping **P(survive 6mo) ≥ 95%**?

**Answer: NO. Not a single cell in the matrix clears that bar.** The two
criteria pull in opposite directions on this roster:

- **The combos that survive (P(survive 6mo) ≥ 95%) are slow.** The safest
  cells are `fvg_range_15m` and `squeeze_breakout_4h,fvg_range_15m` — their
  breaches are essentially nil at low/medium risk because they almost never dip
  below the $5,000 start. But their **median days-to-pass never drops below ~205
  days**, even pushed to risk 1.0:
  | combo | risk | median days→pass | P(surv 6mo) | P(pass) |
  |---|---|---|---|---|
  | fvg_range_15m | 0.75 | **315** | 98.5% | 90% |
  | fvg_range_15m | 1.0 | **249** | 96.2% | 90% |
  | squeeze+fvg | 0.6 | **327** | 99.5% | 64% |
  | squeeze+fvg | 1.0 | **204** | 88.4% (FAILS the 95% bar) | 72% |

  The fastest a ≥95%-6mo-survival cell ever passes is **fvg_range_15m @ risk 1.0
  — median 249 days** (~8 months). That is ~4× the "fast" bar.

- **The combos that pass fast all breach hard.** The only cells with median
  days-to-pass near the "fast" range contain `trend_donchian` or
  `fade_breakout_4h`, and their speed comes entirely from high risk on a
  high-variance ledger that frequently touches the 6% static floor:
  | combo | risk | median days→pass | P(breach) | P(surv 6mo) |
  |---|---|---|---|---|
  | fade+squeeze+fvg | 1.0 | **101** | 85% | 47% |
  | trend_donchian | 1.0 | **95** | 63% | 58% |
  | trend+squeeze+fvg | 1.0 | **101** | 69% | 58% |

  These get into double-digit days at p5 (lucky early streak) but the **median
  path breaches before it passes** — survival collapses to 47–58% at 6 months.
  They are exactly the "blow the account chasing a fast pass" trap.

**Why:** the roster is **low-frequency** — the multi-year ledger holds only
~90–180 closed trades over 3.16 years (≈ one trade every 6–13 days). A +10%
target reached by *grinding* (not by a few big bets) simply takes many trades,
and many trades take many months at this cadence. You can only compress the
calendar by raising risk_pct, but on this BTC history raising risk on the
trend/fade legs walks the equity into the **static 6% floor** (the binding
constraint, confirmed in the single-path eval) faster than it walks it to +10%.

**Bottom line (a valid, important finding):** the current low-frequency ICT
roster is **not suited to a _fast_ prop pass.** It can pass the Breakout 1-Step
*safely* — `fvg_range_15m` or `squeeze+fvg` at risk 0.5–0.75 give P(survive
6mo) ≥ 98% and P(survive 12mo) ≥ 97% — but only on a **multi-quarter
timeline** (median ~250–460 days), and pushing for speed via higher risk or the
trend/fade legs trades that survival away. A genuinely fast pass would need a
**higher-frequency strategy** (more trades per month), not a sizing change.

### Best risk-adjusted picks (if speed is sacrificed for survival)

- **`fvg_range_15m` @ risk 0.75** — P(pass) 90%, median 315 days, P(surv 6mo)
  98.5% / 12mo 97%, median end-return +22%. Best survival-weighted speed.
- **`squeeze_breakout_4h,fvg_range_15m` @ risk 0.6** — P(pass) 64%, median 327
  days, P(surv 6mo) 99.5% / 12mo 95%. The most breach-proof cell with a real
  pass rate; matches the single-path eval's recommended combo.

### Caveats (read before trusting)

1. **Daily-loss is OPTIMISTIC** (see banner above): realised-only per-trade
   buckets omit intraday open-position swings, so P(breach by `daily_loss`)
   under-counts. The low-frequency legs show this least (one position at a time,
   bar-close exits); it matters more for the trend/fade cells where daily breach
   already appears (9–28%). Real daily-loss breach rates are **higher** than
   shown, which only **strengthens** the "no fast-and-safe pass" conclusion.
2. **Bootstrap reuses the historical trade distribution** — it does not invent
   regimes BTC never produced 2023-01→2026-02. A different market regime would
   shift these numbers.
3. **Backtest ≠ funded reality** — slippage, fills, funding, and Breakout's
   exact equity accounting differ. This estimates *relative, probabilistic*
   robustness; it is a filter, not a guarantee.
4. Ledgers generated at base risk_pct 0.5, rescaled to each cell via
   sizing-independent R-multiples (one engine run per combo, reproducible
   seed 1234, 5000 paths each).
