▶️ **START** — `session_01Buj7phmYPjDt6FPTyuVoYp`

**Branch:** `claude/bybit-overcover-names-the-wrong-thing` (base `main`, will be a DRAFT PR)

**Scope — investigation + diagnostic repair, detect-only:**
- `src/runtime/order_monitor.py` — the Bybit broker-naked/over-cover sweep: `_bybit_position_protection`, `_check_broker_naked_bybit_positions`, `_emit_bybit_over_cover_alert` (message construction + classification only).
- Tests under `tests/` for the above.
- `CLAUDE.md` § `IB_BROKER_NAKED_CHECK_SECONDS` row (doc correction for the Bybit half) — Tier-1 doc edit only.

**What I am NOT touching:** `config/`, `ROADMAP.md`, `docs/claude/OPEN-ITEMS.json`, any `docs/claude/*-review-backlog.json` (a drain may be live — backlog row text goes in the PR body for the manager to place). No live orders will be cancelled; remediation of this class is Tier-2/3 and will be PROPOSED, not enacted.

**Why:** `/api/bot/notifications` carried a live `alert` at 2026-09-02T03:03:58Z on `bybit_1`/BTCUSDT reading *"position 0.018 but resting SL legs total 0.478 (2656%)"*. Read against `/api/diag/bybit_open_orders`, the live position's own two legs match it exactly at 100%; the excess is **two `side=Buy` reduce-only legs with no short position to reduce** — orphaned protection of a closed directional short. The banner names a cause no code path tested (UNPROVENANCED DIAGNOSTIC OUTPUT sub-class A). `bybit_2` (MAINNET) was checked and is clean.

**Trader sha observed:** `68e73de8`, `restart_pending: false`.

Will post ✅ DONE when the draft PR is up.
