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
| `cleared` | declares n, effect and basis; n meets the floor |
| `underpowered` | declares them and n is below the floor — **blocked**, converts to a data-acquisition task |
| `undeclared` | does not declare them — **blocked**, and emphatically not "fine" |
| `unverifiable` | declared, but `basis` is missing — a number with no derivation is a wish |
| `not_applicable` | `kind: deterministic` with a written `why_not_inferential` |

⚠️ **`not_applicable` is not `cleared`.** A deterministic job faced no bar; a
cleared one faced it and met it. Tallying them together would report "N jobs
cleared the power gate" over a population where some never took the test.

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
