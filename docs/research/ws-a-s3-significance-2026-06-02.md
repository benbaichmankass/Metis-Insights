# WS-A S3 — Significance & Robustness Verdict (2026-06-02)

> Meantime Expansion Program, WS-A S3. Block bootstrap (B=10k, block=8) +
> walk-forward-by-year on the S2-tuned survivors, **full history** (n now
> 145–193, vs the ~20-trade OOS slice that limited S2). Driver:
> `scripts/research/ws_a_s3_significance.py` (trainer-vm-diag #2637).
> Pass bar: positive in a majority of years **AND** bootstrap 5th-pct
> expectancy > 0.

## Verdict

| Lead (tuned config) | n | total | exp | boot p05 exp | P(total>0) | +yrs | Verdict |
|---|---|---|---|---|---|---|---|
| **Copper / pullback** `lb=15 frac=0.5 stop=2.0 trail=4.0` | 145 | +87.6R | +0.604 | **+0.253** | 99.9% | 17/27 | ✅ **PASS (strongest)** |
| **Gold / pullback** `lb=15 frac=0.618 stop=2.0 trail=4.0` | 189 | +61.7R | +0.327 | **+0.081** | 98.9% | 15/27 | ✅ **PASS** |
| Gold / trend `donch=30 stop=2.0 trail=4.0` | 193 | +39.6R | +0.205 | **−0.027** | 92.4% | 14/27 | ❌ **FAIL (marginal)** |

## What survived, and why it's credible

**Two candidates clear a strict 27-year significance bar: Copper/pullback
and Gold/pullback.** Three reasons this is more than a backtest number:

1. **Bootstrap significance.** Resampling the trade sequence in blocks
   (preserving autocorrelation), the 5th-percentile expectancy stays > 0
   — Copper solidly (+0.25R), Gold modestly (+0.08R). The edge is not an
   artifact of lucky trade ordering.
2. **Cross-asset coherence.** The **pullback** (retracement-continuation)
   logic is the winner on *both* metals, with the *same* tuned
   neighborhood (tighter stop 2.0, trail 4.0, lookback 15). One strategy
   generalizing across two correlated-but-distinct symbols is a far
   stronger signal than a single-cell spike.
3. **Breadth in time.** 27 years, 145–189 trades, positive in a majority
   of years.

**Gold/trend fails** — bootstrap p05 expectancy dips just below zero
(−0.027) despite 92.4% P(total>0). Honest call: marginal, not
distinguishable from luck at the 5th percentile. Do **not** advance it on
this data (the metals *pullback* edge is the real one; index/metal *trend*
is the lower-frequency satellite).

## Honesty bounds (still not a Tier-3 basis)

- Internal significance = "vs resampled luck," **not** a live guarantee.
- Daily continuous-contract (`=F`) data — roll artifacts can still inflate
  a retracement system; real roll-adjusted data is the next check.
- 2.0 bps placeholder fees; real NinjaTrader commissions unverified.
- Both passers are positive-expectancy-but-choppy (~12/27 down years) —
  they diversify the book, they don't print every year.

## WS-A conclusion → the carry-forward set

WS-A set out to find BTC-uncorrelated diversifiers. It delivers **two
statistically-vetted candidates with concrete configs**:

- **Copper/pullback** (MHG/HG) — `pullback-lookback=15, pullback-frac=0.5,
  atr-stop-mult=2.0, trail-mult=4.0`
- **Gold/pullback** (MGC/GC) — `pullback-lookback=15, pullback-frac=0.618,
  atr-stop-mult=2.0, trail-mult=4.0`

## Next (operator-gated — needs a venue/data decision, not another sweep)

The edge-discovery phase is *done*; the binding constraints are now
external:

1. **Roll-adjusted data re-validation** of the two passers (rule out the
   `=F` artifact) — autonomous, can proceed.
2. **Demo-execute ladder** — route Copper/Gold pullback to a futures
   paper account once a venue exists (NinjaTrader/IBKR paper), per the
   WS-E standing ladder: no-risk real fills before any real money.
3. **Verify NinjaTrader per-contract commissions** for GC/MGC + HG/MHG.
4. Only after demo + roll-adjusted backtest agree does a Tier-3
   real-money proposal follow — the same ladder `fvg_range` /
   `htf_pullback` just graduated through.
