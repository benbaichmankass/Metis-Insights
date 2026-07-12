---
name: exit-refinement
description: The binding, repeatable pipeline for building, validating, and shipping EXIT improvements (trailing-stop geometry, stale-stops, giveback-stops, partial-TP ladders, ML exit heads) for any strategy×symbol leg — data → harness lever sweep → E0/E1/E1.5 exit-head → live parity check → Tier-3 flip → first-decision health check — plus the committed coverage matrix that is M20's done-condition. Use when the operator says "improve the exits", "process <strategy> for exit refinement", "run the exit pipeline on X", when wiring a NEW strategy (every new leg gets exit-processed), or when asking "which legs haven't been exit-processed". NOT for entry-side tuning (M7/M8 review/tune) and NOT a replacement for the backtesting skill (it composes with it).
---

# /exit-refinement — the exit-improvement pipeline (M20 system)

Codified from the M20 sprint (operator directive 2026-07-12: "we need a
system for how we build new exit refinement strategies and how we test
them"). Evidence anchors: `docs/research/M20-exit-refinement-2026-07-12.md`
(the memo), `docs/research/M20-exit-head-PROGRAM.md` (the ML exit-head
E0–E3 program), sprint log `S-M20-EXIT-REFINEMENT-2026-07-12`.

## The coverage matrix is the contract

`docs/research/exit-refinement-coverage.json` — one row per strategy leg,
one verdict per lever column (`trail_geometry`, `stale_stop`,
`giveback_stop`, `exit_ladder`, `exit_head_ml`). Statuses:
`shipped / passed_unshipped / honest_negative / pending / blocked:<reason>`,
each with a PR/memo ref.

- **Update it in the SAME PR** as any verdict-producing work. A sweep or
  training run whose verdict isn't in the matrix didn't happen.
- **Honest negatives are recorded, never skipped** — a `honest_negative`
  cell is a completed deliverable.
- **A NEW strategy gets a `pending` row at wiring time** — add this to the
  `new-strategy` checklist output; the leg is not "done" until its exit row
  has verdicts or explicit blocks.
- The milestone/health view of "are we done" = no `pending`/`blocked` rows
  on live legs.

## The pipeline (per strategy×symbol×tf family)

**P0 — data.** The family needs (a) 3–5y of native-TF candles reachable
from the trainer and (b) a config-exact harness that can emit per-trade
paths (`--emit-trades`). If candles are missing, that's a `blocked` row +
an ml-backlog item — fixing coverage IS the task, don't silently skip
(MB-20260712-EXIT-ANALYSIS-COVERAGE).

**P1 — evidence read.** Live trade paths from the journal
(`scripts/research/m20_exit_analysis.py`): MFE vs realized R, giveback,
round-trip fraction, hold times. This quantifies WHICH failure mode the
family has (chop-hold, giveback, early-stop) and which levers are worth
sweeping.

**P2 — hard-lever sweep (IS/OOS, config-exact).**
`scripts/research/m20_exit_sweep.py` + the harness levers (stale-stop,
giveback-stop, trail multiples, ladder variants), net of fees. **Gate: a
lever ships only if it beats baseline on net_R AND maxDD in BOTH IS and
OOS** (capital-efficiency tiebreak: net_R per position-day). One lever per
cell; combos only after singles pass (M20 finding: combos were worse).

**P3 — ML exit head (optional, when hard levers leave money on the
table).** The E0–E3 program (`M20-exit-head-PROGRAM.md`): E0 per-bar
dataset (truncation-honest labels) → E1 LightGBM + purged walk-forward +
τ-policy replay → E1.5 conditional shapes if the unconditional policy
fails the trend-tail. Gate as in the program doc.

**P4 — validation standard.** Purged walk-forward (time folds, embargo,
purge on the trade's LAST bar), truncation-honest replay (exit value =
observed close mark — never a re-simulated barrier), live-set sign
agreement when a live sample exists. **The offline validation IS the
confidence gate** (fast-gate doctrine, operator directive 2026-07-12).

**P5 — live mechanical verification (hours–days, NOT weeks).** Deploy the
observe-only scorer/annotator (Tier-2), then:
1. **Feature/trigger parity** — diff live-logged rows against the offline
   recompute for the same bars. The 2026-07-12 parity diff caught three
   real skews in one hour (partial-bar scoring, entry-anchor off-by-one,
   out-of-family scoring) — run it every time; expect near-exact.
2. **First-decision sanity** — scores/triggers in-distribution, dedup and
   in-family guards holding.

**P6 — Tier-3 flip.** YAML declare per the M20 pattern (params on the
strategy leg; absent = off; rollback = delete the lines). Exact diff to
the operator; merge on approval; deploy + restart; verify live HEAD.

**P7 — online soak + first-decision check.** The lever/head soaks LIVE.
The next `/health-review` MUST verify the mechanics of the first real
lever-driven exit; `/ml-review`/`/performance-review` track the realized
`future_r_delta` record. Demotion = delete the YAML lines.

## Hard rules

- **Never blend real/paper/prop** in any evidence read.
- **Truncation-honest counterfactuals only** — no barrier re-simulation
  (the T0.4 lesson).
- **Config-exact sweeps** — the harness runs the leg's ACTUAL YAML params,
  not defaults.
- **In-distribution guards on any shared-monitor scorer** — every
  donchian-family leg reaches the same monitor hook; a head scores only
  its trained (tf, symbols) (the IWM incident, #6201).
- **Closed bars only in live scorers** (live == train; #6207).
- Tier boundaries: research/tooling/matrix = Tier-1; observe-only scorer
  deploys + restarts = Tier-2; YAML/monitor behaviour flips = Tier-3.

## Composes with

`backtesting` (harness entry points + account-compat matrix — mandatory
before routing), `model-training` (trainer runs), `diag-data` (live
evidence pulls), `vm-ops` (deploy/restart), `doc-freshness` (memo/roadmap/
sprint-log sync), `new-strategy` (adds the pending matrix row).
