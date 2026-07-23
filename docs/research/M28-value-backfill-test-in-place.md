# M28 — historical backfill & the "test-in-place" pattern

**Problem.** A live soak accrues data one tick at a time, so a new signal (or the
M28 P4 value-thesis gate) can't be validated for weeks. The ML side already solved
this with `backfill-shadow-predictions` — replay a model over history instead of
waiting for live shadow rows — so it can promote on mechanical/statistical
evidence quickly. The value/macro sleeve needs the same.

**Why it's cheap here (by design).** The value spine
(`valuation.py` → `valuation_feed.py` → `fred_adapter.py`) is **pure,
deterministic, and takes its data injected** — built explicitly to be replayable
offline. FRED returns each series' *full* history. So we can reconstruct years of
point-in-time snapshots in one shot.

## The mechanism (shipped)

- **`scripts/macro/valuation_snapshot_backfill.py`** — fetches full dated FRED
  history (`fred_adapter.fetch_fred_series_history_dated`) and, for each as-of date
  `D` at a chosen cadence, computes each metric's value-read using **only** the
  history slice `date ≤ D` (as-of-or-prior value + `date ≤ D` history) → one
  point-in-time snapshot row per `(symbol, metric, D)`, stamped `observed_at=D`,
  `source="fred_backfill"`. Leakage-safe by construction; full-regen (idempotent).
- **`scripts/macro/fetch_macro_candles.py`** — pulls historical daily closes
  (Yahoo, keyless) for the seed universe so the P4 gate can score forward returns.
- **`.github/workflows/macro-valuation-backfill.yml`** — runs backfill → candles →
  P4 gate off-VM and lands the reconstructed snapshots + the scorecard on `main`.

Verified: the backfill reconstructed **30,321 point-in-time rows spanning
1962→2026** from real FRED, and the P4 gate ingested them and derived 786 monthly
rebalance dates (candle fetch is runner-side).

## Point-in-time honesty (the one caveat)

FRED `fredgraph.csv` returns **latest-revision** values, not the as-of-published
vintage. For the wired metrics — real yield (DFII10), term slope (DGS10/DGS3MO),
credit spread (BAMLH0A0HYM2) — those are **market rates that are never revised**,
so the backfill is genuinely point-in-time. For **revised** series (future EIA
storage, earnings), true PIT needs FRED's ALFRED vintage API; until then a
revised-series metric's backfill carries mild revised-data lookahead — flagged via
`source="fred_backfill"`, never hidden.

## The generalization — "test-in-place"

The pattern: **any signal built pure + historical-input-injectable gets an
immediate backfill → walk-forward scorecard instead of a live soak**, collapsing
idea→production time. Two instances exist (ML `backfill-shadow-predictions`; this
value backfill). As new strategies/signals are added, wiring their evaluation this
way — a reconstruct-history harness + a leakage-safe replay gate — should be the
default so promotion is gated on evidence available *now*, not weeks out. M29's
system-ID calibration rides the same seam (real EIA/NG history via an injected
reader).
