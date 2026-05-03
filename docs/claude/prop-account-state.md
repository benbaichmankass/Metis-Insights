# Prop account state — configuration & gating

The Velotrade integration adds a mission-aware risk gate on top of the
base `RiskManager`. This doc is the operator reference for the new
fields, the state machine, and the skip-reason vocabulary.

It applies to **prop accounts only** (`type: prop` in
`config/accounts.yaml`). Regular Bybit accounts (`type: regular`)
continue to use the unchanged base `RiskManager` and are unaffected.

---

## Account YAML fields

```yaml
accounts:
  prop_velotrade_1:
    type: prop                      # selects PropRiskManager
    exchange: velotrade             # see EXCHANGE_MAP in integrator.py
    api_key_env: VELOTRADE_API_KEY_1
    strategies: [vwap]              # routing filter (existing)
    enabled: true                   # disabled rows are skipped at load

    account_state: evaluation       # 'evaluation' | 'funded'
    phase_requirements:
      target_profit_pct: 0.05       # +5% to clear the evaluation
      min_active_days: 4            # min trading days before pass
      min_daily_profit_pct: 0.005   # informational (v1: not gated)
    prop_state:                     # in-process counters (seed only)
      cumulative_pnl_pct: 0.0
      active_days: 0
      entry_date: null              # YYYY-MM-DD; updated in-process

    overnight_restricted: true      # blocks new entries in window
    overnight_window: [22, 6]       # [start_hour, end_hour] UTC
    weekend_restricted: true        # blocks Sat/Sun UTC

    risk:                           # base RiskManager block (unchanged)
      max_dd_pct: 0.02
      daily_usd: 50
      pos_size: 200
      risk_pct: 0.005
      min_balance_usd: 50
```

### Defaults
| Field | Default | Notes |
|---|---|---|
| `account_state` | `"evaluation"` | |
| `phase_requirements.target_profit_pct` | `0.05` | |
| `phase_requirements.min_active_days` | `4` | |
| `phase_requirements.min_daily_profit_pct` | `0.0` | not gated in v1 |
| `prop_state.cumulative_pnl_pct` | `0.0` | |
| `prop_state.active_days` | `0` | |
| `overnight_restricted` | `False` | opt-in (legacy fixtures) |
| `overnight_window` | `[22, 6]` | wraps midnight when start > end |
| `weekend_restricted` | inherits `overnight_restricted` | |

---

## State machine

```
              ┌──────────────────────────────────────────────────┐
              │                                                  │
              │              evaluation                          │
              │                                                  │
              │   profit_target_met AND active_days_met          │
              │   → SKIP_MISSION_MET (no upside in more risk)    │
              │                                                  │
              │   else → allow (subject to base + time gates)    │
              │                                                  │
              └──────────────┬───────────────────────────────────┘
                             │  operator manually flips YAML
                             ▼
              ┌──────────────────────────────────────────────────┐
              │              funded                              │
              │                                                  │
              │   identical to base RiskManager                  │
              │   (subject to time gates if enabled)             │
              └──────────────────────────────────────────────────┘
```

The state transition (`evaluation` → `funded`) is **manual** — the
operator updates `account_state` in `accounts.yaml` after the prop
firm confirms the funded payout. There is no automatic graduation in
v1.

---

## Gate evaluation order

`PropRiskManager.evaluate(pkg)` runs the following checks in order
and returns `(allow: bool, reason: str | None)`:

1. **Smoke-test bypass** — `pkg.meta['is_test']=True` allows everything.
2. **Weekend** → `SKIP_WEEKEND_RESTRICTED` (Sat/Sun UTC).
3. **Overnight** → `SKIP_OVERNIGHT_RESTRICTED` (UTC hour in window).
4. **Mission complete** (evaluation only) → `SKIP_MISSION_MET`.
5. **Daily loss cap** → `DAILY_LOSS_CAP`.
6. **Position size cap** → `POSITION_SIZE_CAP`.
7. **Intra-day drawdown** → `INTRADAY_DRAWDOWN`.

The skip reason flows through `multi_account_execute`'s result row's
`error` field, so `/signals`, the diagnostic ping, and the trade
journal can distinguish a mission-aware skip from a true risk breach.

---

## Mission predicates

```python
profit_target_met = cumulative_pnl_pct >= target_profit_pct
active_days_met   = active_days >= min_active_days
mission_complete  = profit_target_met and active_days_met
```

Both predicates must be true to refuse a trade. If only one is true,
trades continue to flow (since the missing one still needs to be
satisfied to pass the evaluation).

---

## State persistence (phase-2b contract)

Live counters (`cumulative_pnl_pct`, `active_days`, `entry_date`)
persist across trader restarts via
`runtime_state/prop_state.json`. The file is the **live source of
truth** — `PropRiskManager.__init__` reads its per-account section
on every load, overriding the YAML `prop_state:` seed.
`PropRiskManager.record_trade_result(pnl_usd, …)` writes the updated
counters back to the file atomically (tmp + os.replace).

Resolution order on construction:

1. JSON file `runtime_state/prop_state.json` → wins if the file
   exists and contains a section keyed by the account name.
2. YAML `prop_state:` block → fallback seed for fresh installs and
   phase resets (delete the JSON section to fall back).
3. Defaults (`0.0` / `0` / `null`) when neither is set.

The file is gitignored (`runtime_state/`) so per-account counters
never land in commits. Tests can redirect via
`prop_state_io.set_prop_state_path(tmp_path)` or the
`PROP_STATE_PATH` env var.

Operator workflow:

1. After each trading day, check `/accounts_status` — the prop
   block shows `cumulative_pnl_pct` (vs target), `active_days` (vs
   `min_active_days`), and `mission_complete`.
2. To reset between phases (evaluation → funded onboarding), delete
   the account's section from `runtime_state/prop_state.json` (or
   the entire file). The next trader restart re-seeds from YAML.
3. Manual YAML edits to `prop_state:` are no longer required —
   the JSON file is canonical.

---

## Velotrade executor — phase-2 infrastructure

`exchange: velotrade` is registered in `EXCHANGE_MAP` (see
`src/units/accounts/integrator.py`) and dispatches live placement to
an injected `DXtradeClient`. The integration shape is real; only the
four DXtrade SDK method bodies (`place` / `cancel` / `status` /
`balance` in `src/units/accounts/dxtrade_client.py`) still raise
`NotImplementedError("DXtrade SDK contract pending — …")` until the
operator drops the API contract.

Routing layers:

- `src/units/accounts/dxtrade_client.py::DXtradeClient` — owns the
  SDK surface. Constructor validates non-empty creds; methods are
  stubs.
- `src/units/accounts/clients.py::velotrade_client_for(account)` —
  factory; returns `None` when env-var creds are missing, mirroring
  `bybit_client_for` / `binance_conn_for`.
- `src/units/accounts/integrator.py::VelotradeAPI.place` — accepts
  an injected client; bare class raises `MissingCredentialsError`
  for live placement without a client.
- `src/units/accounts/execute.py::_submit_order` velotrade branch —
  dispatches to the client, mirrors bybit's retCode-style error
  handling. No client injected → `MissingCredentialsError`.
- `src/core/coordinator.py::multi_account_execute` — its
  client-construction switch routes `exchange == "velotrade"`
  through `velotrade_client_for(account_cfg)`. Missing creds set
  `client_error` and skip the SDK call entirely; the diagnostic
  ping fires.

The legacy `breakout` exchange is kept in `EXCHANGE_MAP` as a
deprecated alias and `_submit_order` raises a clear "migrate to
velotrade" `RuntimeError` for it. New configs should target
`velotrade`.

## "Not fully configured" account state

Phase-2 introduces a generic mechanism: any account that loads
without its env-var credentials populated gets `configured=False`
on its `TradingAccount` instance. Such accounts:

- Still appear in `/accounts_status` (the operator can see them).
- Refuse live actions; the existing per-account "missing API creds"
  path in `multi_account_execute` is the same code path, with a
  clearer message ("account 'X' is not fully configured: …").
- Emit a `runtime_logs/pending_pings/` JSON via
  `enqueue_execution_failure` so the operator gets a Telegram alert
  the next bot tick.

This is what lets `prop_velotrade_1` ship enabled (no `enabled:
false` line) without opening a live-trading risk: the strategies
list is empty (per-account filter blocks routing), the creds are
absent (the not-configured gate refuses any action that bypasses
the filter), and the DXtrade SDK methods are stubs (the contract
hasn't landed). All four safety rails — process interlock, risk
manager, single live entry point, kill-switch — remain in force on
top.

### YAML contract

`enabled: false` (legacy) still hard-skips an account at load. New
accounts should omit `enabled` (or set `enabled: true`) and let the
loader decide configured/not-configured based on env vars.

---

## Operator checklist before enabling a Velotrade account

Phase-2 wired the integration infrastructure; what's left to take a
specific account live is filling in the SDK contract and provisioning
creds. The checklist:

1. Drop the DXtrade API contract under `docs/integrations/` and fill
   in the four `NotImplementedError` method bodies in
   `src/units/accounts/dxtrade_client.py` (`place`, `cancel`,
   `status`, `balance`). The integrator + executor branches already
   call into these methods through the same retCode-style shape as
   the bybit branch — no other code changes should be needed.
2. Provision `VELOTRADE_API_KEY_*` and the matching `_SECRET` env
   vars on the VM via `notebooks/operator/rotate_api_keys.ipynb`.
   Set `VELOTRADE_BASE_URL` (or `base_url:` on the YAML row) to the
   sandbox / prod URL from the contract.
3. Confirm `account_state`, `phase_requirements`, and
   `overnight_restricted` match the prop firm's terms.
4. Add the assigned strategies to `strategies: [...]` (currently
   empty as a belt-and-braces default).
5. Restart the trader. Confirm `/accounts_status` flips
   `configured` from False to True for the account, then run a smoke
   trade with `pkg.meta['is_test']=True` and qty below the DXtrade
   min-lot (the smoke-test bypass in `PropRiskManager.evaluate` skips
   the mission gate so the SDK call layer gets exercised end-to-end).
