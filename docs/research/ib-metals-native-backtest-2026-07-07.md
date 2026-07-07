# IB metals sleeve — native-instrument backtest (2026-07-07)

**Author:** Claude. **Context:** the IB metals sleeve (`mgc_pullback_1d`,
`mhg_pullback_1d` — LIVE on `ib_paper`; `mgc_trend_1h` — shadow) was only ever
validated on **proxy** series (Dukascopy XAUUSD spot, GC=F, HG=F, GLD). This is
the first validation on **native IBKR futures** data, unblocked by the COMEX
exchange fix (#5853) — before it, every MGC/MHG pull died with IBKR Error 200
because the pull adapter requested MGC/MHG on CME instead of COMEX.

**Data (native, via `pull-ibkr-history` on the live VM):**
- `market_raw/MGC/1d/v003` — 940 daily bars, **2022-09-30 → 2026-07-06** (~3.8y)
- `market_raw/MHG/1d/v003` — 1043 daily bars, **2022-05-03 → 2026-07-06** (~4.2y)
- `market_raw/MGC/1h/v002` — 2116 hourly bars (small/stitched — see caveat)

(Requested from 2019 but IBKR daily retention + `max_contracts=28` reached back
to ~2022. Native shards are **stitched dated contracts, NOT roll-back-adjusted** —
a load-bearing caveat for breakout strategies, below.)

## Verdicts

### ✅ `mgc_pullback_1d` — KEEP LIVE (native confirms the edge)
`scripts/backtest_pullback.py`, exact live params (trend_lookback 40,
pullback_lookback 15, pullback_frac 0.618, atr 14, stop 2.0, trail 4.0,
timeout 200):

| Fee | Trades | Win% | Net total R | Long / Short | Expectancy | MaxDD |
|---|---|---|---|---|---|---|
| 1.0 bps | 29 | 37.9% | **+25.08R** | +25.43 / −0.34 | 0.865R | 4.23R |
| 7.5 bps | 29 | 37.9% | **+23.71R** | +24.72 / −1.01 | 0.818R | 4.65R |

Per-year net R (@1bps): 2023 −0.01 (flat), 2024 +3.58, 2025 +17.01, 2026 +0.73
(partial), plus the 2022 stub. Positive, robust to fee (7.5bps barely dents it —
7× the real ~1bps micro-gold cost), healthy expectancy, shallow DD. **Confirms
the WS-A proxy validation on native MGC futures.** Long-dominant (the short side
is ~flat) — consistent with gold's secular uptrend over the window.

### ✅ `mhg_pullback_1d` — KEEP LIVE (native confirms, both sides)
Same harness, `pullback_frac 0.5`:

| Fee | Trades | Win% | Net total R | Long / Short | Expectancy | MaxDD |
|---|---|---|---|---|---|---|
| 1.0 bps | 33 | 45.5% | **+29.88R** | +21.49 / +8.39 | 0.906R | 2.90R |
| 7.5 bps | 33 | 45.5% | **+28.73R** | +21.11 / +7.62 | 0.871R | 3.07R |

Per-year net R (@1bps): 2023 +5.46, 2024 +3.34, 2025 +20.28, 2026 −1.23
(partial). Positive, fee-robust, **both directions contribute** (unlike MGC),
best DD of the sleeve (2.9R). **Confirms the copper pullback edge on native MHG
futures.**

### ⏸ `mgc_trend_1h` — STAYS SHADOW, but the roll-artifact hypothesis is REFUTED (updated 2026-07-07)

**Superseded finding.** The first pass (on the spliced v002 shard, +57.77R / 94
trades) guessed the native "pass" was a **roll-gap artifact** and predicted it
would collapse once the gaps were removed. The **roll-adjusted continuous test
now DISPROVES that guess** — the earlier reasoning (the pre-2026-07-07 points
1–4 in this section) was wrong.

Built the roll-adjusted continuous series from a fresh **per-contract** pull
(`per_contract` pull → `market_raw_percontract/MGC/1h/v001`, 86,075 per-contract
bars → **15,003 continuous 1h bars**, 2023-03-21 → 2026-07-06, 14 stitched
contracts) via `scripts/research/build_continuous_contract.py` (#5870), then ran
`backtest_trend.py` (donchian 20, atr 14, stop 2.5, trail 3.0, 7.5bps) on the
**continuous (panama/back-adjusted)** series vs the **spliced (`none`, = today's
adapter behaviour)** baseline on the SAME 15,003 bars:

| Arm | Trades | Win% | Net R | Expectancy | MaxDD | net R by year (23/24/25/26) |
|---|---|---|---|---|---|---|
| **Spliced** (`none`) | 672 | 38.2% | **+221.6R** | 0.330R | 57.7R | +123 / −14 / +83 / +29 |
| **Continuous** (panama) | 680 | 37.9% | **+196.2R** | 0.289R | 62.0R | +120 / −28 / +72 / +32 |

**Removing the roll gaps cost only −25.3R (−11%).** The edge is NOT
gap-manufactured — a Donchian breakout on **roll-adjusted** native MGC 1h is
still **strongly positive** (+196R, +0.29R expectancy, 680 trades over ~3.3y).
The roll-artifact hypothesis is refuted.

**So why does it STILL stay shadow?** The clean result now **conflicts** with the
shadow demote, which was validated on OTHER continuous gold-1h series — **GC=F
1h −15.5R, XAUUSD spot 1h −50.7R** (`docs/research/recombination-sweep-2026-06-18.md`).
Three continuous gold-1h series disagree by sign and by a wide margin — a genuine
**unresolved cross-series conflict**, not a settled promotion case. Likely
drivers, to test before any promotion:
- **Window / concentration.** The native window is 2023-03→2026-07 and **2023
  alone carries +120R of the +196R**; 2024 was negative (−28R). A single
  strong-trend year does most of the work — concentration risk, not a proven
  multi-regime edge. If the GC=F/spot demote windows spanned different years,
  that alone could flip the sign.
- **Session / vendor structure.** Native MGC futures (~23h/day) vs GC=F (Yahoo
  futures) vs XAUUSD spot (24h) have different bar/session structures a breakout
  reads differently.

**Recommendation (unchanged action, corrected reason):** `mgc_trend_1h` **stays
`shadow`** — but NOT for "roll artifact" (refuted); for an **unresolved
cross-series/regime conflict with 2023-concentrated returns**. Before any Tier-3
promotion it needs a **window-aligned walk-forward / OOS** test reconciling the
native-MGC vs GC=F vs spot disagreement (same windows, same fee, per-year
stability). That is a *proposed research follow-up*, **not** a promotion — no
`strategies.yaml` change here. The roll-adjustment tooling that made this test
possible (`ml/datasets/continuous.py` + the per-contract pull, #5870) is now in
place and reusable for the intraday breakout candidates.

## Cross-cutting caveats
- **Sample size:** 29–33 daily trades over ~4y is modest (a daily pullback fires
  ~8×/year). The result is a directional confirmation, not a high-N verdict; the
  live paper soak keeps accruing real fills.
- **Native net R (+25/+30R over ~4y) is lower than the "+56R" proxy headline** —
  expected (different vendor/window/length). The load-bearing fact is the **same
  positive sign, fee-robust, healthy expectancy** on the real instrument.
- **Roll-adjustment gap** (adapter builds stitched, not back-adjusted, series) —
  fine for pullback/mean-reversion cells. **Its impact on breakout/trend cells is
  now MEASURED, not assumed: on native MGC 1h it cost only ~11% (spliced +221.6R
  → continuous +196.2R), NOT the bulk of the P&L** (2026-07-07 continuous test,
  above). A native-futures breakout backtest should still use the roll-adjusted
  continuous series (`build_continuous_contract.py`) for correctness, but the gap
  is a modest correction here, not an edge-manufacturing artifact.

## Intraday shortlist — deferred (but the roll-artifact worry is now quantified)
The intraday test matrix (`docs/research/ib-intraday-strategy-survey-2026-07-07.md`
#1/#2/#5/#6/#7) can now be run **honestly** on the roll-adjusted continuous series
the per-contract pull + `build_continuous_contract.py` produce. The earlier fear
that native-futures breakout results are dominated by roll gaps is **disproved**
for MGC 1h (~11% impact), so the trend/breakout candidates are worth a clean
native run rather than being written off. The survey's headline (**no *validated*
intraday edge yet**) still stands pending those runs + a window-aligned
walk-forward. Deferred to a scoped follow-up.

## Bottom line
The two **LIVE** metals paper strategies are **validated on their own instrument
for the first time** and both **confirm** — keep them live. The **shadow**
`mgc_trend_1h` cell got its **definitive native-instrument test** (roll-adjusted
continuous MGC 1h): the earlier "roll artifact" call is **refuted** — the edge is
real and strongly positive (+196R clean, only 11% below the spliced +221R) — but
it **stays shadow** because it now *conflicts* with the GC=F −15.5R / spot −50.7R
demote on a 2023-concentrated window; the promotion case needs a window-aligned
walk-forward, not a config flip. And the COMEX fix + the roll-adjustment tooling
(#5870) mean the whole sleeve is now natively AND continuously backtestable.
