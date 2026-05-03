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

## State persistence (v1 contract)

Live counters (`cumulative_pnl_pct`, `active_days`, `entry_date`)
are **in-process only** in v1. The YAML `prop_state:` block is the
**seed** loaded on each `load_accounts()` call.

`PropRiskManager.record_trade_result(pnl_usd, starting_equity_usd=…)`
updates the counters in-process; restarting the trader resets to the
YAML seed.

Operator workflow:
1. After each trading day, read the live counters via the bot
   (`/accounts_status` extended report; future PR).
2. Update the YAML `prop_state:` block manually before the next
   restart.
3. A follow-up sprint adds `runtime_state/prop_state.json`
   write-through so this becomes automatic.

---

## Velotrade executor

`exchange: velotrade` is registered in `EXCHANGE_MAP` (see
`src/units/accounts/integrator.py`) but is **dry-run only** in v1.
Both code paths refuse live placement:

- `VelotradeAPI.place(..., dry_run=False)` →
  `NotImplementedError`.
- `execute._submit_order` with `exchange == "velotrade"` →
  `RuntimeError`.

This preserves the live-by-default invariant for Bybit while making
any mis-routed Velotrade signal structurally inert until the DXtrade
SDK is wired in a follow-up sprint.

The legacy `breakout` exchange is kept in `EXCHANGE_MAP` as a
deprecated alias (same dry-run semantics) so old fixtures that still
reference it continue to load. New configs should target `velotrade`.

---

## Operator checklist before enabling a Velotrade account

1. Set the `VELOTRADE_API_KEY_*` env var on the VM.
2. Wire the live DXtrade SDK in:
   - `src/units/accounts/integrator.py::VelotradeAPI.place`
   - `src/units/accounts/execute.py::_submit_order` (`velotrade` branch)
3. Confirm `account_state`, `phase_requirements`, and
   `overnight_restricted` match the prop firm's contract.
4. Set `enabled: true` and add the assigned strategies to
   `strategies: [...]`.
5. Restart the trader. Confirm the new account appears in
   `/accounts_status` with `account_state` shown.
