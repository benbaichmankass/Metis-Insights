# Sprint S-028 Summary — BUG-050: Dead close-all legacy code removal

**Date:** 2026-05-04  
**Branch:** `claude/bug-050-dead-closeall-cleanup`  
**PR:** #404  
**Checkpoint:** CP-2026-05-04-07  

---

## PRs

| PR | Title | Status |
|---|---|---|
| #404 | BUG-050: remove dead close_all_bybit_positions legacy functions | Merged |

---

## Deliverables

| File / Unit | Change | Tests |
|---|---|---|
| `src/bot/telegram_query_bot.py` | Removed `close_all_bybit_positions` (28 lines, dead since S-031 PR4) | `TestCloseAllBybitPositions` removed |
| `src/units/ui/data_loaders.py` | Removed `close_all_bybit_positions_for_strategy` (52 lines, dead since S-031 PR4) | `TestCmdCloseallStrategy` (data_loaders) removed |
| `tests/test_telegram_query_bot.py` | Removed 3 dead test classes + 2 broken test methods (−175 lines) | n/a |
| `tests/test_data_loaders.py` | Removed `TestCmdCloseallStrategy` + orphaned `_bybit_strategy_account` helper (−79 lines) | n/a |

**Net: −335 lines, 0 behavior change.**

---

## Tests added

None — this sprint was a removal-only cleanup. Dead-code test classes removed.

187 tests pass in scoped suite (`test_data_loaders.py` + `test_telegram_query_bot.py` + `test_env_render_contract.py` + `test_boot_audit.py`); pre-existing failures unchanged.

---

## Architecture note

Both removed functions called `client.place_order()` directly, bypassing the canonical `execute_pkg` entry point — a Tier-1 architecture violation. They had been replaced by `processor.close_open_positions()` → `execute_pkg` in S-031 PR4 but never deleted. The production `/closeall` path was already clean; only the dead legacy code violated the contract.

Identified during Recurring Hardening Session 2 (CP-2026-05-04-06), logged as BUG-050 in `docs/claude/bug-log.md`.

Canonical close path (`_do_closeall_strategy` → `processor.close_open_positions` → `execute_pkg`) remains covered by `tests/test_s031_pr4_closeall_helper.py`.

---

## Deferred items

- **Finding 2** (Session 2): add structured logging to `_fetch_balance()` silent-zero failure path in `execute.py`.
- **Recurring Hardening Session 3**: mode-flag plumbing audit — trace `mode:` field from `accounts.yaml` through `RiskManager.dry_run`; verify no stale `DRY_RUN` / `ALLOW_LIVE_TRADING` env-var override exists.

---

## Lessons learned

1. **Delete dead code in the same PR that replaces it.** S-031 PR4 correctly replaced the direct-client close path but left the old functions. The gap sat undetected for multiple sprints. "Replace + delete" should be a single atomic PR.
2. **Broken tests are self-documenting dead code.** All three failing test classes independently confirmed the functions were dead — they patched a function that real production code never called. Failing tests on "working" code are a strong dead-code signal worth acting on immediately.
