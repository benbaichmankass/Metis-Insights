# Workflows — S-008 9-Unit Architecture

Per-unit operating procedures for the ICT Trading Bot Coordinator pattern.

Every cross-unit interaction goes through `src/core/coordinator.py`.
No unit may call another unit directly.

## Units

| # | Unit | Workflow doc |
|---|------|-------------|
| 1 | Strategies | [strategies.md](strategies.md) |
| 2 | Accounts | [accounts.md](accounts.md) |
| 3 | Dashboards | [dashboards.md](dashboards.md) |
| 4 | Return Commands | [return_commands.md](return_commands.md) |
| 5 | Telegram Bot | [telegram_bot.md](telegram_bot.md) |
| 6 | App | [app.md](app.md) |
| 7 | Trading School | [trading_school.md](trading_school.md) |
| 8 | DB | [db.md](db.md) |
| 9 | Workflows | this file |

## Golden rule

```
Strategy → Coordinator → Account
               ↕
           Dashboards
               ↕
         Return Commands
               ↕
         Telegram / App
```

Never import a unit module from another unit module.
Always go through `Coordinator`.
