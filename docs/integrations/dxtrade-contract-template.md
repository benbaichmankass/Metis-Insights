# DXtrade API contract — drop zone

This file is the **structured drop zone** for the Velotrade DXtrade API
contract. The phase-2 sprint wired the entire integration *shape*
(client class, factory, executor branch, coordinator routing,
not-configured gate, `/accounts_status` rendering, persistent prop
state) so that filling in the four method bodies in
`src/units/accounts/dxtrade_client.py` is the **only** code change
needed once the operator drops the contract.

When the operator receives the API contract from Velotrade:

1. **Replace** the `<TBD>` placeholders below with the values from the
   contract (do not paste the contract verbatim — keep this doc as a
   structured summary; attach the original PDF / spec under
   `docs/integrations/raw/` if needed).
2. **Open a follow-up PR** that fills in the four method bodies in
   `src/units/accounts/dxtrade_client.py` (`place`, `cancel`,
   `status`, `balance`). Use the bybit branch in
   `src/units/accounts/execute.py::_submit_order` as the reference
   implementation for retCode-style error handling.
3. **Run the live smoke test** per § 6 of the original phase-2 sprint
   prompt: enable `prop_velotrade_1` (no YAML change needed — the
   account already loads as not-configured), provision
   `VELOTRADE_API_KEY_1` + `VELOTRADE_API_SECRET_1`, route a
   `pkg.meta['is_test']=True` order with qty below DXtrade min-lot,
   expect rejection. The wiring is in place.

---

## 1. Endpoints

| Operation | HTTP method | Path | Notes |
|---|---|---|---|
| Place order | `<TBD>` | `<TBD>` | |
| Cancel order | `<TBD>` | `<TBD>` | |
| Order status | `<TBD>` | `<TBD>` | |
| Account balance | `<TBD>` | `<TBD>` | |

**Base URLs:**
- Sandbox: `<TBD>` — set via `VELOTRADE_BASE_URL` env var or
  `account['base_url']` in `config/accounts.yaml`.
- Production: `<TBD>` — same channels.

## 2. Authentication

- **Auth scheme:** `<TBD>` (REST headers / OAuth2 / signed query / etc.)
- **Required headers / params:**
  - `<header>: <value-or-template>` — `<TBD>`
  - `<header>: <value-or-template>` — `<TBD>`
- **Signing algorithm (if any):** `<TBD>` (HMAC-SHA256 / RSA / etc.)
- **Token / nonce lifetime:** `<TBD>`
- **Where the api_key + api_secret get used:** `<TBD>`

The `DXtradeClient.__init__` already validates non-empty
`api_key` + `api_secret` and stores them; it just doesn't hit the
network yet. The factory `velotrade_client_for(account)` in
`src/units/accounts/clients.py` resolves the env vars
(`VELOTRADE_API_KEY_1` / `VELOTRADE_API_SECRET_1` by default —
overrideable via `api_secret_env` in YAML).

## 3. Request schemas

### 3.1 Place order

Required fields the executor passes today (from
`src/units/accounts/execute.py::_submit_order` velotrade branch,
mirroring the bybit shape):

- `symbol: str` — instrument symbol (DXtrade format may differ from
  Bybit; document the mapping if so).
- `side: str` — "Buy" or "Sell".
- `direction: str` — "long" or "short" (informational; redundant with
  `side` but kept for parity with the strategy package).
- `entry: float` — limit price (0 means market).
- `sl: float` — stop-loss price.
- `tp: float` — take-profit price.
- `qty: float` — order quantity.
- `strategy: str` — strategy name (informational, for client-side
  tagging).

DXtrade-specific additional fields (`<TBD>`):

| Field | Type | Required? | Notes |
|---|---|---|---|
| `<TBD>` | `<TBD>` | `<TBD>` | |
| `<TBD>` | `<TBD>` | `<TBD>` | |

### 3.2 Cancel order

| Field | Type | Required? | Notes |
|---|---|---|---|
| `order_id` | str | yes | The id returned by `place` (`response['result']['orderId']` in our retCode-style shape). |
| `<TBD>` | `<TBD>` | `<TBD>` | |

### 3.3 Order status

| Field | Type | Required? | Notes |
|---|---|---|---|
| `order_id` | str | yes | |
| `<TBD>` | `<TBD>` | `<TBD>` | |

### 3.4 Account balance

| Field | Type | Required? | Notes |
|---|---|---|---|
| `<TBD>` | `<TBD>` | `<TBD>` | (likely no body — pull from the auth context) |

## 4. Response shapes

The executor reads a **retCode-style** shape (mirrors the bybit branch):

```json
{
  "retCode": 0,
  "retMsg": "OK",
  "result": { "orderId": "<id>" }
}
```

If the actual DXtrade response has a different shape, the
`DXtradeClient` method bodies should normalise to this shape before
returning (do the translation inside the client, not the executor —
keeps the executor branch identical to bybit).

| DXtrade field | Maps to | Notes |
|---|---|---|
| `<TBD>` | `retCode` | 0 / "0" / null = success; anything else = rejection |
| `<TBD>` | `retMsg` | human-readable error message |
| `<TBD>` | `result.orderId` | exchange-side order id |

## 5. Error codes

The executor catches non-zero `retCode` and surfaces it as
`RuntimeError("DXtrade rejected order: <retMsg>")`. The diagnostic ping
(`enqueue_execution_failure`) writes the message to
`runtime_logs/pending_pings/` for the Telegram bot.

Common codes the operator should expect (`<TBD>`):

| Code | Meaning | Operator action |
|---|---|---|
| `<TBD>` | Insufficient funds | Check `/accounts_status` → balance |
| `<TBD>` | Below min-lot | Reduce qty or skip the signal |
| `<TBD>` | Invalid auth | Rotate creds via `notebooks/operator/rotate_api_keys.ipynb` |
| `<TBD>` | Market closed | Time-window check (already enforced via `overnight_restricted` / `weekend_restricted`) |
| `<TBD>` | Mission complete | Should never reach the SDK — `PropRiskManager.SKIP_MISSION_MET` blocks first |

## 6. Min-lot, tick size, instrument metadata

- **Min lot per instrument:** `<TBD>` (per-symbol or global?)
- **Tick size:** `<TBD>`
- **Quantity precision (decimals):** `<TBD>`
- **Price precision (decimals):** `<TBD>`

The phase-1 `RiskManager.qty_precision` + `min_qty` config keys feed
the position-sizing rounding. Operator should populate these in
`config/accounts.yaml::prop_velotrade_1::risk` once the contract
specifies them.

## 7. Rate limits

- **Requests per second:** `<TBD>`
- **Burst allowance:** `<TBD>`
- **Concurrent open orders cap:** `<TBD>`
- **What happens on limit hit:** `<TBD>` (HTTP 429 / specific retCode?)

The `RuntimeError` flow already catches generic exceptions from the
SDK call; if rate-limit hits need a backoff retry, add it inside
`DXtradeClient.place` (best practice: client-side concern, not
executor-side).

## 8. Sandbox vs production

- **Sandbox account creation:** `<TBD>` (operator workflow)
- **Sandbox base URL:** `<TBD>`
- **How sandbox orders behave:** `<TBD>` (do they get filled / rejected differently?)
- **Test instruments available in sandbox:** `<TBD>`

The phase-2 acceptance criteria (smoke test with
`pkg.meta['is_test']=True` + qty below min-lot) will run against
sandbox first.

## 9. Connection lifecycle

- **Does the SDK need a long-lived connection?** `<TBD>`
- **Heartbeat / keepalive interval:** `<TBD>`
- **Reconnect policy on disconnect:** `<TBD>`

If a long-lived connection is required, `DXtradeClient` should manage
it internally (open lazily on first call, close on `__del__` or via
an explicit `close()` method). The executor + coordinator don't know
about connections — they just call the four methods.

## 10. Open questions

- **Does DXtrade expose mission-progress endpoints** (cumulative PnL,
  active days, eval-phase status) that we should reconcile against
  `runtime_state/prop_state.json`? If yes, we can sanity-check our
  in-process counters against the source of truth on each restart.
- **Order modification semantics:** can SL/TP be modified post-fill,
  or do we cancel + re-place? The S-030 PR4
  `execute.modify_open_order` helper assumes Bybit's
  `set_trading_stop` semantics; DXtrade may differ.
- **Position sizing on prop accounts:** does Velotrade impose
  per-position caps beyond the prop firm's daily-loss limit? If yes,
  add to `PropRiskManager.evaluate` as a new skip reason.
- **Funded-phase API differences:** does the funded account use the
  same endpoints + same auth, or does it require re-onboarding?
  Affects the `account_state == 'funded'` transition workflow.

---

## Implementation checklist (for the SDK-drop session)

When this template is filled in:

- [ ] Confirm endpoint URLs + auth scheme are unambiguous.
- [ ] Confirm response shape mapping to `retCode` / `retMsg` /
      `result.orderId` is straightforward (or add normalisation in
      `DXtradeClient`).
- [ ] Fill in `DXtradeClient.place` — exercise the cred header /
      signing flow + the error-code handling.
- [ ] Fill in `DXtradeClient.cancel`, `status`, `balance` — same
      pattern, smaller surface.
- [ ] Update `_submit_order`'s velotrade branch *only if* the
      response shape needed normalisation in the client (executor
      should remain symmetric with the bybit branch).
- [ ] Add a Velotrade smoke-test fixture under `tests/` that
      exercises the full path with a mocked `DXtradeClient` returning
      the documented response shape.
- [ ] Run the live smoke test on sandbox with `is_test=True` + qty
      below min-lot. Expect rejection.
- [ ] Provision sandbox creds via
      `notebooks/operator/rotate_api_keys.ipynb`.
- [ ] Confirm `/accounts_status` flips `prop_velotrade_1` from
      `configured: False` to `configured: True` after creds land.
- [ ] Add `strategies: [vwap]` (or whichever strategy is assigned)
      to `prop_velotrade_1` in `config/accounts.yaml`.
- [ ] Operator turns on production trading by removing
      `enabled: false` (already done in phase-2a) and confirming the
      first live signal lands a real order.

The hard rules from the original phase-2 prompt still apply:

- Do NOT touch `src/runtime/orders.py` or the bybit branch in
  `_submit_order`.
- Do NOT add per-trade operator confirmation (autonomous-live rule).
- Do NOT bypass the live-mode CI guard.
- If DXtrade requires a new dependency, declare it explicitly in the
  follow-up sprint summary.
