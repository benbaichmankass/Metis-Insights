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

### ⏸ `mgc_trend_1h` — STAYS SHADOW; roll-artifact AND 2023-concentration both REFUTED (updated 2026-07-07)

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

**So why does it STILL stay shadow?** The clean result **conflicts** with the
shadow demote, which was validated on OTHER continuous gold-1h series — **GC=F
1h −15.5R, XAUUSD spot 1h −50.7R** (`docs/research/recombination-sweep-2026-06-18.md`).
Three continuous gold-1h series disagree by sign and by a wide margin — a genuine
**unresolved cross-series conflict**, not a settled promotion case.

**The 2023-concentration hypothesis has now also been TESTED and REFUTED
(window-aligned walk-forward, 2026-07-07 —
`docs/research/mgc-trend-1h-walkforward-2026-07-07.md`).** The worry was that the
native +196R was just 2023 (**+120R of it sits in 2023**) while the GC=F/spot
demote windows start in 2024. Restricting the native MGC continuous series to the
**exact demote window (2024-01→2026-06)** — 2023 fully excluded — still yields
**+77.0R** (570 trades; per-year 2024 −26.8 / 2025 +71.0 / 2026-H1 +31.9). So on
the identical window, native MGC (+77R) still disagrees **by sign** with GC=F
(−15.5R) and spot (−50.7R). The cross-series conflict is therefore **structural**
(instrument / vendor / session — native COMEX MGC micro ~23h vs GC=F full-size
Yahoo futures vs 24h spot), **not** a windowing/concentration artifact. Note the
native series is nonetheless **regime-dependent**: even in the aligned window its
2024 is −26.8R (positive-but-with-a-losing-year, not a clean multi-regime edge).

**Recommendation (unchanged action, reason now fully pinned down):**
`mgc_trend_1h` **stays `shadow`** — NOT for "roll artifact" (refuted) and NOT for
"2023-concentration" (refuted by the aligned walk-forward); it stays shadow for
an **unresolved *structural* cross-series conflict**: you can't promote a cell on
one series (native MGC +77R) when the two other legitimate representations of the
same underlying are negative on the identical window, because it's not yet known
which series a live MGC-micro fill tracks. The proposed follow-up is now to
**isolate the driver** (matched-session re-pull of GC=F/spot on the native window
bounds) — see the walk-forward note. **No `strategies.yaml` change here.** The
roll-adjustment tooling that made this possible (`ml/datasets/continuous.py` +
the per-contract pull, #5870) is in place and reusable for the intraday breakout
candidates.

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

## Intraday shortlist — RUN (roll-artifact worry quantified, one strong find)
The intraday test matrix (`docs/research/ib-intraday-strategy-survey-2026-07-07.md`
#1/#2/#5/#6/#7) has now been run **honestly** on the roll-adjusted continuous
series (`docs/research/ib-intraday-shortlist-backtest-2026-07-07.md`, #5902). The
earlier fear that native-futures breakout results are dominated by roll gaps is
**disproved** for MGC 1h (~11% impact). Headline: one strong candidate —
**MGC pullback 1h (+185R over ~3.3y, +0.56R expectancy, positive every calendar
year)** — a GO-to-walk-forward; the trend/breakout intraday cells are weak (MGC
trend 4h +27R, 2023-concentrated) and MES cells are data-starved. Still a
*candidate* pending a proper walk-forward + `account_compat_matrix`, not a
promotion.

## Bottom line
The two **LIVE** metals paper strategies are **validated on their own instrument
for the first time** and both **confirm** — keep them live. The **shadow**
`mgc_trend_1h` cell got its **definitive native-instrument test** (roll-adjusted
continuous MGC 1h) plus a **window-aligned walk-forward**: the "roll artifact"
call is **refuted** (+196R clean, only 11% below the spliced +221R) AND the
"2023-concentration" call is **refuted** (+77R on the 2023-excluded 2024-01→2026-06
window). It **stays shadow** for the surviving reason — an **unresolved
*structural* cross-series conflict** (native MGC +77R vs GC=F −15.5R vs spot
−50.7R on the identical window); the promotion case needs the driver isolated
(matched-session re-pull), not a config flip. And the COMEX fix + the
roll-adjustment tooling (#5870) mean the whole sleeve is now natively AND
continuously backtestable.
