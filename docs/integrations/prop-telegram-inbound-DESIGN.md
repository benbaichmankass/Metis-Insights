# Prop Telegram inbound — report a fill/close by typing in the channel

**Status:** built 2026-06-23. Symbol mapping + parser shipped in PR #4241; all
prop UI (report-back handler + `/menu` with the executor-assistant prompt) lives
in the **dedicated prop bot `ict-claude-bridge`** (`@claude_ict_comms_bot`) — see
"Transport decision" below. (It was briefly on the trader bot while the prop
bot's token was missing post-cutover; that detour was reverted once the operator
restored `TELEGRAM_CLAUDE_BOT_TOKEN`.)

## Problem

The Breakout prop account is a manual bridge: the bot emits a paste-ready
ticket, a human places it on the DXTrade terminal, then **reports back** so the
bot can journal + monitor the trade. Until now that report-back went through
Claude (or the dashboard Prop form) as a middle-man. The operator wants to skip
the middle-man: **type a short command in the prop bot's Telegram channel and
have the system update the trade directly.**

## Approach (structured command, no LLM)

A deterministic, positional command grammar parsed locally — zero token cost, no
ambiguity. Symbols may be typed in either the venue (`ETHUSD`) or canonical
(`ETHUSDT`) form; the symbol map (`src/prop/symbol_map.py`) canonicalises on the
way in.

```
close <symbol> <exit> [pnl] [reason]     close ETHUSD 2950 +80 tp
open  <symbol> <entry> [qty]             open  ETHUSD 3000 0.5
skip  <symbol> [reason]                  skip  ETHUSD stale
bal   <balance> [equity] [realized]      bal   5040 5010
```

- Verb aliases (`close|closed|c|exit`, `open|filled|placed|o`, `skip|cancel|x`,
  `bal|balance|status|equity`); `acct=<id>` (or `@<id>`) anywhere targets a
  specific prop account, else the single configured prop account is the default.
- Numbers tolerate `+`/`$`/`,`/`%`; the trailing non-numeric tokens are the
  reason. A non-command line falls through to the bot's normal reply; a
  recognised verb with bad args gets a usage hint.

## Transport decision (2026-06-23): fold into the live trader bot

The inbound side needs to consume Telegram updates (`getUpdates`), and a second
`getUpdates` consumer on a token already being long-polled would steal updates
and break the existing poller. The right home is the **dedicated prop bot**:

- `ict-claude-bridge` (`@claude_ict_comms_bot`, `TELEGRAM_CLAUDE_BOT_TOKEN`) **is**
  the prop-account bot — the token prop tickets are emitted to
  (`breakout_notify._prop_bot_token`), already long-polling, already authorising
  exactly the operator's `TELEGRAM_CHAT_ID`. Keeping all prop UI here matches the
  intended architecture.
- It was briefly hosted on `ict-telegram-bot` (the trader bot) because the prop
  bot's token didn't carry over the Ampere cutover, leaving it **inactive**. The
  operator restored `TELEGRAM_CLAUDE_BOT_TOKEN` (2026-06-23), so the prop bot is
  the home again and the trader-bot detour was reverted.

So **all** prop UI lives in `ict-claude-bridge`: a `/menu` with the
executor-assistant prompt + a format reminder, and a free-text handler that
ingests a typed report-back. `@bict_trading_bot` (the trader bot) keeps its
menu-only control plane — no prop handlers.

## Components

| Piece | File | Role |
|---|---|---|
| Symbol map | `src/prop/symbol_map.py` | venue↔bot symbol resolver (ETHUSDT↔ETHUSD), one source of truth from `breakout_routing.yaml`. |
| Parser | `src/prop/telegram_commands.py` | **pure** — text → intent → `ingest_report` dict (`parse_prop_command` + `build_report`); also holds `USAGE` + the `REPORT_PROMPT` executor-assistant block (no telegram dep). |
| Handler | `src/prop/telegram_report_handler.py` | enrich (resolve the open ticket's direction/id, default account) + call `prop_report.ingest_report`; returns a one-line ack or `None`. Transport-agnostic. |
| Transport | `src/bot/claude_bridge.py` | the prop bot: `/start`+`/menu` open an inline menu (📋 report prompt · ❓ format); `on_callback` sends `REPORT_PROMPT`/`USAGE` (plain text — the `<SYMBOL>` placeholders forbid HTML parse_mode); `_on_operator_message` runs the prop handler off the event loop (`to_thread`) and acks. Auth = the bridge's existing `TELEGRAM_CHAT_ID` gate. Claude's update delivery is unchanged. |

The handler calls the SAME `ingest_report` chokepoint the REST endpoint +
dashboard form use, so journaling, ticket reconciliation, symbol canonicalisation,
and the `prop_fill`/`prop_closed` notifications are identical — this is just a
third inbound transport.

## Activation & deploy

Code-only **once the prop bot is alive** — it activates when `ict-claude-bridge`
runs the new code with its token set:

1. **Operator hand-off (one time):** add `TELEGRAM_CLAUDE_BOT_TOKEN` (the
   `@claude_ict_comms_bot` BotFather token) to the repo's Actions secrets. Claude
   syncs it to the VM `.env` via `sync-vm-secrets`, then starts/restarts
   `ict-claude-bridge.service`.
2. Merge → `ict-git-sync` / `pull-and-deploy` restarts `ict-claude-bridge` on the
   new code.
3. `PROP_DEFAULT_ACCOUNT` (optional) pins the account a bare command targets;
   unset, it resolves the single `exchange: breakout` account in `accounts.yaml`
   (`breakout_1` today).
4. Verify: `/menu` → 📋 Prop report prompt returns the block; `bal 5040 5010`
   replies `✅ account status recorded …` and writes a `prop_account_status` row.

No BotFather change is needed for a 1:1 chat with the bot. (If the prop channel
is ever a Telegram *group*, disable BotFather privacy mode so the bot sees plain
`close …` lines.)

## Yes/No buttons ON the ticket (built 2026-06-25)

The primary report-back is now **inline Yes/No buttons attached to the trade
ticket itself** (`breakout_notify.emit_prop_signal` →
`build_place_decision_keyboard`): every emitted prop ticket ends with
`[✅ Yes — I placed it] [❌ No — not placed]`. The operator taps after acting —
✅ → the bot replies with the fill-details prompt (ticket → `awaiting_report`);
❌ → logged not-placed (ticket → `expired`). Same `propexp:*` callbacks +
`claude_bridge` handler as the expiry prompt below, so no new transport. The
typed JSON / command grammar stays as a fallback. (The buttons supersede the
operator-typed `/testexpiry` test command + the separate delayed-only prompt as
the day-to-day path; both remain — the expiry prompt below is now the *nag* for a
ticket left un-tapped past its validity window.)

## Expired-ticket Yes/No prompt (built 2026-06-24)

A ticket that passes its `valid_until` with no report-back is silent drift — the
bot can't tell whether the operator placed it and forgot to report, or skipped
it. Instead of leaving it to `prop_reconcile.find_unacted_tickets`, the bot now
**asks**: once per trader tick `src/prop/prop_expiry_prompt.run_prop_expiry_prompts`
(called from `src/main.py`, next to the monitor pulse) finds just-expired un-acted
tickets and sends the prop bot a message with two inline buttons:

```
⏰ PROP TICKET EXPIRED — ETHUSDT SHORT [breakout_1] … Did you place this trade?
    [✅ Yes — I placed it]   [❌ No — not placed]
```

Lifecycle on the `prop_tickets` row:

```
emitted ─(stale, prompt sent)─▶ expiry_prompted ─┬─ No ─▶ expired
                                                 └─ Yes ▶ awaiting_report ─(fill)▶ filled/closed
```

- **No** → the ticket is logged `expired` (operator confirmed it was never placed).
- **Yes** → it moves to `awaiting_report` and the operator gets the same
  `REPORT_PROMPT` block; pasting back `open …` / `close …` flows through the SAME
  `prop_report.ingest_report` chokepoint and links to the ticket
  (`match_fill_to_ticket` now accepts `expiry_prompted` / `awaiting_report`).

| Piece | File | Role |
|---|---|---|
| Detector + runner + callback logic | `src/prop/prop_expiry_prompt.py` | `find_tickets_to_prompt` (expired + un-acted + recency-bounded), `run_prop_expiry_prompts` (per-tick; flips to `expiry_prompted` only after a confirmed send — the idempotency guard, no state file), `build_expiry_keyboard`, `handle_expiry_callback` (the shared, transport-agnostic Yes/No handler). |
| Sender | `src/prop/breakout_notify.py::emit_prop_expiry_prompt` | Telegram-only (buttons are Telegram-only); sent via `_prop_bot_token()` so the answer routes to the prop bot. Uses the new `send_telegram_direct(reply_markup=…)`. |
| Callback transport | `src/bot/claude_bridge.py` (`propexp:*`), with a fallback in `src/bot/telegram_query_bot.py` for the degraded case where `_prop_bot_token` falls back to the trader bot token. |

Knobs (baseline, no enable gate — Prime Directive): `PROP_EXPIRY_PROMPT_SECONDS`
(`<= 0` pauses prompting), `PROP_EXPIRY_PROMPT_MAX_AGE_HOURS` (default 12 — a
ticket that expired longer ago than this is too stale to ask about, so a
historical backlog can't spam on first deploy).

## Price-invalidation prompt (built 2026-07-16)

The expiry prompt above closes the loop on **time** (the ticket passed
`valid_until`). But a ticket can go stale on **price** first: while the bot is
still waiting for the operator's place-decision Yes/No, price can move **beyond
the ticket's `[SL, TP]` brackets** — a run to the SL means the setup already
failed, a run to the TP means the move already happened — so the entry the ticket
describes is no longer worth placing. The operator asked (2026-07-16) for the bot
to keep tracking the emitted ticket and **proactively** send an update the moment
it becomes irrelevant, warning **not to place it if they haven't already**, then
re-asking whether it was placed.

**What it does:** once per trader tick,
`src/prop/prop_invalidation_prompt.run_prop_invalidation_prompts` fetches the
current price for each still-`emitted` ticket (same `connector_for_symbol` +
`fetch_candles` last-close path as `prop_sl_tp_alert`) and, if price has left the
`[SL, TP]` band, sends the prop-bot message:

    🚫 PROP SETUP NO LONGER VALID — ETHUSDT SHORT [breakout_1]
    entry 1717 · SL 1740 · TP 1650 · qty 0.0167
    Price 1745 has moved beyond the brackets (SL 1740 reached).
    ⚠️ Do NOT place this trade if you haven't already.
    Did you already place it?    [✅ Yes — I placed it]  [❌ No — not placed]

The Yes/No **reuses the existing** `propexp:*` keyboard + `handle_expiry_callback`
— no new transport: **No** → `expired`; **Yes** → `awaiting_report` → the
fill-paste prompt.

Lifecycle (parallel to the timeout path):

    emitted ─(price beyond brackets, warn+ask)─▶ invalidated_prompted ─┬─ No ─▶ expired
                                                                       └─ Yes ─▶ awaiting_report ─(fill)─▶ filled/closed

**No double-prompt:** the detector scans `emitted` tickets only, so a flip to
`invalidated_prompted` drops the ticket out of BOTH this path and the timeout
path's `find_unacted_tickets` (also `emitted`-only). The flip happens only after
a confirmed send (delivery failure retries next tick). `match_fill_to_ticket`
accepts `invalidated_prompted` as an open/awaiting status (a fill pasted directly
without tapping a button still links), but it is **not** in
`_CLOSE_LINKABLE_STATUSES` — a never-placed signal must never receive a close
mis-link (BL-20260706-PROP-CLOSE-MISLINK).

| Piece | File | Role |
|---|---|---|
| Detector + runner + crossing logic | `src/prop/prop_invalidation_prompt.py` | `bracket_invalidation` (price left `[SL,TP]`?), `find_tickets_to_check` (`emitted` + has-bracket + recency-bounded), `run_prop_invalidation_prompts` (per-tick; flips to `invalidated_prompted` only after a confirmed send). |
| Sender + message | `src/prop/breakout_notify.py::emit_prop_invalidation_prompt` / `render_invalidation_prompt_message` | Telegram-only via `_prop_bot_token()`; reuses `build_expiry_keyboard` (the `propexp:*` Yes/No). |
| Callback transport | `src/bot/claude_bridge.py` (`propexp:*`) — **unchanged** (shared with the expiry prompt). |

Knobs (baseline, no enable gate — Prime Directive): `PROP_INVALIDATION_PROMPT_SECONDS`
(`<= 0` pauses), `PROP_INVALIDATION_PROMPT_MAX_AGE_HOURS` (default 12 — an
emitted ticket older than this is the timeout path's job, not this one).
Tests: `tests/test_prop_invalidation_prompt.py`.

## Screenshot report-back + folded balance ask (2026-07-11)

Two operator-requested additions close the two friction points that surfaced
while logging a live ETH prop trade: (1) the report-back was text-only, and
(2) logging a trade left the rule-distance guard blind until a *separate*
periodic ping (`prop_status_request`) later asked for the balance.

- **Screenshot path (the image half of the bridge).** The operator can now send
  a **photo** of the Breakout/DXtrade terminal (a Position detail screen and/or
  the account/portfolio summary) to the prop bot instead of typing. A
  `MessageHandler(filters.PHOTO)` in `src/bot/claude_bridge.py`
  (`_on_operator_photo`) downloads the highest-res image and hands it to
  `telegram_report_handler.handle_screenshot`, which calls
  `src/prop/screenshot_parse.py::parse_screenshot` (Claude vision, model
  `PROP_SCREENSHOT_MODEL`, default `claude-sonnet-5`) to extract the SAME
  structured report(s) the text grammar produces, then ingests each through the
  one `prop_report.ingest_report` chokepoint. A single screen can yield a fill
  **and** an account_status (a portfolio screen showing both). **Honest-null:** the
  extractor is instructed to OMIT any field it can't read (a Position screen has
  no balance — "Used Margin" and "Open P/L" are explicitly NOT the account
  balance / a realized pnl), never a fabricated `0`. Fully isolated: no API key /
  bad image / unparseable output → a readable "type it instead" reply, never a
  crash. `anthropic` is already a dependency (M13 insights) — no new package.

- **Folded balance ask.** `telegram_report_handler.account_status_nudge` appends
  a "also send the account balance" reminder to a **fill** ack (open / placed /
  close — never a `skip` or a pure `bal`) when the latest `prop_account_status`
  snapshot is absent or older than `PROP_STATUS_REQUEST_MAX_AGE_HOURS` (the SAME
  threshold the periodic `prop_status_request` uses, so the two never double up;
  `<= 0` disables both). A fresh balance on file suppresses it — and when a
  screenshot carries both a fill and the balance, the account_status is ingested
  first so the trade ack doesn't nag for a balance it just recorded.

| Piece | File | Role |
|---|---|---|
| Vision extractor | `src/prop/screenshot_parse.py` | `parse_screenshot` (image → report list); pure `_reports_from_model_json` shaping + number coercion + honest-null; LLM call isolated in `_call_vision`. |
| Screenshot orchestration | `src/prop/telegram_report_handler.py::handle_screenshot` | parse → ingest each (account_status first) → combined ack + single nudge. |
| Balance nudge | `src/prop/telegram_report_handler.py::account_status_nudge` | folds the status ask into every fill ack when the guard is stale/blind. |
| Photo transport | `src/bot/claude_bridge.py::_on_operator_photo` | downloads the photo, runs the handler off the event loop. |

## Tests

`tests/test_prop_symbol_map.py` + `tests/test_prop_telegram_commands.py` — both
map directions, parser (every verb/alias/edge number/bad-args), `build_report`,
and the handler end-to-end against an isolated journal (venue-symbol `close`
links the canonical-symbol ticket + flips it closed).
`tests/test_prop_expiry_prompt.py` — the detector, per-tick idempotency +
send-failure retry, the Yes/No callback transitions, and the
Yes→awaiting_report→fill-links-back lifecycle.
`tests/test_prop_screenshot_parse.py` — the vision extractor's pure shaping
(position→fill, account→status, both, comma/currency coercion, honest-null
omission, drop-junk) + `parse_screenshot` with the LLM seam monkeypatched.
`tests/test_prop_status_nudge.py` — the folded balance nudge (fires on a fill
when stale/absent, quiet on fresh/skip/status, env-disabled) + `handle_screenshot`
ingesting fill+balance from one image with the nudge suppressed.
