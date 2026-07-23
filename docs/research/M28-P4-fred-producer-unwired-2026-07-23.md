# M28 P4 gate — verification finding: the FRED valuation-snapshot **producer is unwired**

**Date:** 2026-07-23
**Session:** ml-strategies-roadmap-cont-9tk5ja
**Anchor:** `BL-20260723-M28-P4-GATE-FOLLOWUP` (ml-review-backlog) · milestone **M28** (Macro/Value Speculation Sleeve)
**Tier:** Tier-1 (verification / read-only investigation; no `src`/`config`/order-path change)

## TL;DR

The M28 P4 value-thesis backtest gate cannot run because there is **no data**, and
there is no data because **nothing produces it**. The P4 runner
(`scripts/macro/thesis_backtest_run.py`, #7461) and the whole read side are built
and self-tested, but the **write side — the off-VM FRED value soak that is supposed
to append point-in-time rows to `runtime_logs/valuation_snapshots.jsonl` — was never
wired.** No script, service, timer, or GitHub Actions workflow invokes the producer.

So the correct disposition of `BL-20260723-M28-P4-GATE-FOLLOWUP` **step (1)** is: the
soak is **NOT** producing data, and **wiring the producer is the prerequisite blocker**,
exactly as the backlog item anticipated. This is not "wait for history to accrue" — it
is "history will never accrue until a producer is scheduled."

## Evidence (code-side proof)

The runtime confirmation path (diag relay) was unavailable this session — direct egress
to the live VM (`141.145.193.91:8001`) is firewalled at this environment's network level
(the `DIAG_BASE_URL` env still points at the retired x86 micro `158.178.210.252`), and
`valuation_snapshots` is not on the `/api/diag/log_file` allowlist, so it cannot be
tailed via the read-only diag surface. The finding therefore rests on repo-code proof,
which is conclusive on its own: a file cannot accrue rows if nothing writes to it.

1. **The producer function has zero schedulers.**
   `src/units/strategies/macro_thesis/valuation_feed.py::run_valuation_feed` (which
   produces snapshot rows and appends them via `valuation_store.write_snapshots`) has
   **no caller anywhere in `src/` or `scripts/`** except its own `__init__.py`
   re-export. No CLI entrypoint, no `python -m …`, no cron, no timer.

2. **The FRED fetch is off-VM-guarded and equally uncalled.**
   `fred_adapter.fetch_fred_series_history` refuses to open a FRED socket unless
   `ICT_OFFVM_BUILD_HOST=1` is set (correct — the money VM must never fetch). But a
   repo-wide search shows `ICT_OFFVM_BUILD_HOST=1` is set only by **trainer
   dataset-build** scripts/services (`scripts/ops/build_trainer_datasets.sh`,
   `run_mes_training.sh`, `ict-orderflow-capture.service`, …), and those run
   `scripts.ml.fetch_macro` / `fetch_funding_oi` — the **ML-corpus** FRED pulls, a
   different store. **None invoke the M28 valuation feed** (`fred_fetch_and_history` /
   `build_fred_fns` have no callers).

3. **On the VM, only the read-side tick runs.**
   `src/main.py:680` calls `run_macro_thesis_tick(settings)` every trader tick — but
   that path only **reads** the snapshot store (P3 observe-only scanner) and logs
   would-be theses to `runtime_logs/macro_thesis_soak.jsonl`. It never writes a
   valuation snapshot.

Net: the write side of the point-in-time valuation store is a dangling capability —
fully built, fully tested, never scheduled.

## Why the docs read as if a soak exists

The M28 design doc (§ gap table, "Live macro read on the trading path" → *"a small
on-VM cache refreshed by a timer"*; P1 deliverable (i) *"the live macro cache (keyless
FRED → on-VM point-in-time snapshot)"*), the P3 note (*"inert until valuation snapshots
accrue off-VM"*), and the ROADMAP M28 row (*"once real valuation-snapshot history
accrues from the off-VM FRED value soak"*) all **assume** a producer is running. The
store, feed, adapter, and config (`config/macro_valuation.yaml`) were built and
FRED-validated in P1 — but the *scheduled producer* that was to call them was deferred
and then lost from view. This finding closes that gap in the record.

## The prerequisite: wire the valuation-snapshot producer (recommended design)

**This is the next buildable step for M28** — before the P4 gate can mean anything.
Recommended shape, chosen to keep the money VM socket-free and stay file-backed (the
same pattern as `comms/reports/`, `gpu_spend_ledger.json`, `broker_truth_ledger.json`):

- **Producer = an off-VM GitHub Actions cron** (e.g. daily) that:
  1. checks out the repo, sets `ICT_OFFVM_BUILD_HOST=1`,
  2. runs a thin new entrypoint (`scripts/macro/valuation_snapshot_produce.py`) that
     calls `fred_fetch_and_history(config)` → `run_valuation_feed(...)` to build the
     point-in-time rows (`observed_at` = fetch time; append-only, a revision is a new
     line),
  3. **commits** the rows to a committed path (proposal:
     `comms/macro/valuation_snapshots.jsonl`) so the live VM picks them up via
     `ict-git-sync` — no SSH write to the money box, no live-VM socket.
- **Reader** = teach `valuation_store.snapshot_log_path` (and the P4 runner's
  `--snapshots`) to prefer the committed `comms/macro/…` path when present, falling
  back to `runtime_logs/…`. This is an additive, best-effort, **read-path** change to a
  live module — argue Tier-1, but flag it on the board.
- Keyless: FRED `fredgraph.csv` needs no API key (`_FRED_CSV_URL`), so **no operator
  secret** is required. The seed series are already declared in
  `config/macro_valuation.yaml`.

Alternative (design's literal wording): a small **on-VM** timer that refreshes the cache.
Rejected here because it would open a FRED socket from the money VM (against the
off-VM-only discipline) or require relaxing the guard on the live box.

**Tiering:** the producer script + the Actions cron are Tier-1 (offline tooling + CI).
The reader-path fallback is a best-effort read change (Tier-1, board-flagged). Nothing
here influences a live order — P5 (options expression) and P6 (`c_macro` overlay) remain
Tier-3, and the P4 gate itself must PASS out-of-sample before either is proposed.

## Disposition

- `BL-20260723-M28-P4-GATE-FOLLOWUP` **step (1) = DONE**: producer verified unwired;
  blocker surfaced. Step (2) (run the gate on accrued history) stays **blocked** on the
  producer.
- New anchor for the prerequisite build: **`MB-20260723-M28-VALUATION-PRODUCER-UNWIRED`**
  (ml-review-backlog) — wire the off-VM producer + reader fallback per the design above.
- ROADMAP M28 row corrected to state the producer is unwired (not "accruing").
