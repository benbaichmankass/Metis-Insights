# Sprint S-048 — M1 comms infrastructure deep audit (telegram-bot vs new workplan)

> **Type:** roadmap (auto-claude). M1 reopen.
> **Tier (expected average):** Tier 1 (read-only audit + docs). Any code change
> the audit recommends is filed as its own follow-up sprint at the appropriate
> tier.
> **Wall-clock budget:** one Claude session (~3 h).
> **Prereq:** S-047 closes (T3 lands; sprint summary written; M5 rejoins the
> active queue behind this audit).
> **Operator directive (2026-05-07):** "M1 is not complete — it was reviewed
> before workflow hardening and the current state is very far from the specs
> in the new workplan. We need a deep dive to audit the entire telegram-bot
> functionality against the new workplan."

---

## 1. Goal

Produce a structured gap-list comparing the **on-disk** telegram-bot
implementation (both bots: `@bict_trading_bot` and `@claude_ict_comms_bot`)
against the **canonical workplan** § "Telegram bots" + § "Required logs" +
§ "Repeatable operator-triggered workflows". Output is a prioritized backlog
of follow-up sprints, not a code change.

M1 was closed by S-042 on 2026-05-06 against the pre-reconciliation workplan.
The new canonical workplan (`docs/claude/workplan.md`) was adopted later that
same day via S-041, and S-042's evidence ("Pipeline audit passed; smoke-test
ping dispatched; telegram-pings.md updated; tests extended") does not amount
to a verification of the workplan's actual M1 spec. Per workplan § "Verify-
before-trusting-done" the on-disk state must be re-checked.

---

## 2. Scope

### In scope

- All source files under `src/bot/` (telegram-bot side).
- All source files under `comms/` (Claude-comms bridge, repo-driven
  pending-request artifacts, writeback flow).
- The `ict-claude-bridge.service` systemd unit + any associated runner
  scripts (`run_claude_bridge.sh`, `run_telegram_bot.sh`).
- Repo-tracked comms artifacts: `docs/claude/pending-pings.jsonl`, the
  pending-request schema, `docs/claude/telegram-pings.md`, the comms-log
  registry (workplan calls for one — verify it exists or note its absence).
- The deployment side that makes both bots actually run on the VM.
- All tests under `tests/` that touch the bot or bridge code.

### Out of scope (file follow-ups separately)

- Strategy logic, risk-engine logic, dispatcher, order routing.
- The Vercel dashboard repo (M6).
- Anything not part of the comms surface.
- Refactors or new features — this sprint produces an audit only.

---

## 3. Workplan spec (the rubric to audit against)

From `docs/claude/workplan.md` § "Telegram bots":

### `@bict_trading_bot` (AI Trader Bot)

**Notifications (broad and comprehensive — reduction is later):**
- Every entry to every log in the database.
- Hourly snapshots.
- Errors returned by any system component.
- Trade and account events.
- Other operational signals currently exposed to the operator.

**Operator commands:**
- Toggle account live / dry-run.
- Killswitch.
- Close all positions.

**Information menus:**
- Operator commands.
- Trader snapshot.
- Signals Log.
- Order Package Log.
- Trade Log.
- System health.
- Hourly update.
- VM stats.

### `@claude_ict_comms_bot` (ClaudeBot)

**Workflow (repo-driven, five-step):**
1. Claude writes a structured pending-request artifact in the repo.
2. The bot detects it and sends the message in Telegram.
3. The operator responds in Telegram.
4. The response is written back into the repo in structured form.
5. Claude reads it on the next sync cycle and continues.

**Channel must support:**
- Merge-review buttons (Tier 2 PRs).
- PM sprint-start pings.
- Sprint-completion updates.
- Required-user-action prompts.
- Recovery alerts for stuck or stale requests.

### Required logs (§ "Data and logging architecture")

The workplan additions M1 should validate exist or be flagged:
- A **comms log** for Claude / operator communication state transitions
  (workplan § "Additional logs and registries to add").
- The Messages Log (existing).

### Repeatable operator-triggered workflows (§ "Repeatable operator-
triggered workflows")

- `new-session <sprint_id>` command.
- `test <strategy_name>` command (this is M5's deliverable but the
  bot-side dispatch surface lives here).
- Merge-review flow with **Merge** / **Hold** buttons.
- Stuck-request recovery flow.

---

## 4. Deliverables

One PR (Tier 1, docs only) containing:

### D1 — `docs/audits/M1-comms-audit-2026-05-NN.md`

The master audit report. Structure:

```
## Summary
[1-paragraph verdict: how far M1 is from spec.]

## Bot 1 — @bict_trading_bot
### Notifications
| Workplan requirement | On-disk implementation | Status | Gap |
| ... | ... | ✅ / ⚠️ / ❌ | ... |
### Operator commands
[same table shape]
### Information menus
[same table shape]

## Bot 2 — @claude_ict_comms_bot
### Five-step workflow
[step-by-step verification — does each link in the chain exist?]
### Channel features
[merge-review / sprint-start / sprint-completion / required-action / recovery]

## Required logs
[comms log: present? schema? writers? readers?]

## Operator-triggered workflows
[new-session / test / merge-review / stuck-recovery]

## Deployment / runtime
[ict-claude-bridge.service health; bot process supervision; restart behavior;
 autosync-from-main behavior; secret rotation]

## Test coverage
[which workplan requirements have a test pinning them; which don't]

## Cross-cutting concerns
[bug-log entries that touch comms; observability gaps; deduplication;
 pending-request artifact schema drift]
```

For every gap, classify severity:

- **P0 — blocking:** workplan-mandated functionality that does not exist or
  is broken, *and* the system can't operate safely without it (e.g.
  killswitch missing, merge-review flow broken).
- **P1 — significant:** workplan-mandated functionality missing or broken,
  but the system limps along (e.g. info menu missing for one of the eight
  required surfaces).
- **P2 — minor:** drift, redundancy, dead code, naming inconsistency,
  schema-vs-doc mismatch.

### D2 — `docs/audits/M1-comms-audit-followups.md`

A prioritized backlog of follow-up sprint prompts (one per gap cluster).
Each entry:

```
### S-NNN — <gap title>
- **Severity:** P0 / P1 / P2
- **Workplan ref:** § "..."
- **Files in scope:** ...
- **Tier:** 1 / 2 / 3 (per operating-protocol § 4)
- **Goal:** ...
- **Out of scope:** ...
- **Acceptance:** ...
```

Sprint numbers are assigned at the time the prompt is filed, not now
(per workplan § "Sprint and checkpoint numbering").

### D3 — `docs/claude/milestone-state.md` update

M1 row in the M0..M10 table flips from `⚠️ REOPENED` to one of:
- ✅ CLOSED (audit found everything in the workplan is implemented and tested),
- 🔄 PARTIAL (audit found gaps; backlog filed; M1 stays open until those land),
- 🚨 P0 GAP (audit found at least one P0; operator escalation required).

### D4 — `ROADMAP.md` update

M1 row in the M0..M10 table mirrors D3. Audit-followup sprints from D2 added
to the active queue in priority order.

### D5 — `docs/claude/checkpoints/CHECKPOINT_LOG.md` close-checkpoint entry

Standard sprint-close entry per `docs/claude/checkpoint-workflow.md`. Cite
D1/D2 by path; cite the relevant workplan sections audited; cite the next
sprint (the highest-priority follow-up from D2, or M5 if M1 cleared).

---

## 5. Method

### 5a. Read order

1. `CLAUDE.md` (router).
2. `docs/claude/workplan.md` § "Telegram bots", § "Data and logging architecture",
   § "Repeatable operator-triggered workflows", § "Verify-before-trusting-done".
3. `docs/claude/operating-protocol.md` § 4 (merge tiers — to classify
   followup-sprint risk).
4. `docs/sprint-summaries/sprint-042-summary.md` — the M1-close evidence to
   re-verify.
5. `docs/claude/telegram-pings.md` — the existing comms protocol.
6. `docs/claude/pending-pings.jsonl` head — the pending-request artifact
   format.
7. `src/bot/` directory tree — the implementation.
8. `comms/` directory tree — the bridge.
9. `tests/` files matching `test_*bot*` / `test_*ping*` / `test_*comms*`
   / `test_*bridge*`.

### 5b. Audit technique

For each rubric line in § 3:

1. **Locate** the on-disk implementation (file:line). If absent, mark `❌ MISSING`.
2. **Read** the implementation against the workplan wording.
3. **Classify** ✅ matches / ⚠️ partial / ❌ missing.
4. **For partial / missing:** write the gap as a follow-up entry in D2.
5. **Test pin:** find the test that asserts this behavior. If absent, that's
   itself a P1 gap (workplan-mandated behavior with no test).

Use `Bash` (`grep`, `find`, `cat`) and `Read` for evidence collection. Do **not**
execute the bot or the bridge — this is a static audit. Live behavior is
verified later, in the follow-up sprints, not here.

### 5c. Honesty rules

- If a workplan requirement is genuinely already done, say so plainly and
  cite the file:line that proves it. Don't manufacture gaps.
- If a workplan requirement is ambiguous, write the ambiguity into D1
  under "## Cross-cutting concerns" and *do not* mark it ❌ — the operator
  decides how to interpret.
- If on-disk reality differs from the S-042 close summary, say so plainly
  in D1's summary. Drift is the point of this sprint.
- Don't grade severity by personal preference — apply the P0/P1/P2 rubric
  in § 4.D1.

---

## 6. Out-of-scope reminders (operating-protocol § 2.2)

- Do not start fixing gaps in this sprint. Filing one PR per gap is what the
  follow-up sprints are for.
- Do not propose architecture changes. The workplan is the spec.
- Do not touch live order routing, risk caps, strategies, or anything
  outside the bot/comms surface.
- Do not delete files "because they look unused" — the Janitor sprints
  (M4 cadence) own that work, and the audit reveals — it doesn't change.

---

## 7. Definition of done

- [ ] D1 (`docs/audits/M1-comms-audit-<date>.md`) committed and reviewed.
- [ ] D2 (`docs/audits/M1-comms-audit-followups.md`) committed.
- [ ] D3 (milestone-state.md M1 row updated) reflects audit verdict.
- [ ] D4 (ROADMAP.md M1 row + active queue) reflects audit verdict.
- [ ] D5 (CHECKPOINT_LOG.md close entry) lands per checkpoint-workflow.md.
- [ ] Sprint summary at `docs/sprint-summaries/sprint-048-summary.md`.
- [ ] CI green: lint + scan + collect + inventory.

---

## 8. Hand-off

The next session opens against the highest-severity entry in D2:

- **If a P0 was filed:** open the P0 follow-up immediately. Do not skip ahead
  to M5.
- **If only P1/P2 entries:** opener picks the highest-priority follow-up
  from D2 *or* (operator's call) defers and starts M5 with M1 marked
  🔄 PARTIAL.
- **If audit cleared M1 entirely:** M5 (S-049 or higher) opens next per the
  queued-milestones order.

Update `milestone-state.md` and `CHECKPOINT_LOG.md` accordingly when
opening that next session.
