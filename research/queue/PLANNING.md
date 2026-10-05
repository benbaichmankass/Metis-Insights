# research/queue — planning and intake rules (binding)

Short on purpose. The mechanics (themes, fair share, aging) are in [`README.md`](README.md) § Scheduling and the
weights in [`../THEMES.yaml`](../THEMES.yaml). This file says how work gets in, how weights move, and what the
recurring planning session does.

## 1. An idea becomes a unit
- The operator's ideas land in **`docs/claude/work/IDEAS.md`**, the general intake the manager syncs daily from the
  operator's Google Doc. That file is the manager's; the planning session reads it and does not create or reshape it.
  Each row there is triaged to a `research/queue` unit (a question) or a checklist row (a build). This file
  is only the research half.
- **The manager drafts the unit.** `theme` and `priority` are required (the validator refuses a queued unit
  without them). **A decision rule is registered before any run** (`decision_rule.registered_before_run: true`,
  committed in the same PR that adds the unit); `check_research_queue_decision_rule.py` enforces it.
- **An idea too vague to register gets a SCOPING unit, not a drop.** If it cannot yet state a statistic, a bar
  and a minimum n, queue a unit whose question is "what could we measure, at what n, and what would it cost",
  with its own rule. A dropped idea is a finding nobody can re-ask.
- Every unit says what it does not do. An upper-bound or scoping unit says it is not a promotion case.
- Units that need a prior result to be worth running use `requires_result`, not prose.

## 2. Weights
- `research/THEMES.yaml` is the only place weights live. They are a share of fire slots, not an order.
- Set at the operator's direction (2026-09-30: regime 5, live_strategy 4, ml_health 3, new_strategy_prop 2,
  infra 2, macro 1). Changed by a PR that edits that one file and states the evidence for the change.
  2026-10-05, operator popup verbatim "Prop first (Recommended)": new_strategy_prop 2 -> 6, the single highest weight;
  nothing else moved (closes PI-20261005-MG7BGR46-0001).
- Revisited by the planning session each time it runs (section 4). Two rules it applies: the live pipeline
  outranks research breadth, and no single topic may starve the rest, so a weight never goes below 1 and a theme
  holding due work is checked against its fired count.

## 3. Aging
`aging_hours` (THEMES.yaml) lowers a waiting unit's effective priority one step per period. It is the
starvation backstop inside a theme; fair share is the backstop between themes. If a unit is old and still
unrun, read why (precondition, gate, route) before lowering anything.

## 4. What the recurring research-planning session does, every time
1. **Read throughput and results since the last session:** `python3 scripts/ci/check_research_queue_throughput.py --json`
   (fired 24 h / 7 d, results landed, runnable, idle hours) and the `research/results/` records landed since.
2. **Grade landed results** against their registered rules (`research_disposition.py --record`; never hand-edit
   the ledger). A graded-terminal `once` unit is set `done` with a reason.
3. **Re-weight themes** against the system-level plan: live pipeline over research breadth, no topic starving
   the rest. One PR to `research/THEMES.yaml` if the weights move.
4. **Draft new units** from the rows of `docs/claude/work/IDEAS.md` whose `routed_to` names a research unit or
   that are still `new` and research-shaped. Register the rule first (section 1).
5. **Retire stale units** with a stated reason (`status: retired`): superseded, answered elsewhere, or a
   precondition that can no longer be met. Closing a dead unit with a reason is worth more than carrying it.
6. **Write a one-page plan** (what ran, what landed, what the weights are and why, what is queued next, what is
   blocked and on what) that the manager brings to the operator. The page is the deliverable; the PRs above are
   its receipts.
