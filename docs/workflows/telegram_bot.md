# Unit 5 — Telegram Bot workflow

> **⚠️ SUPERSEDED (2026-05, PR #1933).** The operator bot is now **menu-driven**,
> not command-driven — the ~40 slash commands below (incl. `/halt` `/resume`
> `/status` `/alerts` `/strategies` `/last5`) were **removed** and rebuilt into a
> 4-item button menu. The authoritative current spec is
> [`docs/TELEGRAM-SPEC.md`](../TELEGRAM-SPEC.md) (the single source of truth for
> `@bict_trading_bot`); the Claude update channel is
> [`docs/claude/telegram-pings.md`](../claude/telegram-pings.md). The command table
> below is kept only as the historical pre-#1933 design record. (BL-20260525-001.)

## Responsibility
User-facing interface. Pure Coordinator consumer — no direct DB or exchange calls.

## Commands (REMOVED in #1933 — historical record only; see TELEGRAM-SPEC.md)
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
