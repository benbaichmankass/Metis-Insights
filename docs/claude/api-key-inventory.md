# API key inventory

Authoritative list of every place in the repo that reads or writes
exchange API credentials. **Maintained alongside any change that adds,
moves, or removes an API-key call site.** When this list goes stale,
bugs like BUG-030 (the same Bybit balance reported for `bybit_1` and
`bybit_2` because two layers were reading different keys) become
invisible until the operator notices in production.

## Ownership rule

> The accounts unit (`src/units/accounts/`) is the **only** layer
> that turns an account dict into an exchange client.
> Every other layer — Telegram bot handlers, Coordinator, smoke
> tests, scripts — must call into `src.units.accounts.clients` (or
> the `data_loaders` re-exports) and never read exchange env vars
> directly.

The exception is the legacy single-account boot path in `src/main.py`
and `src/runtime/pipeline.py`, which existed before per-account
credentials and is kept for a deployment that has not migrated to
`config/accounts.yaml`. New code must not add to that path.

## Canonical owner — accounts unit

| File | Lines | Purpose |
|---|---|---|
| `src/units/accounts/clients.py` | `bybit_client_for`, `binance_conn_for`, `resolve_credentials` | The single chokepoint. Reads `account["api_key_env"]` (preferred) or `account["env_path"]` (legacy). |
| `src/units/accounts/__init__.py` | `load_accounts` (≈54-72) | Loads `api_key_env` from accounts.yaml into `TradingAccount`. |
| `src/units/accounts/account.py` | `TradingAccount.api_key_env` (53) | Stores the **env var name** — never the actual key. |

## Re-exports (back-compat shims)

| File | Lines | Purpose |
|---|---|---|
| `src/bot/data_loaders.py` | `bybit_client_for` / `binance_conn_for` re-export | Imports from `src.units.accounts.clients` so existing call sites keep working. Do not add new API-client construction here. |
| `src/units/ui/data_loaders.py` | `account_open_positions` delegate | Thin delegate to `src.units.accounts.clients.account_open_positions` (moved there in BUG-042 PR 1, PR #384). |

## Per-account callers (correct: route through accounts unit)

| File | Lines | Caller | Notes |
|---|---|---|---|
| `src/bot/telegram_query_bot.py` | `_smoke_test_client_factory` (~1948) | `dl.bybit_client_for(account_cfg)` / `dl.binance_conn_for(account_cfg)` | Per-account routing — correct. |
| `src/core/coordinator.py` | `accounts_status` | calls `account_balance_with_diagnostic(cfg)` which routes through `bybit_client_for(cfg)` | Correct. |
| `src/units/accounts/clients.py` | `account_open_positions` (canonical) | Moved from `src/bot/data_loaders.py` in BUG-042 PR 1 (PR #384). Per CLAUDE.md architecture rules § 3: exchange-state reads belong to the accounts unit. |
| `src/runtime/order_monitor.py` | `_reconcile_open_trades` | Calls `account_open_positions` from `src.units.accounts.clients` directly. |

## Legacy single-account boot path

These read the **un-suffixed** `BYBIT_API_KEY` / `BYBIT_API_SECRET`
(or Binance equivalents) from the process environment / settings dict.
They predate the per-account contract and only support one wallet at a
time. **Do not extend this path; new code must use the accounts unit.**

| File | Lines | Notes |
|---|---|---|
| `src/main.py` | `_build_exchange_adapter` (≈101-110) | Boot-time single-exchange adapter. |
| `src/runtime/pipeline.py` | `build_exchange_client` (≈84-95) | Same. |
| `src/runtime/validation.py` | `validate_startup` (≈42-46) | Startup check requires only the singular keys for the configured exchange. |
| `src/exchange/bybit_connector.py` | header docstring (≈12) + ccxt instantiation (≈37-42) | The ccxt client used by the legacy path. |

## Render / provisioning

| File | Lines | Purpose |
|---|---|---|
| `scripts/render_env_from_master.py` | 224-225, 280-281, 340-341 | Bridges the encrypted master file to `BYBIT_API_KEY_1`, `BYBIT_API_KEY_2`, etc. and the legacy `BYBIT_API_KEY` for the single-account boot path. |
| `scripts/setup.sh` | 38-48 | Interactive operator onboarding. |
| `scripts/smoke_test_trade.py` | 107, 143-144, 215-216 | Standalone smoke trade script. Reads from a fresh settings dict. |

## Discovery / introspection (read-only references to env-var **names**)

These do not authenticate against an exchange — they only test for the
**presence** of an env-var name to classify which exchange a `.env` file
belongs to or to render help text:

| File | Lines | Notes |
|---|---|---|
| `src/bot/data_loaders.py` | `_exchange_from_env` (175-179) | Classifies a `.env` file by scanning for `BYBIT_API_KEY` / `BINANCE_API_KEY`. |
| `src/bot/telegram_query_bot.py` | env-help text (634-635) | Displays expected env-var names to the operator. |

## How to add a new account / exchange

1. Add the account to `config/accounts.yaml` with `api_key_env: <NEW_VAR>`.
2. Add the credential mapping in `scripts/render_env_from_master.py` so the
   master file emits `<NEW_VAR>` and `<NEW_VAR_SECRET>` into `.env.live`.
3. Update **this file** with the new caller(s).
4. Do NOT add a fresh `os.environ.get("BYBIT_API_KEY_3")` call site.
   If you find yourself wanting to, you are bypassing the accounts unit.

## Maintenance

Run `grep -rn 'BYBIT_API_KEY\|BYBIT_API_SECRET\|BINANCE_API_KEY\|BINANCE_API_SECRET\|BREAKOUT_API_KEY' src/ scripts/ deploy/`
and reconcile the result against this table whenever you touch any
authentication code. A net-new line in the grep output that is not
listed here is a regression.
