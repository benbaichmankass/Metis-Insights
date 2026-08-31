# `research/queue/` — the standing research & testing job queue

One **YAML file per job**, named for its id (`RQ-YYYYMMDD-NNN.yaml`). The
dispatcher (`scripts/research/dispatch_queue.py`) reads this directory, grades
each job, and fires what it routes.

This is **R4 + R5** of
[`docs/research/RESEARCH-WORKFLOW-ARCHITECTURE-2026-08-27.md`](../../docs/research/RESEARCH-WORKFLOW-ARCHITECTURE-2026-08-27.md).
Operator-decided 2026-08-27: one file per job, and the dispatcher fires.

## Why a directory and not a fifth register

Measured before building: **nothing in this repo dispatches compute from a
register.** `docs/claude/strategy-refinement-queue.json` has exactly one non-doc
reader (`scripts/ops/classify_strategy_tier.py`); the three review backlogs
(983 / 109 / 104 rows) are read only by guards and reporters. So this is new
capability, not a rebuild.

A big JSON was rejected on this repo's own history: `scripts/ops/backlog_append.py`
exists *solely* because a naive append to a 983-row JSON reformats ~21k lines and
re-attributes every pre-existing row to the appending PR. One file per job has
none of that — a job is addressable by path, diffs alone, and is added or retired
by adding or deleting a file.

⚠️ **`scripts/ml/_heavy_queue.py` is NOT this.** That is a mutual-exclusion flock
that serialises heavy jobs on the 6 GB trainer. It holds no pending work and
routes nothing. They compose: a job routed to the trainer takes that lock when it
runs.

## The two gates

**Power** (`power_state`) — may this job run at all?

| state | meaning |
|---|---|
| state | meaning | runs? |
|---|---|---|
| `cleared` | declares n, effect and basis; n meets the floor AND observed data supports it | yes |
| `accruing` | declared data-acquisition — the author states up front it cannot answer yet, and what would change that | **yes** |
| `not_applicable` | `kind: deterministic` with a written `why_not_inferential` | yes |
| `underpowered` | n is below the floor — the DESIGN asks for too small a sample | blocked |
| `infeasible` | n clears the floor and **observed data refutes it** — the SCOPE is wrong, not the design | blocked |
| `undeclared` | does not declare them — emphatically not "fine" | blocked |
| `unverifiable` | declared, but `basis` or `feasibility` is missing / unreadable | blocked |

⚠️ **Three different states may run, for three different reasons.**
`not_applicable` faced no bar, `cleared` faced it and met it, `accruing` faced it
and declared it would fail. Tallying them together reports "N jobs cleared the
power gate" over a population where some never took the test. Consumers must
branch on the state, never on `runnable`.

⚠️ **`underpowered` and `infeasible` are not interchangeable**, and reporting one
as the other sends the author to rewrite the wrong half of their design.
*Underpowered* = your n is too small for your own effect size → fix the design.
*Infeasible* = your n is fine on paper and these legs cannot deliver it → narrow
the scope, declare accrual, or wait for data.

**Route** (`route_state`) — `runner` · `trainer` · `gpu` · `unroutable`.
`unroutable` is a **refusal**, never a fallback: a job declaring both a GPU and
trainer-resident data names no destination that exists, and a job declaring more
memory than any destination has is refused rather than sent to be killed.

## The power floor is a floor

`required_n` uses the normal approximation:
`n >= (z_α/2 + z_β)² / d²` (doubled per group for `two_sample`).

⚠️ **Clearing it is necessary, not sufficient, and the error is one-directional.**
It assumes iid draws; backtest trade sequences are autocorrelated and walk-forward
folds overlap, so the true requirement is **higher**, never lower. A job that
clears can still be underpowered; a job that fails is underpowered for certain.
It also does not adjust for multiplicity — a 40-cell sweep must declare its own
adjusted `alpha`.

## Fields

```yaml
id: RQ-20260827-001          # must equal the filename stem
title: ...
question: >-                 # what is being asked, BEFORE it runs
status: queued               # queued | running | done | blocked | retired
cadence: once                # once | daily | weekly | monthly
kind: experiment             # experiment | deterministic

# kind: experiment → required
power:
  expected_n: 400
  min_detectable_effect: 0.3
  effect_units: sd           # `sd` (a standardised d) or the metric's own units
  sd: 1.2                    # required when effect_units is not `sd`
  design: one_sample         # one_sample | two_sample
  alpha: 0.05                # 0.10 | 0.05 | 0.01 — untabulated values are REFUSED
  power: 0.80                # 0.80 | 0.90 | 0.95
  basis: >-                  # HOW expected_n was derived. Empty ⇒ unverifiable.

  # REQUIRED. `basis` is prose and cannot be checked — this is the part the gate
  # goes and VERIFIES. Its own comment said "a number with no stated derivation
  # is a wish", but any non-empty string was accepted as the derivation, which
  # is the `new-table-wiring-guard` lesson: a guard cheaper to lie to than to
  # satisfy is worse than no guard. Worked example — RQ-20260830-002 declared
  # expected_n: 50 against a floor of 49.06 and CLEARED, legitimately, by 0.94,
  # while 5 of its 14 legs are 1d equity legs (~20 trades/year) that achieved
  # 4, 5, 6, 7 and 8. Knowable in advance. Nothing asked.
  feasibility:
    source: corpus           # corpus | none
    corpus: e35              # a key in research_disposition.CORPORA
    statistic: min_per_leg   # min_per_leg | median_per_leg | max_per_leg
    legs: [trend_donchian]   # OPTIONAL but usually right — scopes the statistic
                             # to the legs THIS job runs on. Without it a
                             # per-leg claim is graded against a fleet-wide
                             # number: e35's fleet max is 50 (sol_4h) while
                             # trend_donchian has never exceeded 49, so an
                             # unscoped max_per_leg would certify a single-leg
                             # job on a DIFFERENT leg's data.
    # For a genuinely new leg with no history, use instead:
    #   source: none
    #   accrual_basis: >-    # REQUIRED with `none`, or it is an opt-out rather
    #                        # than an answer. Say why no observed data supports
    #                        # expected_n and what would change that. ⇒ accruing.

# kind: deterministic → required instead
why_not_inferential: >-      # why no sample/effect applies. Empty ⇒ blocked.

routing:                     # DECLARED, never inferred
  needs_trainer_resident_data: false
  needs_gpu: false
  peak_memory_gb: 2.0        # required — undeclared ⇒ unroutable
  est_minutes: 12

run:
  workflow: some-workflow.yml
  inputs: {k: v}

lands:                       # R2 — a run's deliverable is a LANDED result
  store: docs/research/x.jsonl
  assert_field: account_id
  min_rows: 1

last_dispatched_at: null     # stamped by the dispatcher; drives cadence
```

## Running it

```bash
# dry run (the DEFAULT — reports what it would do, fires nothing)
python3 scripts/research/dispatch_queue.py

# actually dispatch
python3 scripts/research/dispatch_queue.py --fire --ref main
```

⚠️ **GPU routes spend real money.** Measured 2026-08-27 from
`comms/gpu_spend_ledger.json`: 7 RunPod runs in 2026-07, lifetime **$0.2164**,
largest single run $0.0987, against a **$10/month** cap. Two gates hold outside
this directory — the burst workflow's ledger preflight and its `GPU_BURST_ARMED`
arm gate — plus `--max-gpu-dispatches-per-run` here, because the ledger cap is
*monthly* and cannot bound a loop inside one run.
