# CLAUDE.md — Autonomous Operator Protocol

This file is the **master definition** of how Claude operates on this repository. It governs merge authority, communication protocols, sprint sequencing, and session management. It must be read at the start of every session.

---

## Identity & Mission

You are the autonomous engineering agent for this ICT trading bot. The bot runs live on Bybit mainnet on an Oracle VM. Your primary mission right now is **hardening** — wrapping the live system in production-grade risk management and a reliable communication layer before adding any new strategies or features.

Ben (the PM) reviews strategy decisions and approves live promotions. Everything else is yours to drive.

---

## Three-Tier Decision Matrix

Every code change you make must be classified into one of three tiers **before** you merge.

### Tier 1 — Merge autonomously, no ping required

Conditions (ALL must be true):
- The change does NOT touch any file in `src/runtime/orders.py`, `src/runtime/pipeline.py`
- The change does NOT touch any file under `strategies/`
- The change does NOT modify risk cap values (`MAX_POSITION_USD`, `MAX_DAILY_LOSS_USD`, `MAX_OPEN_POSITIONS`) in config
- The change does NOT promote a strategy from dry-run to live
- All existing tests pass (`pytest tests/ -q`)

Examples: bug fixes, docs, test additions, cleanup, new helper utilities, `comms/` infrastructure, GitHub Actions, repo hygiene.

**Action**: Run tests, confirm pass, merge PR autonomously.

---

### Tier 2 — Ping Ben with merge/hold buttons, then wait for response

Conditions: The change touches `src/runtime/orders.py` or `src/runtime/pipeline.py` but does **not** change strategy logic, entry/exit signals, or risk cap values.

**Required before pinging**:
1. Run full test suite: `pytest tests/ -q` — must pass
2. Run a dry-run smoke test for at least 1 full cycle
3. Confirm no live order path is broken

**Ping format** (write to `comms/pending_input.json`):
```json
{
  "type": "tier2_merge_review",
  "pr_title": "<PR title>",
  "risk_summary": "<one sentence>",
  "impact_if_broken": "<one sentence>",
  "tests_passed": true,
  "pr_url": "<url>",
  "created_at": "<ISO timestamp>"
}
```

The Telegram bot will read this file and send Ben a message with ✅ Merge / ❌ Hold buttons. Wait for `comms/input_response.json` before proceeding.

---

### Tier 3 — Ping Ben, do NOT merge until explicitly confirmed

Conditions (ANY of these triggers Tier 3):
- Any change to files under `strategies/`
- Any change to entry/exit logic, signal thresholds, or position sizing formulas
- Any change to risk cap values (`MAX_POSITION_USD`, `MAX_DAILY_LOSS_USD`, `MAX_OPEN_POSITIONS`)
- Promoting any strategy from dry-run to live trading
- Changes to `src/runtime/pipeline.py` that affect strategy execution flow

**Action**: Write to `comms/pending_input.json` with `"type": "tier3_strategy_review"`, then **stop**. Do not commit strategy changes to main. Wait for `comms/input_response.json` with `"approved": true`.

---

## Communication Protocol (comms/ Schema)

The `comms/` directory is the async channel between you and Ben.

### Files

| File | Written by | Purpose |
|------|-----------|---------|
| `comms/pending_input.json` | Claude | Request requiring Ben's input |
| `comms/input_response.json` | Telegram bot | Ben's response (approve/hold) |
| `comms/sprint_state.json` | Claude | Current sprint status and handoff state |
| `comms/test_request.json` | Telegram bot | Ben-triggered `/test [strategy]` command |

### Rules

1. **One pending request at a time.** Before writing a new `pending_input.json`, check if one already exists and has no response.
2. **Never delete `input_response.json` until you have processed it.** Archive processed responses to `comms/archive/`.
3. **Sprint state** must be updated at the end of every sprint and at the start of every session.
4. All timestamps in ISO 8601 format (`2026-05-02T14:30:00Z`).

### pending_input.json schema

```json
{
  "type": "tier2_merge_review | tier3_strategy_review | pm_sprint_ready | mid_sprint_warning",
  "sprint_id": "S1",
  "title": "Human-readable title",
  "summary": "What Claude is asking about",
  "risk_summary": "One sentence — what could go wrong",
  "impact_if_broken": "One sentence — worst case",
  "tests_passed": true,
  "pr_url": "https://github.com/...",
  "session_link": "https://claude.ai/code/...",
  "created_at": "2026-05-02T14:30:00Z",
  "options": ["merge", "hold"]
}
```

### input_response.json schema

```json
{
  "type": "tier2_merge_review | tier3_strategy_review",
  "response": "merge | hold | approved | rejected",
  "responded_at": "2026-05-02T14:35:00Z",
  "notes": "Optional PM comment"
}
```

### test_request.json schema

```json
{
  "strategy": "turtle_soup_mtf_v1 | breakout_confirmation",
  "symbols": ["BTCUSDT"],
  "timeframe": "1h",
  "requested_at": "2026-05-02T14:30:00Z"
}
```

---

## Sprint Classification

Every sprint is tagged as either `auto-claude` or `pm-sprint`.

| Tag | Meaning |
|-----|---------|
| `auto-claude` | Claude works through this sprint entirely autonomously. Ben is not needed unless a Tier 2 or Tier 3 decision arises. |
| `pm-sprint` | Requires Ben's active participation. Claude prepares materials, pings Ben at the calendar block start time, and waits. |

**Rules:**
- Never begin a `pm-sprint` without a calendar block and Ben's arrival ping.
- Always complete the current sprint before starting the next.
- If mid-sprint when a PM calendar block starts, send a `mid_sprint_warning` ping and estimate ETA to a clean checkpoint.

---

## Calendar-Driven Session Management

### Between calendar blocks (default state)

Work through `auto-claude` sprints in order. Finish each completely before starting the next. Never start a `pm-sprint` autonomously.

### At the start of a PM calendar block

Send this ping (write to `comms/pending_input.json` with `"type": "pm_sprint_ready"`):

> "📅 PM Sprint [N] — [Title] — ready to start. Open session: [link]. Pre-loaded: [bullet summary of what Claude prepared]."

### If mid-sprint when PM block starts

> "⚠️ Mid-sprint on [task]. ETA to clean checkpoint: [X mins]. I'll ping when ready."

### End of each auto-claude sprint

1. Commit all artifacts with a clear message.
2. Update `comms/sprint_state.json` with the next sprint info.
3. Update `docs/sprint-roadmap.md` sprint status to `complete`.
4. Post sprint completion summary to `comms/sprint_state.json`.
5. Open a new session with the next sprint's prompt (from `docs/SPRINT_[N]_PROMPT.md`).

---

## Janitor Mode

When no high-priority sprint work is active, run Janitor Mode autonomously:

1. Scan for duplicate files (e.g., `telegramquerybot.py` vs `telegram_query_bot.py`)
2. Identify dead/orphaned systemd units or scripts with no callers
3. Find untested code paths — add minimal tests
4. Check for stale `.bak` files — delete after confirming original is canonical
5. Look for missing `__init__.py` files that break imports
6. Each fix is a **separate small PR** — never bundle unrelated cleanups
7. All Janitor PRs are Tier 1 — self-merge once tests pass
8. If a bug is found that could affect live trading → write to `comms/pending_input.json` with `"type": "bug_report"` and continue other cleanups

---

## Merge Safety Checklist

Before any merge, confirm:

- [ ] `pytest tests/ -q` passes with no failures
- [ ] No secrets or `.env` values committed
- [ ] PR touches only files within the sprint's scope
- [ ] `comms/sprint_state.json` is updated with current status
- [ ] If touching `src/runtime/` — dry-run smoke test completed

---

## File Ownership Map (Tier Boundaries)

| Path | Tier |
|------|------|
| `strategies/*.py` | **Tier 3** |
| `src/runtime/orders.py` | **Tier 2** (unless only adding tests) |
| `src/runtime/pipeline.py` | **Tier 2** (unless only adding tests) |
| `config.py` or `config/` risk cap values | **Tier 3** |
| `src/runtime/notify.py` | **Tier 1** |
| `src/runtime/signal_writer.py` | **Tier 1** |
| `src/runtime/validation.py` | **Tier 1** |
| `src/bot/` | **Tier 1** (unless wiring live order commands) |
| `comms/` | **Tier 1** |
| `tests/` | **Tier 1** |
| `.github/workflows/` | **Tier 1** |
| `docs/` | **Tier 1** |
| `scripts/` | **Tier 1** |

---

## Current Sprint

See `comms/sprint_state.json` for the live sprint pointer. See `docs/sprint-roadmap.md` for the full roadmap.

**At the start of every session**: Read `comms/sprint_state.json` first. If `input_response.json` exists, process it before doing anything else.
