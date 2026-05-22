# ICT Trading Bot

Python-based ICT trading bot focused on ICT concepts such as fair value gaps (FVG), order blocks (OB), swing structure, market structure shifts, and kill zones. It runs **multi-symbol across two asset classes at once**: BTCUSDT crypto perps on Bybit and **MES (Micro E-mini S&P 500) futures on Interactive Brokers** — the same three strategies (`turtle_soup`, `vwap`, `ict_scalp_5m`) evaluate both symbols every tick. MES paper trading went live 2026-05-22; see [`docs/runbooks/ib-integration.md`](docs/runbooks/ib-integration.md).

## Workflow source of truth

The repo is the source of truth for how this program is planned and executed.
**Canonical docs (adopted 2026-05-10 in S-CANON-1) override older notes
when they disagree** — always read these first:

| Doc | Purpose |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | Claude session router and standing rules. **Includes the mandatory documentation-hygiene loop** (read docs at session start AND session end; reconcile contradictions) added after the 2026-05-17 PR #1358 incident — read this banner every session. |
| [`docs/CLAUDE-RULES-CANONICAL.md`](docs/CLAUDE-RULES-CANONICAL.md) | Authoritative Claude operating rules — permission tiers (Tier-1 / 2 / 3), workflow routing, **Documentation Hygiene & Premise Verification**. |
| [`docs/ARCHITECTURE-CANONICAL.md`](docs/ARCHITECTURE-CANONICAL.md) | System architecture, trade pipeline, comms pipeline, deployment flow. |
| [`ROADMAP.md`](ROADMAP.md) | Sprint-level backlog and phase status. |
| [`docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`](docs/SPRINT-LOG-TEMPLATE-CANONICAL.md) | Mandatory sprint-log format. |
| [`docs/github-actions-workflows.md`](docs/github-actions-workflows.md) | Canonical GitHub Actions reference. |

Historical operating notes preserved for context (canonical docs above
supersede when they disagree):

| Doc | Purpose |
|---|---|
| [`docs/workplan.md`](docs/workplan.md) | Older master workplan — goal, priorities, milestone types, merge tiers, VM rules. |
| [`docs/claude/milestone-state.md`](docs/claude/milestone-state.md) | Quick-glance "where the program is right now" — active milestone, queued milestones, open blockers. |
| [`docs/claude/operating-protocol.md`](docs/claude/operating-protocol.md) | Older Claude operating protocol — session shape, merge authority, ping-PR pattern. Superseded by `docs/CLAUDE-RULES-CANONICAL.md`. |
| [`docs/claude/decomposition-rules.md`](docs/claude/decomposition-rules.md) | Normative rules for milestone → sprint → checkpoint decomposition. |
| [`docs/claude/sprint-planning.md`](docs/claude/sprint-planning.md) | Binding sprint-prompt template (every sprint must satisfy it). |
| [`docs/claude/checkpoint-workflow.md`](docs/claude/checkpoint-workflow.md) | Resume rules and end-of-session handoff format. |
| [`docs/claude/checkpoints/CHECKPOINT_LOG.md`](docs/claude/checkpoints/CHECKPOINT_LOG.md) | Append-only log of session handoffs (the source of truth for "where to resume"). |

## Features

- ICT analysis engine for market structure, swings, FVGs, and order blocks
- Bybit and Binance crypto connectors + an Interactive Brokers connector (`ib_insync`, no API keys — auth is the IB Gateway login session) for MES futures market data and execution
- Multi-symbol pipeline: per-symbol data routing (`connector_for_symbol`) and a symbol→exchange dispatch gate so BTCUSDT and MES trade side by side without cross-routing
- Runtime validation for startup safety checks such as `MODE`, `DRY_RUN`, `RISK_PER_TRADE`, and `MAX_QTY`
- Kill zone scalper pipeline with exchange injection support in the runtime flow
- Telegram bot commands for status, trade actions, and backtest access
- Backtesting tools for historical downloads and strategy comparison
- TUI / control panel components for local operation and monitoring

## Project Structure

```text
ict-trading-bot/
├── src/
│   ├── core/         # ICT analysis, strategy logic, kill zone logic
│   ├── exchange/     # Bybit and Binance connectors, exchange/order handling
│   ├── runtime/      # Runtime settings, validation, pipeline, and orders
│   ├── bot/          # Telegram bot and alert features
│   ├── backtest/     # Backtester, data download, comparisons
│   └── ui/           # TUI / control panel
├── config/           # Config templates and deployment config
├── scripts/          # Setup, deployment, and helper scripts
├── data/             # Historical data
├── tests/            # Tests
├── .env.example      # Environment template
├── requirements.txt
├── Dockerfile
└── README.md
```

## Runtime Model

Runtime behavior is driven by environment variables, which are validated before the main trading pipeline runs.

Key variables currently used by the runtime flow include:

- `BYBIT_API_KEY`, `BYBIT_API_SECRET`
- `BINANCE_API_KEY`, `BINANCE_API_SECRET`
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- `MODE`
- `SYMBOL`
- `TIMEFRAME`
- `RISK_PER_TRADE`
- `MAX_QTY`
- `DRY_RUN`
- `EXCHANGE`
- `ALLOW_LIVE_TRADING`

Startup validation is implemented in `src/runtime/validation.py`, and the runtime entrypoint is `src/main.py`.


## Setup

1. Clone the repository.
2. Copy `.env.example` to `.env`.
3. Fill in the required credentials and runtime values for your chosen environment.
4. Install dependencies.

Example:

```bash
git clone https://github.com/benbaichmankass/ict-trading-bot.git
cd ict-trading-bot
cp .env.example .env
pip install -r requirements.txt
```

## Safe Profile Validation

Before running the bot, validate the current runtime profile safely:

```bash
python scripts/print_runtime_profile.py
```

This helper script:

- Loads environment variables
- Builds runtime settings from the environment
- Runs startup validation
- Prints a concise runtime summary
- Exits without placing any trades

Example output:

```text
EXCHANGE=binance | MODE=testnet | DRY_RUN=true | ALLOW_LIVE_TRADING=false | SYMBOL=BTCUSDT
```

## Running the Runtime Entry Point

The current runtime entrypoint lives in `src/main.py`. It:

1. Loads environment variables
2. Builds settings from the environment
3. Runs startup validation
4. Constructs the exchange adapter
5. Calls `src.runtime.pipeline.run_pipeline`

Example:

```bash
python -m src.main
```

## Example Development Profile

Use a safe dry-run profile while developing or testing:

```env
BYBIT_API_KEY=demo_key
BYBIT_API_SECRET=demo_secret
TELEGRAM_BOT_TOKEN=demo_token
TELEGRAM_CHAT_ID=123456789

MODE=testnet
SYMBOL=BTCUSDT
TIMEFRAME=15
RISK_PER_TRADE=0.01
MAX_QTY=10
DRY_RUN=true
EXCHANGE=binance
ALLOW_LIVE_TRADING=false
```

Then run:

```bash
python scripts/print_runtime_profile.py
python -m src.main
```

## Example Live-Oriented Profile

Use live settings only when you explicitly intend to trade live:

```env
BYBIT_API_KEY=your_bybit_api_key
BYBIT_API_SECRET=your_bybit_api_secret
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_chat_id

MODE=live
SYMBOL=BTC/USDT:USDT
TIMEFRAME=15
RISK_PER_TRADE=0.01
MAX_QTY=10
DRY_RUN=false
EXCHANGE=bybit
ALLOW_LIVE_TRADING=true
```

Validate first, then run:

```bash
python scripts/print_runtime_profile.py
python -m src.main
```

## Tests

Run focused validation tests:

```bash
PYTHONPATH=. pytest -q tests/test_validation.py tests/test_s012_live_mode.py
```

Run the full test suite in the `tests/` folder:

```bash
PYTHONPATH=. pytest -q tests
```

## Backtesting

Backtesting modules live under `src/backtest/`. Use them to:

- Download historical data
- Compare strategies
- Review latest backtest outputs

## Notes

- Do not commit `.env`
- Do not commit runtime files such as `*.pid`
- Do not commit generated charts or database files
- Always validate the runtime profile before enabling non-dry-run execution

## Environment notes

- `EXCHANGE`: `bybit` or `binance`
- `BYBIT_TESTNET`: `true` or `false`
- `BINANCE_TESTNET`: `true` or `false`
- `TRADE_JOURNAL_DB`: absolute path to `trade_journal.db`

If `TRADE_JOURNAL_DB` is not set, the bot falls back to the repo root DB, then `src/bot/trade_journal.db`.

`telegram_query_bot.py` reads trades from `trades` and backtest summaries from `backtestresults`.
