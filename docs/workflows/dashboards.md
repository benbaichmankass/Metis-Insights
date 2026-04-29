# Unit 3 — Dashboards workflow

## Responsibility
Collect and surface performance stats + alerts.
Read-only view of system state — never triggers trades.

## Entry points
```python
Coordinator.dashboard_stats(exchange_clients=None, strategy_rows=None)
# → {strategies, accounts, alerts, generated_at}

Coordinator.push_alert(message, source, level, **extra)
Coordinator.list_alerts(n=10)
Coordinator.pop_alerts()
```

## Alert sources
| Source | Events |
|--------|--------|
| `accounts` | trade executed, order failed, account paused/resumed |
| `strategies` | signal fired |
| `return_commands` | halt/resume issued |
| `coordinator` | internal errors |

## Alert format
```python
{
    "ts": "2026-04-29T10:00:00+00:00",
    "source": "accounts",
    "level": "info",          # "info" | "warning" | "error"
    "message": "Trade executed: BTCUSDT long dry-abc123",
    # ... any extra fields from the pushing unit
}
```

## AlertsQueue
`src/units/dashboards/alerts.py::AlertsQueue` — thread-safe ring buffer (default maxlen=200).
Module-level singleton `_global_queue` shared across all Coordinator instances in one process.

## Stats builder
`src/units/dashboards/stats.py::build_stats()` — enriches account rows with balance, positions, last_trade, paused flag.
Strategy rows come from `src/bot/data_loaders.strategy_dashboard_data()`.

## Rules
- Never push alerts from strategy unit (strategies are stateless)
- Use `list_alerts(n=10)` for Telegram display; `pop_alerts()` only for draining
- `dashboard_stats()` is safe to call offline (exchange_clients=None → balance=None)
