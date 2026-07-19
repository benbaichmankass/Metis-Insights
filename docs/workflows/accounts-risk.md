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
            Coordinator.multi_account_execute(pkg)   ← the live entry point
                            │
                     RiskManager.evaluate()
                            │
                    ┌───────┴────────┐
                  PASS             FAIL
                    │                │
               execute_pkg()    raise RiskBreach (caught per-account)
                    │
        per-exchange branch in src/units/accounts/execute.py
            ┌───────┴────────┬─────────┬────────┐
          bybit          breakout    oanda    alpaca   (+ interactive_brokers)
```

> **2026-06-28 (audit Workstream B):** the legacy
> `TradingAccount.place_order` → `integrator.route_order` →
> `EXCHANGE_MAP[x].place` router was **removed** (dead code, zero production
> callers — superseded by `execute_pkg`). `EXCHANGE_MAP` + the stub `*API`
> classes are retained as the integration registry (the
> `test_ltmgmt_p5_contract_ci` CI guard + `new-broker` skill consume them).

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
    integrator.py        # EXCHANGE_MAP + stub *API classes (registry; route_order removed 2026-06-28)
    execute.py           # execute_pkg() — the live per-exchange dispatch path

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

A `RiskBreach` exception is raised on the live path inside
`Coordinator.multi_account_execute()` (via `RiskManager.evaluate()`); it is
caught per account so a breach on one account never blocks others. (Pre-2026-06-28
this was raised by the now-removed `TradingAccount.place_order()`.)

## Exchange integrations

| Exchange | Dry-run | Live |
|----------|---------|------|
| `bybit` | `dry-bybit-<hex>` | needs injected `exchange_client` |
| `breakout` | `dry-breakout-<hex>` | `NotImplementedError` (future) |

## Telegram commands (Unit 6) — REMOVED in #1933

> **⚠️ SUPERSEDED (2026-05, PR #1933).** `/accounts_status` and `/risk_check`
> were **removed** when the operator bot went menu-driven — account/PnL/risk
> state is now surfaced through the dashboard + Android app (`/api/bot/*`) and
> the menu, not slash commands. Table kept as historical record; current bot
> spec: [`docs/TELEGRAM-SPEC.md`](../TELEGRAM-SPEC.md). (BL-20260525-001.)

| Command (removed) | Action |
|---------|--------|
| `/accounts_status` | List all accounts with PnL, limit, and halted state |
| `/risk_check <name>` | Full risk detail for one account |

Both routed through `Coordinator.accounts_status()`.

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
