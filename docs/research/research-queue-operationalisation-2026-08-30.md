# Operationalising the research queue — three corrections and two jobs

**Date:** 2026-08-30 · **Session:** `018wqzuqBjxkiaEEBr8kJC59` · **Milestone:** M40 (R5)

Task as given: *make long-running experiments run continuously outside any one
session, and make their results readable by a later session.* The brief came
with a measured state and a ready-made first batch of work. **Two of its three
premises did not survive checking, and the batch is not runnable** — that is the
substance of this document, and it is worth more than the jobs I enqueued.

---

## 1. Correction — R2 is not unwired; it is wired to 2 of 22

The brief: *"`assert_rows_landed.py` is referenced but not wired into the
producing workflows."*

**Measured:** `scripts/ci/assert_rows_landed.py` **exists** (`--self-test` with
13 planted controls, registered in `scripts/ci/run_guards.py`), and is called by
**`e35-bracket-sweep.yml`** and **`trainer-offload-train.yml`**. `ROADMAP.md`
M40 already records R2 as *"BUILT … wired into"* both.

The real gap is the other ~17, and **it is not "add the assertion to them"** —
`BL-20260827-EIGHTEEN-EVIDENCE-WORKFLOWS-UPLOAD-AND-LAND-NOTHING` says so in
terms (*"NOT sufficient: adding `assert_rows_landed` to all of them, which would
answer the wrong question loudly"*), and there is a recorded operator decision of
2026-08-27 against it, because for most of them **nobody has decided what store
they would assert against**. That is R1, not R2. The classification half is
already done in
[`evidence-workflow-landing-triage-2026-08-29.md`](evidence-workflow-landing-triage-2026-08-29.md),
which also replaced the hand-typed inventory with
`scripts/ops/evidence_workflow_inventory.py` after it went stale in one day.

---

## 2. Correction — the 12 ungraded legs are REFUSED BY DESIGN, not neglected

The brief offered these as a ready-made first batch: *"12 of the 52 M20 matrix
rows carry no corpus rows at all… Eight are `ict_scalp` legs… Three are
`blocked:no_free_lane_candle_feed` — that blocker is itself a queueable
data-acquisition task."*

**The 12 reproduce exactly** — but only against `e35-bracket-corpus.jsonl`, not
`m20-sweep-corpus.jsonl` (against the latter only **2** rows are absent, and
neither is `ict_scalp`). So the ungraded column is `bracket_geometry`
specifically; these legs are graded on the other lever columns.

⚠️ **State the probe as well as the answer.** My first pass asked "which matrix
rows have no *graded lever cell*" and returned **0** — a clean negative that was
simply the wrong population. It was separable only because the join carried a
positive control (50 of 52 strategies found in the m20 corpus; 40 of 52 in the
e35 corpus). A zero from an unvalidated probe would have read as "the brief is
wrong", which is the opposite of the truth.

**Why they are ungraded** — from `e35_shard_plan.py`, not inferred:

```
shard-plan: 41 job(s); 14 not scheduled (no_feed_source=2, out_of_scope_family=12)
  REFUSED qld_trend_long_1d: no_feed_source:QLD
  REFUSED tqqq_trend_long_1d: no_feed_source:TQQQ
```

and the scope rule itself, `e35_bracket_geometry_sweep.py::plan_legs`:

```python
if fam not in ("donchian", "pullback", "squeeze"):
    # `scalp`/`fvg` carry a REAL `tp_at_r`/`tp_r` bracket rather than the
    # 50.0 sentinel, so this grid would mean something different on them.
    # Out of SCOPE, recorded as such — not silently absent.
```

So the breakdown of the 12 is:

| n | legs | why ungraded |
|---|---|---|
| 9 | the 8 `ict_scalp` legs + `fvg_range_15m` | **`out_of_scope_family`** — the bracket grid sweeps `tp_r` against a 50.0 no-TP sentinel; these legs carry a real TP, so the same grid measures a different thing |
| 2 | `tqqq_trend_long_1d`, `qld_trend_long_1d` | `no_feed_source` — no free-lane feed for TQQQ/QLD |
| 1 | the aggregate "shadow fleet" row | not a dispatchable leg at all |

**Dispatching the 9 would not merely be a no-op — it would be methodologically
wrong.** The sweep would refuse them by family and schedule zero jobs, which its
own guard already calls a failure (*"An empty matrix is a green run that tested
nothing"*). Had the guard been absent, it would have produced a green run and a
verdict measuring the wrong quantity.

⚠️ **A matrix-accuracy finding falls out of this, and it is what made the brief
wrong.** Those 9 legs carry `bracket_geometry: pending`, whose legend meaning is
*"not yet processed"* — which reads as **someone should just run it**. That is
exactly what the brief inferred and instructed. The matrix legend already has
two statuses that fit better: `n/a` (*"structurally inapplicable"*) and
`blocked` (*"cannot process yet (reason given)"*). `pending` on a leg the only
grading sweep refuses by design is a label naming a state the code does not
have — the **unprovenanced-diagnostic class A** substitution this repo already
enforces against in `scripts/`, appearing here in a data file instead.

**Not changed here.** `docs/research/exit-refinement-coverage.json` is inside a
live session's claimed scope (`01HYXKHpDQeWv3u4rjWWoL2J`, board START 10:11Z),
and the fix is a judgement about which of `n/a` / `blocked:out_of_scope_family`
each row should carry. Filed, not applied.

---

## 3. Observation — the dispatcher's cron does not run when it says

Not a diagnosis; the numbers, so the next session does not have to re-derive
them. Declared schedule: `cron: "20 6 * * *"`.

| run | event | started |
|---|---|---|
| 1 | `schedule` | **2026-08-28T18:49:56Z** |
| 2 | `schedule` | **2026-08-29T12:44:50Z** |
| — | — | **no 08-30 run** as of 10:30Z |

Two runs, both `schedule`, zero `workflow_dispatch`. They are **12.5 h and 6.4 h
after** the declared 06:20Z, and **17h55m apart** rather than 24h.

GitHub documents `schedule` as best-effort and delayed or dropped under load,
and this repo carries 120 workflow files with heavy CI, so ordinary free-tier
deprioritisation is the leading explanation. **I did not establish it**, and the
alternative — that something else is wrong — is not excluded.

⚠️ **This bears directly on the operator's ask.** *"Runs continuously outside any
one session"* currently rests on a trigger that has fired twice in three days,
each time hours off schedule. Whatever else is built, **the cadence mechanism
itself is not yet trustworthy**, and a `cadence: monthly` job's dispatch date is
only as reliable as the cron that evaluates it.

---

## 4. What was actually enqueued

Two jobs, both `route=runner`, both landing into a store whose producer
**already** calls `assert_rows_landed`, so neither needs the R1 decision the 17
outstanding workflows are blocked on.

- **`RQ-20260830-001`** (`once`) — re-sweep `trend_donchian`'s shipped bracket
  cell. The census's highest-value leg, and it trades real money on `bybit_2`.
- **`RQ-20260830-002`** (`monthly`) — re-validate **all 14 live in-scope legs
  whose `bracket_geometry` is `shipped`**. The leg list is *derived* (status ==
  shipped ∧ execution == live ∧ present in the shard plan), not typed.

**Why a re-validation is the right recurring job, not a filler.** A validation is
a measurement with a date. The matrix carries a dedicated `shipped_gate_failed`
status for *"LIVE in config, but a LATER re-sweep failed its gate"*, and on the
one family systematically re-measured at live parity the stale-decision base rate
was **3 of 4**. Nothing re-measures these on a cadence today; every re-sweep so
far happened because a session chose to look. That is precisely the class of work
the operator asked to run without a session driving it.

⚠️ **`RQ-20260830-002` is a watchlist, never a disposition.** 14 legs at a
per-cell `alpha=0.05` produces roughly one spurious verdict by construction; the
queue README requires a multi-cell sweep to declare its own adjusted alpha, and
this one declares that its output routes to a human instead. Changing a live
lever is Tier-3 regardless.

**The power gate was verified to BITE, not merely to pass** — two planted
controls:

| control | verdict |
|---|---|
| `expected_n: 20` (below floor) | `blocked_power` — *declared n=20 is below the floor 49.1 for d=0.4* |
| `power.basis: ""` | `blocked_power` — *`power.basis` is empty …* |
| restored | `would_dispatch  route=runner` |

The floor it computed (49.1) matches the hand-derivation
`(1.96+0.8416)² / 0.4² = 49.06`, so `expected_n: 50` clears **deliberately and
by ~1 trade** — recorded in both job files as *read a null result here as
underpowered, never as no effect*.

---

## 5. What remains

- **The 9 `out_of_scope_family` legs still have no path to a bracket verdict.**
  Grading them needs a grid designed for legs carrying a real TP — a research
  question, not a dispatch. The matrix status should stop implying otherwise (§ 2).
- **R1 for the 10 accumulate-bucket workflows** — unchanged, operator-ordered.
- **The cron (§ 3)** — until it is understood, cadence is aspirational.
- **The GPU spend gates remain unobserved.** The queue contains no GPU job, so
  nothing here exercises the ledger preflight, the `GPU_BURST_ARMED` gate, or
  `--max-gpu-dispatches-per-run`. Per
  `OI-20260827-RESEARCH-QUEUE-DISPATCHER-NEVER-FIRED` they must not be described
  as verified, and this session does not describe them so.
