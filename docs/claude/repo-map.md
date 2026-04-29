# Repo map

This project is a Python ICT trading bot with ML/backtesting/live execution, Telegram control, exchange integrations, and cloud deployment.

## S-008 architecture (current): 9-unit Coordinator pattern

All cross-unit data flows through `src/core/coordinator.py` (the TRANSLATOR).
No unit calls another unit directly.

| Unit | Location | Purpose |
|------|----------|---------|
| 1. Strategies | `src/units/strategies/` | Generate `OrderPackage` objects |
| 2. Accounts | `src/units/accounts/` | Risk-size + execute orders |
| 3. Dashboards | `src/units/dashboards/` | Stats + alerts ring buffer |
| 4. Return Commands | `src/core/coordinator.py` | halt/resume via `_PAUSED_ACCOUNTS` |
| 5. Telegram Bot | `src/bot/telegram_query_bot.py` | UI consumer of Coordinator |
| 6. App | (stub) | Extended UI with config |
| 7. Trading School | `src/units/trading_school/` | Strategy metric validation |
| 8. DB | `trade_journal.db`, `data/trades.db` | Unified storage |
| 9. Workflows | `docs/workflows/` | Per-unit operating procedures |

**Config:** `config/units.yaml` — all 9 units declared here. Adding a strategy = 1 line.

**Full architecture diagram:** `docs/architecture.md`

**Per-unit workflows:** `docs/workflows/`

## Common areas

- `src/`: application code.
- `src/core/coordinator.py`: **TRANSLATOR** — all cross-unit routing. Read this first for any architectural question.
- `src/units/`: 9-unit layer — strategies, accounts, dashboards, trading_school.
- `src/bot/`: Telegram bot and command handlers. `data_loaders.py` is the single facade for all operational data reads.
- `src/core/`: trading loop and core strategy flow.
- `src/data_layer/`: SQLite `Database` class (`trade_journal.db` bootstrap + migrations).
- `src/runtime/`: runtime config/validation/pipeline pieces.
- `scripts/`: operational scripts. `scripts/init_db.py` bootstraps `trade_journal.db`.
- `tests/`: pytest suite. `tests/test_s008_*.py` + `tests/test_coordinator_flow.py` cover the 9-unit layer (178 tests).
- `config/`: `units.yaml` (9-unit declarations), `strategies.yaml` (strategy registry).
- `data/`, `ml/data/`, `ml/models/`: data/model artifacts. Prefer remote storage for large files.
- `docs/`: human-readable project docs.

## Rule

Before changing architecture, update this map if the structure changed.
