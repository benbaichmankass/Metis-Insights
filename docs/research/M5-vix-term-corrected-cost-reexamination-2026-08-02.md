# M5 — `vix_term` re-examination with corrected costs (2026-08-02)

Closes the macro-program item **M5** (RESEARCH-PROGRAM-2026-07-30 § Track M): *"Re-examine
the `vix_term` lead (the one robust M28 survivor) with corrected costs. It is
ETF-expressed, so T1's fee fix applies to its Sharpe too."*

**Bottom line — honest negative, verdict UNCHANGED and REINFORCED.** `vix_term` remains a
*validated lead, not a deployable standalone edge* (the A-S5 / #7577 landing). Two findings:

1. **The M5 premise is a misconception — there was no fee to correct.** The deployability
   backtest (`scripts/macro/vix_term_backtest.py`, M31 Track A-S5) expresses `vix_term` as an
   **index-future** long/short/flat timing position and already charges a realistic **~1.5 bp
   round-trip futures cost** — it was *never* ETF-expressed and never carried the T1 phantom-fee
   over-charge (the ~25× commission bug that hit the commission-free-instrument harnesses). So
   T1's fee fix does not apply here; the size read was already correct-cost.
2. **Re-running today surfaces NEW evidence FOR the "not deployable" verdict.** The SP500/DJIA
   21-day full-sample Sharpe has swung from **0.18 / 0.05 (#7577)** to **0.64 / 0.62 (today)** at
   **identical n=118**, purely because FRED's `SP500`/`DJIA` daily series are **rolling ~10-year
   windows** that slid forward since #7577. NASDAQ100 (deep history, no roll) is **byte-identical**.
   A standalone edge that triples its Sharpe from a few-month window slide with no change in n is,
   by definition, window-fragile — which is exactly what "validated lead, not deployable" means.

## The measurement

`ICT_OFFVM_BUILD_HOST=1 python scripts/macro/vix_term_backtest.py --horizons 5,10,21,42 --cost-bps 1.5`
(keyless FRED, a-priori direction short-high/long-low term-ratio, non-overlapping stride=horizon,
OOS split 40%). FULL-sample Sharpe, this run vs the #7577 record:

| target | window (this run) | #7577 FULL Sharpe (H5/10/21/42) | today FULL Sharpe (H5/10/21/42) |
|---|---|---|---|
| **SP500** | 2016-08-01 → 2026-07-30 (rolling 10y) | 0.07 / −0.02 / **0.18** / 0.05 | 0.07 / 0.16 / **0.64** / 0.29 |
| **DJIA**  | 2016-08-01 → 2026-07-30 (rolling 10y) | 0.08 / 0.10 / **0.05** / −0.02 | 0.08 / 0.06 / **0.62** / 0.25 |
| **NASDAQ100** | 1990-01-02 → 2026-07-30 (no roll) | −0.06 / −0.30 / 0.04 / 0.15 | −0.06 / −0.30 / 0.04 / 0.15 |

(Today's SP500 21d: FULL Sharpe 0.643, CAGR 9.2%, maxDD −24.0%, n=118; OOS Sharpe 0.677, ret
+41.0%, maxDD −12.6%, n=48. DJIA 21d comparable. Short horizons 5–10d stay weak-to-nil; NASDAQ100
stays the worst leg — negative FULL Sharpe at 5d/10d, brutal drawdowns — which continues to qualify
the A-XI "generalizes to NQ" *statistical* claim as not a *deployable* one.)

## Why the swing is the mechanism, not a new edge

The window-slide hypothesis was verified against the raw FRED spans, not assumed:

| series | n (dated) | span | rolling? |
|---|---|---|---|
| `SP500` | 2514 | 2016-08-01 → 2026-07-31 | **yes — exactly 10 years** |
| `DJIA` | 2514 | 2016-08-01 → 2026-07-31 | **yes — exactly 10 years** |
| `NASDAQ100` | 10225 | 1986-01-02 → 2026-07-31 | no |
| `VIXCLS` | 9241 | 1990-01-02 → 2026-07-30 | no |
| `VXVCLS` | 4692 | 2007-12-04 → 2026-07-30 | no (binds the ratio start) |

`SP500` and `DJIA` are FRED's rolling-10-year daily convenience series — they drop the oldest data
as time advances. #7577 ran on a window ending ~months earlier, so its SP500/DJIA 10-year window
*started* ~months earlier too; the trailing-percentile feature + non-overlapping 21d anchoring then
graded a materially different decade at the same period count (n=118). `NASDAQ100` has a fixed 1986
start (the ratio is bound by `VXVCLS`'s 2007 start), so it is invariant to the slide → byte-identical
across the two runs. NDX is the control that isolates the mechanism: **only the rolling legs moved.**

This is precisely the fragility the #7577 verdict named — *"a thin tilt that happened to work in the
recent 40%"* — now demonstrated on the full sample too: the "recent decade" the rolling window
happens to hold determines the headline Sharpe. A deployment decision cannot rest on a number that
is an artifact of *when it was run*.

## Reproducibility fix shipped alongside

`vix_term_backtest.py` now **emits each leg's actual data span** (`data_span` in the JSON; a
`window … → … (n_aligned=…)` line in the text output) with a note that SP500/DJIA are rolling ~10y.
Before this, a reader could not see that two runs graded different windows — the silent-window gap
that let the 0.18→0.64 swing look like a real change rather than a slide. Filed the residual as
`BL-20260802-VIXTERM-ROLLING-WINDOW-SPAN` (low): a fully reproducible SP500/DJIA size read needs
either a pinned start date or a non-rolling S&P source (e.g. `^GSPC` via Stooq/yfinance) — not
required to trust the *verdict* (NDX's fixed-window null already settles deployability), only to make
the SP500/DJIA *magnitudes* comparable across dates.

## Disposition

- **M5: DONE — honest negative.** No corrected cost changed anything (there was no fee error on this
  leg); the re-run reinforces `edge_real_but_thin` / not-deployable-standalone. `vix_term`'s only
  defensible use stays a low-weight conviction/exposure tilt inside a larger book — Tier-3,
  operator-gated, and optional given the size read. No new proposal is enacted or drafted.
- Cross-reference: `docs/research/M28-signal-research-ledger.md` § "M31 Track A-S5" (the original
  size read) + the follow-up note added there today.
