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

### ⏸ `mgc_trend_1h` — STAYS SHADOW (native "pass" is a roll artifact, not an edge)
`scripts/research/backtest_trend.py` (donchian 20, atr 14, stop 2.5, trail 3.0,
fee 7.5bps hardcoded): native MGC 1h = **+57.77R / 94 trades / 41.5% win**
(long +39.5, short +18.3).

This **contradicts** the shadow demote (−15.5R on real GC=F 1h, −50.7R on XAUUSD
spot; `docs/research/recombination-sweep-2026-06-18.md`). **It does NOT reverse
the demote** — the native result is almost certainly a **roll-gap artifact**:
1. The native 1h shard is **stitched dated contracts without roll back-adjustment**.
   At each contract roll the price gaps (contango/backwardation); a Donchian
   **breakout** reads that gap as a breakout and "rides" it → manufactured edge.
2. It's not a fee artifact — it's net of **7.5 bps** (the research script's
   hardcoded fee), so the +57R survives a high cost. Fee isn't the cause; the
   **data** is.
3. The demote was validated on **continuous** GC=F/spot (no roll artifacts) —
   the trustworthy series for a breakout cell. Two independent continuous series
   (GC=F futures + XAUUSD spot) both said negative.
4. The pullback cells (which enter on pullbacks into a range, not on gap-driven
   breakouts) are far less roll-sensitive — which is why their positive native
   results ARE credible while the breakout's is not.

**Recommendation:** `mgc_trend_1h` stays `shadow`. A genuine native-futures test
of the trend cell would need **roll-back-adjusted continuous** MGC data (the
`ibkr_offvm` adapter does not build a continuous series today) — logged as a
follow-up, not a promotion path.

## Cross-cutting caveats
- **Sample size:** 29–33 daily trades over ~4y is modest (a daily pullback fires
  ~8×/year). The result is a directional confirmation, not a high-N verdict; the
  live paper soak keeps accruing real fills.
- **Native net R (+25/+30R over ~4y) is lower than the "+56R" proxy headline** —
  expected (different vendor/window/length). The load-bearing fact is the **same
  positive sign, fee-robust, healthy expectancy** on the real instrument.
- **Roll-adjustment gap** (adapter builds stitched, not back-adjusted, series) —
  fine for pullback/mean-reversion cells, **corrupting for breakout/trend cells**.
  Any future native-futures backtest of a breakout strategy must account for it.

## Intraday shortlist — deferred
The intraday test matrix (`docs/research/ib-intraday-strategy-survey-2026-07-07.md`
#1/#2/#5/#6/#7) is **partly compromised by the same roll issue**: the trend/breakout
candidates (MGC trend 4h) on stitched native data would inherit the artifact. The
pullback-based intraday cells (MGC pullback 1h, MES pullback 1h) are credible and
worth a native run, but the survey's headline stands — **no validated intraday
edge yet**, and now with the added constraint that native-futures breakout
backtests need roll-adjusted data. Deferred to a scoped follow-up.

## Bottom line
The two **LIVE** metals paper strategies are **validated on their own instrument
for the first time** and both **confirm** — keep them live. The one **shadow**
strategy's surprising native "pass" is a data artifact, not a reason to promote —
it stays shadow. And the COMEX fix means the whole sleeve is now natively
backtestable going forward.
