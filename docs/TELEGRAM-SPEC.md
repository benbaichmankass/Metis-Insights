# Telegram Bot — Product Spec

**Status:** authoritative for Sprint S-001 (Telegram bot hardening).
**Owner:** Ben (PM). Implementation pinged at the start of S-001.
**Source:** Sprint S-001 brief, 2026-04-29.

---

## 1. Goal

Make the Telegram bot fully **dynamic**: it reads live system state
(configs, accounts, strategies, logs, DB) instead of hardcoded values.
No more manual bot code edits when the system changes (new strategy,
new account, renamed service, etc.).

The bot is the operator's only mobile-first control plane for live
trading. It must be resilient — missing data should produce a friendly
"service unavailable" message, never a crash.

## 2. Scope

The bot must respond to **exactly these 11 commands**, with the
behaviour specified in §4. Anything beyond these 11 is out of scope
for this sprint (existing commands such as `/halt`, `/resume`,
`/balance`, `/start`, `/backtest` may stay or be removed in a follow-up
sprint, but are not part of S-001's acceptance criteria).

## 3. Vocabulary

These terms have specific meanings inside the bot:

| Term | Definition |
|---|---|
| **Account** | A single live exchange account that the bot can place orders on. Identified by an `account_id` (e.g. `bybit-main`, `binance-sub-1`). One account is bound to exactly one trader process / systemd service. |
| **Strategy** | A signal-producing module (e.g. `breakout_confirmation`, `vwap`, `killzone`, `ict`). One trader process can multiplex several strategies; the **account** is what gets toggled, not the strategy. |
| **Trader service** | The systemd unit that runs the trader process for one account. Default: `ict-trader-live`. Future accounts will follow `ict-trader-<account_id>`. |
| **Live signal** | A signal recently emitted by a strategy and persisted to the signals DB. |
| **Trade journal** | The SQLite DB recording placed/closed trades and backtest results (`trade_journal.db` at repo root, override via `TRADE_JOURNAL_DB`). |

### Today vs. tomorrow

The runtime today (April 2026) runs **one** trader service against
**one** `.env`, with the strategy multiplexer (`STRATEGIES` in
`src/runtime/pipeline.py`) running multiple strategies inside that
single process. The bot must already be designed around the
**account-registry model** so that when sprint M6 lands a second
account, no bot code changes are needed.

In the single-account state of today:
- `/accounts` returns 1 row (the live account).
- `/toggle` prompts with that 1 account and toggles its trader service.
- `/closeall` hits that 1 account.
- `/log` and `/last5` prompt with the live services / strategies that
  are actually present at runtime.

## 4. Command specs

### 4.1 `/help`
Show the command menu and a one-line description for each of the 11
commands. No live data needed.

### 4.2 `/status`
Report **per-strategy** status. For each strategy currently configured
in the runtime (read from `src.runtime.pipeline.STRATEGIES` or
equivalent), display:
- strategy name and friendly label
- running? (yes / no — based on the trader service being `active` AND
  the strategy being in the configured `STRATEGIES` list)
- last signal time for that strategy (most recent `timestamp` in
  signals DB filtered by strategy/setup_type)
- basic P&L summary if available (today's realised P&L for trades
  attributed to this strategy; "n/a" if attribution column is missing)

Rendering: one section per strategy. Top-line kill-switch state
(halted vs. running) is also shown so the operator can see the global
flag at a glance.

### 4.3 `/accounts`
List **all connected accounts** registered with the bot. For each:
- account_id and exchange
- strategy bindings (the `STRATEGIES` list this account's trader runs)
- account balance (live exchange query, USDT total or main quote)
- last trade for this account: timestamp, symbol, long/short,
  win/loss, P/L

If a live exchange query fails, render the row with `balance: ⚠️
unavailable` rather than crashing the whole command.

### 4.4 `/trades`
**Open positions** across **all accounts**. Each position line:
`<account_id> | <symbol> <side> | size | entry | uPnL`. Group by
account for readability.

If an account's open-positions query fails, render
`<account_id>: ⚠️ unavailable` and continue with the others.

### 4.5 `/closeall`
**Emergency close ALL positions** across **all accounts**. Iterate
every registered account, close everything via `reduceOnly` market
orders, then report a summary:
```
🚨 CLOSE ALL — multi-account
✅ Closed N positions across M accounts
❌ Failed: K (with first 5 errors)
```
This command is destructive. It requires no extra confirmation today
(matches existing `/closeall` behaviour) but must always log the
operator's chat-id and timestamp.

### 4.6 `/log`
Prompt the user with an **inline keyboard** of all live trader
services that are currently registered. On selection, show the last
20 journalctl lines for that service (with the existing
`get_last_logs` helper, parameterised by service name).

If only one service is registered, the bot may skip the prompt and
show that service's logs directly.

### 4.7 `/toggle`
Prompt the user with an **inline keyboard** of all registered
**accounts** (NOT strategies). On selection, toggle the trader service
for that account: `start` if currently inactive, `stop` if active.
Confirm with the new state.

### 4.8 `/download_journal`
Download `trade_journal.db` (or whichever path `TRADE_JOURNAL_DB`
points to) as a Telegram document attachment. If the file does not
exist, return `⚠️ trade journal not found at <path>`.

### 4.9 `/last5`
Prompt the user with an **inline keyboard** of the live strategies
(read from `STRATEGIES` at runtime). On selection, show the **last 5
signals** for that strategy from the signals DB (FVGs, OBs,
crossovers, etc. — whatever the strategy emits) with timestamps.
Format: timestamp | symbol | signal_type | direction | price.

### 4.10 `/latest_backtest`
Show backtest status / summary for the **last round of training
sessions, one summary per model**. Read from the
`backtest_results` table (or equivalent), grouped by `strategy_version`
or `model_id`, and render the latest row per group. If only one model
exists, this is one summary; if multiple, one summary per model.

### 4.11 `/price`
Current BTC price from the Bybit public REST endpoint (as today). If
Bybit is unreachable, fall back to "n/a" rather than crashing.

## 5. Tech approach

- **Reuse** `src/bot/telegram_query_bot.py` as the entry point. Do not
  spin up a parallel bot file.
- Add **dynamic data loaders** in a new module `src/bot/data_loaders.py`
  (kept inside `src/bot/` to match existing convention; the sprint
  brief's `src/telegram/` path is a hint, not a hard requirement).
  Loaders provide:
  - `list_accounts()` → list of account dicts (registry).
  - `list_live_strategies()` → list of strategy names from runtime.
  - `list_trader_services()` → list of systemd unit names actually
    deployed.
  - `recent_signals_for(strategy, n=5)` → DB query.
  - `recent_logs_for(service, n=20)` → journalctl wrapper.
  - `latest_backtests_per_model()` → DB grouped query.
  - `account_balance(account)` / `account_open_positions(account)` /
    `account_last_trade(account)` — exchange-aware (Bybit / Binance).
- **Inline keyboards** drive `/log`, `/toggle`, `/last5` selection.
- **Account registry** seed implementation: scan `.env` files matching
  `<repo>/.env` and `<repo>/.env.<account_id>`, plus an optional YAML
  file `config/accounts.yaml`. The data-loader must work with either
  source so adding an account does not require code changes.
- **Resilience**: every loader catches its own exceptions and returns
  a neutral fallback (empty list / `None` / "unavailable"). Command
  handlers never see exceptions that didn't originate in their own
  rendering code.
- **Tests**: extend `tests/test_telegram_query_bot.py`, add new test
  files for data loaders and inline-keyboard flow with mocks.

## 6. Acceptance criteria (binary)

- [ ] All 11 commands respond as specified in §4.
- [ ] No hardcoded strategy or account list anywhere in command
      handlers; everything pulled via data loaders.
- [ ] Toggle and start/stop act on **accounts**, never on strategies.
- [ ] `/log`, `/last5`, `/toggle` use inline-keyboard prompts.
- [ ] `/closeall` iterates every registered account.
- [ ] Bot does not crash on missing data (DB absent, exchange down,
      service unknown). Each failure shows a friendly message.
- [ ] New tests cover happy path + at least one failure mode per loader.
- [ ] No new third-party dependencies introduced.

## 7. Out of scope for S-001

- Risk-cap or order-logic changes (covered by separate sprint).
- New strategies or backtest workloads.
- Multi-user authorisation. The bot remains single-`TELEGRAM_CHAT_ID`.
- Deprecating the existing extra commands (`/halt`, `/resume`,
  `/balance`, `/backtest`) — they may stay during the sprint and be
  cleaned up in a follow-up.

## 8. Open questions for PM

These are documented now so they are answered before the matching PR
lands. Not blocking the spec doc itself.

1. **Account registry source.** Default plan: read accounts from a new
   `config/accounts.yaml` (with `<repo>/.env` as the fallback for the
   single-account case). OK?
2. **Strategy → trade attribution.** `/status` and `/accounts` need
   per-strategy P&L. Today's `trades` table does not store
   `strategy_name`. Two options: (a) add a column in a follow-up PR
   so today's bot just shows "n/a"; or (b) infer from `setup_type`
   where possible. Default plan: (a).
3. **`/closeall` confirmation.** Today's `/closeall` fires immediately.
   With multi-account, do you want a 2-step confirm via inline button?
   Default plan: keep one-step (matches today) but log every fire.

---
*Last updated: 2026-04-29.*
