# Prop Telegram inbound — report a fill/close by typing in the channel

**Status:** built 2026-06-23. Symbol mapping + parser shipped in PR #4241; the
inbound handler is **folded into the live trader bot's message handler** (no new
service, no new token — see "Transport decision" below).

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
and break the existing poller. So the transport has to be a bot that is **both
already running AND the channel prop tickets actually land in**:

- `ict-claude-bridge` (`TELEGRAM_CLAUDE_BOT_TOKEN`) was *designed* as the
  prop-account bot, but on the Ampere VM its token never carried over the cutover
  — the service is **inactive** (verified via `/api/diag/services`, 2026-06-23).
  Reviving it would need the operator to mint a token (the hand-off we're
  avoiding).
- `ict-telegram-bot` (the trader bot, `TELEGRAM_BOT_TOKEN`) is **active** and is
  the live fallback `breakout_notify._prop_bot_token` resolves to — so prop
  tickets are **already delivered there today**. It's menu/callback-driven with
  **no free-text handler**, so adding one for prop commands is clean and can't
  shadow the menu.

So the handler is folded into `ict-telegram-bot`'s message handler: the operator
replies to a prop ticket in the channel it already arrives in, and the trade
updates. **No new bot token, no new service, no new secret** — a code-only change
the running `ict-telegram-bot` picks up on its next deploy restart. (If the
dedicated prop/claude channel is revived later, the same transport-agnostic
handler can be wired into `ict-claude-bridge` too.)

## Components

| Piece | File | Role |
|---|---|---|
| Symbol map | `src/prop/symbol_map.py` | venue↔bot symbol resolver (ETHUSDT↔ETHUSD), one source of truth from `breakout_routing.yaml`. |
| Parser | `src/prop/telegram_commands.py` | **pure** — text → intent → `ingest_report` dict (`parse_prop_command` + `build_report`). |
| Handler | `src/prop/telegram_report_handler.py` | enrich (resolve the open ticket's direction/id, default account) + call `prop_report.ingest_report`; returns a one-line ack or `None`. Transport-agnostic. |
| Transport | `src/bot/telegram_query_bot.py::on_text_message` | the trader bot's free-text handler (registered last so it never shadows the menu/command handlers) runs the prop handler off the event loop (`to_thread`) and replies on a recognised command; non-commands are ignored. Auth = the bot's existing `is_authorised` / `TELEGRAM_CHAT_ID` gate. |

The handler calls the SAME `ingest_report` chokepoint the REST endpoint +
dashboard form use, so journaling, ticket reconciliation, symbol canonicalisation,
and the `prop_fill`/`prop_closed` notifications are identical — this is just a
third inbound transport.

## Activation & deploy

Code-only — it activates the moment `ict-telegram-bot` runs the new code:

1. Merge → `ict-git-sync` pulls `main` and `deploy_pull_restart.sh` restarts
   `ict-telegram-bot.service` (or dispatch `pull-and-deploy` to force it now).
2. `PROP_DEFAULT_ACCOUNT` (optional) pins the account a bare command targets;
   unset, it resolves the single `exchange: breakout` account in `accounts.yaml`
   (`breakout_1` today).
3. Verify: reply `bal 5040 5010` in the prop channel → expect `✅ account status
   recorded …` and a `prop_account_status` row.

No BotFather change is needed for a 1:1 chat with the bot. (If the prop channel
is ever moved to a Telegram *group*, disable BotFather privacy mode so the bot
sees plain `close …` lines.)

## Tests

`tests/test_prop_symbol_map.py` + `tests/test_prop_telegram_commands.py` — both
map directions, parser (every verb/alias/edge number/bad-args), `build_report`,
and the handler end-to-end against an isolated journal (venue-symbol `close`
links the canonical-symbol ticket + flips it closed).
