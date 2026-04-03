# ICT Trading Bot

Python-based ICT trading bot for crypto trading workflows, focused on ICT concepts such as fair value gaps (FVG), order blocks (OB), swing structure, market structure shifts, and kill zones.

## Features

- ICT analysis engine for market structure, swings, FVGs, and order blocks
- Bybit Testnet integration for BTC/USDC trading workflows
- Telegram bot commands for status, trade actions, and backtest access
- Backtesting tools for historical downloads and strategy comparison
- TUI / control panel components for local operation and monitoring

## Project Structure

```text
ict-trading-bot/
├── src/
│   ├── core/       # ICT analysis, strategy logic, kill zone logic
│   ├── exchange/   # Bybit connector, exchange/order handling
│   ├── bot/        # Telegram bot and alert features
│   ├── backtest/   # Backtester, data download, comparisons
│   └── ui/         # TUI / control panel
├── config/         # Config templates and deployment config
├── scripts/        # Setup and start scripts
├── data/           # Historical data
├── tests/          # Tests
├── .env.example    # Environment template
├── requirements.txt
├── Dockerfile
└── README.md
```

## Exchange

- Exchange: Bybit Testnet
- Trading mode: test environment
- Pair: BTC/USDC

## Setup

1. Clone the repository.
2. Copy `.env.example` to `.env`.
3. Fill in your Bybit and Telegram credentials.
4. Install dependencies.

### Example setup

```bash
git clone https://github.com/the-lizardking/ict-trading-bot.git
cd ict-trading-bot
cp .env.example .env
pip install -r requirements.txt
```

## Run

Use the provided scripts where available:

```bash
bash scripts/setup.sh
bash scripts/start.sh
```

Or run the main modules directly once the imports are confirmed.

## Telegram Bot

Expected command coverage includes:

- `/status`
- `/backtest`
- `/latest_backtest`
- `/trade`

## Backtesting

Backtesting modules live under `src/backtest/`. Use them to:

- Download historical data
- Compare strategies
- Review latest backtest outputs

## Notes

- Do not commit `.env`
- Do not commit runtime files such as `*.pid`
- Do not commit generated charts or database files
