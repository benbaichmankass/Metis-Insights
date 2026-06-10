# M15 Phase 0 — Generalization Sweep Results (2026-06-10)

> Evidence artifact for the M15 market/platform migration
> ([`market-alternatives-2026-06-10.md`](market-alternatives-2026-06-10.md) §6
> Phase 0). Decides which platform gets wired first in Phase 2.
> Raw outputs live on the trainer VM under
> `/home/ubuntu/m15-phase0/results/m15_phase0/` (one JSON per run +
> `SUMMARY.md` + per-trade `*_trades.jsonl` for the ict_scalp legs).

## Method

- **Data** (Dukascopy via `scripts/ops/fetch_dukascopy_ohlcv.py`; free,
  bid-side): EURUSD / GBPUSD / XAUUSD 15m since 2019-01-01 (~185k bars
  each, resampled in-harness to 1h/2h/4h); QQQ / SPY ETF-CFD 5m since
  2019 filtered to US RTH (month-based DST windows — pass 1 ran on a
  mis-windowed filter that dropped the cash open in DST months; the
  equity legs were **re-run on corrected data** in pass 2, commit
  `7dccf4d`); QQQ / SPY / GLD / copper daily since 2010.
- **Harnesses**: the repo's research harnesses at **default / live-mirror
  params — generalization screening, not tuning**. `trend` 1h+4h,
  `pullback` 2h, `fvg_range` 15m, `ict_scalp` 15m/5m; dailies mirror the
  live futures legs (`trend1d` = `mes_trend_long_1d` params long-only;
  `pullback1d` GLD frac .618 / copper frac .5 = `mgc/mhg_pullback_1d`).
- **Fees**: 2.0 bps roundtrip on trend/pullback/fvg_range. The
  `ict_scalp` harness has **no fee model** (BL-20260610-M15-1) — its
  table rows are gross; the **NET section** below computes exact
  per-trade fees from enriched emit rows
  (`fee_r = bps/1e4 × entry / |entry−sl|`,
  `scripts/ops/m15_net_ict_scalp.py`).
- **Windows**: train = pre-2025-01-01, OOS = 2025-01-01→now (dailies
  split at 2022-01-01). Single split, no k-fold — this is the screening
  pass; anything promoted to Phase 2 gets the full M8-style validation.
- **Caveats**: CFD/spot series proxy the tradeable instruments (ETF ≠
  CFD ticks; OANDA spreads ≠ flat 2 bps); 1.5y OOS for intraday; default
  params favor symbols whose volatility resembles BTC's.

## Headline matrix (net R unless marked gross)

| Leg | Train | OOS (2025→) | Verdict |
|---|---|---|---|
| **trend XAUUSD 1h** | **+78.4R** / 1024t | **+36.8R** / 245t, maxDD 9.2R | ✅ strongest cell in the sweep |
| **trend XAUUSD 4h** | +18.1R / 277t | +20.6R / 65t | ✅ consistent |
| **pullback XAUUSD 2h** | +13.6R / 320t | +25.0R / 62t | ✅ positive both (train maxDD 35R — needs tuning) |
| **ict_scalp XAUUSD 15m** (gross→net below) | +47.3R / 203t | +12.8R / 76t | ✅ see NET section |
| fvg_range XAUUSD 15m | +6.8R / 30t | +3.5R / 23t | ✅ thin but positive both |
| **trend1d QQQ** (MES-replacement mirror) | +16.2R / 15t | +10.9R / 12t, maxDD 3.1R | ✅ clean |
| **trend1d SPY** (MES-replacement mirror) | +16.0R / 14t | +9.2R / 12t, maxDD 2.7R | ✅ clean |
| **pullback1d GLD** (MGC mirror) | +4.9R / 28t | +19.7R / 22t | ✅ positive both |
| pullback1d COPPER (MHG mirror) | −9.6R / 66t | +5.2R / 31t | ⚠️ inconsistent (matches live MHG's lukewarm profile) |
| ict_scalp QQQ 5m / SPY 5m | *pass-2 pending* | *pass-2 pending* | see NET section |
| fvg_range QQQ / SPY 15m | −6.9R / −4.7R | +1.0R / +7.3R | ⚠️ sign-flips across windows |
| trend EURUSD 1h | −28.5R | +6.4R | ❌ |
| trend EURUSD 4h | +7.4R | +2.2R | ⚠️ weakly positive both |
| trend GBPUSD 1h / 4h | +21.1R / −8.0R | +0.6R / −2.6R | ❌ |
| pullback EURUSD / GBPUSD 2h | −6.7R / −11.5R | −8.4R / −12.9R | ❌ |
| ict_scalp EURUSD / GBPUSD 15m | +7.9R / +3.1R (gross) | −0.1R / −0.8R (gross) | ❌ flat OOS before fees |
| fvg_range EURUSD / GBPUSD 15m | 0–3 trades | 0 trades | ∅ uninformative — BTC-scale width filter never triggers on FX majors |

### ict_scalp NET of fee (exact, pass 2)

*To be filled from `SUMMARY.md` § NET when the pass-2 rerun completes —
includes corrected-RTH QQQ/SPY and the fee-adjusted XAU/EUR/GBP legs.*

## Findings

1. **Gold (XAU/USD) is the strongest new market in the sweep** —
   positive train AND OOS across four strategy families *at default
   params*. It also happens to be the metals exposure the futures legs
   were buying (MGC). On the platform map gold is OANDA territory
   (`XAU_USD` trades 24/5 on the practice/live API, fits the
   1h–4h family unchanged).
2. **The daily ETF futures-replacements validate cleanly** — `trend1d`
   QQQ/SPY and `pullback1d` GLD mirror the live IBKR legs' logic with
   tiny drawdowns. The Alpaca leg of the migration is evidence-backed.
3. **EUR/USD and GBP/USD do not carry the roster at default params** —
   consistent with WS-A's "crypto params don't transfer." FX-major work
   would be a tuning project, not a port; deprioritized.
4. **The BTC fvg_range width filter is the wrong scale for FX** —
   0-trade cells. A re-parameterized FX variant is possible follow-up
   work, not a blocker.
5. **QQQ/SPY intraday ict_scalp** — pass-1 numbers were inconsistent
   across windows AND ran on mis-windowed session data; the pass-2
   corrected-RTH + net-of-fee numbers (section above) are the ones to
   trust. *(Interpretation to be added with the numbers.)*

## Recommendation for Phase 2 (operator decision)

*Finalized after pass 2; current evidence points to:* **wire OANDA
first** — gold alone gives the 1h–4h trend/pullback family a validated
new home and covers the metals exposure; the FX-major weakness doesn't
matter because XAU_USD rides the same API. **Alpaca second** for the
daily ETF replacement legs (validated) and any intraday equities work
that survives pass 2. Both stay behind the existing gates: paper/practice
accounts, `execution: shadow` first, Tier-3 promotion per strategy.

## Reproduction

```bash
# trainer VM, worktree /home/ubuntu/m15-phase0 (branch m15-phase0)
bash scripts/ops/m15_phase0_fetch.sh        # datasets -> data/
bash scripts/ops/m15_phase0_sweep.sh        # pass 1 -> results/m15_phase0/
bash scripts/ops/m15_phase0_rerun_ict.sh    # pass 2 (corrected RTH + net)
```
