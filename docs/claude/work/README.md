# `docs/claude/work/` — the manager's one register

Since the operating reset of **2026-09-21** this directory holds exactly one
live file:

- **[`MANAGER-CHECKLIST.json`](MANAGER-CHECKLIST.json)** — every plan item and
  every live lane. It is served to the operator as the **Workflow page** on the
  SPA (`GET /api/bot/work/checklist`), read from the VM's working tree.
  `ict-git-sync` pulls `main` every ~5 minutes, so the page is exactly as fresh
  as the last push **to `main`**. **Push, then answer.**

Everything else that used to live here — the work store (`objects/`,
`intents/`, `steps/`), `MANAGER-LEASE.json`, `SESSIONS.json`,
`MERGE-QUEUE.json`, `OPEN-PRS.json`, the watch receipts and the dated evidence
memos — is archived verbatim under
[`docs/archive/2026-09-21-operating-reset/work/`](../../archive/2026-09-21-operating-reset/work/).
Do not resurrect them. Measured over the 30 days before the reset: **1,005
commits to this directory against 141 to the whole of `src/`.**

## What goes where

| A question | → `research/queue/<id>.yaml`, carrying its `decision_rule` |
|---|---|
| **A build** | **→ a row in `MANAGER-CHECKLIST.json`** |

Nothing else is work. A spec, memo or design doc that no checklist row and no
queue unit points at is a memo, not work.

## Rules for the checklist file

1. `items[]` is the content. Top-level keys are schema only — **no narrative,
   no timestamped `manager_observation_*` / `manager_tick_*` entries, no
   incident logs.** The previous file reached 67 top-level keys and 323 rows.
2. **One status field: `state`**, from the `states` vocabulary the file
   declares. There is no `status`.
3. **`done` and `landed_unproven` are different facts** — merged-and-observed
   versus merged-and-unobserved — and are never collapsed.
4. Record a lane's `model` and its `ceiling_usd`, **with the reason in
   `note`**. A successor reads the reasoning, not just the value.

Contract: [`.claude/skills/manager/SKILL.md`](../../../.claude/skills/manager/SKILL.md).
Plan: [`docs/plans/OPERATING-PLAN-2026-09-21.md`](../../plans/OPERATING-PLAN-2026-09-21.md).
