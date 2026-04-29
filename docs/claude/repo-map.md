# Repo map

This project is a Python ICT trading bot with ML/backtesting/live execution, Telegram control, exchange integrations, and cloud deployment.

## Common areas

- `src/`: application code.
- `src/bot/`: Telegram bot and command handlers. `data_loaders.py` is the single facade for all operational data reads.
- `src/core/`: trading loop and core strategy flow.
- `src/data_layer/`: SQLite `Database` class (`trade_journal.db` bootstrap + migrations).
- `src/runtime/`: runtime config/validation/pipeline pieces.
- `scripts/`: operational scripts. `scripts/init_db.py` bootstraps `trade_journal.db`.
- `tests/`: pytest suite.
- `config/`: configuration examples and defaults.
- `data/`, `ml/data/`, `ml/models/`: data/model artifacts. Prefer remote storage for large files.
- `docs/`: human-readable project docs.

## Rule

Before changing architecture, update this map if the structure changed.
