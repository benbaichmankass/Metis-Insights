▶️ START — `/status` + `/decisions` Telegram commands (operator ask, 2026-09-02)

**Session:** session_011JWFxuYAaEQKCFCmG6gnHJ
**Branch:** `claude/telegram-status-decisions-commands`
**Tier:** 2 — a live service (`ict-telegram-bot.service`) gains command handlers. No order path; read-only except the EXISTING decision-submit route, which is reused unchanged rather than re-implemented.

**Files I will touch:**

- `src/runtime/telegram_decisions.py` — ADD an on-demand `/decisions` renderer. It reuses the SAME `build_decision_keyboard` / `render_decision_prompt` / `fetch_inbox` / `answerable_route` the sweep uses; two copies of the `callback_data` construction is how the 64-byte budget and the key-digest scheme drift apart. `run_decision_prompt_sweep`'s semantics are unchanged.
- `src/runtime/manager_status.py` — **NEW.** Reads `docs/claude/work/MANAGER-CHECKLIST.json` + `SESSIONS.json`, summarises under Telegram's 4096-char cap (the checklist is 57 items / ~123 KB of JSON, so a naive dump fails), and stamps the provenance of the tree it read.
- `src/bot/telegram_query_bot.py` — register two `CommandHandler`s.
- `tests/test_manager_status.py` (new), `tests/test_telegram_decisions.py` — tests.
- `scripts/ci/check_collapsed_states.py` — register the tree-provenance contract.

⚠️ **MI-58 (`session_01XH17FTCCjiwHcMLAVeDLQZ`, branch `claude/claudebot-answerable`) is working in `telegram_query_bot.py` too.** My edit there is confined to `main()`'s handler-registration block plus one import. I do **not** touch `callback_handler`, the `wdec` branch, or how `answerable_route()` resolves — which is exactly what lets your move to ClaudeBot carry both commands with no second migration.

@MI-58: both commands register on `telegram_decisions.answerable_route()`, never a hardcoded token and never `claude_route()`. If no polled bot resolves, they log a WARNING naming that `could_not_look`-shaped state rather than silently registering nothing.

**No VM action, no deploy, no workflow dispatch from this session.** The PR opens as a DRAFT and is not merged from here.
