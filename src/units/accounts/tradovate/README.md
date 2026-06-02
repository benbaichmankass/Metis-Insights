# Tradovate integration

Demo-first Python integration for Tradovate's REST + WebSocket APIs. Built
to plug into the ICT bot as a self-contained broker package — does **not**
yet route into the existing intent / risk / order paths. The wiring step is
a separate sprint (Tier-3, operator-gated) so this package can land safely
on `main` for paper-trading development.

> **Live promotion is a config change, not a code change.** Switch
> `TRADOVATE_ENV=demo` → `live` after the operator confirms the Tradovate
> live API add-on is active on the funded account.

## Layout

```
src/units/accounts/tradovate/
├── __init__.py
├── config.py               # env-driven config; demo is default
├── endpoints.py            # every REST path + WS topic lives here
├── exceptions.py           # typed error vocabulary
├── auth.py                 # token acquisition + refresh
├── rest_client.py          # httpx wrapper with retry/backoff
├── websocket_client.py     # async WS w/ heartbeat + reconnect
├── account_service.py
├── market_data_service.py
├── order_service.py
├── position_service.py
├── risk_manager.py
├── models.py               # Account/Contract/Quote/Order/Fill/Position
├── retry.py                # exponential backoff helper
├── logging_utils.py        # secret-safe JSON logger
├── recorder.py             # NDJSON event recorder
├── event_bus.py            # in-process pub/sub
├── adapter.py              # broker-agnostic facade (build())
├── .env.example
├── cli/
│   ├── check_auth.py
│   ├── list_accounts.py
│   ├── list_contracts.py
│   ├── stream_quotes.py
│   ├── place_demo_order.py
│   └── cancel_all_orders.py
└── examples/
    └── smoke_test_demo.py
```

Tests live under `tests/unit/tradovate/`.

## Demo mode (start here)

1. Sign up at [tradovate.com](https://www.tradovate.com/) and complete the
   free 14-day simulated trial. Generate an API app on
   https://trader.tradovate.com → Settings → API Access.
2. Copy `.env.example` to `.env.tradovate` and fill in:

   ```
   TRADOVATE_ENV=demo
   TRADOVATE_USERNAME=…
   TRADOVATE_PASSWORD=…
   TRADOVATE_APP_ID=…
   TRADOVATE_APP_VERSION=1.0
   TRADOVATE_CID=…
   TRADOVATE_SECRET=…
   TRADOVATE_DEVICE_ID=ict-bot-linux-01
   TRADOVATE_DRY_RUN=true
   ```

3. Install dependencies:

   ```bash
   pip install httpx websockets
   ```

   `httpx` is already a project dep; `websockets` is the only new one.

4. Source the env and run the smoke test:

   ```bash
   set -a && source .env.tradovate && set +a
   python -m src.units.accounts.tradovate.examples.smoke_test_demo \
       --symbol MESM6 --with-quotes
   ```

   You should see a JSON report with `authed: true`,
   `accounts_found > 0`, and `quote_seen: true`.

5. Useful CLI scripts (all default to demo; pass `--env live` to override):

   ```bash
   python -m src.units.accounts.tradovate.cli.check_auth
   python -m src.units.accounts.tradovate.cli.list_accounts
   python -m src.units.accounts.tradovate.cli.list_contracts MESM6
   python -m src.units.accounts.tradovate.cli.stream_quotes MESM6 --seconds 60
   python -m src.units.accounts.tradovate.cli.place_demo_order \
       --account-id 123456 --symbol MESM6 --side buy --qty 1
   ```

   `place_demo_order` runs in dry-run by default. Pass `--live-fire` to
   actually send a paper order against the demo account.

## Switching to live

When the operator has confirmed:

- The Tradovate account is funded and approved for live trading.
- The API Access add-on is enabled and the live API has been tested.
- The bot's Tier-3 wiring (intent layer → adapter) has been merged and
  operator-approved.

Then:

1. Update the `.env.tradovate` on the VM:

   ```
   TRADOVATE_ENV=live
   TRADOVATE_DRY_RUN=true   # leave on for the first live session
   TRADOVATE_ALLOWED_SYMBOLS=<pinned list>
   ```

2. Restart the consuming service. The adapter will resolve live URLs
   automatically — no code edits needed.

3. After a clean live read-only session (auth + list accounts + stream
   quotes), flip `TRADOVATE_DRY_RUN=false` to start sending real orders.

## systemd unit example

If you want to run a long-lived quote recorder against demo:

```ini
# /etc/systemd/system/tradovate-demo-recorder.service
[Unit]
Description=Tradovate demo quote recorder
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/etc/ict-trading-bot/tradovate.env
WorkingDirectory=/opt/ict-trading-bot
ExecStart=/opt/ict-trading-bot/venv/bin/python -m src.units.accounts.tradovate.cli.stream_quotes MESM6 --seconds 0
Restart=on-failure
RestartSec=10
User=ubuntu

[Install]
WantedBy=multi-user.target
```

## Endpoint uncertainty

`endpoints.py` flags the REST paths and WS topics that aren't fully
verified — `/order/list`, `/position/list`, `/fill/list`, the DOM
subscribe topic. They were chosen based on community-reported usage but
the operator should confirm against Tradovate's published swagger
(https://api.tradovate.com/) before relying on any of them in a live
order path. Fixing a path is a one-file edit.

## Health checks

`TradovateAdapter.health()` returns a `HealthReport`:

```python
{
  "env": "demo",
  "authed": True,
  "ws_connected": True,
  "last_quote_ts": "2026-06-02T13:15:42.012345+00:00",
  "last_order_event_ts": None,
}
```

Wire this into the bot's existing health surface (`/api/diag/services`)
when the integration is promoted into the runtime.

## Wiring into the ICT bot (future work)

This package is intentionally not yet imported by `src/runtime/` or
`src/units/accounts/integrator.py`. When the operator approves
promotion, the steps are:

1. Add a Tradovate-typed account to `config/accounts.yaml` (default
   `mode: dry_run`).
2. Extend `src/units/accounts/integrator.py` to build a
   `TradovateAdapter` for accounts of that type.
3. Add the account credential env vars to the live VM's systemd unit
   (`EnvironmentFile=`).
4. Wrap `TradovateAdapter.place_order` behind the existing
   `Coordinator.multi_account_execute` so the `accounts.yaml::mode` +
   `strategies.yaml::execution` gates apply.

Each step is a small, reviewable PR rather than one big drop.
