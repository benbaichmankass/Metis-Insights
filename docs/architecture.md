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

## Trade Journal Database

SQLite file: `src/bot/trade_journal.db` (also searched at repo root).
Bootstrapped by `scripts/init_db.py` and `src/data_layer/database.py` — both
paths are idempotent and run every startup.

### `trades` table (current schema)

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | autoincrement |
| `timestamp` | TEXT | signal/entry time |
| `symbol` | TEXT | e.g. `BTCUSDT` |
| `direction` | TEXT | `LONG` / `SHORT` |
| `entry_price` | REAL | |
| `exit_price` | REAL | |
| `stop_loss` | REAL | |
| `take_profit_1/2/3` | REAL | |
| `position_size` | REAL | |
| `setup_type` | TEXT | `FVG`, `OB`, `COMBO`, … |
| `killzone` | TEXT | `London Open`, `NY Open`, … |
| `bias` | TEXT | `BULLISH`, `BEARISH`, `NEUTRAL` |
| `entry_reason` | TEXT | |
| `exit_reason` | TEXT | |
| `pnl` | REAL | |
| `pnl_percent` | REAL | |
| `status` | TEXT | `OPEN`, `CLOSED`, `CANCELLED` |
| `notes` | TEXT | |
| `is_backtest` | INTEGER | `0` = live, `1` = backtest |
| `strategy_name` | TEXT | e.g. `breakout_confirmation`, `vwap` |
| `account_id` | TEXT NOT NULL DEFAULT `'live'` | multi-account identifier (added Sprint S-002 M1a) |
| `created_at` | TEXT | `datetime('now')` |

**Index:** `idx_trades_account_created` on `(account_id, datetime(created_at) DESC)` for per-account history queries.

**Migration helpers:** `migrate_add_strategy_name` and `migrate_add_account_id` in both bootstrap files handle pre-existing DBs — safe to run repeatedly.

### `backtest_results` table

Stores aggregate backtest run summaries. See `scripts/init_db.py` for the full column list.
