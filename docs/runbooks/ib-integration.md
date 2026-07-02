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

> **Before `ib_live` can trade, the live *login* must work headlessly — and as
> of 2026-06-15 it does NOT.** The bot user's IB Key 2FA is in
> challenge/response mode (Seamless Authentication OFF), so a headless live login
> hangs at "Authenticating…" with no push. Paper is 2FA-exempt and unaffected.
> The fix (enable Seamless push for the bot user) + the read-only validation
> tooling (`vm-ib-gateway-live-login-test`) are documented in
> [`docs/runbooks/ib-live-login-2fa.md`](ib-live-login-2fa.md) (tracked:
> `BL-20260615-IBLIVE-2FA`).

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

## Gateway isolation redesign (2026-06-10) — the gateway on its own VM

The recurring 2026-06-10 CPU-wedge cascade had one root cause: the IB Gateway
(a heavy headless Java/Xvfb/IBC desktop app in Docker + a socat relay) sharing
the **2-core money box** with the live trader. A wedged or restarting gateway,
plus the reactive 5-min watchdog's restart churn, plus the trader's per-tick
connects, thrashed 2 cores (loadavg ~8.5) until the trader — and even the
gateway-recovery workflow — timed out. We had been treating the feedback loop
with band-aids (`IB_FETCH_TIMEOUT_S`, the connect breaker, CPU caps #3232,
watchdog escalations) instead of the disease.

**New topology (operator directive, Plan B):**

- **The gateway runs on its own dedicated Ampere VM (`ict-ib-gateway`,
  1 OCPU / 6 GB)**, isolated from the live trader (which stays on the
  E2.1.Micro). The trader reaches it over the network via
  `config/accounts.yaml::ib_paper` (`ib_host`/`ib_port`). A wedged or
  restarting gateway can now never touch the money loop. OCPU budget:
  trainer 1 + gateway 1 = 2 of the 4 Always-Free Ampere OCPUs.
- **One scheduled `docker restart`/day** (`ict-ib-gateway-reset.{service,timer}`,
  **06:05 UTC** — retimed 2026-07-02, was 05:30, see below) — deterministic
  belt-and-suspenders recovery for the single known failure (the in-place
  re-login wedging on the reset), complementing the reactive watchdog below
  (which catches a wedge that sets in at any other time). The reset unit is
  gated to the gateway VM via `ConditionPathExists=/etc/ict/ib-gateway-docker.env`.
  **2026-07-02 retime (BL-20260623-002):** the original 05:30 fire was actually
  *inside* IBKR's documented overnight reset window (~03:45–05:45 UTC), not
  after it — the one deterministic restart the whole design relies on was
  racing the outage it exists to fix, reproducing as a recurring wedge right
  around 06:00–06:05Z (confirmed recurring 2026-06-23 and 2026-07-02). Retimed
  to 06:05 (20min margin past the window's close), and the watchdog below now
  carries `--suppress-window-utc 03:45-05:45` so it logs but never *acts* on a
  wedge detected inside that window either — a restart in there can't succeed,
  and attempting one just burns the `--cooldown-min` budget for no benefit.
- **Reactive auto-restart re-armed (2026-06-22, BL-20260622-GATEWAY-MIDDAY-WEDGE).**
  `check_ib_gateway.py` / `ict-ib-gateway-watchdog.{service,timer}` runs **on the
  gateway VM** (auto-enabled only where `/etc/ict-vm-role` == `gateway`, via
  `_GATEWAY_ONLY_TIMERS` in `scripts/install_systemd_units.sh`; it is NOT enabled
  on the trader — verified inactive there 2026-06-22). It was briefly demoted to
  once-daily alert-only on 2026-06-10 (the original reactive churn could starve
  the box the gateway then SHARED with the trader). Now that the gateway is
  isolated that objection is moot, and daily-only left a real gap: a session that
  wedges MID-DAY had no recovery until the next 05:30 (an open MHG position
  tripped a MONITOR BLIND alert on 2026-06-22; recovery needed a manual
  `vm-ib-gateway-recover`). The watchdog probes every ~5 min and, after
  `--restart-after 2` sustained-wedge checks, runs the SAME local
  `restart_ib_gateway.sh` — bounded by `--max-restarts 3` / `--cooldown-min 20` /
  `--exhaustion-reset-min 120` so it can never become a restart loop (and the
  restart can't touch the money loop — different VM). Once the budget is
  exhausted it falls back to alert-only.
  **Dep-free local probe (BL-20260622-GATEWAY-LOCAL-PROBE):** the gateway VM is a
  MINIMAL box (just the Docker container — no bot venv, no `ib_insync`/`httpx`,
  no writable `/data`, no `.env`), so the account probe `ib_connect_check.py`
  can't run there (it fails to import `ib_insync` and falsely reads
  "connect failed" — which would loop-restart a HEALTHY gateway). The watchdog
  therefore points `--probe-script` at **`scripts/ops/ib_gateway_local_probe.py`**,
  which diagnoses the wedge from the container's own state + recent `docker logs`
  (socat → `127.0.0.1:4002` "Connection refused" / IBC re-auth pending, with no
  recent "Login has completed") using ONLY the `docker` CLI. Its `--state` is
  pinned to the repo-local (writable) `runtime_logs/` so the wedged streak
  persists between runs. Belt-and-suspenders hardening in `classify_probe`: a
  connect-failure whose error is a missing client library (`ib_insync`/`ib_async`
  not installed, import errors) is treated as **non-actionable** — a `docker
  restart` can't fix a broken probe environment, so it never drives a restart.
  Deploy changes to the gateway VM with the `vm-ib-gateway-deploy` workflow (the
  box has no `ict-git-sync`).
- **The thin trader-side connect breaker stays** (`IB_PROBE_TIMEOUT_S` /
  `IB_BREAKER_COOLDOWN_S`) so a gateway or network blip can never block the
  BTCUSDT loop. Manual emergency restart remains via the `vm-ib-gateway-recover`
  workflow — which, since the gateway VM has **no public IP**, SSHes to it at
  `10.0.0.251` **via ProxyJump through the live trader** (`VM_SSH_HOST`, the
  on-subnet bastion) and runs the `docker restart ib-gateway` THERE (not on the
  trader). The same `VM_SSH_KEY` authorizes both hops. Override the target with
  the `IB_GATEWAY_HOST` repo variable if the gateway VM's private IP changes.
  When the gateway parks on an IBKR login prompt the restart can't clear it
  autonomously: a **fresh-provision** login needs the operator's IBKR-Mobile 2FA
  tap (run `provision-ib-gateway` pointed at the gateway VM), whereas the common
  **overnight-reset wedge** is a username/password re-login dialog (no 2FA — see
  the auto-heal note below) that a plain `docker restart` clears. The recover
  workflow's log tail surfaces which one it is.
- **Deploying code to the gateway VM** (BL-20260622-GATEWAY-NO-AUTODEPLOY /
  -GATEWAY-VM-ROLE / -GATEWAY-GIT-SYNC). Historically the gateway VM was
  provisioned with only the credential env + the Docker container and had **no
  `ict-git-sync`**, so it never auto-pulled `origin/main` (it was found **269
  commits stale** on 2026-06-22). It now auto-deploys SAFELY:
  - **Host role marker.** `scripts/ops/provision_ib_gateway.sh` writes
    `/etc/ict-vm-role=gateway`. That marker makes `install_systemd_units.sh`
    enable the gateway-only timers (so they're reboot-safe, not just
    hand-enabled), and gates the deploy branch below. (The
    `vm-ib-gateway-deploy` workflow also sets it on an already-provisioned box.)
  - **Gateway-safe git-sync.** `ict-git-sync.timer` runs on the gateway too, but
    `scripts/deploy_pull_restart.sh` takes a **minimal gateway branch** when
    `/etc/ict-vm-role==gateway`: on a HEAD move it only re-runs
    `install_systemd_units.sh` + bounces the gateway timers, and **exits before**
    the `pip install -r requirements.txt` + trader-service-restart section
    (neither belongs on the minimal, venv-less box — a pip install would bloat
    it and the service enumeration could start the trader/web-api there).
  - **On-demand deploy** (`vm-ib-gateway-deploy` workflow). For an immediate
    push (or the initial sync), drive a `vm-ib-gateway-deploy`-labelled issue: it
    SSHes to the gateway VM (same ProxyJump transport as recover), sets the role
    marker, `git reset --hard origin/main`, re-runs `install_systemd_units.sh`,
    restarts the watchdog timer, and prints a self-deploy diagnosis.

The live→3-OCPU trader migration is **paused**: with the gateway off the money
box, the micro may hold the trader + web-api + sidecars on 2 cores (the
#3232/#3202 isolation fixes already help). Measure the micro's load sans-gateway
once it's moved; migrate live to 3 only if 2 cores can't hold it. The paused
migration plan is [`docs/runbooks/live-vm-migration-ampere.md`](live-vm-migration-ampere.md)
(PR #3257) — kept as a contingency, **not** the active plan.

### Networking the isolated gateway (private subnet only)

The trader reaches the gateway across the private subnet, so two things must
line up — and neither may expose the unauthenticated broker socket publicly:

- **Container bind (durable).** `scripts/install_ib_gateway_docker.sh` publishes
  the API with `-p ${IB_BIND_ADDR}:4002:4004` (default `127.0.0.1`). On the
  gateway VM the provisioner passes **`IB_BIND_ADDR=10.0.0.251`** (the private
  IP) — wired through `provision-ib-gateway.yml` (`bind_addr:` input / issue
  body line) → `provision_ib_gateway.sh` → the installer. This makes the
  private-IP bind **survive a full re-install**; without it a re-provision
  silently reverts to loopback and MES goes dark across the subnet. Never set
  `IB_BIND_ADDR=0.0.0.0` (the workflow refuses it).
- **Cloud Security List (intra-subnet ingress).** OCI needs a TCP/4002 ingress
  rule scoped to the private subnet (`10.0.0.0/24`) on the subnet's Security
  Lists. Apply it with the **`vm-cloud-open-ib-port`** workflow (label
  `vm-cloud-open-ib-port`), which parameterizes `scripts/ops/cloud_open_port.py`
  via `INGRESS_SOURCE_CIDR` and **hard-refuses any `/0` (public) source** — and
  does **no** public-internet `/api/health` probe (there is, by design, none).
  Run it against the micro (it shares the `10.0.0.0/24` subnet with the gateway,
  so the rule covers both boxes). Verify MES out-of-band via the trader logs:
  the IB connect-breaker `OPEN for 10.0.0.251:4002` clears and `net_liquidation`
  populates (`/api/diag/journalctl?unit=ict-trader-live` + `ib_connect_check`).

### Decommissioning a stray gateway on the micro

`vm-ib-gateway-stop` (label `vm-ib-gateway-stop`, default host = the micro) is
now a **full teardown** for any gateway left on a host: it `docker stop` +
`docker rm -f ib-gateway`, `systemctl disable --now` BOTH the
`ict-ib-gateway-watchdog.timer` and the `ict-ib-gateway-reset.timer`, and
`rm -f /etc/ict/ib-gateway-docker.env` (which makes `ict-ib-gateway-reset.service`
inert via its `ConditionPathExists` gate). This is what neutralizes the daily
05:30 UTC reset on the micro after the gateway is moved off it — otherwise the
reset would `docker restart` and revive the container back onto the money box.

The historical reactive-watchdog design below is **superseded** by the above —
kept as the record of the failure mode + why the daily reset is sufficient.

**Auto-heal watchdog (`ict-ib-gateway-watchdog.{service,timer}`, 2026-05-28) — SUPERSEDED 2026-06-10, see redesign above.**
The Gateway can stay *up* yet lose its IBKR session during IBKR's overnight
server-reset window: the in-place re-login hits a transient "Unrecognized
Username or Password" dialog, IBC parks on it and never retries (`restart:
unless-stopped` doesn't help — the process *hangs*, it doesn't die), so the
data farms read "broken", every MES request times out, and `ib_paper` goes
dark for hours until a container restart. The watchdog
(`scripts/check_ib_gateway.py`, fired every 5 min) probes `ib_paper` via
`ib_connect_check` — note a logged-out Gateway still reports `connected=true`
but `net_liquidation=None`, so **health = connected AND net_liquidation
populated** — and after 2 consecutive wedged checks runs
`scripts/ops/restart_ib_gateway.sh` (the same `docker restart` the manual
`vm-ib-gateway-recover` workflow performs). Guard rails `--max-restarts 3` +
`--cooldown-min 20` mean a genuine bad-credential / IBKR lockout can never
become a restart loop — once exhausted it alert-only escalates to Telegram.
This automates the recovery that previously needed a manual
`vm-ib-gateway-recover` dispatch. Background + the diagnosis that the failure
is the overnight-reset login dialog (not 2FA, which the paper account doesn't
use): health-review backlog `BL-20260527-003`.

**Exhaustion re-arm (`--exhaustion-reset-min 120`, added 2026-06-09,
BL-20260605-004).** `--max-restarts 3` is a *per-episode* cap, where an
"episode" lasts until a probe reads healthy. The 2026-06-09 incident showed
the failure mode this strands: a wedge began ~09:53 UTC and the watchdog's
3 restarts were all spent early — inside IBKR's reset/maintenance window,
when a container restart re-logins cleanly (farms report OK) but the upstream
IBKR session still won't service `reqCurrentTime`/account-data — so the
budget exhausted and the watchdog went **silent (`action=none`) for 5.5h+**,
leaving MES/MGC/MHG dark for the whole day even after IBKR recovered. (A
manual `vm-ib-gateway-recover` later, well past the window, also needed a
clean session token; two clean restarts during/just-after the CME break did
not re-establish it — confirming the residual cause is IBKR-side, not the
container.) `--exhaustion-reset-min` closes the gap: once the budget is
spent, it is re-armed after that many minutes of no restart (default 120),
so a multi-hour wedge gets retried once IBKR's reset window is over. It stays
loop-proof — the re-arm only fires after a long quiet gap — and `0` reverts
to the original give-up-for-the-episode behaviour. Env override:
`IB_WATCHDOG_EXHAUSTION_RESET_MIN`.

## Headless Gateway on the VM (IBC — superseded native install)

> Superseded by the Docker path above; kept for historical record. The
> modern flat standalone Gateway 10.45 is incompatible with native IBC.

The production bot runs on the OCI live VM, so the IB Gateway must run there
too (the bot connects to `127.0.0.1:<port>`). A logged-in Gateway is the one
hard prerequisite for any IB order to execute — no Gateway, no fills.

**Artifacts (native IBC — REMOVED; superseded by the Docker gateway above):**
The native-IBC files were deleted from the repo once the Docker gateway on its
own VM became the live path (the flat standalone Gateway 10.45 is incompatible
with native IBC). Kept here only as historical record of what used to exist:
- `deploy/ib-gateway.service` — **REMOVED.** Was a systemd unit running IB
  Gateway under `xvfb` via IBC, independent of `ict-trader-live`.
- `deploy/ibc/config.ini.template` — **REMOVED.** Was the IBC config
  (auto-restart mode, loopback API bind, auto-accept connection), credentials
  substituted at render time.
- `scripts/install_ib_gateway.sh` — **REMOVED.** Was the idempotent native
  installer (xvfb, IB Gateway standalone, IBC, config render, unit install).
- `scripts/ib_gateway_start.sh` — **REMOVED.** Was the native unit's
  `ExecStart` wrapper (auto-detected the Gateway version + exec'd IBC's
  `ibcstart.sh`).

The Docker-path files remain LIVE and are documented in the Docker section
above — `scripts/ops/provision_ib_gateway.sh` (VM-side: stages creds + runs the
**Docker** installer `scripts/install_ib_gateway_docker.sh`) and
`.github/workflows/provision-ib-gateway.yml` (renders creds from the
`IB_USERNAME` / `IB_PASSWORD` repo secrets, scps them to the VM, runs the
provisioner). These were never native artifacts.

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
