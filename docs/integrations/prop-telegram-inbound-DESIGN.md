# Prop Telegram inbound — report a fill/close by typing in the channel

**Status:** built 2026-06-23, **draft / not yet deployed** (new live service =
Tier-2; needs operator OK + the prop bot configured for inbound). Branch
`claude/prop-trade-symbol-mapping-xqpxpa`.

## Problem

The Breakout prop account is a manual bridge: the bot emits a paste-ready
ticket, a human places it on the DXTrade terminal, then **reports back** so the
bot can journal + monitor the trade. Until now that report-back went through
Claude (or the dashboard Prop form) as a middle-man — the operator told Claude
"eth closed at 2950", Claude built the JSON and POSTed it. The operator wants to
skip the middle-man: **type a short command in the prop bot's Telegram channel
and have the system update the trade directly.**

## Approach (chosen 2026-06-23: structured command, no LLM)

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
  `bal|balance|status|equity`) so the operator isn't memorising one exact word.
- `acct=<id>` (or `@<id>`) anywhere targets a specific prop account; otherwise
  the single configured prop account is the default.
- Numbers tolerate `+`/`$`/`,`/`%`; the trailing non-numeric tokens are the
  free-text reason. A non-command line is ignored; a recognised verb with bad
  args gets a usage reply.

## Components

| Piece | File | Role |
|---|---|---|
| Parser | `src/prop/telegram_commands.py` | **pure** — text → intent → `ingest_report` dict (`parse_prop_command` + `build_report`). |
| Listener | `src/prop/telegram_inbound.py` | long-poll `getUpdates` loop; enriches an intent with the open ticket's direction/ticket-id, calls `prop_report.ingest_report`, replies with a one-line ack. |
| Service | `deploy/ict-prop-telegram-listener.service` | runs `python -m src.prop.telegram_inbound` (Restart=always, Nice=10). Data-dir drop-in wired in `scripts/install_systemd_units.sh`. |

The listener calls the SAME `ingest_report` chokepoint the REST endpoint +
dashboard form use, so journaling, ticket reconciliation, the symbol
canonicalisation, and the `prop_fill`/`prop_closed` notifications are identical —
this is just a third inbound transport.

## Activation & safety

Credential-driven, same shape as the outbound prop bot:

- **Bot token** — `TELEGRAM_PROP_BOT_TOKEN` → `TELEGRAM_CLAUDE_BOT_TOKEN` →
  `TELEGRAM_BOT_TOKEN` (via `breakout_notify._prop_bot_token`).
- **Chat allowlist** — `TELEGRAM_PROP_ALLOWED_CHAT_IDS` (CSV), falling back to
  `TELEGRAM_CHAT_ID`. A message from a chat NOT on the allowlist is logged and
  ignored — the listener never writes the prop journal off an unknown chat. With
  neither token nor allowlist set the service logs "inactive" and exits.
- **Default account** — `PROP_DEFAULT_ACCOUNT`, else the single
  `exchange: breakout` / `account_class: prop` account in `accounts.yaml`. With
  several prop accounts and none pinned, a bare command asks the operator to add
  `acct=<id>` rather than guess.

> ⚠ **Telegram bot privacy mode.** A bot added to a group only receives messages
> by default if privacy mode is OFF (or the message is a command/reply/mention).
> For a group prop channel, disable privacy mode via BotFather
> (`/setprivacy` → Disable) so the listener sees plain `close …` lines. A 1:1
> chat with the bot needs no change.

## Deploy (operator-gated, not done here)

1. Confirm/repoint the prop bot token + set `TELEGRAM_PROP_ALLOWED_CHAT_IDS` (and
   optionally `PROP_DEFAULT_ACCOUNT=breakout_1`) on the live VM `.env` (via
   `sync-vm-secrets` / the env path).
2. Disable BotFather privacy mode if it's a group channel.
3. `install_systemd_units.sh` installs the unit + drop-in on the next deploy;
   `systemctl enable --now ict-prop-telegram-listener.service`.
4. Verify: type `bal 5040 5010` in the channel → expect the `✅ account status
   recorded …` reply and a `prop_account_status` row.

## Tests

`tests/test_prop_telegram_commands.py` — parser (every verb, aliases, signed/
`$`/`,` numbers, account override, non-command→None, bad-args→ValueError),
`build_report` shapes, and the handler end-to-end against an isolated journal
(venue-symbol `close` links the canonical-symbol ticket + flips it to closed).
