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

**This section derives from the `credentials-and-vm-mutations` skill,
not from any precedent runbook.** Operator steps are restricted to the
three categories that contract allows: originate a secret value at the
third party, add it to GitHub Actions secrets, or approve a tier-gated
decision. Everything else (VM-side propagation, account discovery,
smoke testing, mode promotion) is Claude-driven via workflows.

### 1. Sign up for the Tradovate demo *(originate at third party)*

- Go to https://www.tradovate.com/ → create an account.
- Activate the **free 14-day simulated trial** for futures
  (https://support.tradovate.com/s/article/Free-Trial-of-Tradovate).
  No payment info required for sim.
- (Optional, do later) For live: a funded live Tradovate account +
  the paid **API Access** add-on. Demo is enough for everything
  here.

### 2. Create the API app and capture credentials *(originate at third party)*

- Sign in to https://trader.tradovate.com.
- **Settings → API Access → Create new app.**
- Pick any unique app name (e.g. `ict-bot-prod`); set version to
  `1.0`.
- Copy the **`cid`** (numeric integer) and **`secret`** (long
  random string) that Tradovate generates. The secret is shown
  once — save it before navigating away.
- Pick a stable **device id** string (e.g. `ict-bot-linux-01`). Use
  the same string in step 3.

### 3. Add the 7 env vars to GitHub Actions secrets *(originate value into Actions)*

Repo → **Settings → Secrets and variables → Actions → New repository
secret**. Add each (exact names — these are the keys
`TradovateConfig.load()` reads):

| Secret name | Value |
|---|---|
| `TRADOVATE_USERNAME` | the username you used to sign up |
| `TRADOVATE_PASSWORD` | your Tradovate password |
| `TRADOVATE_APP_ID` | the app name from step 2 |
| `TRADOVATE_APP_VERSION` | `1.0` (matches what you set in step 2) |
| `TRADOVATE_CID` | the numeric cid from step 2 |
| `TRADOVATE_SECRET` | the secret from step 2 |
| `TRADOVATE_DEVICE_ID` | the device-id string you picked |

### 4. Ping Claude

That's the complete operator surface for provisioning. Tell Claude
"Tradovate secrets provisioned" and the rest is Claude-driven via
workflows — no SSH, no systemd edits, no VM commands from you.

---

## What Claude does after your ping

The propagation workflow (`provision-tradovate-creds`) doesn't exist
yet — Claude's first action on the ping is opening the Tier-1 PR that
adds it (a mirror of `rotate-account-keys.yml` for Tradovate's 7-tuple,
with SSH `SendEnv` so secret values never reach run logs). Once that
PR lands:

1. **Dispatch the propagation workflow** — reads the 7 Actions secrets,
   writes them to the trader's systemd environment via SSH `SendEnv`,
   reloads + restarts `ict-trader-live.service`. Output goes to the
   workflow run, secret values never appear there.
2. **Verify the post-state** via the diag relay — auth probe to
   Tradovate succeeds (the trader logs `auth ok env=demo` when
   `TradovateConfig.load()` constructs the adapter).
3. **Discover the numeric account id** by dispatching
   `python -m src.units.accounts.tradovate.cli.list_accounts` on the
   VM through the system-actions allowlist (a new entry, added in the
   same PR as the propagation workflow or in a tiny follow-up).
4. **Patch `accounts.yaml::tradovate_demo_1.tradovate_account_id`** in
   a Tier-1 PR with the discovered id.
5. **Run the demo smoke test** —
   `python -m src.units.accounts.tradovate.examples.smoke_test_demo
   --symbol MESM6 --with-quotes` — via the same allowlist entry.
   Expected JSON:
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
   On failure, Claude diagnoses and tells you whether the issue is
   credential-side (your re-provisioning) or code-side (Claude's fix).
6. **Open the Tier-3 strategy-assignment PR** (next section). Draft;
   waits for your approval to merge.
7. **Promote `mode: dry_run` → `mode: live`** via the `set-account-mode`
   operator-action workflow once you give explicit approval (the third
   operator-only category: approve a tier-gated decision).

### Tier-3 decision you'll be asked to approve: strategy assignment

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

Either is a separate Tier-3 PR — Claude opens it as a draft for your
approval. Claude dispatches the `set-account-mode` operator action to
flip `mode: dry_run → live` only after you approve the assignment PR
and confirm the paper smoke test ran clean. The mode flip itself is
the third operator-only category (approve a tier-gated decision); the
workflow does the on-VM write.

### Later: switch demo → live Tradovate API

When the 14-day demo trial expires (or you skip it), and after the
funded live Tradovate account + paid API Access add-on is purchased:

1. Repeat steps 1–4 above to provision the live-side credentials
   into the same 7 Actions secret names. Same auth shape; only the
   values change.
2. Claude opens a Tier-3 PR flipping
   `accounts.yaml::tradovate_demo_1.tradovate_env: demo → live`
   (the single switch that maps to live URLs) and renaming the
   account as appropriate. You approve; Claude dispatches the
   propagation workflow with the new values and re-verifies via
   the diag relay.

No SSH, no systemd edits — same contract throughout.

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
