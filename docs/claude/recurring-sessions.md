# Recurring Sessions — Audit, Strategy Improvement, Model Training

**Status**: Binding spec. Every recurring session must follow this format.
**Owners**: Operator (Ben) sets cadence; Claude executes.
**Cadence (initial)**:
- **Hardening & Stability Audit**: bi-daily (every 2 days)
- **Strategy Improvement Review**: weekly
- **Model Training & Evaluation**: weekly (or as the HF cron and Colab triggers fire)

This document defines *what each session does, in what order, and what artifacts it produces*. The actual session prompts live in `docs/sprints/recurring-*-prompt.md` and are loaded at session start.

---

## Why these exist

The roadmap (`ROADMAP.md`) is for **forward-feature work**. Recurring sessions cover the work that *isn't* a feature:

- Verifying the live system actually works end-to-end (not just that tests pass)
- Catching architectural drift, stale config, dead code, mode-flag bugs
- Iterating on strategy parameters as market regime changes
- Retraining ML models on fresh data

These sessions are not optional. The roadmap can ship features all day; the system still degrades silently if no one is auditing it. The hourly report on 2026-05-02 (VWAP firing 36 signals with 0 placements; `multi_account_execute` calling the wrong execute path; comms ping never firing despite env flag) is the operational proof.

---

## Common Workflow (all three session types)

Every recurring session follows the same three-phase shape:

### Phase 1 — End-to-End Health Check (always first)

Before *any* deep-dive work, run the e2e sanity check defined in the session-specific prompt. The check must:

1. Verify the live system is in the expected mode (live or dry-run as configured).
2. Verify the most recent hourly report is "all green" — no `failed_validation`, no `unknown` strategy attribution, no API errors on enabled accounts.
3. Verify the comms ping system actually delivers (bot poll + git push round-trip).
4. Verify each enabled strategy has placed at least one order in the last 24h **OR** has a documented reason it hasn't (insufficient signals, intentional pause).

If **any** check fails:
- Stop the deep-dive plan.
- Open a draft `BLOCKED:` PR with the specific failure (per CLAUDE.md ping-PR rule).
- Pin operator with a `comms_ask` request asking whether to:
  (a) pivot the entire session to fixing the issue,
  (b) skip the session, log the issue to `bug-log.md`, defer fix to a focused sprint,
  (c) continue the original deep-dive (operator accepts the risk).
- Wait for response. Do not silently proceed.

### Phase 2 — Targeted Work

If e2e is green (or operator picked option a/c), execute the session-specific work:
- **Hardening session**: deep-dive into the prioritized target subsystem.
- **Strategy session**: pull recent backtest + live results, propose parameter adjustments, queue a test run.
- **Training session**: pull latest data, run training/eval, evaluate against incumbent, propose promote/reject.

### Phase 3 — End-of-Session Summary Ping

At session end, **always** ping the operator via `comms_ask` (or `pending-pings.jsonl` fallback) with a structured summary:

```
📊 [Session Type] — [Date]

Focus: <subsystem or strategy or model>
Findings: <2–4 bullets — what was discovered>
Fixes: <PRs merged or "none">
Issues raised: <items added to bug-log.md or pending review>
Next session target: <what the prioritization rule says is next>
Time: <duration>
```

Also append a checkpoint to `docs/claude/checkpoints/CHECKPOINT_LOG.md` per CLAUDE.md.

---

## Hardening & Stability Audit

**Cadence**: Bi-daily (every 2 days, ideally morning)
**Prompt file**: `docs/sprints/recurring-hardening-prompt.md`
**Cap**: 3 hours. If deeper work needed, file a focused sprint and stop.

### Phase 1 — E2E Health Check

Run `scripts/e2e_health_check.py` (to be created in first hardening session — see prompt). At minimum, the check must read the most recent hourly report and confirm:
- No `failed_validation` errors in the last 6 hours
- No `unknown` strategy attribution in the last 6 hours
- Each enabled account API status is OK
- Comms round-trip works (issue a `comms_ask` smoke; confirm response in archive)
- Git is in sync with origin/main; no uncommitted changes on VM

### Phase 2 — Deep Dive Target Selection

Pick **one** subsystem for the deep dive based on this prioritization (highest score wins):

```
score = (criticality × 3) + (days_since_last_audit × 2) + (recent_commit_count × 1) + (open_bugs × 4)
```

**Criticality (1–5)**:
- 5: live order path (`src/runtime/orders.py`, `src/runtime/pipeline.py`, `src/units/accounts/execute.py`)
- 4: mode flags, risk engine (`trading_mode.py`, `RiskManager`, `risk_counters.py`)
- 3: strategies, signal generation
- 2: comms, bot, observability
- 1: docs, scripts, notebooks

**Recent commit count**: number of commits in the last 14 days touching that subsystem.

**Days since last audit**: from `docs/claude/audit-log.md` (created in first hardening session).

**Open bugs**: count of unresolved entries in `bug-log.md` for that subsystem.

### Deep dive activities

For the selected subsystem, do all of:
1. Read the code (no shortcuts — actually read it, not just grep).
2. Verify each public function has a test.
3. Verify the unit can be imported in isolation (no hidden side-effects at import time).
4. Verify error paths are logged with structured fields.
5. Check that any env-var or config it reads has a documented default in `docs/claude/`.
6. Look for dead code, unused imports, commented-out blocks.
7. Add to `audit-log.md` with date, subsystem, findings, follow-up actions.

### Phase 3 — Summary

Standard end-of-session ping. If anything Tier 2 or higher was changed, follow CLAUDE.md ping-PR rule.

---

## Strategy Improvement Review

**Cadence**: Weekly (proposed Sundays)
**Prompt file**: `docs/sprints/recurring-strategy-improvement-prompt.md`
**Cap**: 4 hours.

### Phase 1 — E2E Health Check

Same as hardening, plus:
- Each enabled strategy's last-24h signal count is non-zero
- Each enabled strategy's last-24h fill rate is documented (% of signals that became orders)
- No strategy is showing > 3% intra-day drawdown that wasn't caught by RiskManager

### Phase 2 — Strategy Work

Per strategy in the active roster (`config/strategies.yaml` enabled=true):
1. Pull last 7 days of live signals + outcomes from `runtime_logs/`.
2. Pull most recent backtest results from `outputs/` or HF dataset.
3. Compare live performance to backtest expectations — flag drift.
4. Identify candidate parameter adjustments (timeframe, filters, thresholds).
5. **Do NOT change parameters in this session.** Output proposals to `docs/strategy-reviews/strategy-NAME-YYYYMMDD.md` and ping operator with the proposal.
6. Operator approves → next session triggers a `/test [strategy]` flow → after staging gate, parameter change ships in a focused sprint with full PM review.

### Phase 3 — Summary

Standard ping plus a structured "proposals queued" list. Each proposal is a candidate for an operator-approved parameter sprint.

**Critical rule**: parameter changes are **Tier 3** (per CLAUDE.md `Live-mode invariant`) — recurring strategy sessions only *propose*, never *commit* parameter changes.

---

## Model Training & Evaluation

**Cadence**: Weekly (aligned to existing HF cron / Colab schedule)
**Prompt file**: `docs/sprints/recurring-model-training-prompt.md`
**Cap**: 6 hours (most of which is offloaded to Colab/HF).

### Phase 1 — E2E Health Check

Same as hardening, plus:
- Latest model artifact in HF registry is loadable
- `training-run.yml` workflow last ran successfully (no red x in `.github/workflows/` runs)
- Training data freshness ≤ 7 days

### Phase 2 — Training Work

Per `docs/claude/ml-training-policy.md` and `docs/claude/training-improvement-workflow.md`:
1. Pull fresh data (HF dataset bumped, or Colab fetched).
2. Run training in Colab (Claude prepares the notebook; operator triggers Run All).
3. Evaluate against incumbent on holdout — fixed metrics: win rate, Sharpe, max DD, R-multiple distribution.
4. **Do NOT promote.** Output report to `docs/model-evals/model-NAME-YYYYMMDD.md` with promote/reject recommendation.
5. Operator decides → promotion happens in a separate Tier 3 sprint.

### Phase 3 — Summary

Standard ping plus link to evaluation report.

---

## Setup Instructions

### Step 1 — Pick a cadence

The operator decides when to run each session type. Three options:

**A. Manual via Telegram** (simplest, recommended initially):
- Operator types `/audit` → bot writes `comms/requests/REQ-audit-YYYYMMDD.json` with a "kick off audit session" payload
- Operator opens a Claude Code session and pastes:
  > Read `CLAUDE.md` and `docs/sprints/recurring-hardening-prompt.md`. Begin a recurring hardening session.
- Bot commands `/improve_strategy`, `/train_model`, `/roadmap` follow the same pattern.

**B. Cron via systemd timer** (autonomous):
- Add `deploy/ict-recurring-audit.timer` firing every 48h
- Timer invokes `scripts/dispatch_recurring_session.sh audit` which appends a request to `comms/requests/`
- Operator sees a Telegram nudge: "Audit session due — open: [Claude Code link]"
- Operator clicks and starts the session

**C. Cron + auto-launch** (most autonomous, deferred):
- Same as B, but the trigger also opens a Claude Code session via API
- Requires building a "session launcher" — track as a future sprint

**Recommendation**: start with **A** (Telegram-triggered). Move to **B** once the first three audit sessions confirm the format works. **C** is later.

### Step 2 — Create the bot commands (deferred sprint)

The `/audit`, `/improve_strategy`, `/train_model`, `/roadmap` commands need a small bot sprint:
- `S-NNN: Recurring-Session Triggers + /roadmap Command`
- 1 session, all `auto-claude`, Tier 1 (bot code only, no live order path touched)
- Adds 4 commands; each writes a `comms/requests/` artifact and replies "session queued"
- `/roadmap` reads `ROADMAP.md` and returns the current phase + next sprint + status counts

### Step 3 — First hardening session has known targets

The first hardening session should NOT pick a target via the prioritization formula — it should fix the four visible production bugs from the 2026-05-02 hourly report:
1. `pipeline_order:multi_account_execute` calling the wrong execute path (must use `execute_pkg()` from `src.units.accounts.execute`)
2. VWAP firing signals with 0 fills (downstream of #1)
3. `ALLOW_LIVE_TRADING=true` not propagating to the live process
4. Comms ping system not firing despite `COMMS_PUSH_ENABLED=1` set

These should all be addressed before normal prioritization kicks in. After session 1, the prioritization formula is the rule.

### Step 4 — File the audit-log

Create `docs/claude/audit-log.md` in session 1. Each subsequent hardening session appends a row:

```
| Date | Subsystem | Findings | Fixes | Time | Operator Pings |
|------|-----------|----------|-------|------|----------------|
| 2026-05-04 | src/runtime/orders.py | ... | PR#... | 2h15m | 1 |
```

This drives the "days since last audit" input to the prioritization formula.

---

## Operator Reference

**Need a quick health check?** `/status` (existing).
**Need to start a session?** `/audit`, `/improve_strategy`, `/train_model` (deferred until S-NNN ships).
**Need to know where the roadmap is?** `/roadmap` (deferred until S-NNN ships).
**Need to weigh in mid-session?** Claude pings via Telegram if the session needs you. Otherwise let it run.
**Need to halt everything?** `/halt` (existing).

---

## Cross-references

- Session governance: `CLAUDE.md`
- Comms protocol: `docs/claude/comms-architecture.md`
- Live-mode invariant: `CLAUDE.md` § Live-mode invariant
- Bug tracking: `docs/claude/bug-log.md`
- Cleanup tracking: `docs/claude/cleanup-policy.md`, `docs/claude/cleanup-report.md`
- Training policy: `docs/claude/ml-training-policy.md`
- Training workflow: `docs/claude/training-improvement-workflow.md`
- Telegram pings: `docs/claude/telegram-pings.md`
