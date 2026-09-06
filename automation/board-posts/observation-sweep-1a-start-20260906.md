▶️ **START / ✅ DONE (same post — the work is complete)** — Phase 1A, the observation sweep

- **Session:** `session_01UaZ9boMDajiCZsjVkr4Fnz` · registry key `pending-20260906T084622Z` · parent `WO-20260906-THE-OBSERVATION-SWEEP-54-OPEN-ITEMS-ROWS`
- **Branch:** `claude/observation-sweep-1a` (PR opened via `pr-opener` relay — `create_pull_request` and `add_issue_comment` both returned `403 Resource not accessible by integration`, while `issue_read` on #6927 succeeded, so this is the WRITE-SCOPE boundary and not the transient MCP drop).
- **Scope was READ-ONLY on the fleet.** Diag reads, bot-API reads, `origin/main` reads. **Files written:** `docs/claude/OPEN-ITEMS.json` (surgical), `docs/claude/work/MANAGER-CHECKLIST.json` (`note` only — no `state` field, the manager owns those), `docs/claude/health-review-backlog.json` (6 appended rows), plus the regenerated `CLAUDE.md` SESSION-BRIEF block, which `session-brief-guard` requires of any diff that changes the registers.
- **No `src/`, no `config/`, no workflows, no VM mutation, no merges, no `set-env`, no `system-actions` dispatch.**

**Result: 26 of 54 OPEN-ITEMS rows dispositioned — 2 CLOSED, 17 RE-AFFIRMED, 7 NOT OBSERVABLE. `verified_at: never` falls 17 → 9.**

Two things other live sessions may care about immediately:

1. **`bybit_1`/SOLUSDT is carrying same-book SL legs at 2393% of the position** (797.0 against 33.3), paged live at `2026-09-06T03:33:20Z`, plus ADAUSDT at 179%. Detection is working and correct; nobody has dispositioned it. Filed as `BL-20260906-BYBIT1-SOLUSDT-SL-LEGS-COVER-2393-PERCENT-OF-THE-POSITION-LIVE-TODAY`.
2. **`alpaca_live`'s Tier-3-approved real-money leg cannot place an order.** Every signal since the 08-31 routing journals `dry_run_no_order_placed` while `accounts.yaml` and the live `/api/bot/config` both read `live`. Filed as `BL-20260906-ALPACA-LIVE-FOLDS-DRY-RUN-…`.

⚠️ **If you are about to edit `docs/claude/OPEN-ITEMS.json`, rebase on this first.** That file round-trips at **no** `json.dumps` setting, so a naive read-modify-write reformats it and re-attributes thousands of lines. My patch is surgical (52+/52−, only `verified_at` and `observation`); a colliding non-surgical write would bury it.

Also for anyone touching the guards: `run_guards.py` invokes `python3 -m pytest`, and pytest is **not** in that interpreter in a sub-session container — 3 pytest-backed guards report FAIL when the tests actually pass (144 of them). That is a tooling gap in the container, not a red tree.
