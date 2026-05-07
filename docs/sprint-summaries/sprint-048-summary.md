# Sprint S-048 Summary — M1 comms infrastructure deep audit

**Sprint:** S-048 | **Milestone:** M1 — Comms infrastructure (REOPENED)
**Type:** roadmap (auto-claude). M1 reopen.
**Date:** 2026-05-07
**Tier:** 1 (docs-only audit; self-merged per operating-protocol § 4)
**Status:** CLOSED ✅ — verdict 🔄 PARTIAL; M1 stays open per audit findings

---

## Outcome

S-048 produced a static audit of the on-disk telegram-bot + comms
implementation against the new canonical workplan
(`docs/claude/workplan.md`, adopted 2026-05-06). M1 was REOPENED on
2026-05-07 by operator directive because S-042 had closed M1 against the
*pre-reconciliation* workplan one day before the new workplan was
adopted, so the on-disk state had never been audited against the
correct rubric.

**Verdict: 🔄 PARTIAL.** No P0 gap surfaced. Seven P1 follow-ups + one
P2 cluster filed in `docs/audits/M1-comms-audit-followups.md`. M1 stays
open until at least the relocation, merge-review-buttons,
recovery-alert, and surface-unification follow-ups land. Per
sprint-prompt § 8 hand-off, no P0 → default next sprint = **S-047 T3**.

---

## What was done

### T0 — Session open

- Sprint-start ping appended to `docs/claude/pending-pings.jsonl`.
- Read order completed per sprint-prompt § 5a: CLAUDE.md → workplan
  § "Telegram bots" / "Data and logging" / "Repeatable operator-triggered
  workflows" → operating-protocol § 4 → sprint-042-summary.md →
  telegram-pings.md → pending-pings.jsonl → src/bot/ → comms/ →
  tests/.

### T1 — Static audit

For each rubric line in sprint-prompt § 3 the on-disk implementation was
located by file:line, classified ✅ / ⚠️ / ❌, and pinned (or noted as
unpinned) by test:

- **Bot 1 — `@bict_trading_bot`** (`src/bot/telegram_query_bot.py`):
  notifications drained from `runtime_logs/pending_pings/`; killswitch
  `/halt` (line 2960) + close-all `/closeall` (line 2965) +
  live/dry `/toggle` (line 2973) all present and tested; 8 information
  menus all present (`/status`, `/signals`, `/packages`, `/log`,
  `/last5`, `/health`, `/hourly`, `/vmstats`). Hourly snapshot is
  on-demand only — no auto-fire timer.
- **Bot 2 — `@claude_ict_comms_bot`** (`src/bot/claude_bridge.py`):
  is an Anthropic-API chat companion + one-way ping inbox drainer +
  three session-trigger commands. Does **not** implement the
  workplan's five-step request/response workflow.
- **S-027 comms infrastructure** (`src/comms/`,
  `src/bot/comms_handler.py`, `comms/`): exists, schema-validated,
  with git writeback and a comms event log (`comms/log.ndjson`). But
  installed on the **trader bot** (`telegram_query_bot.py:2955`),
  not on ClaudeBot per the workplan.

### T2 — Gap classification

Five P1 functional gaps + one P1 architectural drift + one P1 docs
correction + one P2 hygiene cluster identified. Filed in D2.

### T3 — Deliverables

- **D1** — `docs/audits/M1-comms-audit-2026-05-07.md`. Master audit
  report.
- **D2** — `docs/audits/M1-comms-audit-followups.md`. Prioritized
  backlog of 7 P1 + 1 P2 follow-up sprints (sprint numbers `S-NNN`
  pending — assigned at filing time).
- **D3** — `docs/claude/milestone-state.md` updated: M1 row → 🔄
  PARTIAL; Active sprint flipped from S-048 to S-047 T3; queue
  updated.
- **D4** — `ROADMAP.md` updated: M1 row mirrors verdict; queue
  updated; ledger row for S-048 added.
- **D5** — Close-checkpoint entry `CP-2026-05-07-13-s048-complete`
  appended to `docs/claude/checkpoints/CHECKPOINT_LOG.md`.

### T4 — Sprint close

- This summary.
- Sprint-complete ping appended to `pending-pings.jsonl`.

---

## Files changed

- `docs/audits/M1-comms-audit-2026-05-07.md` (NEW) — D1
- `docs/audits/M1-comms-audit-followups.md` (NEW) — D2
- `docs/claude/milestone-state.md` — D3
- `ROADMAP.md` — D4
- `docs/claude/checkpoints/CHECKPOINT_LOG.md` — D5
- `docs/claude/pending-pings.jsonl` — sprint-start + sprint-complete
- `docs/sprint-summaries/sprint-048-summary.md` (NEW) — this file

---

## Audit findings — top-line

The dominant structural drift is process-level:

- **`@bict_trading_bot`** (the AI Trader Bot) currently hosts both the
  trade-control surface *and* the entire S-027 repo-driven
  request/response system (`install_comms_handlers` at
  `src/bot/telegram_query_bot.py:2955`).
- **`@claude_ict_comms_bot`** (the workplan's nominal comms bot) is just
  an Anthropic-API chat companion + one-way ping drain.

The workplan splits these surfaces by bot. The on-disk install puts
the wrong code on the wrong process.

A second drift: S-042's close-out evidence ("ClaudeBot is one-way
send-only; no response path; intentional design",
`docs/claude/telegram-pings.md:6-10` and `:195-199`) was already wrong
when it was written — S-027 had shipped the two-way response path
*before* S-042 closed M1. The workplan's "Verify-before-trusting-done"
rule is exactly why this audit reopened M1.

Two parallel comms surfaces (`pending-pings.jsonl` for one-way pings
and `comms/requests/` for two-way structured exchanges) coexist
without a unifying schema, dedup, or messages-log home. Filed for
unification in D2.

Missing features: auto-hourly snapshot timer; Merge / Hold inline
buttons for Tier 2 PR review; stuck-request recovery alerts;
`/new-session <sprint_id>` and `/test <strategy>` commands.

Operator-control critical safety surfaces (kill, close-all, live/dry)
are intact and tested, which is why the verdict is PARTIAL not P0.

---

## M1 validation checklist

| Check | Status |
|---|---|
| D1 audit report committed | ✅ |
| D2 follow-up backlog committed | ✅ |
| D3 milestone-state.md M1 row updated | ✅ (🔄 PARTIAL) |
| D3 Active sprint flipped to S-047 T3 | ✅ |
| D4 ROADMAP.md M1 row mirrors verdict | ✅ |
| D4 queue updated | ✅ |
| D5 close-checkpoint entry filed | ✅ |
| Sprint summary | ✅ (this file) |
| `scripts/secret_scan.py` clean | ✅ Expected — pure docs |
| `scripts/check_dry_run_in_diff.py` clean | ✅ Expected — no code touched |
| `pytest` green | ✅ Expected — no Python touched |

---

## Hand-off

Per sprint-prompt § 8: no P0 surfaced → default next sprint = **S-047 T3**.

Plan: `docs/sprint-plans/S-047-bybit2-spot-margin.md` § T3 —
`feat(exec): route spot-margin orders via isLeverage=1` +
`feat(coordinator): direction-aware balance for spot-margin accounts`
(D4 + D5 land together — one diff is incoherent without the other).

Tier 2/3 — will pause at the operator-merge gate.

The M1 comms-followup queue (filed in D2) re-queues *after* S-047 T3
closes. M5 inherits the `/test <strategy>` follow-up from M1's queue
because that command is the bot-side dispatch surface for M5's strategy
testing workflow.

---

## Deferred / unchanged holds

- BUG-057 diagnostic review — VM logs, awaiting next live VWAP
  rejection with `BUG-057-DIAG` log lines (unchanged).
- S-047 T3 operator-merge gate — opens once T3's work-PR lands.
- All M1 follow-ups — see `docs/audits/M1-comms-audit-followups.md`.
