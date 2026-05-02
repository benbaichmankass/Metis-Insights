# comms/ — Async Communication Layer

This directory is the async channel between Claude and Ben. The Telegram bot monitors this directory on every git sync cycle.

## How It Works

1. **Claude writes** `pending_input.json` when a decision is needed (Tier 2 or Tier 3) or a PM sprint is ready.
2. **The bot detects** the new file on the next git pull cycle and sends a Telegram message to Ben with formatted text and inline buttons.
3. **Ben responds** by tapping a button in Telegram.
4. **The bot writes** `input_response.json` with the response.
5. **Claude reads** `input_response.json` on the next git pull and continues.

## Files

| File | Writer | Reader | Purpose |
|------|--------|--------|---------|
| `pending_input.json` | Claude | Telegram bot | Decision request to Ben |
| `input_response.json` | Telegram bot | Claude | Ben's response |
| `sprint_state.json` | Claude | Claude + bot | Current sprint and session state |
| `test_request.json` | Telegram bot | Claude | `/test [strategy]` command from Ben |
| `test_results.json` | Claude | Telegram bot | Backtest results to send to Ben |
| `archive/` | Both | — | Processed request/response pairs |

## Schemas

See `schemas/` for example JSON for each file type.

## Rules

- Only one `pending_input.json` active at a time.
- Never delete `input_response.json` without archiving it first.
- All timestamps in ISO 8601 UTC format.
- Claude always checks this directory at the start of every session.
