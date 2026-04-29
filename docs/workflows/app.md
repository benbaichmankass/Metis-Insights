# Unit 6 — App workflow

## Responsibility
Extended UI (extends Telegram Bot) with configuration capabilities:
API key management, new account registration, strategy enable/disable.

## Status
Stub — extends `telegram_bot` unit config in `units.yaml`.
Config-enabled operations (API key writes, account registration) to be implemented.

## Config
```yaml
units:
  app:
    extends: telegram_bot
    config_enabled: true
```

## Planned entry points (via Coordinator)
```python
Coordinator.register_account(account_cfg)       # add to units.yaml
Coordinator.update_strategy_config(name, cfg)   # update units.yaml entry
```

## Rules
- Same authorisation rules as Telegram Bot
- All mutations go through Coordinator — never write units.yaml directly from UI
- Config changes must push an alert to dashboards
