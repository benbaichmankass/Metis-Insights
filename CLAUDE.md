# ICT Trading Bot - Project Overview

## Architecture Summary
- Python-based cryptocurrency trading bot
- ICT (Inner Circle Trader) methodology strategies
- VWAP mean reversion strategies
- ML model integration for signal generation
- Telegram bot for monitoring and alerts
- Deployed on Oracle Cloud Infrastructure

## Repository Structure
```
ict-trading-bot/
  config/       - Configuration files
  data/         - Historical and live market data
  deploy/       - Deployment scripts and configs
  docs/         - Documentation
  logs/         - Runtime logs
  ml/           - Machine learning models
  runtime_logs/ - Live trading logs
  scripts/      - Utility scripts
  src/          - Main source code
  strategies/   - Trading strategy implementations
  tests/        - Test suite
```

## Key Commands
- `python src/main.py` - Run the bot
- `python scripts/backtest.py` - Run backtests
- `./run_telegram_bot.sh` - Start Telegram monitoring
- `./run_trader.sh` - Start live trading

## Environment
- Python 3.x
- Bybit API (primary exchange)
- Oracle Cloud VM deployment
- Docker support available
