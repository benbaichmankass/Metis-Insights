# Sprint M2.S1 — Comms Infrastructure

**Classification**: `auto-claude`
**Tier**: All Tier 1 — no live trading path touched. Self-merge once tests pass.
**Depends on**: M1.S1 complete (live system stable; VWAP execution fixed; mode flags canonical)
**Unlocks**: M2.S2, M3, M5 — everything async requires the comms ping system

---

## Session Start Checklist

1. Read `CLAUDE.md` in full.
2. Check `comms/sprint_state.json` — confirm `current_sprint: S1`.
3. Check if `comms/input_response.json` exists — process before starting if so.
4. Check `comms/pending_input.json` — if exists with no response, wait.
5. Run `pytest tests/ -q` — confirm baseline passes before making any changes.
6. Check git status — should be clean on `main`.

---

## Goal

Wire the Telegram bot to the `comms/` directory so that:
- When Claude writes `comms/pending_input.json`, the bot automatically sends Ben a formatted Telegram message with inline buttons
- When Ben taps a button, the bot writes `comms/input_response.json` back to the repo
- The bot can send PM sprint start pings, sprint completion notifications, and bug alerts
- Ben can trigger `/test [strategy]` and `/new-session [sprint_id]` from Telegram
- The full autonomous ping loop is operational

---

## Deliverables

### 1. comms/ Polling in the Telegram Bot

**File**: `src/bot/telegram_query_bot.py` (canonical — `telegramquerybot.py` is the duplicate to be deleted in S3)

Add a background task that runs every 30 seconds:

```python
async def poll_comms_directory():
    """Poll comms/pending_input.json and send Telegram message if new request found."""
```

Logic:
- Read `comms/pending_input.json` if it exists
- If `type` is `tier2_merge_review`:
  - Send: "🔶 Merge Review — [title]\n\nRisk: [risk_summary]\nImpact if broken: [impact_if_broken]\n\nTests passed: ✅" with inline buttons ✅ Merge / ❌ Hold
- If `type` is `tier3_strategy_review`:
  - Send: "🔴 Strategy Change — [title]\n\n[summary]\n\nThis requires your explicit approval." with inline buttons ✅ Approve / ❌ Reject
- If `type` is `pm_sprint_ready`:
  - Send: "📅 PM Sprint [sprint_id] — [title] — ready. Open session: [session_link]\n\nPrepared: [summary]" with ✅ Starting Now button
- If `type` is `mid_sprint_warning`:
  - Send: "⚠️ Mid-sprint on [title]. ETA to clean checkpoint: [summary]. I'll ping when ready."
- If `type` is `bug_report`:
  - Send: "🐛 Bug Found — [title]\n\n[summary]\n\nClaude is continuing other cleanups." (no buttons needed)

Track already-sent requests by storing the `created_at` timestamp to avoid re-sending on every poll cycle. Use a local state file `comms/.sent_state.json`.

### 2. Button Response Handler

When Ben taps a button:
- Write `comms/input_response.json` with the response
- Run `git add comms/input_response.json && git commit -m "comms: bot response for [type]" && git push origin main`
- Confirm to Ben in Telegram: "✅ Response recorded. Claude will process on next sync."

The git push from the bot is what signals Claude to process the response.

### 3. test_results.json → Telegram

Add a polling check for `comms/test_results.json`:
- When found, send Ben: "📊 Backtest Results — [strategy]\n\nTrades: [trade_count] | Win rate: [win_rate]% | Avg R: [avg_r_multiple] | Max DD: [max_drawdown_pct]%\n\nReady for live: [promote_to_live_eligible]"
- Archive the file after sending

### 4. New Bot Commands

#### `/test [strategy_name]`
Writes `comms/test_request.json`:
```json
{
  "strategy": "turtle_soup_mtf_v1",
  "symbols": ["BTCUSDT"],
  "timeframe": "1h",
  "requested_at": "<ISO timestamp>"
}
```
Confirms to Ben: "🧪 Test queued for [strategy]. Claude will process on next sync and ping results."

Supported strategy names (validate against this list, reject others):
- `turtle_soup_mtf_v1`
- `breakout_confirmation`

#### `/new-session [sprint_id]`
Triggers sprint handoff:
- Validate `sprint_id` is a valid sprint from the roadmap (S1–S7)
- Write a handoff note to `comms/sprint_state.json` with `status: "pm_requested_start"`
- Reply: "🚀 Sprint [sprint_id] start requested. Claude will initialize on next sync."

#### `/status` (enhance existing)
Current status must now include:
- Current sprint and its status (from `comms/sprint_state.json`)
- Whether any `pending_input.json` is waiting
- Whether any `test_request.json` is queued

### 5. Unit Tests

Add to `tests/test_comms.py`:
- Test that `poll_comms_directory` sends correct message for each `pending_input.json` type
- Test that button callbacks write correct `input_response.json`
- Test `/test` command with valid and invalid strategy names
- Test `/new-session` command with valid and invalid sprint IDs
- Test deduplication (same `created_at` is not re-sent)
- Mock all Telegram API calls and git operations

### 6. Docs

Update `src/bot/README.md` (create if missing) with:
- All commands including new ones
- comms/ polling behavior
- How to test locally with mock files

---

## Implementation Notes

### Git Operations from the Bot

The bot needs to run `git commit` and `git push` when writing `input_response.json`. The bot runs on the Oracle VM where the repo is already cloned. Use `subprocess.run` with:
```python
subprocess.run(["git", "add", "comms/input_response.json"], cwd=REPO_ROOT, check=True)
subprocess.run(["git", "commit", "-m", f"comms: {response_type} response"], cwd=REPO_ROOT, check=True)
subprocess.run(["git", "push", "origin", "main"], cwd=REPO_ROOT, check=True)
```
`REPO_ROOT` should come from an environment variable or be derived from the script's location.

### Avoiding Import Loops

The comms polling should be a standalone async task, not wired into the existing command handler classes, to avoid tight coupling.

### `.sent_state.json` Format

```json
{
  "last_sent_created_at": "2026-05-02T14:30:00Z",
  "last_sent_type": "tier2_merge_review"
}
```

---

## Acceptance Criteria

- [ ] `pytest tests/test_comms.py -q` passes with no failures
- [ ] `pytest tests/ -q` (full suite) passes with no regressions
- [ ] Manually verified: write a mock `comms/pending_input.json` → bot sends correct Telegram message
- [ ] Manually verified: click button → `comms/input_response.json` is written with correct content
- [ ] `/test turtle_soup_mtf_v1` command works in bot
- [ ] `/new-session S2` command works in bot
- [ ] `/status` shows sprint state

---

## Merge Protocol

This sprint is **Tier 1** throughout — no live order path touched.

1. All tests pass → open PR
2. CI passes → self-merge
3. Update `comms/sprint_state.json`: set `status: "complete"`, `completed_at: <timestamp>`, `current_sprint: "S2"`
4. Update `docs/sprint-roadmap.md`: set S1 to ✅ complete
5. Post sprint completion to Telegram if bot is live: "✅ Sprint S1 complete — Comms Infrastructure live. Starting Sprint S2 (Risk Caps) autonomously."
6. Open new session with `docs/SPRINT_2_PROMPT.md`

---

## End of Sprint Checklist

- [ ] All deliverables above complete
- [ ] Tests passing
- [ ] PR merged to main
- [ ] `comms/sprint_state.json` updated to S2
- [ ] `docs/sprint-roadmap.md` S1 marked complete
- [ ] `docs/SPRINT_2_PROMPT.md` created (from sprint-roadmap.md S2 spec)
