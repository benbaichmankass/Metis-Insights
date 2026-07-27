# M28-P4 value-thesis gate — RUN on 21yr history (2026-07-27)

## The headline (operator-directed correction)

The M28-P4 value gate had been recorded as "waiting ~weeks for the FRED producer
to accrue point-in-time history." **That was a stale phantom gate.** The historical
backfill `comms/macro/valuation_snapshots_backfill.jsonl` — **~21 years of
point-in-time valuation snapshots (2005-01-01 → 2026-07-18, 10,125 rows: SPY ERP,
TLT/IEF real-yield, GLD/SLV gold-silver-ratio, credit spread)** — was already
**committed** (built by `scripts/macro/valuation_snapshot_backfill.py`, the
value-sleeve analogue of `backfill-shadow-predictions`, which reconstructs decades
of dated FRED history in one shot). The gate runs on that history **in minutes, not
weeks**. This is the same lesson as the M30 pivot: **discover on historical
backtest, don't wait for forward accrual.**

Run (trainer-VM relay #7754): fetch ~21yr daily ETF closes (SPY/TLT/GLD/SLV/IEF,
5,091–5,423 closes each via the off-VM `fetch_macro_candles.py`) → the P4 scorecard
on the committed backfill.

## Result — a clean NULL (the unconditioned value sleeve)

`thesis_backtest_run.py --snapshots comms/macro/valuation_snapshots_backfill.jsonl
--candles-dir data/macro_candles --rebalance-every 30 --horizon-days 30
--fee-frac 0.001`:

| Metric | Value | Read |
|---|---|---|
| theses scored | **1,104** (263 rebalances) | powered — NOT "n=0 forever" |
| win_rate | 0.497 | coin-flip |
| mean_net_return | +0.0018 | ~flat |
| **calibration_rank** (Spearman conviction→net) | **−0.0038** | **conviction does NOT predict return** |
| baseline_mean_net (naive all-long) | +0.0065 | — |
| **edge_vs_baseline** (must be > 0 net) | **−0.0047** | **FAILS — loses to naive all-long** |

Calibration bins show no monotonic conviction→return relationship (high-conviction
bin 0.75–1.0, n=712, mean_net +0.0029, hit 0.489; mid bin 0.5–0.75, n=308, −0.0020).

**Verdict:** the *unconditioned* value sleeve (S1 former: cheap→long / rich→short on
ERP/real-yield/GSR) does **not** beat naive all-long out-of-sample net-of-cost over
21 years, and `thesis_conviction` does not calibrate. This matches the M28
signal-research program's conclusion (value, like COT/crypto, is null as a
standalone construction). It is the honest **baseline** the M36 Track C conditioners
must beat.

## Why this is the *right* result, not a dead end

This is exactly the substrate M36 Track C exists to improve. The C1 scenario
(M29-conditioned) conviction, the C2 progress-exit, and the C3 crowding conditioner
are meant to **condition** this sleeve — and they run on the **same committed 21yr
history**. The immediate, runnable next step is **C4**: extend `thesis_backtest.py`
to score the **conditioned lifecycle** (scenario-conviction + progress/crowding
exit) vs this null baseline on the 21yr backfill. No waiting.

## Corrected the phantom gate

The stale "wait for the FRED producer to accrue" claim is corrected in `ROADMAP.md`
(M28 row + M36 Track C) and `M36-macro-intelligence-and-crowding-DESIGN.md` (C4):
the value gate + the C4 conditioned lifecycle run on the committed backfill today.
`BL-20260723-M28-P4-GATE-FOLLOWUP` is resolved by the backfill, not by accrual.
