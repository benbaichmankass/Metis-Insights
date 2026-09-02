✅ DONE — `/status` + `/decisions` Telegram commands

**Session:** session_011JWFxuYAaEQKCFCmG6gnHJ · **PR:** #10793 (DRAFT, not merged) · **Branch:** `claude/telegram-status-decisions-commands`

Files touched, as declared in the START: `src/runtime/manager_status.py` (new), `src/bot/operator_commands.py` (new), `src/runtime/telegram_decisions.py`, `src/bot/telegram_query_bot.py`, `scripts/ci/check_collapsed_states.py`, and three test files. No VM action, no deploy, no workflow dispatch from this session.

**@MI-58 — the overlap is minimal and deliberate.** `src/bot/telegram_query_bot.py` changed in two places only: one import, and one `install_operator_commands(...)` call inside `main()`'s handler-registration block (plus a `_COMMAND_SURFACE` constant beside `_MENU_OPENERS`, which keeps its original meaning so the existing "no stale command wall" assertion still tests its own claim). `callback_handler`, the `wdec` branch and `answerable_route()`'s resolution are **untouched**. Both commands resolve `telegram_decisions.answerable_route()`, so when you move what that returns, they follow with no second migration.

⚠️ **A GREEN HARNESS PROVES NONE OF THIS, and the PR body says so at the top.** The done-condition is the operator typing each command and getting a correct reply; that needs a Tier-2 deploy + an `ict-telegram-bot.service` restart, neither of which happened here. Treat as `landed_unproven` when it merges.

⚠️ **CI IS WEDGED ON THIS PR AND IT IS NOT THE DIFF.** On head `0ec0278`, `guards`, `pytest-collect` and `repo-inventory` all completed **success** (guards in 1m51s). On the current head `e6963ca`, the same three jobs plus `pytest-run` have sat `in_progress` for **~55 minutes** with no completion — `repo-inventory` is the only one that finished (success, 10s). Flagging rather than walking past it: if other sessions see the same, it is the Actions queue, not our changes. A fresh trigger is the obvious first remedy.

Verified locally on the final tree instead, and stated for what it is:
- **147 tests** across the three touched test files, all passing.
- **Full suite: 14,617 passed / 0 failed.** The first run showed 21 failures; all 21 were a missing `scikit-learn` in this sandbox (`lightgbm.sklearn` raising), none in a module this change touches, and all 37 of those tests pass once it is installed. Stated because "21 failed" without that cause would read as a regression.
- `scripts/ci/run_guards.py`: **49 passed, 0 failed**, including the new `manager_status.tree_state` contract on `collapsed-state-guard`.
- `ruff` **0.15.22** (the pinned `>=0.15.0,<0.16` range): clean repo-wide. Note for anyone else linting locally — 0.16.x expands the default rule set and reports ~14k pre-existing errors repo-wide; that is the version, not the tree.

One incidental observation, recorded not fixed: **3 of the 4 `blocked` items in `MANAGER-CHECKLIST.json` declare no `blocked_on` edge.** `/status` surfaces that as `⚠️ blocked_on NOT DECLARED` rather than rendering them as ordinary blocked rows, which is how it came to light. Checklist data, not code — for the manager.
