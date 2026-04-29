# Accounts & Risk Workflow

Sprint S-010 — Per-Account Risk Engine + Accounts Modularisation.

## Architecture

```
config/accounts.yaml
        │
        ▼
load_accounts()  ──►  [TradingAccount, ...]
                            │
                            ▼
                       place_order(pkg)
                            │
                     RiskManager.approve()
                            │
                    ┌───────┴────────┐
                  PASS             FAIL
                    │                │
              route_order()    raise RiskBreach
                    │
              EXCHANGE_MAP
            ┌───────┴────────┐
          BybitAPI       BreakoutAPI
        (dry / live)    (dry only)
```

The **Coordinator** (Unit 2) is the only cross-unit entry point:

| Method | Description |
|--------|-------------|
| `accounts_status(path?)` | Return per-account risk dicts |
| `multi_account_execute(pkg, path?, *, dry_run, account_type?)` | Dispatch order to all (or filtered) accounts |
| `reload_accounts(path?)` | Verify accounts.yaml and push `source="app"` alert |

## File layout

```
src/units/accounts/
    __init__.py          # load_accounts()
    risk.py              # RiskManager (+ legacy size_order functions)
    account.py           # TradingAccount, RiskBreach
    integrator.py        # EXCHANGE_MAP, route_order(), BybitAPI, BreakoutAPI

config/
    accounts.yaml        # per-account risk config (env var names only — no secrets)
```

## accounts.yaml schema

```yaml
accounts:
  <account_id>:
    type: regular | prop          # prop = stricter rules, future Breakout support
    exchange: bybit | breakout
    api_key_env: ENV_VAR_NAME     # name of env var holding the actual key
    risk:
      max_dd_pct: 0.05            # max drawdown fraction (5 %)
      daily_usd: 100              # max daily loss in USD
      pos_size: 500               # max single-position size in USD
```

**Never** store actual API keys in this file — use the `api_key_env` field to
reference an environment variable name.

## Risk checks (RiskManager.approve)

1. **Daily loss limit**: `daily_pnl < -max_daily_loss_usd` → reject
2. **Position size**: `order.meta['estimated_value'] > max_pos_size_usd` → reject

A `RiskBreach` exception is raised by `TradingAccount.place_order()` when
`approve()` returns `False`.  `multi_account_execute()` catches `RiskBreach`
per account so a breach on one account never blocks others.

## Exchange integrations

| Exchange | Dry-run | Live |
|----------|---------|------|
| `bybit` | `dry-bybit-<hex>` | needs injected `exchange_client` |
| `breakout` | `dry-breakout-<hex>` | `NotImplementedError` (future) |

## Telegram commands (Unit 6)

| Command | Action |
|---------|--------|
| `/accounts_status` | List all accounts with PnL, limit, and halted state |
| `/risk_check <name>` | Full risk detail for one account |

Both route through `Coordinator.accounts_status()`.

## Adding a new account

1. Add an entry to `config/accounts.yaml`.
2. Set the API key environment variable on the server.
3. Call `coordinator.reload_accounts()` (or restart the process).

## Adding a new exchange

1. Add a new API class to `src/units/accounts/integrator.py`:
   ```python
   class MyExchangeAPI:
       def place(self, order, *, dry_run=True) -> str:
           if dry_run:
               return f"dry-myexchange-{uuid.uuid4().hex[:10]}"
           raise NotImplementedError("live not implemented")
   ```
2. Add `"myexchange": MyExchangeAPI` to `EXCHANGE_MAP`.
3. Add account entries with `exchange: myexchange` in `accounts.yaml`.
