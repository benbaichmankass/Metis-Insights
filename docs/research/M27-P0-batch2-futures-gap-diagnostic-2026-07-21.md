# M27 P0 Batch-2 follow-up — futures 5m data-continuity diagnostic (2026-07-21)

**Resolves the open question in `PB-20260721-M27-FUTURES-5M-LOWSIGNAL`:**
is MES/MGC/MHG's 8-16 trades/yr on `ict_scalp_5m` (Batch-2,
`M27-P0-batch2-futures-findings-2026-07-21.md`) genuine setup rarity, or a
data-continuity artifact suppressing the detector?

**Answer: predominantly a data characteristic of the current pull, not
(mainly) genuine setup rarity — the 5m series for all three symbols is
majority flat-bar-contaminated.** This is evidenced, not merely inferred
from the low trade count.

## What the diagnostic found

`scripts/research/m27/diagnose_futures_gaps.py` run against the real
Batch-2 IBKR 5m CSVs (trainer relay #7279):

| Symbol | Total bars | Flat-bar % of series | 20-bar windows contaminated |
|---|---|---|---|
| MES | 57,519 | 48.9% | 61.4% |
| MGC | 59,109 | 50.8% | 72.0% |
| MHG | 58,911 | **86.0%** | **96.6%** |

"Flat-bar" = a bar whose close is byte-identical to the previous bar's
close (a run of ≥5 such bars). **Nearly half to nearly all of each
series is a run of repeated, unchanging prices**, and — critically — the
overwhelming majority of the strategy's own 20-bar rolling lookback
windows (the largest of `ict_scalp_5m`'s `sweep_lookback_bars=12` /
`swing_lookback_bars=20` / `atr_period=14`) contain at least one such run.
This is the exact, direct mechanism behind the Batch-2 finding that the
frozen 5m vol terciles were degenerate (`q33=0.0` on all three symbols) —
a rolling-window vol calc over a flat run is mathematically zero — and it
starves the FVG+sweep detector of real price action for most of the
series, independent of whether the underlying setup is genuinely rare.

## Why: liquidity, not (obviously) a pipeline bug

The flat-bar percentage ranks **MES (49%) < MGC (51%) < MHG (86%)** —
exactly the real-world liquidity ordering of these three micro contracts
(S&P micro > gold micro > copper micro). The pull (`ibkr_offvm.py`,
`pull_mes_ibkr_history.sh`) deliberately uses `useRTH=False` (24-hour
Globex session, `whatToShow=TRADES`) to capture the full trading day —
so a large share of each "day" is the genuinely thin overnight electronic
session for a micro futures contract, which is a fundamentally different
liquidity profile from crypto's always-on market (Batch-1's basis for
comparison). Several of the longest flat runs DO extend into what should
be the RTH window (e.g. MES's longest run, 262 bars ≈ 21.8h, ends almost
exactly at the 20:00 UTC RTH close) — that's harder to explain by
overnight thinness alone and isn't fully disambiguated here; it may
reflect a genuinely quiet session (holiday-adjacent, etc.) or a data-pull
gap that got backfilled as a repeated last price. **Not conclusively
resolved which mechanism dominates** — both a real liquidity effect and a
possible artifact in how no-trade periods are represented are consistent
with the data; this diagnostic establishes the *scale* of the problem
(evidenced above), not a single root cause.

## Roll-boundary discontinuities — a secondary, minor factor

235-335 large single-bar return spikes (|z| ≥ 6) per symbol, but only
9-19% are within 5 days of a quarterly roll date — most extreme moves are
NOT contract-roll splicing artifacts. Given how dominant the flat-bar
issue is, a plausible explanation for many non-roll spikes is simply the
"catch-up" jump when price resumes after a long flat/thin stretch, not a
roll artifact. Roll-proximity is not the primary confound here.

## Recommendation (no Tier-3 — proposals only)

Regardless of which exact mechanism dominates, the fix path is the same
and matches the original Batch-2 recommendation — now evidence-backed
rather than inferred purely from trade-count scarcity:

- **Re-pull RTH-only** (`useRTH=True`) for a genuinely liquid, low-flat-bar
  5m series, and/or **go coarser (15m/1h)** where genuine trading activity
  dominates each bar even with some overnight thinness folded in.
- **Do not re-run the existing 24h/5m data expecting a different answer** —
  the binding constraint is proven to be the bar composition, not (only)
  the setup's rarity.
- If pursued further: inspect the raw pulled JSONL's volume field (not
  just the CSV's OHLC) for the longest RTH-spanning flat runs (e.g. MES's
  262-bar run) to distinguish "genuinely zero volume" from "a pull gap
  backfilled with a repeated price" — this diagnostic script only has the
  OHLC CSV, not the source volume-annotated JSONL.

## Status update — `PB-20260721-M27-FUTURES-5M-LOWSIGNAL`

Marked **resolved-with-next-step** in the backlog: the genuine-rarity vs
data-artifact ambiguity is resolved (predominantly data-characteristic,
quantified above); the concrete next action (RTH-only or coarser re-pull)
is a fresh piece of work, not a re-run of what already exists.
