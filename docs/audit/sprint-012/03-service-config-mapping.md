# § 3 — Service ↔ config mapping

Cross-table: every strategy and every service file, with reality checks.

## 3.1 `config/strategies.yaml` (current)

Source: `config/strategies.yaml:12-55`.

| Strategy | service | model | signal_prefixes | enabled |
|---|---|---|---|---|
| `breakout_confirmation` | `ict-trader-breakout` | `btc_v1.joblib` | `[ml_breakout, breakout]` | false |
| `vwap` | `ict-trader-vwap` | null | `[vwap]` | **true** |
| `killzone` | `ict-trader-live` | null | `[killzone, trade_signal]` | **true** |
| `ict` | `ict-trader-ict` | null | `[fvg, ob, ict]` | **true** |

Note: `test_s007_strategy_registry` asserts ordering (ict last, killzone
before ict). When PR B1 rewrites this file, the order assertion either
moves to the new roster or is dropped.

## 3.2 `config/units.yaml` (current)

Source: `config/units.yaml:13-33` (strategies) and `36-41` (accounts).

| Strategy | service | enabled | listed in `accounts.live.strategies`? |
|---|---|---|---|
| `ict` | `ict-trader-ict` | true | yes |
| `vwap` | `ict-trader-vwap` | true | yes |
| `breakout_confirmation` | `ict-trader-breakout` | false | yes |
| `killzone` | `ict-trader-live` | true | yes |

The `live` account at `units.yaml:37-41` has
`strategies: [ict, vwap, breakout_confirmation, killzone]` — it explicitly
references all four including the disabled breakout.

## 3.3 `config/accounts.yaml` (current)

Source: `config/accounts.yaml:7-33`.

| Account | type | exchange | max_dd_pct | daily_usd | pos_size |
|---|---|---|---|---|---|
| `bybit_1` | regular | bybit | 0.05 | 100 | 500 |
| `bybit_2` | regular | bybit | 0.05 | 100 | 500 |
| `prop_breakout_1` | prop | breakout | 0.02 | 50 | 200 |

**No `strategies:` field per account here** (different file from the `live`
account in `units.yaml`). The account-id space in `accounts.yaml`
(`bybit_1`, `bybit_2`, `prop_breakout_1`) is **disjoint** from the
account-id space in `units.yaml` (`live`). PR B3 must reconcile this — see
§ 8 PM-decision item 3.

## 3.4 `deploy/` systemd files

| File | ExecStart |
|---|---|
| `ict-env-check.service` | `scripts/startup_env_check.py` |
| `ict-git-sync.service` + `.timer` | `scripts/deploy_pull_restart.sh` |
| `ict-heartbeat.service` + `.timer` | `scripts/daily_heartbeat.py` |
| `ict-telegram-bot.service` | `python3 -u -B -m src.bot.telegram_query_bot` |
| `ict-trader-live.service` | `python3 -u -B -m src.main` |

**Files NOT present** (but referenced in `strategies.yaml` / `units.yaml`):
`ict-trader-breakout.service`, `ict-trader-vwap.service`,
`ict-trader-ict.service`.

## 3.5 The drift table

| Strategy | service field | in strategies.yaml | in units.yaml | .service file in `deploy/` | Actually launched? | Status |
|---|---|---|---|---|---|---|
| `breakout_confirmation` | `ict-trader-breakout` | yes (false) | yes (false) | **no** | no — disabled | aspirational metadata |
| `vwap` | `ict-trader-vwap` | yes (true) | yes (true) | **no** | no — runs inside `ict-trader-live` | **DRIFT** |
| `killzone` | `ict-trader-live` | yes (true) | yes (true) | yes (shared) | yes — inside `ict-trader-live` | OK (intentional shared name) |
| `ict` | `ict-trader-ict` | yes (true) | yes (true) | **no** | no — runs inside `ict-trader-live` | **DRIFT** |

## 3.6 Architecture conclusion

The runtime reality is **single process, multi-strategy**: one
`ict-trader-live.service` runs `src/main.py`, which calls
`run_pipeline(...)` and dispatches to enabled strategies via
`Coordinator.strategy_order_pkg()`.

The per-strategy `service:` fields are **documentation that was never
realised**. They produced the symptom that triggered this sprint — anything
reading them and trying to `systemctl start <service>` fails because the
unit files don't exist.

Recommendation, formalised in PR C4 / D2: **drop the `service:` field
entirely from both YAMLs**, and remove the consumers
(`strategy_registry.service_name()`, any code that maps a strategy to a
unit). Keep `ict-trader-live.service` as the only trader-side unit.

This is **decision-request item #1** for the PM: confirm single-process
direction before D-phase ships.

## 3.7 Post-sprint shape

| YAML file | strategies entries | service field |
|---|---|---|
| `config/strategies.yaml` | `turtle_soup`, `vwap` (both `enabled: true`) | **removed** |
| `config/units.yaml` | same two | **removed** |
| `config/accounts.yaml` | bybit + prop accounts; per-account `strategies:` reconciled per § 8 item 3 | n/a |

| `deploy/` post-sprint | unchanged set: `ict-trader-live`, `ict-telegram-bot`, `ict-env-check`, `ict-git-sync`, `ict-heartbeat` |
| --- | --- |
