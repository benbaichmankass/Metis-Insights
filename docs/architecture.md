# Architecture Documentation

## Current Structure
```
ict-trading-bot/
  config/       # Configuration files
  data/         # Market data
  deploy/       # Deployment configs
  docs/         # Documentation
  logs/         # App logs
  ml/           # ML models
  runtime_logs/ # Live trading logs
  scripts/      # Utility scripts
  src/          # Source code
  strategies/   # Trading strategies
  tests/        # Tests
```

## Target Structure
```
ict-trading-bot/
  config/
    env/
    settings/
  data/
    historical/
    live/
  docs/
    audit/
    strategies/
    deployment.md
  ml/
    models/
    training/
  src/
    api/
    core/
    strategies/
    utils/
  strategies/
    ict/
    vwap/
  deploy/
    docker/
    scripts/
```

## Components
- **API Layer**: Bybit, Binance exchange APIs
- **Strategy Engine**: ICT, VWAP mean reversion
- **ML Pipeline**: Signal generation models
- **Telegram Bot**: User interface
- **Backtester**: Historical validation
- **Risk Manager**: Position sizing, stop-loss
