# Unit 8 — DB workflow

## Responsibility
Unified storage for trades, signals, and backtest results across all units.
All units write through the Coordinator or their own module — never raw SQL from UI layers.

## Databases

| File | Tables | Written by |
|------|--------|-----------|
| `trade_journal.db` | `trades`, `backtest_results` | accounts unit, backtester |
| `data/trades.db` | `signals` | strategies unit |

Configured in `config/units.yaml → units.db`.

## Schema — `trades` table
See `docs/architecture.md` → Trade Journal Database for full column list.

Key columns: `timestamp`, `symbol`, `direction`, `entry_price`, `exit_price`,
`stop_loss`, `pnl`, `status`, `strategy_name`, `account_id`, `is_backtest`.

## Bootstrapping
```bash
python scripts/init_db.py        # creates tables, idempotent
python -c "from src.data_layer.database import init_db; init_db()"
```
Both paths add missing columns via `ALTER TABLE` migrations — safe on existing DBs.

## Reading data (via data_loaders)
```python
from src.bot import data_loaders

data_loaders.strategy_dashboard_data()   # per-strategy summary rows
data_loaders.account_last_trade(acct_id) # last trade for an account
data_loaders.recent_signals_for(strat, n)# last n signals for strategy
```
Dashboard stats call these via `Coordinator.dashboard_stats()`.

## Rules
- UI layers (Telegram, App) never query DB directly — use Coordinator
- Migrations must be idempotent (`ALTER TABLE IF NOT EXISTS` or try/except)
- `is_backtest=1` rows must never appear in live PnL dashboards
