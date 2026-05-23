# VWAP Viability Verdict — S-STRAT-IMPROVE-S4-B (2026-05-23)

> **Sprint:** S-STRAT-IMPROVE-S4-B (Tier-1 analysis). **No live change.**
> **Question:** can the live vwap strategy be made net-of-fee profitable
> by selectivity (entry threshold) and/or fee-efficiency (SL width)?
> **Answer: No.** vwap has no durable inherent edge in backtest; tuning
> the entry/SL knobs does not make it net-positive.

## Evidence (live relay, fresh 365-day BTCUSDT 5m data, net-of-fee @ 7.5 bps rt)

| Run | Issue | Mode | Coverage |
|---|---|---|---|
| Threshold sweep | #1784 | entry σ ∈ {0.8,1.0,1.2,1.5,2.0} | 8 windows × 14d / 365d, seed 42 |
| Param sweep | #1785 | entry σ × SL σ (12 configs) | 3 windows × 14d / 365d, seed 42 |

### Threshold sweep (#1784) — net-of-fee by entry threshold

| Entry σ | Trades/win | Gross R | Net R | Net+ windows |
|---|---|---|---|---|
| 0.8 | 127 | −1.3 | −60.3 | 0/8 |
| 1.0 (live) | 110 | −0.4 | −49.7 | 0/8 |
| 1.2 | 95 | −0.8 | −43.4 | 0/8 |
| 1.5 | 77 | −1.9 | −36.5 | 0/8 |
| 2.0 | 47 | −2.1 | −21.8 | 1/8 |

Selectivity halves the bleed (−50R→−22R) but never reaches net-positive;
**gross is ~zero-to-negative over the full year** — the signal has no edge
before fees.

### Param sweep (#1785) — net-of-fee by entry × SL

| Entry σ | SL σ | Trades/win | Net R | Net long | Net short |
|---|---|---|---|---|---|
| 0.8 | 0.3 | 178.7 | −87.1 | −68.7 | −18.4 |
| 0.8 | 0.5 | 157.3 | −102.1 | −53.3 | −14.2 |
| 0.8 | 0.7 | 137.7 | −79.6 | −34.9 | −12.4 |
| 1.0 | 0.3 | 158.3 | −67.5 | −59.4 | −12.5 |
| 1.0 | 0.5 | 139.0 | −75.0 | −46.4 | −7.4 |
| 1.0 | 0.7 | 124.0 | −63.7 | −30.9 | −6.1 |
| 1.2 | 0.3 | 137.7 | −47.3 | −51.3 | −5.4 |
| 1.2 | 0.5 | 121.3 | −58.6 | −37.1 | −3.6 |
| **1.2** | **0.7** | 108.0 | **−41.7** (best) | −26.3 | −5.0 |
| 1.5 | 0.3 | 109.7 | −71.8 | −44.4 | −2.9 |
| 1.5 | 0.5 | 96.3 | −73.6 | −36.3 | −0.6 |
| 1.5 | 0.7 | 85.7 | −71.0 | −23.1 | −1.8 |

**0 of 36 windows (12 configs × 3) are net-positive.** The best
(least-bad) config is still −41.7R per 14-day window. Wider SL improves
fee-efficiency modestly; it does not create edge.

## Findings

1. **No inherent edge.** Across both sweeps, vwap mean-reversion on
   BTCUSDT 5m is net-negative at every tested entry/SL combination over a
   regime-diverse year. Gross R is ~flat-to-negative *before* fees.
2. **Selectivity + fee-efficiency are necessary-not-sufficient.** They
   reduce the bleed (fewer trades = less fee drag; wider stop = smaller
   fee fraction of R) but cannot rescue a no-edge signal.
3. **Long-leg bleed is regime (down-market), not a tradeable asymmetry.**
   Per the operator directive, this is NOT to be hard-coded as a
   short-bias; it should resolve via a regime-robust trend filter, if at
   all.
4. **The earlier "thin +$11 gross" (S2) was a favorable 7-day live
   window.** Powered over 365 days, gross edge is absent.

## Caveats (intellectual honesty)

- The backtest is a **simplified model**: it does not replicate the live
  strategy's break-even move, partial close, recent-context filter, or a
  per-config HTF gate. It is a conservative proxy for the *raw signal*.
- The param sweep is **3 windows** (low power) — magnitudes are noisy.
  But the unanimous 0/36 net-positive across a 12-config grid is a robust
  *qualitative* verdict, corroborated by the 8-window threshold sweep
  (0/8 to 1/8) and the live audit (−$36/7d net).
- **One vwap lever remains untested:** an HTF/regime *edge* filter
  (compare mode) — does trend-alignment create gross edge? Filed for
  S4-B-3. Even a positive result there must clear the fee hurdle.

## Implication — program pivot (operator-directed 2026-05-23)

The operator's steer: "think deeper about base strategies that have an
inherent edge … our current strategy just isn't actually that robust,
even in theory." S4-B confirms this for vwap. The program pivots from
**tuning vwap** to **edge-first strategy assessment**:

- **S4-B-3** — vwap HTF/regime edge filter (compare mode), last vwap
  experiment.
- **S5** — inherent-edge audit of `turtle_soup` + `ict_scalp` net-of-fee
  on fresh 365-day data (ict_scalp backtest already instrumented;
  turtle_soup needs a harness). Do THEY have an edge before fees?
- **S6** — strategy-edge assessment + recommendation: which (if any)
  current strategy has a durable, fee-survivable edge; what a robust
  base strategy looks like; whether to retire/replace vwap.

No live change is proposed. Retiring or replacing a live strategy is
Tier-3 and stops at the operator-approval gate.
