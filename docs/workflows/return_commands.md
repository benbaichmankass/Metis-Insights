# Unit 4 — Return Commands workflow

## Responsibility
Process UI commands that flow back to the accounts/risk manager.
The only unit allowed to modify `_PAUSED_ACCOUNTS`.

## Entry point
```python
Coordinator.return_command(cmd, **kwargs)
# → {"cmd": str, "status": "ok"|"partial"|"error", "detail": str, ...}
```

## Supported commands
| Command | Aliases | Action |
|---------|---------|--------|
| `halt` | `killswitch`, `pause` | Pause all accounts |
| `resume` | `unpause` | Resume all accounts |

Configured in `config/units.yaml → units.return_commands.supported`.

## Halt flow
1. Add all account IDs to `_PAUSED_ACCOUNTS`
2. Push alert to dashboards: `source="return_commands", level="warning"`
3. Return `{"cmd": "halt", "status": "ok", "paused": [...]}`

## Resume flow
1. Discard all account IDs from `_PAUSED_ACCOUNTS`
2. Push alert to dashboards: `source="return_commands", level="info"`
3. Return `{"cmd": "resume", "status": "ok", "resumed": [...]}`

## Flag file (Telegram bot integration)
The Telegram bot also writes/removes `/tmp/trader_halt.flag` in parallel with calling
`Coordinator.return_command()`. The flag file is used by the legacy pipeline;
the in-process `_PAUSED_ACCOUNTS` set is used by `execute_pkg()`.
Both mechanisms run together — no conflict.

## Rules
- Only coordinator methods `_cmd_halt` / `_cmd_resume` may mutate `_PAUSED_ACCOUNTS`
- Strategies keep running after halt — only account execution is blocked
- Unknown commands return `{"status": "error"}` — never raise
