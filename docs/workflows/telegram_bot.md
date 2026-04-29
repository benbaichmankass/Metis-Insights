# Unit 5 — Telegram Bot workflow

## Responsibility
User-facing interface. Pure Coordinator consumer — no direct DB or exchange calls.

## Commands
| Command | Coordinator call |
|---------|-----------------|
| `/strategies` | `coordinator.dashboard_stats()["strategies"]` |
| `/halt` | creates flag file + `coordinator.return_command("halt")` |
| `/resume` | removes flag file + `coordinator.return_command("resume")` |
| `/alerts` | `coordinator.list_alerts(n=10)` |
| `/status` | `coordinator.dashboard_stats()` |
| `/last5` | `coordinator.recent_signals()` |

## Coordinator singleton
```python
_coordinator: Coordinator | None = None

def get_coordinator() -> Coordinator | None:
    global _coordinator
    if _coordinator is None:
        _coordinator = Coordinator()
    return _coordinator
```
Set `bot._coordinator = mock` in tests to inject a mock.

## Authorisation
All handlers check `update.effective_chat.id == TELEGRAM_CHAT_ID`.
Return early (no reply) if unauthorised.

## Fallback behaviour
- `cmd_strategies`: falls back to `data_loaders.strategy_dashboard_data()` when coordinator is None
- `cmd_alerts`: replies "unavailable" when coordinator is None

## Rules
- Never import from `src/units/accounts/` or `src/units/strategies/` directly
- All data must come through `Coordinator`
- Use `asyncio.new_event_loop().run_until_complete()` pattern in tests (no pytest-asyncio)
