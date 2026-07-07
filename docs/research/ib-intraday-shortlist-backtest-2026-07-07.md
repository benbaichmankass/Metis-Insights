# IB intraday shortlist — native/continuous backtest (2026-07-07)

**Author:** Claude. **Context:** the step-3 intraday survey
(`ib-intraday-strategy-survey-2026-07-07.md`) concluded "no *validated* intraday
edge yet" and deferred a test matrix (#1/#2/#5/#6/#7). That matrix is now RUN on
native IBKR futures data — with the roll-adjusted continuous tooling
(`build_continuous_contract.py`, #5870) for the breakout/trend candidate so the
roll-gap worry (quantified at ~3–11% on native MGC in
`ib-metals-native-backtest-2026-07-07.md`) is removed rather than assumed.

All net-of-fee. Micro futures round-trip cost ~1bp; graded at **2bps (≈2×)** for
the pullback/fvg harnesses (`--fee-bps-roundtrip 2.0`); the trend harness bakes
in **7.5bps** (≈7×, more conservative); the scalp harness uses the unit's own
fee model.

## Results

| # | Candidate | Data (window) | Trades | Win% | Net R | Exp R | MaxDD R | Per-year net R | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| **1** | **MGC pullback 1h** (continuous) | 2023-03→2026-07 (~3.3y) | 328 | 36.6% | **+185.3** | **+0.56** | 21.5 | 2023 **+70.6** / 2024 **+27.8** / 2025 **+50.6** / 2026 **+36.3** | ✅ **GO (strong)** |
| 7 | MGC trend 4h (continuous) | 2023-03→2026-07 | 193 | 42.5% | +27.0 | +0.14 | 14.4 | 2023 +23.8 / 2024 −7.7 / 2025 +8.4 / 2026 +2.5 | ⚠️ **MARGINAL / NO-GO** |
| 2 | MES pullback 1h (5m→1h) | 2025-07→2026-07 (~1y) | 41 | 34.2% | +10.1 | +0.25 | 9.0 | 2025 +7.3 / 2026 +2.8 | ◻️ **NEEDS-MORE-DATA** |
| 5 | MES fvg 15m | 2025-07→2026-07 (~1y) | 4 | 75% | +2.1 | +0.52 | 1.0 | 2026 +2.1 | ◻️ **NEEDS-MORE-DATA** |
| 6 | MES scalp 5m | 2025-07→2026-07 (~1y) | 7 | 42.9% | +0.08 | +0.01 | 1.7 | — | ❌ **NO-GO** |

(MGC trend 4h continuous vs the spliced `none` baseline: +27.0R vs +27.8R — roll
impact **−3%** at 4h, even smaller than the −11% at 1h; the trend edge is not a
roll artifact, it is just weak.)

## The one real find — #1 MGC pullback 1h

The standout by a wide margin. Applying the **live `mgc_pullback_1d` parameters**
(trend_lookback 40, pullback_lookback 15, pullback_frac 0.618, atr 14, stop 2.0,
trail 4.0, timeout 200) to native MGC **1h** bars (roll-adjusted continuous)
yields **+185.3R over ~3.3y at 2bps**, with:
- **Positive in EVERY calendar year** (2023 +70.6, 2024 +27.8, 2025 +50.6, 2026
  +36.3) — NOT the 2023-concentration that plagues the trend cells;
- **healthy expectancy +0.56R** and **both sides contributing** (long +138.6R,
  short +46.7R);
- 328 trades (good N), shallow DD (21.5R), exits balanced (162 stop / 164
  trail / 2 timeout).

These params were carried over from the daily cell, **not tuned to 1h**, so this
is not an obviously over-fit grid. It is the single most promising intraday
result in the whole survey. **It is NOT a promotion** — it is one in-sample
window and needs a proper **walk-forward / OOS** validation + the mandatory
`account_compat_matrix` before it could be *proposed* as a new intraday strategy
variant (e.g. `mgc_pullback_1h`). Tier-3, operator-gated.

## The rest

- **#7 MGC trend 4h — MARGINAL/NO-GO.** Positive (+27R) but 88% of it is 2023;
  2024 was negative; expectancy is a thin +0.14R and the short side is flat. Same
  shape as `mgc_trend_1h` (which stays shadow). The roll gaps are NOT the problem
  (−3% impact) — the edge just isn't durable.
- **#2 MES pullback 1h — NEEDS-MORE-DATA.** Directionally encouraging (+10.1R,
  positive in both partial years, +0.25R exp) but only **41 trades over <1y** —
  the trainer MES shard is ~1 year (2025-07→2026-07). A deeper native MES history
  (a per-contract MES pull, same mechanism as the MGC one) is the prerequisite to
  judging it. Worth pulling given #1's result on the sibling instrument.
- **#5 MES fvg 15m — NEEDS-MORE-DATA.** Only **4 trades** (the range/FVG gate is
  very selective on ~1y of 15m). 3/4 winners is noise at that N. Not judgeable.
- **#6 MES scalp 5m — NO-GO.** +0.08R over **7 trades** — essentially flat. This
  is consistent with the live `ict_scalp_5m` **demote** (2026-06-29,
  live-verified negative): the cell is inert/flat on native MES 5m too.

## Bottom line

The survey's "no *validated* intraday edge yet" is **partially upgraded**: the
matrix produced **one strong candidate — MGC pullback 1h (+185R, positive every
year, +0.56R expectancy)** — worth a proper walk-forward, and one instrument
(MES) where the pullback logic looks directionally right but is **data-starved**
(needs a deeper native-MES pull to judge). The breakout/trend intraday cells
(MGC trend 4h) and the scalp cell are NOT edges. Nothing here changes config —
the GO/NEEDS-MORE items are **proposals**:

1. **`mgc_pullback_1h`** — walk-forward + `account_compat_matrix`, then propose
   as a new intraday strategy variant (Tier-3). *The recommended next step.*
2. **Deeper native MES history** (per-contract MES pull) → re-run #2 MES pullback
   1h + #5 fvg on 3y+ before judging.
3. MGC trend 4h + MES scalp 5m — parked (no edge).
