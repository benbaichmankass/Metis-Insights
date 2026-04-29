# § 2 — Registry inventory

Every place strategies are registered, enumerated, or dispatched.

## 2.1 `src/strategy_registry.py` — YAML-driven, **canonical** (S-007)

99 LOC. Source of truth for service names, model paths, and signal prefixes.

**Public API:**
- `load_strategies(path) -> list[dict]` — read `config/strategies.yaml`,
  cache in module-global.
- `model_path(name) -> str | None` — resolve model artefact path.
- `service_name(name) -> str` — return the systemd unit stem
  (e.g. `"ict-trader-vwap"`).
- `signal_prefixes(name) -> list[str]` — DB attribution strings.

**Loaded entries (current):**

| name | service | model | enabled |
|---|---|---|---|
| `breakout_confirmation` | `ict-trader-breakout` | `btc_v1.joblib` | false |
| `vwap` | `ict-trader-vwap` | null | true |
| `killzone` | `ict-trader-live` | null | true |
| `ict` | `ict-trader-ict` | null | true |

**Imported by:** `tests/test_strategy_registry.py`. **Not imported by**
`src/main.py`, `src/runtime/pipeline.py`, or `src/core/coordinator.py`. The
registry exists but the live runtime does not consult it for dispatch — it
is metadata that other tooling (the Telegram bot, CI checks, the registry
test suite) reads. After S-012 it remains the canonical reader of
`config/strategies.yaml`; the `service:` field gets dropped when we go
single-process (PR D2).

## 2.2 `src/strategies_manager.py` — in-memory dict, **orphan**

29 LOC. Legacy in-memory registry pattern.

**Public API:**
- `register(name, strategy_class)`
- `list_strategies()`
- `get_signal(strategy_name, candles_df)`

**Default registration:** `_ensure_defaults()` hardcodes import of
`strategies.breakout_confirmation.BreakoutConfirmationStrategy:11`. If that
import fails it logs and continues with an empty registry.

**Consumers in repo:** only `src/units/strategies/breakout_confirmation.py:82-94`
(legacy ML model invocation path).

**Verdict:** **Delete in PR C5** along with the breakout strategy.

## 2.3 `src/units/strategies/__init__.py` — config loader

77 LOC. Not a registry — a YAML reader/writer for the per-strategy
parameter dict (risk_pct, timeframe, symbols, threshold, etc.).

**Public API:**
- `load_strategy_config(path) -> dict[str, dict]`
- `save_strategy_config(params, path) -> None`

**Used by:**
- `src/web/config_ui.py` (S-011 PR #144 Streamlit editor)
- `src/core/coordinator.py::reload_strategy_config()` (S-011 PR #4)

Stays. The S-011 contract is unchanged — only the **set of strategy keys**
in the YAML changes (turtle_soup + vwap only).

## 2.4 `src/units/__init__.py` — `load_enabled_units()`

(S-009 PR #133.) Reads `config/units.yaml::units.strategies[]`, filters by
`enabled: true`, returns the list. `Coordinator.reload_units()` consumes it.

Stays. The set of enabled entries narrows after PR B2.

## 2.5 `src/core/coordinator.py` — runtime dispatch

Two relevant methods:

- `strategy_order_pkg(strategy, symbol, candles_df) -> OrderPackage`
  (lines 124-178): dynamically imports `src.units.strategies.<strategy>`
  and calls `order_package(cfg, candles_df)`. Raises `NotImplementedError`
  if the module or function is missing.
- `list_strategies() -> list[dict]`: returns the strategies section of
  `config/units.yaml`.

The Coordinator does **not** consult `src/strategy_registry.py`. It treats
`units.yaml` as ground truth. This is the actual dispatch path.

## 2.6 Entrypoint → registry usage map

| Entrypoint | Registry consulted | How |
|---|---|---|
| `src/main.py` | none | reads env (`DRY_RUN`, `ALLOW_LIVE_TRADING`, `MODE`) and starts pipeline |
| `src/runtime/pipeline.py` | hardcoded `strategies.vwap_signal_builder` import (line 119) | bypasses the registry entirely; calls VWAP directly |
| `src/core/coordinator.py` | `config/units.yaml` via dynamic import | the actual dispatcher |
| `src/bot/telegram_query_bot.py` | `src/bot/data_loaders.list_accounts()` + `units.yaml` | reads `accounts[*].service` field |
| Tests of registry behaviour | `src/strategy_registry.py` | unit-level only |

## 2.7 Verdict

**Two registries today** (`strategy_registry.py` YAML + `strategies_manager.py`
in-memory). After S-012:

- `strategies_manager.py` deleted (PR C5).
- `strategy_registry.py` retained as the canonical reader of
  `config/strategies.yaml`. The `service:` field is dropped from the YAML
  in PR D2; `service_name()` and the wider mapping concept are removed
  with the same PR.
- `src/units/strategies/__init__.py` retained as the canonical reader of
  per-strategy parameters.
- `src/units/__init__.py::load_enabled_units()` retained as the canonical
  enumerator of enabled strategies.

DoD checkbox "Exactly one strategy registry exists" is satisfied by reading
the post-sprint state as: one registry source-of-truth (the YAMLs), one
parameter loader, one runtime dispatcher (Coordinator). The parallel
in-memory dict is gone.
