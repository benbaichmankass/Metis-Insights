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
| **ict_scalp SPY 5m (NET, corrected RTH)** | **+6.9R / 82t** | **+4.6R / 23t** | ✅ modest but positive both, net of fees |
| ict_scalp QQQ 5m (NET, corrected RTH) | −1.3R / 138t | +17.0R / 35t | ⚠️ train-flat, OOS-strong — unproven (same one-window pattern as the original 13-trade QQQ result) |
| **fvg_range SPY 15m (corrected RTH)** | +7.4R / 87t | +7.9R / 27t | ✅ positive both |
| fvg_range QQQ 15m (corrected RTH) | −23.0R / 112t | +1.3R / 30t | ❌ |
| trend EURUSD 1h | −28.5R | +6.4R | ❌ |
| trend EURUSD 4h | +7.4R | +2.2R | ⚠️ weakly positive both |
| trend GBPUSD 1h / 4h | +21.1R / −8.0R | +0.6R / −2.6R | ❌ |
| pullback EURUSD / GBPUSD 2h | −6.7R / −11.5R | −8.4R / −12.9R | ❌ |
| ict_scalp EURUSD / GBPUSD 15m | +7.9R / +3.1R (gross) | −0.1R / −0.8R (gross) | ❌ flat OOS before fees |
| fvg_range EURUSD / GBPUSD 15m | 0–3 trades | 0 trades | ∅ uninformative — BTC-scale width filter never triggers on FX majors |

### ict_scalp NET of fee (exact, pass 2 — 2.0 bps roundtrip)

| run | trades | net win% | gross R | **NET R** | net exp R |
|---|---|---|---|---|---|
| XAUUSD 15m train | 203 | 58.1 | +47.3 | **+39.4** | +0.194 |
| XAUUSD 15m OOS | 76 | 55.3 | +12.8 | **+10.2** | +0.134 |
| SPY 5m train | 82 | 50.0 | +10.4 | **+6.9** | +0.084 |
| SPY 5m OOS | 23 | 52.2 | +5.6 | **+4.6** | +0.201 |
| QQQ 5m train | 138 | 46.4 | +4.2 | −1.3 | −0.009 |
| QQQ 5m OOS | 35 | 65.7 | +18.4 | +17.0 | +0.487 |
| EURUSD 15m train | 58 | 51.7 | +7.9 | +4.9 | +0.085 |
| EURUSD 15m OOS | 17 | 47.1 | −0.1 | −1.2 | −0.070 |
| GBPUSD 15m train | 107 | 46.7 | +3.1 | −2.1 | −0.020 |
| GBPUSD 15m OOS | 15 | 53.3 | −0.8 | −1.8 | −0.122 |

Fees matter exactly where expected: tight 15m/5m stops make 2 bps cost
0.03–0.15R per trade. **XAU/USD and SPY survive net; EUR/GBP die; QQQ
was never consistent to begin with.**

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
5. **SPY — not QQQ — is the intraday equities candidate.** On corrected
   session data and net of fees, SPY is positive in both windows on TWO
   families (ict_scalp 5m net +6.9R/+4.6R; fvg_range 15m +7.4R/+7.9R).
   QQQ stays unproven: train-flat, OOS-strong — the same
   one-window-only pattern that made the original 13-trade QQQ result
   decay (see `market-alternatives-2026-06-10.md` §4). QQQ belongs in
   shadow data collection, not promotion.

## Recommendation for Phase 2 (operator decision)

**Wire OANDA first.** Gold alone gives the 1h–4h trend/pullback family a
validated new home (the sweep's strongest cells), covers the metals
exposure the futures legs were buying, and the ICT scalp logic survives
fees there. The FX-major weakness doesn't matter — XAU_USD rides the
same API. **Alpaca second**, carrying (a) the validated daily ETF
replacement legs (trend1d QQQ/SPY ≈ `mes_trend_long_1d`, pullback1d GLD
≈ `mgc_pullback_1d`) and (b) the SPY intraday candidates; QQQ runs in
shadow only. Everything stays behind the existing gates:
paper/practice accounts first, `execution: shadow` before paper-live,
Tier-3 operator approval per strategy promotion. Promotion candidates
must additionally pass the full M8-style validation (k-fold
walk-forward, fee headroom) — this sweep is the screening pass, not the
promotion evidence.

## Reproduction

```bash
# trainer VM, worktree /home/ubuntu/m15-phase0 (branch m15-phase0)
bash scripts/ops/m15_phase0_fetch.sh        # datasets -> data/
bash scripts/ops/m15_phase0_sweep.sh        # pass 1 -> results/m15_phase0/
bash scripts/ops/m15_phase0_rerun_ict.sh    # pass 2 (corrected RTH + net)
```
