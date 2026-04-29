# Backtesting UI Workflow

Sprint S-011 PR #3 — Streamlit web dashboard for historical backtest results.

## Running locally

```bash
# Install dependencies first (if not done)
pip install -r requirements.txt

# Launch the dashboard
streamlit run src/web/backtest_ui.py
```

The app opens at `http://localhost:8501`.

## Data sources

The UI loads backtest results in this priority order:

| Priority | Source | How to set |
|----------|--------|-----------|
| 1 | `BACKTEST_CSV` env var | `export BACKTEST_CSV=/path/to/results.csv` |
| 2 | `data/backtests.csv` | Place CSV in repo root `data/` dir |
| 3 | `data/backtest_candles.csv` | Legacy fallback |
| 4 | Mock data | Always available — no setup required |

## Expected CSV columns

| Column | Type | Description |
|--------|------|-------------|
| `strategy` | str | Strategy name (ict, vwap, killzone, ...) |
| `symbol` | str | Trading pair (BTCUSDT, ETHUSDT, ...) |
| `win_rate` | float | 0.0–1.0 |
| `profit_factor` | float | Gross profit / gross loss |
| `total_trades` | int | Number of trades in backtest |
| `max_drawdown_pct` | float | Max drawdown as fraction |
| `total_pnl` | float | Net PnL in USD |
| `run_date` | str | YYYY-MM-DD |

Missing columns are filled with `None` — the UI handles them gracefully.

## Features

- **Sidebar filters**: strategy, symbol, date range
- **Metrics bar**: total runs, avg win rate, avg profit factor, total PnL
- **Equity curve**: cumulative PnL over time (plotly interactive)
- **Results table**: formatted with % and $ signs

## Telegram

`/backtest_ui` — the bot replies with the `streamlit run` command and data source instructions.

## Importable helpers (no Streamlit dependency)

```python
from src.web.backtest_ui import (
    load_backtest_data,    # load CSV or return mock DataFrame
    filter_backtest_data,  # apply strategy/symbol/date filters
    build_equity_curve,    # compute cumulative PnL series
    summary_stats,         # aggregate metrics dict
)
```

These are unit-testable without installing Streamlit.
