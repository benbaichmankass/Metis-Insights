# Runbook — Tradovate (futures) integration

**Status: WIRED, INERT (2026-06-02).** PR #2647 added the self-contained
Tradovate package at `src/units/accounts/tradovate/`; PR #2649 wired it
into the bot's broker routing (integrator + executor branch +
`accounts.yaml::tradovate_demo_1`). The account ships **inert** —
`mode: dry_run`, `strategies: []`, `tradovate_account_id: 0` — so no
order ever leaves the process until the operator completes the
**hookup checklist below**.

Long-term intent (operator decision, 2026-06-02): replace IBKR for
**futures execution** with Tradovate (cheaper / better fills for the
same instruments). The Tradovate sleeve runs in parallel to `ib_paper`
during validation; once paper fills are confirmed equivalent or better
across a multi-week window, the operator deprecates the IB futures
sleeve in a Tier-3 PR.

## Operator hookup checklist

Do these in order. Each step is independent and rerunnable; the
account stays inert until **every** step is complete.

### 1. Sign up for the Tradovate demo

- Go to https://www.tradovate.com/ → create an account.
- Activate the **free 14-day simulated trial** for futures
  (https://support.tradovate.com/s/article/Free-Trial-of-Tradovate).
  No payment info required for sim.
- (Optional, do later) If/when you decide to take it live, you'll
  also need: a funded live Tradovate account + the **API Access**
  add-on (paid subscription). Demo is enough for everything in
  this runbook.

### 2. Create the API app and capture credentials

- Sign in to https://trader.tradovate.com.
- **Settings → API Access → Create new app.**
- Pick any unique app name (e.g. `ict-bot-prod`); set version to
  `1.0`.
- Copy the **`cid`** (numeric integer) and **`secret`** (long
  random string) that Tradovate generates. You will not be able
  to see the secret again — write it down securely.
- Pick a stable **device id** (any string you choose) — e.g.
  `ict-bot-linux-01`. It must be the same string in every place
  you put the credentials below.

### 3. Add the 7 env vars to GitHub Actions secrets

Repo → **Settings → Secrets and variables → Actions → New repository
secret**. Add each of these (exact names — they're the keys
`TradovateConfig.load()` reads):

| Secret name | Value |
|---|---|
| `TRADOVATE_USERNAME` | the username you used to sign up |
| `TRADOVATE_PASSWORD` | your Tradovate password |
| `TRADOVATE_APP_ID` | the app name you chose in step 2 (e.g. `ict-bot-prod`) |
| `TRADOVATE_APP_VERSION` | `1.0` (or whatever you set) |
| `TRADOVATE_CID` | the numeric cid from step 2 |
| `TRADOVATE_SECRET` | the secret from step 2 |
| `TRADOVATE_DEVICE_ID` | the device-id string you picked |

### 4. Provision the same 7 env vars on the live VM

The trader process reads them from the systemd `EnvironmentFile=` —
**not** from the Actions secrets directly. The Actions secrets are for
CI / future automation; the live process needs them in its own env.

Operator path (manual):

```bash
# On the live VM (158.178.210.252), as the user that owns the
# trader service:
sudo $EDITOR /etc/systemd/system/ict-trader-live.service.d/tradovate.conf
```

Add this drop-in:

```ini
[Service]
Environment=TRADOVATE_USERNAME=...
Environment=TRADOVATE_PASSWORD=...
Environment=TRADOVATE_APP_ID=...
Environment=TRADOVATE_APP_VERSION=1.0
Environment=TRADOVATE_CID=...
Environment=TRADOVATE_SECRET=...
Environment=TRADOVATE_DEVICE_ID=...
```

Then reload + restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart ict-trader-live.service
```

**Once steps 3+4 are done, ping me ("creds provisioned") and I'll
drive steps 5+6 autonomously via the diag relay.**

### 5. Discover the numeric account id (I drive this)

I'll dispatch this on the VM via the `system-actions` workflow:

```bash
python -m src.units.accounts.tradovate.cli.list_accounts
```

Tradovate returns one or more accounts — usually a single
simulation account when you start. I'll read the numeric `id`
field and open a tiny PR that fills `tradovate_account_id` into
`config/accounts.yaml::tradovate_demo_1`.

### 6. Run the smoke test (I drive this)

After the account id is patched in, I'll run the demo smoke test
on the VM:

```bash
python -m src.units.accounts.tradovate.examples.smoke_test_demo \
    --symbol MESM6 --with-quotes
```

Expected output (JSON):

```json
{
  "env": "demo",
  "authed": true,
  "accounts_found": 1,
  "selected_account": {"id": 12345, "name": "DEMO123"},
  "contract": {"id": 67890, "name": "MESM6"},
  "ws_connected": true,
  "quote_seen": true,
  "order_placed": false,
  "errors": []
}
```

If anything fails I'll diagnose and tell you whether the issue is
credential-side (your fix) or code-side (my fix).

### 7. Decide strategy assignment (Tier-3, your call)

Currently MES routes to `ib_paper` exclusively. Adding it to
`tradovate_demo_1.strategies` would put **both** brokers in line
to receive MES signals — the coordinator's symbol→exchange
dispatch gate doesn't tie-break between two same-exchange-class
accounts. Options:

- **A. Paper parallel.** Add `[mes_trend_long_1d]` (or another
  futures strategy) to `tradovate_demo_1.strategies` and **remove
  it from `ib_paper.strategies`** in the same PR. Single source
  of execution per strategy; runs Tradovate-paper for validation
  alongside the existing IB paper book (different strategies).
- **B. Tradovate-only futures.** Move every MES strategy off
  `ib_paper` onto `tradovate_demo_1` at once. Faster cutover,
  larger change; IB futures sleeve immediately becomes dormant.
  Use after some paper-parallel observation.

Either is a separate Tier-3 PR — I'll open it as a draft and you
approve.

### 8. Promote `mode: dry_run` → `mode: live`

**Only after steps 1–7** are complete, a paper trade is verified
end-to-end on Tradovate, and you've decided on strategy
assignment. The sanctioned wire is the `set-account-mode`
operator action:

```
# Open a labelled issue in the repo:
# Label: system-action
# Title: [system-action] set-account-mode tradovate_demo_1 live
# Body:
#   action: set-account-mode
#   account: tradovate_demo_1
#   mode: live
#   reason: <one line — e.g. "promote to paper after smoke-test verification">
```

The workflow flips `mode:` in `accounts.yaml` on the VM, restarts
the trader, and posts the post-state back to the issue. Same wire
as every other account-mode flip.

### 9. (Optional, later) Switch demo → live API

When the demo trial expires (or you skip it), and after the funded
live account + API add-on is purchased:

1. Set `tradovate_demo_1.tradovate_env: live` in `accounts.yaml`
   (Tier-3 PR).
2. Update the 7 env vars (steps 3+4) with the live-side
   credentials. Same names; the live API uses the same auth
   shape.
3. Re-run the smoke test against the live env to confirm.

No code edits required — `TRADOVATE_ENV=demo|live` is the single
switch.

## What's actually wired (code map)

| Layer | File | Role |
|---|---|---|
| Package | `src/units/accounts/tradovate/` | Self-contained Tradovate client — config, auth, REST, WebSocket, services, models, risk manager, recorder, event bus, broker-agnostic `TradovateAdapter`. Demo by default; 42 unit tests under `tests/unit/tradovate/`. |
| Adapter factory | `src/units/accounts/clients.py::tradovate_client_for` | Builds a `TradovateAdapter` from `TradovateConfig.load()` (reads the 7 env vars). Returns `None` on missing creds, matching the velotrade pattern. |
| Integrator | `src/units/accounts/integrator.py::TradovateAPI` + `EXCHANGE_MAP["tradovate"]` | The legacy entry point that routes `OrderPackage` → adapter. Mirrors `VelotradeAPI`. |
| Executor | `src/units/accounts/execute.py::_submit_order` (`exchange == "tradovate"` branch) | The canonical live-placement path. Translates the bot's order shape to `OrderRequest`, calls `adapter.place_order`, and surfaces Tradovate's typed errors as `RuntimeError` for the coordinator's diagnostic-ping wrapper. |
| Account model | `config/accounts.yaml::tradovate_demo_1` | The inert account entry; full hookup checklist also lives in its YAML comments. |
| Endpoint catalogue | `src/units/accounts/tradovate/endpoints.py` | Single source of truth for every REST path + WS topic. Items still flagged `UNCERTAIN` (`/order/list`, `/position/list`, the DOM subscribe topic) — verified during live smoke testing. |
| CLI tools | `src/units/accounts/tradovate/cli/` | `check_auth`, `list_accounts`, `list_contracts`, `stream_quotes`, `place_demo_order`, `cancel_all_orders`. All default to demo; `--env` overrides. |
| Smoke test | `src/units/accounts/tradovate/examples/smoke_test_demo.py` | One-shot auth + accounts + contract + quotes + (optional) order test. |

## Safety gates (defense-in-depth)

Four independent gates have to **all** open before a live order reaches
Tradovate:

1. **`config/accounts.yaml::mode: live`** — `RiskManager.evaluate()`
   rejects with `account_mode_dry_run` while this is `dry_run`. Ships
   `dry_run`; flipped via `set-account-mode` only.
2. **Non-empty `strategies:` list** — the coordinator's per-account
   strategy filter blocks every signal when this is `[]`. Ships `[]`.
3. **`tradovate_account_id: <non-zero>`** — `_submit_order` refuses
   with `TradovateConfigError` when this is `0` / unset. Ships `0`.
4. **Adapter dry-run flag (`TRADOVATE_DRY_RUN`)** — the package's
   own gate. Defaults to `true`; when set, `OrderService.place()`
   short-circuits with a synthetic negative-id `Order` instead of
   hitting the wire. Flip to `false` only after gates 1–3 are open
   and you've verified a real paper order via `place_demo_order
   --live-fire`.

## Why no `api_key_env` field

Unlike Bybit / Velotrade, Tradovate auth is a 7-tuple
(username/password/app_id/app_version/cid/secret/device_id) read
directly from `os.environ` by `TradovateConfig.load()` — not via the
single `api_key_env` field on the `accounts.yaml` entry. So Tradovate
accounts always load `configured=True` (like IB, for the same reason)
and `tradovate_client_for(account)` does its own cred check before
constructing the adapter.

The `master-secrets.template.yaml` carries a `no_secret: true`
placeholder so the per-account drift guard passes; the render script
skips writing anything for Tradovate accounts.

## Endpoint uncertainty

`endpoints.py` flags three paths as `UNCERTAIN`:

- `/order/list` — community references both `/order/list` and
  `/order/items`. I chose the former per the official
  `example-api-js` convention; verify during live smoke test.
- `/position/list`, `/position/item` — community-confirmed but no
  swagger entry seen.
- `md/subscribeDOM` — WebSocket DOM subscribe topic; not used by
  the current smoke test, only by future depth-of-book features.

Verified during the 2026-06-02 endpoint audit:
`/auth/accesstokenrequest`, `/auth/renewAccessToken`, `/account/list`,
`/order/placeOrder`, `/order/cancelOrder`, `/order/modifyOrder`,
`/fill/list`. Tradovate's REST router is case-insensitive in practice;
the paths follow the docs' camelCase convention for hygiene.

## Cross-references

- The standalone package's own README: `src/units/accounts/tradovate/README.md`
- The base architecture doc lists Tradovate alongside Bybit / IB under
  Step 6: `docs/ARCHITECTURE-CANONICAL.md`.
- The IB precedent (shaped most of the wiring decisions):
  `docs/runbooks/ib-integration.md`.
- The Velotrade precedent (shaped the "wired-but-inert" account
  scaffolding pattern): `prop_velotrade_1` in `accounts.yaml`.
