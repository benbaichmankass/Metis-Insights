# Lane queues — 2026-09-12

> **Not a plan of record, and deliberately not named one.** The ONE live plan is
> [`../WORKPLAN-2026-08-29.md`](../WORKPLAN-2026-08-29.md) and it stays live. This
> file is the manager's **lane dispatch register**: which lanes are running, and
> the ordered queue each one works. Operator-directed 2026-09-12 · owner: the
> manager holding `docs/claude/work/MANAGER-LEASE.json`.
>
> ⚠️ **I nearly made this a second live plan, and `one-live-workplan` stopped it.**
> That guard exists because `WORKPLAN-2026-08-14.md` read `ACTIVE` for 24 days and
> misled a manager into a wrong status read. Superseding the 08-29 plan would also
> have been **off the manager surface** — `check_manager_scope.py` named it as
> doing an item rather than managing, and it was right. Two guards, two different
> objections, both correct.
>
> **How this register relates to the live plan** — it staffs one of its lanes and
> adds two; it replaces none of them:
>
> | 08-29 plan lane | staffed by |
> |---|---|
> | **Lane B — M20 Active Trade Management** *(already "the operator's stated priority" on 2026-08-29)* | **MI-278.** Read Lane B first — it is MI-278's prior art, not a parallel thread |
> | Lane C — order-path correctness | partly, where health-register rows exist (MI-279) |
> | Lane A — Alpaca real-money go-live | **nobody** — blocked on a T+1 settlement decision |
> | Lane P — promotion pipeline | **nobody** — gates every real-money graduation |
> | Lane R — prop account | **nobody** — MI-274 attributed it; the unblock is a sequence |
> | Lanes D / S / T / E | **nobody** |
> | *(new)* the machinery that loses lanes' work | **MI-280** |
>
> ⚠️ **"Nobody" means no lane is working it, not that it stopped mattering.** Four
> are real-money-adjacent. They are the candidates for a fourth lane the moment the
> operator's 3-concurrent cap allows one, and the manager names them at every
> handoff rather than letting them fall off the edge.
>
> ⚠️ **Neither this nor the 08-29 plan outranks the cycle priority.**
> `docs/claude/CYCLE-PRIORITY.json` (`CY-20260906-TRADING-TRUTH`) is what reaches a
> session before its first tool call. Here they agree: the research lane IS it.

## Why this file exists

The operator's handoff asked for **three lanes with workplans that keep working
for days**. On 2026-09-12 the manager let all three go terminal, then spent four
hourly ticks reporting on a clean queue. The operator's correction, kept close to
verbatim because the paraphrase is what rots:

> *"we already said at the very beginning that we have to have the three lanes
> with the work plans that we can keep working through. If nothing's going on,
> that's already a problem… we need a work plan for you to keep working for
> several days without stopping that you know what to do. The only thing you need
> from me is to give me updates and ask me questions."*

⚠️ **THE FAILURE WAS NOT A SHORTAGE OF WORK.** Measured the same morning: **702
open health rows (20 critical, 274 high)**, **82 open-items rows**, and a roadmap
carrying M20/M21/M24/M26/M27/M31/M36 in flight. The manager confused *an
investigation is answered* with *the work is done*, and treated routing a
question to the operator as a reason to stop rather than as one item among many.

## The standing rule this file encodes

**A lane that finishes a unit takes the next unit. It does not go idle and it
does not wait for the manager.** The manager's job is to keep three lanes
saturated, not to approve each step. A lane blocked on a Tier-3 decision keeps
working the rest of its queue while that decision is pending.

**The manager dispatches a replacement the moment a lane goes terminal — at that
moment, not at the next tick.** An hourly cadence is for reporting, never for
deciding whether work should exist.

---

## Lane 1 — RESEARCH · MI-278 · M20 Active Trade Management

**Object:** `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER`
**This lane IS the cycle priority** (`CY-20260906-TRADING-TRUTH`); it needs no
spawn exception.

M20 is named **Active Trade Management** (renamed from *Exit Refinement*,
operator directive 2026-08-20). MI-277 measured its subject exactly: winner hold
**5.17h → 1.90h** while **loser hold ROSE**, losses unchanged at **≈1R**, the R
term **97.4%** of the winner-size fall. The sizer and the stops are intact. What
changed is how long a winner is allowed to run.

| unit | what it must produce |
|---|---|
| U1 | per-leg inventory of every mechanism that can END a winning trade, and which are ARMED |
| U2 | attribution of each post-2026-08-27 winning close to the mechanism that ended it, provenance-graded |
| U3 | counterfactual on the implicated levers — config-exact harness, IS/OOS, yearly walk-forward |
| U4 | **a Tier-3 proposal per cleared lever.** A memo is not the deliverable |
| U5 | the MFE/MAE-in-ATR instrument (filed, unowned, the natural measuring stick) |
| U6 | next open `high` performance-backlog row bearing on trade management |

## Lane 2 — OPS · MI-279 · drive the health backlog down

**Object:** `WO-20260912-OPS-LANE-DRIVE-THE-HEALTH-BACKLOG-DOWN`
Same trading-health intent; no spawn exception needed.

| unit | what it must produce |
|---|---|
| U1 | all **20 open `critical`** rows dispositioned with evidence — a row left UNREAD is the failure, not a row left open |
| U2 | an opened-vs-closed **burn-down**, so the pile cannot grow silently while rows are worked |
| U3 | **class-first** attack on the 274 `high` rows — one fix that closes nine beats nine fixes |
| U4 | a triage proposal for `loud:` — **69 of 82 rows carry it**, and a flag set on 84% selects nothing |
| U5 | the DUE monitoring rows, OBSERVED (a green test is not an observation) |
| U6 | the `duty` pass over `docs/claude/DUE.md` |

## Lane 3 — ENGINEERING · MI-280 · stop the machinery losing work

**Object:** `WO-20260912-ENGINEERING-LANE-MAKE-THE-LANDING-AND-REGISTER`
⚠️ **This lane sits under `IN-20260901-OPERATING-LAYER`, not the cycle priority,
so it required a spawn-priority exception** — filed, operator-approved directly,
2026-09-12. The argument that earns its slot: **this machinery loses the other
two lanes' work**, measured, not theorised.

| unit | what it must produce |
|---|---|
| U1 | a detector for **green-but-dirty** — #11842 was 4-of-4 green and unmergeable for 3.5h |
| U2 | a fix for the **post-arming commit-drop race** — 5 measured instances; one dropped a live lane's registry row and that lane ran unrecorded for 9 minutes |
| U3 | the **scope-overlap declaration parser** — one blank line is the whole difference between 4 paths parsed and 0 |
| U4 | **shared-register merge safety** — a union-by-id-set proof is blind to a merge that drops a FIELD |
| U5 | `claim_merge_slot.py` has no release mechanism |
| U6 | the stale-automation class, currently tracked by two items under dead sessions |

**The bar for this lane:** a fix without a **planted-defect test** does not count.
A green run proves nothing unless the probe has been shown to go red.

---

## What goes to the operator, and what does not

**To the operator:** Tier-3 decisions (strategy logic, params, risk caps, account
modes, live promotion), Tier-2 approvals, and status updates. **That is all.**

**Not to the operator:** which unit a lane takes next, whether a lane should
exist, how to sequence the queue, or anything a lane can establish by measuring.

⚠️ **Asking the operator a question NEVER means waiting for the answer.** State
the assumption, keep working, and put the question in the next status update.
A manager that blocks becomes an extra decision gate in front of the operator,
which is the measured constraint reproduced by the person meant to relieve it.
