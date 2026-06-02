# WS-A S1 — Futures Generalization Matrix (2026-06-02)

> Meantime Expansion Program, WS-A S1. First cross-asset probe: do the
> roster's higher-TF edges generalize beyond BTC? Run on the trainer VM
> via `scripts/research/ws_a_futures_sweep.py` (trainer-vm-diag #2632/#2633).
> Raw: trainer `~/ws_a_sweep_out/2026-06-02/{all_metrics.json,SUMMARY.md}`.

## Method (and its limits — read before trusting a cell)

- **Universe:** 18 NinjaTrader-tradeable CME futures (WS-A0 scope), daily
  bars from yfinance continuous `=F` series (ES/NQ ~25y history).
- **Strategies:** the four higher-TF harnesses (trend / fade / squeeze /
  pullback). `ict_scalp` (5m) + `fvg_range` (15m) need intraday history
  yfinance can't serve deep — deferred to an IBKR-intraday follow-up.
- **Eval:** net-of-fee @ **2.0 bps round-trip** (placeholder — futures are
  commission-based; real NinjaTrader commissions verified before live),
  full-period + OOS(2023+) split, both directions.
- **DEFAULT PARAMS, NOT TUNED.** This is the *where-does-an-edge-exist*
  probe. Crypto params don't transfer; winners get a per-symbol re-tune
  before any Tier-3 proposal.

**Caveats that bound the conclusions:**
1. **Continuous-contract artifacts.** `=F` series are back-adjusted; roll
   gaps can inflate breakout/fade edges. A real validation needs proper
   roll handling — treat fat fade/breakout numbers with suspicion.
2. **Low win-rate + fat tails.** Most edges are 23–45% WR positive-
   expectancy R-systems (normal for trend/breakout) — but the fade
   winners (23% WR, maxDD 30–40R) are fat-tailed and fragile.
3. **Default params** understate tunable edges and overstate lucky ones.
4. OOS n is the ~2023+ slice (smaller than full-period n shown).

## Matrix — full-period net-R (OOS-2023+ net-R), n = full-period trades

| Symbol (contract) | Class | trend | fade | squeeze | pullback |
|---|---|---|---|---|---|
| S&P 500 (MES/ES) | index | **+30.2 (+13.2)** n=145 | −113.0 (−7.4) n=399 | +5.4 (+4.0) n=127 | +2.1 (+2.8) n=101 |
| Nasdaq 100 (MNQ/NQ) | index | **+26.5 (+13.1)** n=166 | −103.6 (−40.7) n=379 | −7.3 (−3.5) n=112 | +17.2 (+7.7) n=93 |
| Dow (MYM/YM) | index | +9.6 (+1.5) n=159 | −74.7 (−30.3) n=350 | −14.8 (+2.2) n=111 | −4.1 (−1.4) n=97 |
| Russell 2000 (M2K/RTY) | index | −7.8 (−4.2) n=71 | −5.7 (+3.9) n=105 | −3.3 (+1.9) n=41 | +1.2 (−0.9) n=39 |
| Gold (MGC/GC) | metals | **+27.0 (+8.9)** n=246 | +23.5 (+2.8) n=255 | +20.0 (−0.0) n=61 | **+27.7 (+13.0)** n=143 |
| Silver (SIL/SI) | metals | +70.9 (+7.2) n=253 | +41.0 (+2.2) n=225 | −2.3 (−2.6) n=44 | **+6.4 (+19.8)** n=165 |
| Copper (MHG/HG) | metals | +14.5 (−4.4) n=241 | −31.9 (−0.4) n=269 | +9.7 (+0.3) n=76 | **+80.3 (+13.4)** n=118 |
| Crude Oil (MCL/CL) | energy | +20.2 (+1.2) n=179 | **+27.8 (+23.6)** n=321 | +26.8 (+4.9) n=117 | +3.3 (−3.7) n=102 |
| Nat Gas (QG/NG) | energy | +12.0 (−7.0) n=160 | +1.7 (−3.0) n=347 | +18.3 (−3.6) n=115 | −3.0 (−3.0) n=110 |
| 10Y Note (ZN) | rates | −6.8 (−8.8) n=197 | −13.8 (−15.0) n=297 | −1.3 (−2.3) n=120 | −14.8 (−0.5) n=103 |
| T-Bond (ZB) | rates | +10.1 (−8.1) n=191 | −9.0 (+4.0) n=316 | −15.7 (−5.8) n=114 | −6.5 (−0.4) n=102 |
| Corn (ZC) | grains | +16.8 (+1.8) n=216 | −12.1 (−19.5) n=298 | −4.2 (+5.3) n=86 | +18.9 (+1.2) n=126 |
| Soybeans (ZS) | grains | +54.7 (+1.9) n=216 | +7.7 (−2.2) n=298 | +15.4 (+7.0) n=80 | +21.0 (+0.8) n=121 |
| Wheat (ZW) | grains | +7.7 (−8.1) n=238 | +36.9 (+6.4) n=290 | −1.1 (−1.0) n=83 | −25.4 (−10.2) n=148 |
| Euro FX (6E/M6E) | fx | +2.9 (−2.6) n=194 | +4.7 (+4.2) n=305 | −3.0 (−2.0) n=111 | +4.4 (−6.1) n=104 |
| Yen FX (6J) | fx | −5.1 (−3.9) n=191 | −31.3 (−1.3) n=281 | −8.8 (−4.4) n=108 | −10.3 (−0.5) n=108 |
| CME Bitcoin (MBT) | crypto | +18.4 (+1.7) n=60 | −10.9 (+7.0) n=85 | +23.8 (+2.7) n=38 | +14.9 (−4.0) n=30 |
| CME Ether (MET) | crypto | −1.0 (−5.3) n=42 | −3.8 (−1.5) n=58 | −6.8 (−4.7) n=20 | −6.4 (+4.0) n=24 |

## Findings

1. **Trend generalizes; it's the real edge.** `trend` is net-positive +
   OOS-holding on both equity indices (SPX +30/+13, NQ +26/+13) and the
   metals/grains complex (Gold +27/+8.9, Silver +70/+7.2, Soy +54/+1.9).
   Corroborates `trend_donchian` as the flagship — the edge is structural,
   not BTC-specific.
2. **Fade does NOT generalize to indices — it detonates.** SPX −113R,
   NQ −104R, Dow −75R. This is the *same failure mode* fade showed live on
   BTC (−86R). Strong corroboration for dropping fade from real money, and
   a direct input to **WS-C1** (diagnose fade): fade only survives on
   mean-reverting commodities (Crude +27.8/+23.6, Wheat, Gold) — never on
   trending index futures. Fade is a *regime-specific* tool, not a
   roster strategy.
3. **Best BTC-uncorrelated diversifiers (OOS-holding):** the metals +
   energy + index-trend cluster — exactly the WS-A0 diversification
   target. Top by OOS net-R: Crude/fade, Silver/pullback, Copper/pullback,
   SPX/trend, NQ/trend, Gold/pullback, Gold/trend.
4. **Crypto-via-CME-futures:** MBT (Bitcoin) trend +18/+1.7 and squeeze
   +23.8/+2.7 are modestly positive; MET (Ether) is weak across the board.
   A regulated-futures BTC path exists but adds little over the Bybit perp.

## Re-tune shortlist (S2 — per-symbol tuning, then Tier-3 if it holds)

Ranked by OOS net-R, filtered to net-positive full **and** OOS, n≥20.
**Scrutinize maxDD + the continuous-contract caveat before trusting:**

| Rank | Symbol / strategy | full R | OOS R | n | win% | maxDD R | note |
|---|---|---|---|---|---|---|---|
| 1 | Crude Oil / fade | +27.8 | +23.6 | 321 | 23 | 38.3 | fat-tail; verify roll artifacts |
| 2 | Silver / pullback | +6.4 | +19.8 | 165 | 28 | 20.4 | OOS > full = encouraging |
| 3 | Copper / pullback | +80.3 | +13.4 | 118 | 39 | 10.8 | clean DD |
| 4 | S&P 500 / trend | +30.2 | +13.2 | 145 | 39 | 11.1 | aligns w/ live flagship |
| 5 | Nasdaq 100 / trend | +26.5 | +13.1 | 166 | 44 | 5.9 | lowest DD of the leads |
| 6 | Gold / pullback | +27.7 | +13.0 | 143 | 32 | 15.8 | |
| 7 | Gold / trend | +27.0 | +8.9 | 246 | 39 | 10.7 | |
| 8 | Silver / trend | +70.9 | +7.2 | 253 | 43 | 10.2 | |

(Full 26-cell net-positive+OOS list in the trainer SUMMARY.md / #2633.)

## Next

- **S2 re-tune** the top diversifiers (lead with the low-maxDD trend cells:
  NQ/trend, SPX/trend, Gold/trend — robust + clean DD) on proper
  roll-adjusted history; the fade cells only after the continuous-contract
  artifact is ruled out.
- **WS-C1** gets its first evidence: fade's index blowup vs commodity
  survival names the regime dependence.
- **IBKR-intraday follow-up** to sweep `ict_scalp` + `fvg_range` (the two
  harnesses this daily probe couldn't cover).
- Verify NinjaTrader per-contract commissions before any of these reaches
  a real-money Tier-3 proposal.
