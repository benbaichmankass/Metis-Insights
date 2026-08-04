# Sprint Log: S-ROADMAP-REVIEW-WORKPLAN-2026-08-04

## Date Range
2026-08-04 (single session).

## Objective
Operator-requested **full roadmap review + forward-looking prioritized workplan**,
with three explicit emphasis areas that the operator directed be treated as
**converging into one more robust system** (a "master AI model" at the top, mutual
support at every decision level — not three parallel tracks):
1. **Technical strategies** — the *next* step, not fine-tuning: genuinely new ideas,
   incl. combining aspects of existing strategies into more optimized ones.
2. **Macro sleeve** — planning + deep research on *which indicators* and *what
   framework* turns a macro view into winning trades, BEFORE firing off more tests.
3. **MLs** — clean up the long roster (promising vs drop), and unlock the *next
   level* of models with a more prominent place in the system.

Deliverable: a prioritized, autonomous-ready workplan grounded in the verified
current state, organized so the three areas support each other.

## Tier
Tier-1 (docs only). No `src/`, `config/`, order-path, live-VM, or review-backlog-JSON
writes.

## Starting Context
Built on the live-verified [`S-ROADMAP-STATUS-REVIEW-2026-08-01`](S-ROADMAP-STATUS-REVIEW-2026-08-01.md)
baseline (diag-relay #8266, 15 endpoints) and the [`S-WEEKLY-REVIEW-EXEC-2026-08-01`](S-WEEKLY-REVIEW-EXEC-2026-08-01.md)
execution arc — this session does **not** re-run a `/system-review`; it is
roadmap- and workplan-centric and forward-looking.

## Repo State Checked
- `ROADMAP.md` milestone table + `ROADMAP_MACRO.md` (read in full).
- `config/strategies.yaml` (57 cells → ~9 types on 4 engines), `config/regime_policy.yaml`,
  `config/pairs.yaml`.
- `ml/configs/*.yaml` (89 active manifests), `ml/manifest.py`, `ml/promotion/gates.py`,
  `ml/shadow/factory.py`, the dataset families.
- The decision path: `src/runtime/intent_multiplexer.py`, `intents.py::aggregate_intents`,
  `src/core/coordinator.py::multi_account_execute`, `src/runtime/conviction*.py`,
  `src/runtime/allocator_ev.py`.
- `docs/research/` verdict docs + `docs/claude/{health,performance,ml}-review-backlog.json`.

## Files and Systems Inspected
Three parallel read-only research passes (strategy roster, ML roster,
signal-convergence architecture) over the above. No VM mutation, no live-state
pull beyond the 08-01 baseline (no material drift expected in 3 days for a
forward-planning doc; every runtime claim tagged verified-live-08-01 or repo-record).

## Work Completed
Authored [`docs/research/ROADMAP-REVIEW-WORKPLAN-2026-08-04.md`](../research/ROADMAP-REVIEW-WORKPLAN-2026-08-04.md)
— the review + workplan. Key syntheses:

1. **Executive posture:** one proven deployed edge (BTC/SOL ML vol-gate); real
   money modestly negative; the research frontier is now mostly honest-null. The
   next level is **not** more strategies/models/macro-tests in parallel — it is the
   **convergence layer** that makes existing pieces support each other.

2. **The spine + two keystones.** The decisive code finding: the fused conviction
   number (`conviction.py::compute_conviction`) is **stamped on every order package
   and read by nothing on the order path** — the operator's "one basis for risk"
   is already computed and thrown away. **Keystone A** = a net-of-cost EV/P_win head
   that unblocks FIVE stalled things (conviction sizing, M18 allocator selection,
   M23 meta-labeling, conviction-driven conflict resolution, the `c_reg` lens).
   **Keystone B** = breaking the ~78–400 real-label wall as shared infra.

3. **Strategies:** the roster has *already disproven* standalone MR/fade; only
   Donchian-trend + ICT-scalp survive. Proposed 6 structural hybrids that harden
   the survivors (FVG-confirmed breakout, vol-gating the pullbacks, the built-but-
   unwired killzone-hardened ict_scalp, cross-asset gating, VWAP-partial exits, a
   4h vol-cycle sleeve) — each fuses ≥2 existing validated components and has a
   ready backtest harness.

4. **Macro:** reframed per the M28 08-02 correction — macro's job is to **condition
   the master model (`c_macro` lens), not trade a standalone book.** The work is a
   research-design phase FIRST (indicator universe + per-indicator mechanism +
   pre-registered gate + a conviction-PnL harness), not more single-signal
   percentile producers.

5. **ML:** only the vol-gate reaches an order; 61/89 manifests train daily and do
   nothing. Plan: cleanup (~89→~25–30, move dead/disproven to `ml/configs/retired/`)
   → wire the OFF-cells for gate-ready heads → the net-of-cost EV/P_win head
   (Keystone A) → M20 exit head to advisory → learned fusion.

6. **The convergence spine (C1–C6)** and a **4-wave prioritized workplan** (W0
   ready-now → W4 master model), each item with Tier/first-action/done-condition.

7. **Operator directive mid-session (2026-08-04) — the #1 priority correction.** The
   operator flagged that framing the label wall as "still unsolved" is itself the
   **recurring breach**: the augmentation infra has been built repeatedly, yet each
   new session re-cites it as the blocker. Verified against the code + backlog: the
   infra IS fully built (`conviction_meta.include_backtest`/`union`,
   `setup_candidates` `event_source` taxonomy incl. `backtest`+`live_paper`,
   `record_harness_trades`, `split_live_holdout`, the walk-forward family, 6 macro
   backfills) — **but `trades.is_backtest=1` count is ZERO in every month**
   (`BL-20260731-BACKTEST-AUGMENTATION-NEVER-FED`): the harness writes the
   `backtest_results` table not `trades.is_backtest=1`, and the pooled builds never
   pass `include_backtest`. It was scoped (`WORK-PLAN-2026-08-02.md §A1`, roster
   corrected #8417, "Build = fresh session") and **the fresh session never ran.**
   Reframed **Keystone B** from "break the wall (research)" to a new **§2b — the #1
   priority**: EXECUTE the built path (P0.1 feed the rows / P0.2 wire the builds) +
   PROVE-IN-USE (P0.3 source_breakdown) + ENFORCE (P0.4 `training-population-guard` +
   extend `claim-basis-guard`) + CANONIZE (P0.5). Promoted to **W0.0**, ahead of all
   other wave items. This is the durable never-again mechanism: a live-only decision
   head or an un-sourced "blocked on labels" claim goes RED in CI. Highest-leverage
   first move within the master model itself remains **C1** (reductive conviction
   sizing on demo), but **P0 runs first.**

## Validation Performed
Cross-checked every disproven/proven/needs-more claim against the quoted verdicts
in the record (the 08-01 review's research ledger, `ROADMAP_MACRO.md` change log,
the three review backlogs). No new runtime claims made beyond the 08-01 baseline.
The workplan's "do-not-reopen" list (§8) mirrors the record's closed verdicts so a
future session doesn't re-litigate them.

## Documentation Updated
This log + the workplan doc + the ROADMAP Historical Sprint Ledger row (same PR).
No canonical-doc contradictions introduced (no schema/endpoint/architecture change;
the doc is additive planning). ROADMAP.md milestone statuses left untouched (this
is a planning doc, not a status edit — avoids racing any concurrent status session).

## Contradictions or Drift Found
None introduced. The workplan explicitly reconciles the M28 "value dead vs
validated-but-weak lead" nuance (08-02 correction) and the exit-capture-deepdive
wrong-premise caveat by not depending on either.

## Risks and Follow-Ups
- The workplan is a **plan**, not an execution — each wave item still carries its
  own Tier gate (most order-path items are Tier-3, backtest-gated). Nothing here
  changes live behavior.
- The broker-truth ledger is ~3 weeks stale (08-01 §C-T3) — the "real money"
  authoritative figure will drift until an operator Bybit-UM export is provided;
  noted but out of scope for this planning doc.
- Next session should pick from **Wave 0** (all Tier-1, cold-startable): W0.1 ML
  cleanup, W0.2 read the two convergence soaks, W0.3 macro series-id fix, W0.4 the
  pullback vol-split evidence.

## Next Recommended Sprint
**W0.0 (§2b / P0) — connect + use + canonize the label-augmentation infra — is the
operator-designated #1 priority and the immediate next action.** It is execution +
enforcement, not research: build the `research-backtest-augment` free-runner
workflow, run the config-exact pooled backtests so `trades.is_backtest=1` goes
0→thousands, wire the pooled builds to pass `include_backtest`, ship
`training-population-guard`, and close `MB-20260530-001` +
`BL-20260731-BACKTEST-AUGMENTATION-NEVER-FED` with measured proof. Executes the
already-scoped `WORK-PLAN-2026-08-02.md §A1` (do NOT re-scope). Then W0.1 (ML
cleanup), W0.2 (read the convergence soaks), W1.1 (reductive conviction sizing on
demo).

## Wrap-Up Check
- [x] Current state grounded in the live-verified 08-01 baseline (no re-pull needed for a plan).
- [x] Three focus areas addressed with NEW directions, not fine-tuning.
- [x] Areas organized to converge (the spine + two keystones), per the operator's directive.
- [x] Prioritized, autonomous-ready workplan (Tier/action/done each).
- [x] Disproven vs needs-more cleanly separated; a "do-not-reopen" list included.
- [ ] doc-freshness run at session end (pending).
