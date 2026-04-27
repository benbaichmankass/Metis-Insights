# Repo map

This project is a Python ICT trading bot with ML/backtesting/live execution, Telegram control, exchange integrations, and cloud deployment.

## Common areas

- `src/`: application code.
- `src/bot/`: Telegram bot and command handlers.
- `src/core/`: trading loop and core strategy flow.
- `src/runtime/`: runtime config/validation/pipeline pieces.
- `scripts/`: operational scripts.
- `tests/`: pytest suite.
- `config/`: configuration examples and defaults.
- `data/`, `ml/data/`, `ml/models/`: data/model artifacts. Prefer remote storage for large files.
- `docs/`: human-readable project docs.

## Rule

Before changing architecture, update this map if the structure changed.
