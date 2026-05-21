# Runbook — Interactive Brokers (MES) integration

Wired 2026-05-21. Connects the trader to Interactive Brokers via the TWS
API (`ib_insync`) for **MES** (Micro E-mini S&P 500) futures on CME. This
is the connection + execution plumbing; assigning a MES *strategy* is the
remaining step (see "Remaining wire-up" below).

## Why there are no API keys

The IB TWS API does **not** use API key/secret pairs. Authentication is
the **IB Gateway / TWS login session** — a desktop process (or a headless
IBC / Docker Gateway) the operator keeps logged in. A client connects to
that process over a local socket and is identified only by a numeric
`clientId`. So IB accounts in `config/accounts.yaml` carry **no
`api_key_env`** — connection identity is host + port + clientId + the IB
account code. Because there is no `api_key_env`, IB accounts always load
`configured=True`.

## Accounts (config/accounts.yaml)

| Account | IB code | Port | `mode:` | Meaning |
|---|---|---|---|---|
| `ib_paper` | `DUQ325724` | 7497 | `live` | Executes against the IB **paper** gateway — **paper money**, no real-money risk. Same pattern as `bybit_1` running `mode: live` on Bybit's demo endpoint. |
| `ib_live` | `U25907316` | 7496 | `dry_run` | Real-money account, **held dry**. RiskManager rejects with `account_mode_dry_run`; the coordinator never even constructs an `IBClient`, so no socket is opened against the live gateway. |

`mode:` is the **only** dry/live toggle (per the 2026-05-03 operator
directive). Promoting `ib_live` to live money is a Tier-3 change via the
`set-account-mode` operator action — never an inline YAML edit on `main`.

Both accounts ship `strategies: []`. The three live strategies (`vwap`,
`turtle_soup`, `ict_scalp_5m`) emit **BTCUSDT** signals, which must never
route to an MES futures account. The empty list is belt-and-braces (same
as `prop_velotrade_1`): the coordinator's per-account strategy filter
blocks every signal until a MES-symbol strategy is explicitly assigned.

Per-account connection params can be overridden by environment variables
(`IB_HOST` / `IB_PORT` / `IB_ACCOUNT` / `IB_CLIENT_ID`); unset → the
committed `ib_*` YAML fields are used. Host defaults to `127.0.0.1`.

## Code map

| Layer | File | Role |
|---|---|---|
| Client | `src/units/accounts/ib_client.py` | `IBClient` — connect, MES contract resolution, market-entry bracket placement (TP limit + SL stop), status, balance, `self_test()`. Lazy `ib_insync` import (falls back to the `ib_async` fork). Connections cached per `(host, port, client_id)` via `get_ib_client`. |
| Factory | `src/units/accounts/clients.py::ib_client_for` | Builds an `IBClient` from the account dict (no creds path — IB has no keys). Returns `None` when `ib_port` is unset. |
| Executor | `src/units/accounts/execute.py::_submit_order` | `interactive_brokers` branch — dispatches to `IBClient.place`, reads the Bybit-style `retCode` envelope, raises `RuntimeError` on rejection / `IBConnectionError` on a missing client. Also a `_fetch_balance` branch (NetLiquidation). |
| Coordinator | `src/core/coordinator.py::multi_account_execute` | Client-construction switch builds the IB client (only when `not effective_dry`) and forwards `ib_*` fields into `account_cfg`. |
| Account model | `src/units/accounts/account.py` + `__init__.py` | `TradingAccount` carries `ib_host/ib_port/ib_account/ib_client_id`, loaded from YAML. |

## Verifying connectivity

Run the non-mutating connection self-test (connect → read server version /
managed accounts / NetLiquidation → disconnect; **never places an order**):

```bash
python scripts/ib_connect_check.py            # probe all IB accounts
python scripts/ib_connect_check.py ib_paper   # one account
python scripts/ib_connect_check.py --json     # machine-readable
```

Exit code is `0` only when every probed account connects, so a diag relay
or CI step can gate on it. A green run means the live trader can reach the
Gateway too — it exercises the exact `ib_client_for → IBClient.connect`
path the trader uses.

Prerequisites for a green run: `ib_insync` (or `ib_async`) installed, and
an **IB Gateway / TWS running with the API enabled** on the configured
port (7496 live / 7497 paper), with "Allow connections from localhost"
and the API socket port matching `ib_port`.

## Remaining wire-up (MES strategy)

The connection is live but no signal flows to MES yet. To actually trade
MES on the paper account:

1. Add a strategy whose symbol is `MES` (see the `new-strategy` skill /
   `docs` for the strategy wiring checklist). `MES` is already in
   `config/instruments.yaml` and `InstrumentProfile.mes_cme()`.
2. Assign it under `ib_paper.strategies:` in `config/accounts.yaml`.
3. Confirm `IBClient._build_contract` covers the symbol — today it builds
   the MES front-month only and rejects any other symbol.
4. Keep `ib_live` at `mode: dry_run` until the paper account is proven;
   promote via `set-account-mode` (Tier-3, operator-approved).

## Headless Gateway on the VM (IBC)

The production bot runs on the OCI live VM, so the IB Gateway must run there
too (the bot connects to `127.0.0.1:<port>`). A logged-in Gateway is the one
hard prerequisite for any IB order to execute — no Gateway, no fills.

**Artifacts:**
- `deploy/ib-gateway.service` — systemd unit running IB Gateway under `xvfb`
  via IBC. **Independent of `ict-trader-live`** so bot deploys/restarts never
  re-auth IBKR.
- `deploy/ibc/config.ini.template` — IBC config (auto-restart mode, loopback
  API bind, auto-accept connection). Credentials substituted at render time.
- `scripts/install_ib_gateway.sh` — idempotent installer (xvfb, IB Gateway
  standalone, IBC, config render, unit install).
- `scripts/ops/provision_ib_gateway.sh` — VM-side: installs the staged
  credential env file (0600 root) + runs the installer + restarts.
- `.github/workflows/provision-ib-gateway.yml` — renders creds from the
  `IB_USERNAME` / `IB_PASSWORD` repo secrets, scps them to the VM (encrypted,
  never in logs), runs the provisioner.

**Provision (operator):**
1. Add `IB_USERNAME` / `IB_PASSWORD` repo secrets (Settings → Secrets → Actions).
2. Dispatch `provision-ib-gateway` (`trading_mode=paper`, `ib_port=7497`).
3. Approve the IBKR Mobile 2FA prompt on your phone when the run completes.

**2FA frequency:** with auto-restart mode (`IbAutoClosedown=no`), IBKR's daily
restart reuses the auth token silently. A 2FA tap is needed only on the
**weekly** IBKR re-auth (~Sunday) and on cold starts (VM reboot / Gateway
maintenance). Because the Gateway unit is isolated from the trader, bot
deploys do not trigger 2FA. If a tap is missed, the Gateway logs out and MES
**pauses gracefully** (candle fetch returns `None`); the live crypto trader is
unaffected.

**Live account 2FA:** `U25907316` cannot disable 2FA (funded account). A live
Gateway needs the same IBKR Mobile approval; it stays `mode: dry_run` until
proven and separately promoted (Tier-3).

## Notes

- `ib_insync` is no longer actively maintained. `requirements.txt` pins
  `ib_insync`; `ib_client.py` transparently accepts the API-compatible
  fork `ib_async` if only it is installed.
- Bracket prices are snapped to the MES `0.25` tick grid before
  transmission (IB rejects off-grid futures prices).
- The dry-run guard (`scripts/check_dry_run_in_diff.py`) fires on the
  `ib_live: mode: dry_run` line — that is **intended**: it surfaces the
  dry configuration to the operator for review.
