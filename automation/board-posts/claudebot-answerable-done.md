✅ **DONE** — decision prompts moved to the dedicated Claude bot, and that bot is now POLLED

- **Session:** `session_011JWFxuYAaEQKCFCmG6gnHJ` (manager-spawned)
- **Branch:** `claude/claudebot-answerable`
- **PR:** [#10789](https://github.com/benbaichmankass/Metis-Insights/pull/10789) — **DRAFT, not merged by me.** Head `1a36209`, **all 4 checks green** (`guards`, `pytest-collect`, `pytest-run`, `repo-inventory`).
- **Tier-2.** No order path, no strategy config, no risk caps.

## What landed

Both halves, in order — because doing only the second ships **dead buttons that look healthy**:

1. **`src/bot/claude_decision_bot.py` + `deploy/ict-claude-decision-bot.service`** — poll `TELEGRAM_CLAUDE_BOT_SECRET` and register the SAME `wdec` path into `telegram_decisions.handle_decision_callback` (imported, never forked; the API route stays the one owner of every refusal: 400 unknown option / 400 empty / 409 already-answered / 503 gate).
2. **`src/runtime/telegram_poll_registry.py`** — a heartbeat-backed, three-state answer to *is a tap on this bot received?*: `polled_with_handler` / `token_only_not_polled` (we looked) / `unknown` (we could not look). Registered with `collapsed-state-guard` as `telegram_poll.poll_state` — 3 consumers, all states read.
3. **`answerable_route()`** prefers the Claude bot **only on positive poll evidence**; its delivery-vs-answerability docstring is sharpened, not deleted.

Also: `src/web/api/routers/diag.py` (the new unit in `_CANONICAL_UNITS` + two `log_file` names), `src/bot/telegram_query_bot.py` (poll registration + one log line), `scripts/install_systemd_units.sh`, `docs/ARCHITECTURE-CANONICAL.md`, `docs/claude/deployment-ops.md`, `CLAUDE.md`, `tests/test_s012_service_consolidation.py`.

## ⚠️ What this does NOT establish

**A green harness cannot prove this, and I am not claiming it does.** Every passing test is equally true of a process that is never started. The done-condition is a **real tap on ClaudeBot producing a `work_decision_transit.jsonl` row** — the manager's to run with the operator.

Worse, the fallback makes failure *quiet-but-working*: if the Claude bot is not confirmed polled, prompts go to the **trader** bot, so **"decisions still arrive" is NOT evidence the move happened.** Read `destination` in the sweep stats or `/api/diag/log_file?name=telegram_poll_claude` — never the mere arrival of a prompt.

Tracked as `OI-20260902-DECISION-PROMPTS-MOVED-TO-CLAUDEBOT-AND-NO-TAP-HAS-LANDED-THERE` (`loud: true`).

## ⚠️ Two things the manager/operator owns

- **`TELEGRAM_CLAUDE_BOT_SECRET` must be in the VM `.env`.** I cannot read whether it is. If absent, the unit exits `EX_CONFIG` (78) and stops in a visible `failed` state rather than crash-looping — an operator action, reported not worked around. **Do NOT substitute `TELEGRAM_CLAUDE_BOT_TOKEN`: it drives the PROP bot despite its name.**
- **The new unit must be installed + enabled**, which `install_systemd_units.sh` does on the next deploy run. Merging is not deploying.

**Not verified by me:** I am told the round trip is already proven on the *trader* bot at 2026-09-02T10:16:36Z. `runtime_logs/` is gitignored, so I could not reach the transit log from a session clone. This changes *where* it happens, not *whether* it works.

## ⚠️ Heads-up — `claude/claude-ping-double-delivery-x4mq2p`

I touched `src/bot/telegram_query_bot.py`, which you declared. My edit is **additive and confined to `main()` immediately before `run_polling()`** — a `record_poll` call, a job-queue heartbeat, and one banner line; no change to `_resolve_claude_bot` / `_drain_claude_pings` / `callback_handler`. Should rebase cleanly, but you own that file — shout if you'd rather I move it.

## Filed, not fixed

`BL-20260902-WDEC-DISPATCH-BRANCH-TESTS-A-LITERAL-WHILE-ENCODER-AND-POLL-CLAIM-USE-THE-CONSTANT` — `callback_handler` dispatches on the literal `"wdec"` while the encoder, the new handler and the poll claim all resolve `CB_PREFIX`, so a rename moves two of three. Not currently broken (`low`); left alone precisely because that file is claimed above.

Also fixed en route: `unwired-artifact-guard` was blind to `python -m <dotted.module>`, so it graded the new bot unwired while its own systemd unit runs it. Negative control **run, not assumed** — a planted unwired file is still reported.
