---
name: new-strategy
description: Wiring checklist + scaffold for adding a new live trading strategy to the ICT bot. Use when the operator says "add a new strategy", "wire up <strategy-name>", "create a strategy adapter", or asks how to plug a strategy into the execution layer / intent multiplexer. Covers the strategy unit module, signal builder, intent-layer registration, risk allocation, YAML config, account routing, tests, and the activation gate. NOT for tuning an existing strategy's parameters — those are config-only edits to `config/strategies.yaml`.
---

# /new-strategy — wire a new trading strategy through the execution layer

The execution layer (intent multiplexer + delta-aware dispatcher,
S-MSE-1 / Phase 2) is fully strategy-agnostic. Adding a new strategy
is a wiring exercise: implement the signal logic in one place, then
register the strategy's name at five thin touch points so the
multiplexer, the dispatcher, the risk gate, and the audit log all
see it as a peer of Turtle Soup, VWAP, and ICT scalp.

If you find yourself editing `src/runtime/intents.py::aggregate_intents`,
`compute_execution_delta`, or `src/core/coordinator.py::multi_account_execute`
to make a new strategy work, **stop** — that's a sign the strategy is
trying to bypass an invariant. The right move is almost always to
adjust the strategy's intent fields (priority, target_qty) rather
than the aggregator.

## Inputs the operator should give you before starting

- **Strategy name** in `snake_case_with_timeframe`, e.g. `ict_scalp_5m`.
  Naming convention: append the primary timeframe so a future 1m
  variant slots in as a sibling block (`ict_scalp_1m`) without
  collision.
- **Signal logic** — at minimum: which timeframe, what fires the
  entry, how SL/TP are computed. If unclear, ask before coding.
- **Risk fraction** — multiplier on the account's `risk_pct`. Typical
  range 0.2 – 1.0; lower for scalps, higher for high-conviction setups.
- **Priority for conflict resolution** — integer; the existing roster
  uses turtle_soup=50, vwap=40, ict_scalp_5m=30. Higher wins ties.
- **Which accounts route this strategy** — for Bybit2 (the funded
  live linear-perp account), add the name to `bybit_2.strategies` in
  `config/accounts.yaml`. For dry-run-only smoke, add to `bybit_1` or
  leave off the list and use `STRATEGY=<name>` env override.

If any of these are missing, **ask first**. Do not invent values.

## Touch points (canonical wiring)

The order below matches the order you should edit. Land all steps
in a single PR — partial wiring leaves the strategy in a confusing
half-state. Keep the PR draft until the operator approves; activation
is the final, separate step.

### 1. Strategy module — `src/units/strategies/<name>.py`

The single home for the signal logic. Public surface:

```python
def order_package(cfg: dict, candles_df=None) -> dict
```

Returns a dict ready for the Coordinator's `OrderPackage` constructor:
`{symbol, direction, entry, sl, tp, confidence, meta}` (the
Coordinator inserts `strategy=<name>` itself). Use `src/units/strategies/_base.py`
helpers — `side_to_direction`, `derive_sl_tp`, `require_candles`,
`monitor_breakeven_sl` — for the boilerplate.

**Strategies are pure signal generators.** They have no knowledge of
accounts, dry/live mode, exchange clients, or order placement. Raise
`ValueError` for non-actionable ticks (the runtime builder catches it
and treats it as `side="none"`).

Reference implementations:
- `src/units/strategies/turtle_soup.py` — MTF sweep + reversal
- `src/units/strategies/vwap.py` — VWAP mean-reversion
- `src/units/strategies/ict_scalp.py` — sweep + displacement + FVG

### 2. Signal builder — `src/runtime/strategy_signal_builders.py`

Thin runtime wrapper that fetches candles for the strategy's
timeframe, calls `order_package()`, and maps the result into the
pipeline-shape signal dict. Always follow the existing pattern:

```python
def <name>_signal_builder(settings: dict) -> Dict[str, Any]:
    from src.units.strategies.<name> import order_package
    from src.units.strategies import load_strategy_config
    strategies_cfg = load_strategy_config() or {}
    cfg = strategies_cfg.get("<name>", {}) or {}
    # ... fetch candles via _build_killzone_exchange / fetch_candles ...
    # ... call order_package(cfg, candles_df=df) ...
    # ... return {"symbol", "side", "price", "stop_loss", "take_profit", "meta"} ...
```

Honour the `enabled: false` flag: when the YAML block has
`enabled=false`, return `side="none"` immediately so the builder
short-circuits and the strategy stays inert until the operator opts
in.

### 3. Pipeline registration — `src/runtime/pipeline.py`

Three edits in this file:

a) Import the new builder at the top of the file alongside the
   existing builders:

```python
from src.runtime.strategy_signal_builders import (
    ict_scalp_signal_builder,
    <new>_signal_builder,
    turtle_soup_signal_builder,
    vwap_signal_builder,
)
```

b) Add an entry to `_STRATEGY_BUILDERS` (drives the legacy
   first-wins multiplexer + the `STRATEGY=<name>` env override path):

```python
_STRATEGY_BUILDERS: Dict[str, Callable[[dict], Dict[str, Any]]] = {
    ...
    "<name>": <new>_signal_builder,
}
```

c) Add an entry to `STRATEGY_RISK_PCT` — the per-strategy MULTIPLIER on
   each account's `risk_pct` (NOT a 100% split). Match the value to
   the strategy's `risk_pct` field in `config/strategies.yaml` so the
   YAML and the dispatcher agree.

```python
STRATEGY_RISK_PCT: Dict[str, float] = {
    ...
    "<name>": <fraction>,  # e.g. 0.3 for a scalp
}
```

d) Optional — if the operator wants `STRATEGY=<name>` as a CLI alias,
   add an `elif` branch in `run_pipeline`:

```python
elif strategy_name in ("<name>", "<alias>"):
    builder = <new>_signal_builder
```

The intent multiplexer's `STRATEGY=multiplexed` (production default)
picks up the new strategy automatically once steps (b) and the YAML
are landed.

### 4. Intent-layer registration — `src/runtime/intent_multiplexer.py` + `src/runtime/intents.py`

a) **`src/runtime/intent_multiplexer.py::_default_intent_builders`** —
   add the builder so the intent multiplexer (the production path
   when `MULTI_STRATEGY_INTENT_LAYER=true`) can call it:

```python
def _default_intent_builders() -> Dict[str, IntentBuilder]:
    return {
        "turtle_soup": turtle_soup_signal_builder,
        "vwap":         vwap_signal_builder,
        "ict_scalp_5m": ict_scalp_signal_builder,
        "<name>":       <new>_signal_builder,
    }
```

b) **`src/runtime/intents.py::DEFAULT_PRIORITIES`** — add the
   conflict-resolution priority:

```python
DEFAULT_PRIORITIES: Dict[str, int] = {
    "turtle_soup":  50,
    "vwap":         40,
    "ict_scalp_5m": 30,
    "<name>":       <priority>,
}
```

Picking a priority: lower than the strategies whose signals the new
strategy should **lose** to in a conflict, higher than the ones it
should **win** over. Use the existing roster as anchors. Pick a
deliberately low value for an untested strategy so a wiring mistake
can't override Turtle Soup / VWAP at runtime.

The aggregator and delta computer in `intents.py` are unchanged —
the strategy plugs into the same `aggregate_intents()` and
`compute_execution_delta()` primitives via the registration above.

### 5. Strategy config — `config/strategies.yaml` *(Tier-3, draft PR)*

Add a `<name>:` block following the existing pattern:

```yaml
<name>:
  model: null
  signal_prefixes: [<token-that-prefixes-the-DB-signal_type>]
  enabled: false              # off by default; operator flips after backtest
  risk_pct: <fraction>        # matches STRATEGY_RISK_PCT entry
  timeframe: "5m"             # primary timeframe
  symbols:
    - BTCUSDT                 # currently the only supported symbol
  # ... strategy-specific parameters ...
  shadow_model_ids: []        # WS7 — empty until an approved model lands
```

`enabled: false` is mandatory until the strategy has a passing
backtest. The runtime builder must honour the flag (see step 2).

This is a **Tier-3** file per CLAUDE.md — open the PR as draft, ping
the operator. Never merge to main without explicit approval.

### 6. Account routing — `config/accounts.yaml` *(Tier-3, separate PR)*

Add the strategy name to the relevant account's `strategies:` list.
For Bybit2 (the funded live linear-perp account):

```yaml
bybit_2:
  strategies: [vwap, ict_scalp_5m, <new>]
```

This step **activates the strategy on the account**. Open as a
separate draft PR from the wiring PR so the activation is a clearly
distinguished commit the operator can revert with a single
`pull-and-deploy` if anything misbehaves.

Tier-3 file — same draft + operator-approval rule as step 5.

### 7. Tests — `tests/test_<name>.py` and the intent test files

a) **Strategy unit test** — `tests/test_<name>.py`. Verify
   `order_package()` produces the expected dict on a known input
   candle frame. Reference: `tests/test_s012_turtle_soup.py`,
   `tests/test_vwap_strategy.py`, `tests/test_ict_scalp_5m.py`.

b) **Intent-layer pluggability** is already covered generically by
   `tests/test_multi_strategy_intents.py::TestFutureStrategyPluggability`.
   You should not need to add a new test there — the existing tests
   prove the aggregator accepts any strategy name. If the new
   strategy has unusual priority semantics (e.g. dynamic priority
   based on confidence), add a focused test for that behaviour.

c) **End-to-end smoke** — at least one test that calls
   `multiplexed_intent_signal_builder(settings, builders={..., <name>:
   <new>_signal_builder})` with a fixture candle frame and asserts the
   resulting signal carries the new strategy's name in
   `meta.strategy_name`.

Run the full intent test suite as a regression gate before pushing:

```bash
pytest tests/test_multi_strategy_intents.py \
       tests/test_intent_delta_dispatch.py \
       tests/test_<name>.py
```

100+ passing means the wiring is sound.

### 8. Activation (after merge of the wiring PR)

1. Land the wiring PR (steps 1–4, 7).
2. Land the strategies.yaml PR with `enabled: false` (step 5).
3. Backtest the strategy (the M5 backtest consumer via
   `/test <name>` Telegram command — see
   `docs/runbooks/strategy-testing.md`).
4. Flip `enabled: true` in strategies.yaml (separate draft PR).
5. Add the strategy name to `bybit_2.strategies` in accounts.yaml
   (step 6, separate draft PR).
6. Fire `pull-and-deploy` once both Tier-3 PRs are merged. The
   trader picks the new strategy up on the next tick.

## Files you should NOT need to edit

- `src/runtime/intents.py::aggregate_intents` — strategy-agnostic.
- `src/runtime/intents.py::compute_execution_delta` — strategy-agnostic.
- `src/core/coordinator.py::multi_account_execute` — strategy-agnostic.
- `src/core/coordinator.py::_build_intent_legs` — strategy-agnostic.
- `src/units/accounts/risk.py::RiskManager` — strategy-agnostic
  (reads `meta["strategy_risk_pct"]`).
- `src/units/accounts/execute.py::execute_pkg` — strategy-agnostic.

If you're editing any of these, you're either fixing a bug in the
execution layer (a separate sprint) or you've taken a wrong turn.

## Single-symbol invariant (BTC/USDT)

`src/runtime/intents.py::SUPPORTED_SYMBOLS` is `{"BTCUSDT"}`. The
`StrategyIntent` constructor refuses other symbols at the type level.
Multi-symbol routing is a separate sprint; do not "fix" the
constructor or symbol filter to make a non-BTC strategy work. If the
new strategy must trade a different symbol, raise that with the
operator first — it requires per-symbol open-position state wiring
and is explicitly out of scope of the current execution layer.

## When you're done

Report back with:
1. The PR URL for the wiring (steps 1–4, 7).
2. The PR URL for `config/strategies.yaml` (step 5, draft, Tier-3).
3. Whether the strategy passed unit tests + the existing intent
   regression suite.
4. The recommended priority + risk_pct (justified against the
   existing roster).
5. The next-action checklist for the operator: backtest, then flip
   `enabled: true`, then add to accounts.yaml, then `pull-and-deploy`.

Do **not** open the accounts.yaml PR (step 6) until the operator has
explicitly authorized live activation.

## Worked example — ICT scalp 5m (PR #1140 + #1141)

Reference for what a complete new-strategy PR looks like:

- Strategy module: `src/units/strategies/ict_scalp.py`
- Signal builder: `ict_scalp_signal_builder` in
  `src/runtime/strategy_signal_builders.py`
- Pipeline registration: `_STRATEGY_BUILDERS` + `STRATEGY_RISK_PCT`
  in `src/runtime/pipeline.py` (entry: `"ict_scalp_5m": 0.3`)
- Intent builder registration:
  `src/runtime/intent_multiplexer.py::_default_intent_builders`
- Priority: `src/runtime/intents.py::DEFAULT_PRIORITIES`
  (`"ict_scalp_5m": 30`)
- Config: `config/strategies.yaml::ict_scalp_5m` block,
  `enabled: false`
- Tests: `tests/test_ict_scalp_5m.py`
- Docs: `docs/strategies/ict_scalp_5m.md`

Activation (steps 5 → 8) is operator-gated and pending the backtest
result at the time of this skill's introduction. The wiring itself
is fully landed — the strategy will flow through the same
intent → aggregator → delta → dispatch pipeline as Turtle Soup and
VWAP the moment `enabled: true` and `bybit_2.strategies` are flipped.
