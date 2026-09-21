# Engineering plan — the infra the research programme needs

> **Doc status:** `unknown` · category `plan` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
> **Companion to** [`OPERATING-PLAN-2026-09-21.md`](OPERATING-PLAN-2026-09-21.md) and
> [`RESEARCH-PLAN-2026-09-21.md`](RESEARCH-PLAN-2026-09-21.md).
> **Carried by** rows `E3`–`E9` in [`MANAGER-CHECKLIST.json`](../claude/work/MANAGER-CHECKLIST.json).

---

## The split, because it keeps being confused

Phases **A–D** of the operating plan repair the **operating model** — how work
is chosen, moved and reported. This plan is the **research infrastructure** —
what actually runs an experiment and lands its result. They are different
programmes with different failure modes, and the second is what has quietly
been broken.

**Measured 2026-09-21:**

| finding | number |
|---|---|
| Backtest harnesses in the tree | **15** |
| …wired to no workflow | **9** |
| …carrying no cost terms at all | **6**, including the `vwap` harness |
| Research workflows producing an artifact + an issue comment and nothing durable | **19 of 23** |
| `replay-pregate-nightly` reports ever landed on `main` | **0**, since inception |
| Default candle fixture | 5,001 rows spanning **3.5 days** of 2022 |
| Research queue units | **5**, nothing due since 2026-08-31 |

**Every one of these is a reason a research result does not become a decision.**

---

## E3 — Make the cost model reach every harness

**The single highest-value engineering item in the repo**, because R1 depends on
it and R1 gates everything.

- `src/runtime/execution_costs.py` is correct and is the one owner. The problem
  is entirely at the call sites: harness module globals set
  `SLIPPAGE_BPS_ROUNDTRIP = 0.0` and `FUNDING_BPS_PER_WINDOW = 0.0`, documented
  as *"deliberate and load-bearing"* for backward compatibility.
- **That compatibility argument has expired.** It preserved the comparability of
  old runs; the old runs are what we no longer trust.
- **Work:** flip the defaults to the venue-resolved values; wire the 6 harnesses
  that have no cost terms at all; add a guard that fails a harness importing a
  cost constant it then overrides with a literal.
- ⚠️ **`resolve_cost_policy` already distinguishes `None` ("nobody chose" →
  venue default) from an explicit `0.0` (a deliberate fee-only arm). Keep that
  distinction** — a fee-only arm is a legitimate experiment; a fee-only
  *default* is the defect.

## E4 — A real corpus, and one way to get candles

- The default fixture is 3.5 days of 2022. Anything that silently falls back to
  it produces a confident answer about nothing.
- **Work:** a per-symbol committed corpus with stated coverage; `--data` made
  mandatory rather than defaulted; a guard that fails a harness run whose input
  row count is below a floor.
- ⚠️ **The fixture stays** — it is a fast smoke path and deleting it would break
  every self-test. What changes is that **reaching it by default becomes
  impossible**. It is the *default*, not the file, that is the defect.

## E5 — Every research workflow lands a durable, queryable result

- 19 of 23 produce an artifact and an issue comment. Artifacts expire; comments
  are not queryable. **A result nobody can query later is not evidence** — and
  under the standing mandates, "an evidence record" has to be a real object a
  resolver can read.
- **Work:** one result schema, one committed location, and the commit step in
  the shared composite action rather than re-implemented per workflow.
- **This is what makes `MD-PROMOTE-S1-S2` possible at all.** Without it there is
  nothing for the mandate to read.

## E6 — Unblock `replay-pregate`

Two independent faults, and both must go:

1. `runtime_logs/**` is not in `TIER1_SURFACE`, so `pr-landing-guard` R5 rejects
   the commit — **0 reports on `main` since inception**.
2. The trainer swap-thrashes: 6 of 6 nightly runs die at 10 of 22 heads, and
   **no non-BTC head has ever been graded.**

- **Work:** add the surface; then either cut the head count to fit the trainer's
  memory or raise the memory. The second is an OCI capacity question — the
  Ampere pool is **full at 4 of 4 OCPU**, so raising it means taking capacity
  from somewhere.
- ⚠️ **Fixing only (1) produces a green workflow that still grades a third of
  the fleet.** Do not report it as fixed on the strength of the commit landing.

## E7 — Wire the 9 orphan harnesses

- A harness with no workflow runs when a human remembers it, which is never.
- **Work:** one dispatchable workflow per harness family, taking the leg and the
  corpus as inputs, landing its result through E5.
- Pairs with the research queue: a queued unit names its instrument, and the
  instrument must be dispatchable for the queue to mean anything.

## E8 — The testing queue

The operator's original ask, and it only becomes real after E3–E7:

> *"build out infra as a testing queue so there are always tests running,
> maximizing the existing infrastructure and resources."*

- **Work:** a scheduler that keeps the runners busy with queued research units,
  bounded by cost, prioritised by the cycle priority.
- ⚠️ **It goes last on purpose.** A queue that keeps runners busy against a
  fee-only corpus and a 3.5-day fixture manufactures wrong answers faster. The
  capacity is not the constraint today — **the constraint is that results do not
  become decisions**, and E3–E7 are what fix that.
- **Capacity that exists and is idle:** GitHub-hosted runners (free for this
  repo), the trainer VM (1 OCPU / 6 GB, currently swap-thrashing on one job).
  The runner pool is the underused one.

## E9 — Retire what the reset orphaned

- 67 guard scripts are on disk, unreachable from `run_guards.py`, kept because
  30+ live files still name their paths. That is the right call today and a poor
  steady state.
- **Work:** one bounded pass — for each, either delete it and its references, or
  record why it stays. Not urgent, and **not** to be done by bulk deletion; the
  reference sweep is the whole job.

---

## Order, and what blocks what

```
E3 cost model ──▶ R1 re-run ──▶ R2 cut ──▶ arm MD-PROMOTE-S1-S2
  │                                 ▲
  └─ E4 corpus ────────────────────┘
E5 durable results ──▶ (the mandates have something to read)
E6 replay-pregate ─┐
E7 orphan harnesses ┴──▶ E8 testing queue
E9 orphan guards ── independent, low priority
```

**E3 and E4 are the critical path.** Everything the research plan does is
downstream of the corpus being honest. E5 is the second priority because the
standing mandates cannot fire on evidence that is not a durable object.

## What this plan does not do

- **It does not add capacity.** The Ampere pool is full (4 of 4 OCPU, 24 of
  24 GB). Any capacity increase is a cost decision, and there is no case for one
  while the runners are idle and the corpus is wrong.
- **It does not touch the order path.** Nothing here changes what reaches a
  venue. The riskiest item is E3, and its blast radius is *which legs pass a
  backtest* — which is precisely the thing that ought to change.
