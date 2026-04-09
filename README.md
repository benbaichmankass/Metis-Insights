# ICT Trading Bot

Python-based ICT trading bot for crypto trading workflows, focused on ICT concepts such as fair value gaps (FVG), order blocks (OB), swing structure, market structure shifts, and kill zones.

## Features

- ICT analysis engine for market structure, swings, FVGs, and order blocks
- Bybit and Binance exchange connectors
- Runtime validation for safe startup (MODE, DRY_RUN, RISK_PER_TRADE, MAX_QTY)
- Kill zone scalper pipeline with injected exchange connector
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

Runtime behavior is driven by environment variables, which are validated before any trading logic runs.

Key variables:

- `BYBIT_API_KEY`, `BYBIT_API_SECRET`
- `BINANCE_API_KEY`, `BINANCE_API_SECRET` (when using Binance)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- `MODE`: `testnet` or `live`
- `SYMBOL`: e.g. `BTCUSDT` or `BTC/USDT:USDT`
- `TIMEFRAME`: e.g. `15`
- `RISK_PER_TRADE`: float between 0 and 0.02
- `MAX_QTY`: maximum order size
- `DRY_RUN`: simulated orders when true
- `EXCHANGE`: `bybit` or `binance`
- `ALLOW_LIVE_TRADING`: required when `DRY_RUN=false`

Startup validation is implemented in `src/runtime/validation.py` and enforced by `src/main.py` before the pipeline runs.

## Setup

1. Clone the repository.
2. Copy `.env.example` to `.env`.
3. Fill in your Bybit and Telegram credentials (and Binance keys if needed).
4. Install dependencies.

Example:

```bash
git clone https://github.com/the-lizardking/ict-trading-bot.git
cd ict-trading-bot
cp .env.example .env
pip install -r requirements.txt
```

## Safe Profile Validation

Before running the bot, validate the runtime profile safely:

```bash
python scripts/print_runtime_profile.py
```

This will:

- Load `.env` and environment variables
- Build settings from the environment
- Run startup validation
- Print a summary like:

```text
EXCHANGE=bybit | MODE=testnet | DRY_RUN=true | ALLOW_LIVE_TRADING=false | SYMBOL=BTCUSDT
```

If settings are invalid, it will raise a clear error instead of starting the pipeline.

## Running the Pipeline

The main runtime entrypoint lives in `src/main.py`. It:

1. Loads environment variables
2. Builds settings via `build_settings_from_env`
3. Runs `validate_startup`
4. Constructs the exchange adapter (Bybit or Binance)
5. Calls `src.runtime.pipeline.run_pipeline`

Example:

```bash
python -m src.main
```

## Recommended Profiles

### Colab / Local Dev (dry run)

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

Run:

```bash
python scripts/print_runtime_profile.py
python -m src.main
```

### Oracle / Server – Bybit Live (caution)

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

Validate then run:

```bash
python scripts/print_runtime_profile.py
python -m src.main
```

## Tests

Run focused runtime tests:

```bash
pytest -q tests/test_runtime_validation.py tests/test_runtime_pipeline.py tests/test_runtime_orders.py tests/test_print_runtime_profile.py
```

Run full test suite:

```bash
pytest -q
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
