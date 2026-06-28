# P5 — Opening-Range Breakout on MES: result (2026-06-26)

**Verdict: SHELVE.** ORB-on-MES does **not** clear the P5 gate on the available
MES data, at any configuration tested. Recorded as a clean negative result; the
pre-gate did its job (caught a regime-specific edge before it reached paper).

## What was tested
- **Harness:** `scripts/backtest_orb.py` (this session, PR #4648, merged to `main`).
  5-min RTH bars (America/New_York 09:30–16:00), OR = first N bars, entry on first
  close beyond OR high/low, stop = opposite-OR end (or 0.5×ATR), skip day if
  OR width > W×ATR(14), flat by RTH close, unleveraged, fees in points round-trip.
- **Data:** trainer `datasets-out/market_raw/MES/5m/v001/data.jsonl` — 86,897 bars,
  span **2025-01-03 → 2026-05-22** (~16.5 months). Run on the trainer VM
  (diag relay issues #4663, #4664).
- **Gate (P5):** PF>1.3 AND daily-Sharpe(×√252)>0.7 in **every** in-sample k-fold
  (k=5), holdout daily-Sharpe ≥0.5, ≥2 distinct positive fold-years — evaluated at
  **2× fees** (`--fee-multiplier 2.0`).

## Results (all at 2× fees unless noted)

| Config | Trades | Net R | Gate | Failure signature |
|---|---|---|---|---|
| N=3, opposite-OR, W=1.5 | 45 | +12.7 | FAIL | folds 1/2/4 negative; holdout Sharpe −2.26 |
| N=1, opposite-OR, W=1.5 | 99 | — | FAIL | holdout Sharpe −2.81; 2026 −8.6R |
| N=6, W=1.5 | 10 | — | FAIL | too few trades |
| N=3, 0.5×ATR stop, W=1.5 | 45 | +178 (1×) | FAIL | fold 1 −10.7R; 2026 flat; maxdd 20R — mirage |
| Walk-forward {1,3,6}, W=1.5 | 83 OOS | +38.6 (1×) | FAIL | picks N=1 every seg; holdout Sharpe −2.89 |
| N=1, W=2.5 / 3.0 / 5.0 | 145 / 158 / 175 | ~+39–45 | FAIL | fold 1 negative; 2026 −12 to −17R |
| N=3, W=2.5 / 3.0 / 5.0 | 111 / 134 / 208 | ~+29–38 | FAIL | fold 1 negative; 2026 +3 to −0.2R (flat) |

## Why it fails (the pattern is identical across every config)
1. **All profit is concentrated in 2025; 2026 is flat-to-negative.** Across every
   (N, W, stop) combination, the 2025 by-year net_r is solidly positive and 2026 is
   ≤0. The gate's "≥2 positive fold-years" rule fails structurally, but more
   importantly the *economic* read is a decayed/regime-specific edge.
2. **The most-recent holdout is negative in every variant** (daily-Sharpe −2.2 to
   −2.9). Whatever worked in 2025 H1 is not working on the most recent data.
3. **Not a sample-starvation artifact.** Loosening the OR-width skip filter from
   1.5×→5.0×ATR raised the sample 3–5× (45→208 trades) without changing the verdict:
   fold 1 stays negative and 2026 stays flat/negative at every threshold. The edge is
   genuinely absent in the recent regime, not hidden by the filter.
4. **Low win-rate, convex profile (29–38% WR)** with profit from a few RTH-close
   runners — so single bad folds (a cluster of OR-width stops) dominate. Inconsistent
   fold-to-fold, which is exactly what the per-fold gate rejects.

## Caveats / what would change the call
- **History is short (16.5 months) and recent (2025–2026).** It cannot test the
  memo's multi-year (2015–2025) ORB-on-index thesis — it only shows that on the most
  recent ~16.5 months ORB-on-MES has no robust edge net of 2× fees. A deeper pull via
  `scripts/ops/pull_mes_ibkr_history.sh` would let us test the long-horizon claim.
- **But the recent-holdout negativity is a real red flag regardless of depth** — even
  if a multi-year backtest were positive in aggregate, the strategy is not working
  *now*, so it would not be paper-promotable today.

## Recommendation
**Do not promote.** Shelve ORB-on-MES as a directional sleeve on current evidence.
Logged to the performance-review backlog (PB-20260626-001) with explicit revisit
conditions: (a) pull multi-year MES 5-min history, AND (b) only re-test if a
materially different exit structure is motivated (the opposite-OR / 0.5×ATR stops
both produce the same 2025-only, holdout-negative shape). The `backtest_orb.py`
harness stays in the tree for that future re-test and for the SPY/QQQ/IWM extensions
named in the memo.
