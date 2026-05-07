# Claude operating protocol

> **Purpose:** the consolidated, opinionated rulebook for how Claude executes
> work in this repo. The router (`CLAUDE.md`) sends sessions here when they
> need the "how do I behave?" answer. Other docs are **task-specific**; this
> one is **session-wide**.
>
> **Read order at session start:**
>
> 1. `CLAUDE.md` (router — already loaded by the harness).
> 2. `docs/claude/checkpoints/CHECKPOINT_LOG.md` (where to resume tactically).
> 3. `docs/claude/milestone-state.md` (where the program is strategically).
> 4. **This file.**
> 5. The task-specific docs your sprint touches (per the routing table in
>    `CLAUDE.md`).

---

## 1. The four standing principles

These four rules override any clever-sounding alternative. If a sprint prompt,
a comment, or even an earlier section of this protocol seems to contradict
them, the principles win and the conflicting text is a bug to file.

1. **Repo is the source of truth.** Plans, logs, decisions, and state
   transitions live in version control. Chat continuity is best-effort and
   must never be the only place a decision exists.
2. **Safety before expansion.** No new live behavior ships before risk
   controls, visibility, and validation paths are in place. The autonomous
   live-trading rule in `CLAUDE.md` is the canonical safety-rail spec.
3. **Autonomy is the default.** Claude works without per-task approval. The
   approval categories (Tier 2 / Tier 3 below) are explicit and finite.
4. **Operator actions are simple.** Anything the operator must do on the VM
   is a one-click Colab notebook under `notebooks/operator/`, never a
   copy-paste CLI checklist.

---

## 2. The session shape

Every Claude session, regardless of trigger (operator, recurring auto-task,
or sprint follow-up), follows the same shape.

### 2.1 Session start

1. Read `CLAUDE.md` (already loaded by the harness — re-read if context
   summaries removed it).
2. Read **the most recent entry** of `docs/claude/checkpoints/CHECKPOINT_LOG.md`.
   Do **not** start from the top of any sprint plan.
3. Read `docs/claude/milestone-state.md` to confirm the active milestone /
   sprint / checkpoint pointers agree with the log.
4. If the two disagree, **reconcile first** — that is the session's first
   task before any feature work.
5. Read only the task-specific docs the next checkpoint requires. Don't
   pre-load every doc in the routing table.

### 2.2 Session middle

- One task per session. Do not chain tasks silently. Split tasks into the
  smallest safe subtask if needed.
- Keep every change PR-sized. Long-lived branches and giant diffs are
  anti-patterns.
- Use Colab / Hugging Face / Google AI Studio for heavy compute and research.
  Claude's session time is for repo architecture, code changes, tests, and
  reviews.
- Update the smallest relevant doc as soon as you learn something durable —
  a recurring bug, a cleanup decision, a test rule. Don't batch doc updates
  until end-of-session.

### 2.3 Session end

The closing checkpoint of every sprint is **documentation + project-state
maintenance**. It must:

1. Update the affected docs (`README.md`, `ROADMAP.md`, sprint logs,
   relevant `docs/claude/*.md` files, bug log, architecture docs).
2. Update `docs/claude/milestone-state.md` (active milestone / sprint /
   checkpoint pointers; queued milestones; open blockers).
3. Append a handoff entry to `docs/claude/checkpoints/CHECKPOINT_LOG.md`
   using `HANDOFF_TEMPLATE.md`. The entry must contain exactly:
   1. Completed.
   2. Files changed.
   3. Tests run.
   4. Remaining.
   5. Next checkpoint.
4. Commit, push, and open the PR per the merge-authority rules in § 4.
5. Send the Telegram session-complete ping per
   `docs/claude/telegram-pings.md` (rides on the checkpoint commit; the
   manual `scripts/notify_session.py` is only a fallback).

If limits are near (usage / context / wall-clock), **stop at the first safe
checkpoint** and write the handoff. Half-finished tasks ship as a clear
"Remaining" + "Next checkpoint" entry — never as silent partial commits.

---

## 3. Milestone → sprint → checkpoint decomposition

Claude must always create and maintain a milestone plan. The full normative
rules live in `docs/claude/decomposition-rules.md`. The short version:

- A **milestone** is a coherent body of work with a single goal and a single
  Definition of Done. It can take 1–N session-sized sprints to finish.
- A **session-sized sprint** is a sprint that fits in one Claude session
  with PR-sized changes. The binding template lives in
  `docs/claude/sprint-planning.md`.
- A **checkpoint** is a single resumable step inside a sprint. Checkpoints
  end with a handoff entry; they don't end with a merge.

Three milestone types exist (per `docs/workplan.md`):

| Type | Trigger | Examples |
|---|---|---|
| Roadmap | Planned via `ROADMAP.md` | S-014 web client, S-016 key management |
| Ad-hoc | Operator request, urgent bug | BUG-056 spot-routing, incident response |
| Auto-task | Recurring auto-trigger | hardening audit, strategy improvement, model training |

All three types use the same milestone → sprint → checkpoint shape.

---

## 4. Decision and merge authority

Three tiers. Pick the highest-tier classification that applies; when in
doubt, escalate one tier.

### Tier 1 — self-merge

Claude self-merges Tier 1 work as soon as tests + lint are green. Examples:

- Documentation updates, this protocol, sprint prompts, summaries.
- Tests, CI workflows, fixtures, secret-scan rules.
- Observability: logs, metrics producers, dashboard read-path code.
- Schemas, type stubs, refactors that don't change runtime behavior.
- Tooling: scripts in `scripts/`, notebooks in `notebooks/`.
- Infra changes whose safety can be proven by tests, dry-run validation,
  or a staging check.

### Tier 2 — ping-with-decision

Claude opens a draft PR, posts a structured ping, and **waits** for the
operator to choose Merge or Hold. Triggers:

- Touches the live order path, runtime orchestration, deployment timers,
  service behavior, or any integration that could break execution even if
  strategy logic doesn't change.
- Safety can't be proven end-to-end from inside the session.
- Risk of restart churn, duplicate sends, sync loops, deployment instability.

Ping payload:

| Field | Content |
|---|---|
| PR title | one line |
| Summary | one sentence |
| Risk if broken | one sentence |
| Validation done | bullet list (commands + results) |
| Action | inline buttons: **Merge** / **Hold** |

Use the ping-PR pattern (see § 6) so the ping rides on a commit and Telegram
fires.

### Tier 3 — explicit operator approval before merge

Claude **must not merge** without operator approval when a change involves:

- Strategy parameters (`config/strategies.yaml`).
- Entry or exit logic (`src/units/strategies/*.py` business logic).
- Signal thresholds.
- Position sizing formulas.
- Risk cap values (`src/runtime/risk_counters.py`, per-account caps).
- Promotion of any strategy from dry-run to live (per-account `mode` flag).

Tier 3 PRs are always opened as draft, never auto-merged, and the operator's
explicit "merge" reply is required. Tests passing is necessary but not
sufficient.

> **Note on `CLAUDE.md`'s simpler self-merge rule.** The router currently
> states a binary "self-merge unless secrets / live-trading / VM deploy".
> The three-tier model here **refines** that rule — every Tier 1 case is
> still self-merge, every Tier 2/3 case is still flag-for-PM. The routing
> doc and this protocol agree on outcomes; they differ only in granularity.

### 4.4 Compliance check before every ship-or-escalate

Operator rule, 2026-05-07: **all code is checked for compliance before it
is shipped or escalated to the operator.** "Compliance" = the diff is
verified against `CLAUDE.md`, `docs/claude/workplan.md`, and this
protocol. The check runs before:

- a self-merge (Tier 1),
- a request for operator approval (Tier 2 / Tier 3),
- any "merge and continue" reply where the operator implicitly trusts
  the diff is rule-conformant.

Minimum check before any of those:

1. Does the diff add a refuse-to-trade decision **outside** the dispatcher's
   `live | dry_run` switch (the only canonical execution gate per
   `docs/claude/workplan.md` § "Live / dry-run rule")? If yes → **stop**;
   move the logic into the risk manager or remove it.
2. Does the diff add a per-account flag, schema field, env var, or
   pre-flight check whose failure path refuses a trade? Same answer.
3. Does the diff put exchange-side state behind an operator-run notebook,
   manual capture step, or any "operator extracts value, pastes into PR"
   workflow? **Workflow gates count.** The system must operate
   exchange-agnostic — query at runtime if the live value is needed,
   default sensibly in config (operator can edit), or just send the call
   and let the exchange decide. A read-only diagnostic notebook whose
   output conditions a downstream PR is still a refuse-to-progress gate
   at the workflow layer.
4. Does the live-mode invariant (§ 5) pass on the diff?
5. Are tests + lint + scan + collect + inventory green?

The PR body should record the check result under a `## Compliance check`
heading the same way it records the live-mode check. Past sprints where
this check would have caught the problem before it shipped:

- **S-047 trigger session (PR #450, 2026-05-07)** — the merged sprint
  plan described an `is_leverage` boolean in `accounts.yaml` and an
  "if not is_leverage: refuse" branch in `execute.py`. Both are
  refuse-to-trade decisions outside the risk manager. Caught + reverted
  in #453 the same day, but only after the operator flagged it. Bullets
  1 and 2 of this list would have caught it.
- **S-047 T0 notebook (PR #452, 2026-05-07)** — read-only Colab notebook
  that asked the operator to verify Bybit Spot Margin enablement and
  capture the BTC max-borrow tier as input to T2's risk-manager rules.
  Even though the notebook adds no runtime gate, it makes T1+ workflow
  conditional on operator-extracted values. Deleted in PR #455. Bullet
  3 was added in that same PR after the operator flagged this as the
  same anti-pattern.

---

## 5. Live-mode invariant (every PR)

Before opening **and** before merging any PR, run the live-mode invariant
check from `CLAUDE.md` § *Live-mode invariant* and record the result in the
PR body under a `## Live-mode check` heading:

1. ✅ no flip away from live anywhere in the diff (CI guard
   `scripts/check_dry_run_in_diff.py` agrees), **or**
2. ⚠️ a flip is intentionally proposed and a ping-PR linking to operator
   approval exists.

PRs that touch `src/runtime/orders.py`, `src/runtime/pipeline.py`,
`src/runtime/trading_mode.py`, `src/units/accounts/*`, or any live-routing
code path **always ping the operator** regardless of test outcome.

---

## 6. Ping-PR vs work-PR (mandatory separation)

When a PR needs operator input or fires a Telegram ping:

1. The **work-PR** stays draft (`BLOCKED: <q>` or `(PM REVIEW): <q>`).
   The operator clicks this to review the actual change. Claude **does not
   merge it**.
2. A separate **ping-PR** on branch `claude/ping-<slug>` carries a tiny
   payload (≤ 5 lines) — usually an append to
   `docs/claude/pending-pings.jsonl` or `CHECKPOINT_LOG.md` — with the
   question and a link to the work-PR.
3. Claude **self-merges the ping-PR**. That commit is what fires Telegram.
4. Claude stops. No unrelated work until the operator replies.

Never merge a work-PR to "fire the ping" — that approves your own pending
change. The ping-PR is the channel; the work-PR is the content.

---

## 7. VM and operator actions

The operator is non-technical. The VM is a free-tier Oracle box. Therefore:

- Any manual VM action ships as a one-click Colab notebook under
  `notebooks/operator/`, structured per
  `notebooks/operator/rotate_api_keys.ipynb` and `docs/claude/colab-workflows.md`
  § "Operator VM steps".
- Pre-fill these constants in any operator-facing notebook or script:

  ```python
  SSH_KEY_FILE = 'ict-bot-ovm-private.key'
  VM_USER = 'ubuntu'
  VM_HOST = "158.178.210.252"
  REPO_DIR = '/home/ubuntu/ict-trading-bot'
  ```

- Never ship a markdown CLI checklist when a notebook is appropriate.

---

## 8. Compute delegation

| Resource | Use it for | Don't use it for |
|---|---|---|
| Claude session | Repo architecture, code edits, tests, reviews, sprint planning, doc updates | Long-running training, large grid searches, public-data scraping |
| Colab | Heavy backtests, model training, anything > 10 min wall-clock | Tasks the operator must run manually (those are operator notebooks) |
| Hugging Face | Datasets, model registry, scheduled training, private Spaces for dashboards | Live trading state |
| Google AI Studio | Long-context research, prompt prototyping | Anything that produces a binding artifact (commit-only what came back via review) |

Source-of-truth artifacts always end up in this repo, regardless of which
service produced them.

---

## 9. What this protocol explicitly does **not** override

- The autonomous live-trading rule in `CLAUDE.md` (no per-trade
  confirmation; per-account `mode` is the only toggle).
- The architecture rules in `CLAUDE.md` (unit boundaries, strategy ≠
  execute, UI mirrors the DB unit, Telegram bot is a thin shell).
- The Telegram pings spec in `docs/claude/telegram-pings.md`.
- The bug-log requirement in `docs/claude/bug-log.md`.
- The sprint-completion checklist in `CLAUDE.md` § *Sprint Completion
  Checklist*.

If this protocol drifts from any of those, **they win** and a fix-PR for
this file is the next session's first task.

---

## 10. Cross-references

- `CLAUDE.md` — session router and standing rules.
- `docs/workplan.md` — master workplan and priorities.
- `docs/claude/milestone-state.md` — current program state.
- `docs/claude/decomposition-rules.md` — milestone / sprint / checkpoint contract.
- `docs/claude/sprint-planning.md` — binding sprint-prompt template.
- `docs/claude/checkpoint-workflow.md` — resume rules and end-of-session handoff.
- `docs/claude/checkpoints/CHECKPOINT_LOG.md` — append-only log of session handoffs.
- `docs/claude/checkpoints/HANDOFF_TEMPLATE.md` — handoff format.
- `docs/claude/telegram-pings.md` — Telegram ping wiring.
- `docs/claude/colab-workflows.md` — operator-facing notebook structure.
