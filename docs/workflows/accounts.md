# Unit 2 — Accounts workflow

## Responsibility
Risk-size and execute `OrderPackage` objects on exchange accounts.
Never generate signals, never modify strategy config.

## Entry point
```python
Coordinator.account_execute(account_id, pkg, exchange_client=None, balance_usdt=None, dry_run=None)
```
Delegates to `src/units/accounts/execute.py::execute_pkg()`.

## Execution flow
1. Check `is_paused(account_id)` → raise `RuntimeError` if halted
2. `dry_run=True` when `exchange_client=None` or `DRY_RUN=true` env var
3. Fetch balance from `exchange_client` or use `balance_usdt` override
4. `size_order_from_cfg(pkg, account_cfg, balance_usdt)` — fixed-fractional sizing
5. Submit limit order to Bybit / return `"dry-<uuid>"` in dry-run

## Risk sizing
```
qty = (balance × risk_pct) / abs(entry - sl)
qty = clip(qty, min_qty=0.001, max_qty=100.0)
qty = round(qty, qty_precision=3)
```
Configured via `units.yaml → accounts[*].risk_pct`.

## Pause / resume
Managed by `_PAUSED_ACCOUNTS` set in `src/core/coordinator.py`.
Set via `Coordinator.return_command("halt")`, cleared via `return_command("resume")`.
`is_paused()` module-level helper is available to `execute_pkg()`.

## Dry-run mode
- `exchange_client=None` → always dry-run
- `DRY_RUN=true` env var → always dry-run
- Returns trade_id prefixed `"dry-"` — never touches exchange

## Rules
- Never call strategies unit directly
- Always check `is_paused()` before submitting
- Always push an alert after execution (success or failure)
