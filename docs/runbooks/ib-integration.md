# Runbook — Interactive Brokers (MES) integration

**Status: LIVE for MES paper trading (2026-05-22).** Wired 2026-05-21,
taken live 2026-05-22. Connects the trader to Interactive Brokers via the
TWS API (`ib_insync`) for **MES** (Micro E-mini S&P 500) futures on CME.

The bot now trades **two symbols at once**: BTCUSDT (Bybit) and MES (IB
paper). All three live strategies (`turtle_soup`, `vwap`, `ict_scalp_5m`)
are symbol-parameterized and evaluate **both** symbols every tick through
the intent multiplexer / coordinator; MES signals route to `ib_paper` and
BTCUSDT signals route to the Bybit accounts (symbol→exchange dispatch gate
in `src/core/coordinator.py`). Market data is **delayed** CME data, so no
paid real-time subscription is required (see "Market data" below).

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

| Account | IB code | `ib_port` (host) | `mode:` | Meaning |
|---|---|---|---|---|
| `ib_paper` | `DUQ325724` | 4002 | `live` | Executes against the IB **paper** gateway — **paper money**, no real-money risk. Same pattern as `bybit_1` running `mode: live` on Bybit's demo endpoint. `ib_port: 4002` is the **host loopback** the bot dials; inside the Docker gateway it reaches the API via a socat relay (see "Headless Gateway" below). |
| `ib_live` | `U25907316` | 7496 | `dry_run` | Real-money account, **held dry**. RiskManager rejects with `account_mode_dry_run`; the coordinator never even constructs an `IBClient`, so no socket is opened against the live gateway. |

`mode:` is the **only** dry/live toggle (per the 2026-05-03 operator
directive). Promoting `ib_live` to live money is a Tier-3 change via the
`set-account-mode` operator action — never an inline YAML edit on `main`.

**Activation is config-driven — there is no enable flag.** MES trades
simply because `ib_paper` is configured (IB needs no creds) with
`mode: live`, a non-empty `strategies` list, and `symbols: [MES]`. The
tick loop (`src/main.py::_resolve_tick_symbols`) unions every configured
account's `symbols`, so `[BTCUSDT, MES]` falls out of the config with no
on/off switch. The earlier `MULTI_SYMBOL_ENABLED` env (and the
`enable-mes`/`disable-mes` operator actions that flipped it) were removed
2026-05-22 — they were a forbidden second gate (Prime Directive rule 6).
To stop MES, set `ib_paper` `mode: dry_run` via `set-account-mode`
(signals still log, nothing executes) or remove its `strategies`/`symbols`
in a PR.

`ib_paper.strategies` is `[turtle_soup, vwap, ict_scalp_5m]` — the three
live strategies, which are symbol-parameterized and produce MES signals
for this account (and BTCUSDT signals for the Bybit accounts). `ib_live`
keeps `strategies: []` (belt-and-braces, same as `prop_velotrade_1`):
combined with `mode: dry_run` the real-money account never receives a
signal or opens a socket. The symbol→exchange dispatch gate in the
coordinator guarantees a BTCUSDT signal can never reach an MES futures
account and vice-versa.

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
| Read path | `src/units/accounts/clients.py::ib_read_client_for` + `IBClient.balance/positions`, `src/units/ui/data_loaders.py::account_balance_with_diagnostic` (IB branch) + `account_open_positions` (IB branch) | The **reporting / observability** surface — feeds the hourly Telegram digest (`account_snapshots()`) and the dashboard balance snapshot (`runtime_logs/balance_snapshots.json`). Uses a **read-only, PID-salted clientId** (`ib_read_client_for`) so a probe never collides with the trader's execution socket (clientId 497/496), and **gates on `mode`** so a dry IB account (`ib_live`) is never dialled — the live gateway socket stays closed until promotion, mirroring the coordinator. |

## Reporting / observability (read path)

Distinct from the execution path above. The execution path was wired at
the 2026-05-21 MES go-live, but the **read path** that feeds the hourly
Telegram digest and the Streamlit dashboard was blind to IB until it was
wired separately:

- **Field preservation.** `src/units/ui/data_loaders.py::_load_yaml_accounts`
  (the UI/Telegram account loader, distinct from the production
  `src/units/accounts/__init__.py::load_accounts`) must carry the IB
  connection fields (`ib_host/ib_port/ib_account/ib_client_id`) **and**
  `mode` through to the account dict the read path receives. When they
  were dropped, `ib_client_for` saw no `ib_port` and every IB account
  read failed with "ib_port unset". This is the IB equivalent of the
  S-023 credential-field-preservation fix.
- **Balance + positions.** `account_balance_with_diagnostic` reports IB
  `NetLiquidation` (falling back to `AvailableFunds`) as `total_usdt`,
  and `account_open_positions` returns the per-account open positions
  from `IBClient.positions()` (IB portfolio, filtered to the account
  code). A Gateway-down probe surfaces a **precise** `api_error` (the
  real "failed to connect to IB Gateway …" reason) instead of the
  earlier generic "exchange not supported".
- **Read clientId.** `ib_read_client_for` uses `readonly=True` and a
  process-unique clientId (`9000 + os.getpid() % 900`) so the probe can
  never transmit an order and never collides with the live execution
  socket — whether it runs inside the trader process (hourly report) or
  another process (Telegram `/accounts_status`).
- **Dry-run gate.** Both read functions return early for a `mode:
  dry_run` IB account **without opening a socket**, so the live gateway
  (`ib_live`, port 7496) is never dialled from the read path. `ib_live`
  therefore reports `dry_run` rather than a false connection error.

Because the dashboard balance endpoint (`/api/bot/accounts/balances`) is
connection-free — it reads `runtime_logs/balance_snapshots.json` written
by the trader's `account_snapshots()` — fixing the trader-process read
populates the dashboard for free, no socket from the web-api process.

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
an **IB Gateway / TWS running with the API enabled** reachable on the
account's `ib_port` — `127.0.0.1:4002` for paper (Docker socat relay),
`127.0.0.1:7496` for live.

## Event loop (why MES candle fetch needs a persistent loop)

`ib_insync` is async under the hood; its sync calls (`connect`,
`qualifyContracts`, `reqHistoricalData`, …) resolve the thread's asyncio
loop afresh on every call via `asyncio.get_event_loop_policy().get_event_loop()`.
Other code in the trader process runs `asyncio.run(...)` (e.g. Telegram
alerts), which calls `set_event_loop(None)` on exit — **poisoning** the
thread's current loop so the next `ib_insync` call raises `There is no
current event loop in thread 'MainThread'` (the symptom that blocked the
first MES go-live attempt). `IBClient` therefore keeps **one persistent
loop** (the loop the `IB` instance is built on) and **re-asserts it as the
current loop on every `connect()`** — including the cached-connection path
— so order, balance, contract and `get_ohlcv` calls always resolve the
loop the socket transport is bound to. Re-using the *same* loop is
essential: a fresh loop would not be bound to the live IB socket and the
request would hang. See `src/units/accounts/ib_client.py::_ensure_event_loop`
and the regression tests in `tests/test_ib_integration.py`
(`TestEventLoopResilience`). Fixed in PR #1712.

## Headless Gateway on the VM (Docker — current)

The Gateway runs as the **gnzsnz/ib-gateway Docker container** (image
`ghcr.io/gnzsnz/ib-gateway:stable`, installer `scripts/install_ib_gateway_docker.sh`;
`deploy/ib-gateway.compose.yml` is kept for reference only — see the env-file
note below). The hand-rolled native IBC install further down is **superseded**:
the modern standalone IB Gateway 10.45 installs flat in `~/Jts`, a layout IBC
refuses ("can't find jars/vmoptions; install the offline version"). The Docker
image bundles a known IBC-compatible Gateway+IBC in the correct layout, so it
just works headless.

**Port mapping (the socat relay — PR #1706).** Inside the container, IB
Gateway binds its paper API on `127.0.0.1:4002` **localhost-only**. A
connection arriving over Docker's NAT bridge has a non-loopback source IP
and the Gateway refuses it (the earlier `-p …:4002` map produced
`API connection failed: TimeoutError()`). The gnzsnz image ships a **socat
relay** on container port `4004` that accepts the bridged connection and
forwards it to the Gateway's loopback `4002`. So the installer maps
**host `127.0.0.1:4002` → container `4004`** (`docker run … -p 127.0.0.1:4002:4004`),
and the bot dials `127.0.0.1:4002` (`ib_paper.ib_port: 4002`). `docker ps`
shows `127.0.0.1:4002->4004/tcp`.

**Credentials.** The container reads creds from `/etc/ict/ib-gateway-docker.env`
(rendered from the `IB_USERNAME` / `IB_PASSWORD` repo secrets). It is loaded
via `docker run --env-file` (read **literally**), NOT compose `env_file:`,
because Compose v2 performs `$`-interpolation on env-file values and mangles a
password containing `$`.

**Login / 2FA.** The `provision-ib-gateway` workflow drives the (re)create.
The **paper** account logs in straight through — it clicks "Paper Log In" and
reaches `Login has completed` with **no IBKR Mobile 2FA prompt**. (2FA only
applies to the live account; see below.) `READ_ONLY_API=no` so the API accepts
orders. After login the journal shows `Market data farm connection is OK` +
`HMDS data farm connection is OK` and MES candles flow.

**Provision (Docker — current path):** Claude fires the
`provision-ib-gateway` workflow (issue label `provision-ib-gateway`,
body `mode: paper`), which runs `scripts/install_ib_gateway_docker.sh` on
the VM: it (re)creates the container with the socat port-map above and the
container logs into the **paper** account with no 2FA. The `IB_USERNAME` /
`IB_PASSWORD` repo secrets must be set first. A code change to the IB Python
path (e.g. `ib_client.py`) needs only `pull-and-deploy` (restart trader) —
**no** gateway re-provision; re-provision only when the container/port-map
or creds change.

**Paper account 2FA:** none. The paper login completes without an IBKR
Mobile prompt, so paper go-live and gateway re-provisions are fully
autonomous. If the Gateway is ever down, MES **pauses gracefully** (candle
fetch returns `None`) and the live crypto trader is unaffected.

**Live account 2FA:** `U25907316` cannot disable 2FA (funded account). A
live Gateway needs an IBKR Mobile approval on (re)login; it stays
`mode: dry_run` until proven and separately promoted (Tier-3). This is the
one place a physical operator tap is unavoidable.

## Headless Gateway on the VM (IBC — superseded native install)

> Superseded by the Docker path above; kept for historical record. The
> modern flat standalone Gateway 10.45 is incompatible with native IBC.

The production bot runs on the OCI live VM, so the IB Gateway must run there
too (the bot connects to `127.0.0.1:<port>`). A logged-in Gateway is the one
hard prerequisite for any IB order to execute — no Gateway, no fills.

**Artifacts (native IBC — no longer used):**
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

## Market data (delayed by default)

MES candles come from IB via `IBMarketData.get_ohlcv` (`reqHistoricalData`).
The connector calls `reqMarketDataType(3)` (**delayed**) by default, so it
works **without a paid CME real-time subscription** — IB serves free delayed
futures bars. This is the intended mode for strategy refinement and model
training (the operator's 2026-05-21 decision). Quotes/bars lag ~10–15 min, so
this mode is **not** for latency-sensitive live execution.

To switch to real-time later: add the **CME Real-Time (NP, L1)** subscription
on the billable IB account (the paper account shares the live account's
subscriptions), then set `IB_MARKET_DATA_TYPE=1`. No code change needed.

## Notes

- `ib_insync` is no longer actively maintained. `requirements.txt` pins
  `ib_insync`; `ib_client.py` transparently accepts the API-compatible
  fork `ib_async` if only it is installed.
- Bracket prices are snapped to the MES `0.25` tick grid before
  transmission (IB rejects off-grid futures prices).
- The dry-run guard (`scripts/check_dry_run_in_diff.py`) fires on the
  `ib_live: mode: dry_run` line — that is **intended**: it surfaces the
  dry configuration to the operator for review.
