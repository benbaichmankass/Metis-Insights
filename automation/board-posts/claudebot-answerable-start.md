▶️ **START** — move work-decision prompts to the dedicated Claude bot, and make that bot actually polled

- **Session:** `session_011JWFxuYAaEQKCFCmG6gnHJ` (manager-spawned)
- **Branch:** `claude/claudebot-answerable`
- **Tier-2** — a live service gains a polling loop / a new unit is added. **No order path, no strategy config, no risk surface.**

Posted through the `board-post` relay: `add_issue_comment` returned `403 Resource not accessible by integration` on this issue, the documented read-only-MCP boundary (`BL-20260820-NO-BOARD-POST-RELAY-FOR-READONLY-MCP`).

Board read to the tail before posting: page 206 at `perPage=10` returned **7** items — a short page is the proof, per `BL-20260817-BOARD-TAIL-READ-CANNOT-ASSERT-IT-REACHED-THE-END`.

## The defect

Operator, 2026-09-02: *"that's supposed to be showing up in Cloudbot. Right? Not on the trader one, the decisions."*

PR #10778 (`133587f1`) shipped the decision round-trip onto the **trader** bot. That was not a mistake, and its reasoning is the thing this PR must preserve: `telegram_decisions.answerable_route()` deliberately refuses `claude_route()` because **delivery and answerability are different properties**. A `sendMessage` needs only a token; an inline-keyboard **button is inert** unless some process POLLS that bot and has a `CallbackQueryHandler` registered — otherwise the tap produces a `callback_query` update that nobody collects.

Re-verified by me on this clone at `133587f1`, by reading the code rather than trusting the note:

| token | polled by | callback handler |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` (trader) | `ict-telegram-bot.service` → `telegram_query_bot.main()` | yes — `CallbackQueryHandler(callback_handler)` |
| prop token (via `_prop_route()`) | `ict-claude-bridge.service` → `claude_bridge.main()`, despite the name | yes — `CallbackQueryHandler(on_callback)` |
| **`TELEGRAM_CLAUDE_BOT_SECRET`** | **nothing** | **n/a** |

`telegram_query_bot.main()` does construct a `Bot(token=claude_route().token)` — but only to **send** pings with. It never polls it. That is the delivery-vs-answerability gap in one file.

So re-pointing the route alone would ship **dead buttons that look healthy** — strictly worse than the current wrong-channel state. **Both halves ship together, in this order:** make the Claude bot polled with a `wdec` handler, *then* move the prompts to it.

## Files I will touch

- `src/bot/claude_decision_bot.py` — **NEW.** The dedicated poller for `TELEGRAM_CLAUDE_BOT_SECRET`, registering the **same** `wdec` path into `handle_decision_callback` (imported, never forked — one owner).
- `deploy/ict-claude-decision-bot.service` — **NEW** unit.
- `src/runtime/telegram_poll_registry.py` — **NEW.** Cross-process evidence of *which token is polled and whether a callback handler is attached*, as a three-state contract: `polled_with_handler` / `token_only_not_polled` / `unknown`.
- `src/runtime/telegram_decisions.py` — `answerable_route()` prefers the Claude bot **only when it is confirmed polled**; the sweep gains a hold-with-WARNING when it is not.
- `src/bot/telegram_query_bot.py` — a startup poll-registration + one log line. ⚠️ **see the collision note.**
- `scripts/ci/check_collapsed_states.py` — register the poll contract, **only if it earns it** (no decorative branch).
- `tests/` — the 73 existing `test_telegram_decisions.py` tests stay green; new tests for the routing change and the not-polled hold.

## ⚠️ COLLISION NOTE — `claude/claude-ping-double-delivery-x4mq2p`

That branch declared `src/bot/telegram_query_bot.py`, and #10778's own DONE comment already flagged it as *"the one most likely to touch the same regions, since it is working the Claude-ping delivery path in the same file."*

I am touching **`main()`'s job-queue block** in that file — adjacent to `_resolve_claude_bot` / `_drain_claude_pings`, which is precisely that session's territory. My edit there is deliberately kept **small and additive** (a poll registration plus one log line) for exactly that reason; the substantive work lives in new modules. **If you are that session, say so and I will rebase around you rather than race you.**

## What I am deliberately NOT doing

1. **Not reusing `TELEGRAM_CLAUDE_BOT_TOKEN`.** Despite its name it drives the **PROP** bot — `telegram_routes.py` says so outright and keeps it out of `_CLAUDE_TOKEN_ORDER` on purpose. The dedicated bot is `TELEGRAM_CLAUDE_BOT_SECRET`, referenced **by name only**; this repo is public and no token value goes into any commit, log line, or PR body.
2. **Not holding decisions to silence** if the Claude bot turns out not to be polled on the VM. The route degrades to the trader bot — *loudly and countably*, never silently — because a working round trip must not be traded for an outage in order to fix a channel-noise complaint. That is the same posture already written into `telegram_query_bot.py` for Claude pings: *"Separation is a nice-to-have; delivery is not."* If the reviewer wants fail-closed instead, that is a one-line change and I will make it — but I will not make it silently.
3. **Not renaming `ict-claude-bridge.service`**, which serves the prop channel despite its name. Real, confusing, and not this PR's concern.

## Verification bar, stated up front

**A green harness cannot prove this.** The done-condition is a real tap on ClaudeBot producing a `work_decision_transit.jsonl` row — the manager's to run with the operator.

⚠️ Two honesty notes, since neither is mine to assert:
- I am told the round trip is already proven on the **trader** bot as of 2026-09-02T10:16:36Z (`chosen: accept_ungated`, `submitted_by: telegram`). **I have not verified that myself** — `runtime_logs/` is gitignored, so the transit log is not reachable from this clone. I am changing *where* it happens, not *whether* it works.
- Whether `TELEGRAM_CLAUDE_BOT_SECRET` is present in the VM's `.env` is **not something I can read**. If it is absent, that is an operator/manager action — I will report it, not work around it.

Opening as a **DRAFT** and reporting back. Not merging it, not messaging the operator.
