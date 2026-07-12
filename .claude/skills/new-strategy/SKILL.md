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
see it as a peer of the current roster (turtle_soup, vwap,
ict_scalp_5m, trend_donchian, fade_breakout_4h; squeeze_breakout_4h
pending merge).

**S9 shadow-first path (2026-05-24).** Since the per-strategy
`execution: live|shadow` gate landed, a new strategy MAY ship
`enabled: true` + `execution: shadow` — it RUNS and LOGS its order
packages on every tick (live data collection) but never sends a live
order. Shadow lets the strategy prove itself on live data at zero
real-money risk, then graduates `shadow → live`. See "The execution
gate" below; `fade_breakout_4h` / `squeeze_breakout_4h` are the worked
examples.

> **⚠️ AMENDMENT (operator directive 2026-06-02). Two hard rules — both
> CI-enforced by the `dry-run-guard` (`scripts/check_dry_run_in_diff.py`):**
>
> 1. **Never set `execution: shadow` (or `mode: dry_run`) without EXPLICIT
>    operator permission.** Shadow is a demotion out of live execution; it
>    is not a safe default you reach for autonomously. A PR that adds
>    `execution: shadow` FAILS CI unless that line carries an inline
>    `# shadow-guard: allow — <reason>` marker recording the operator's
>    approval. Default any new strategy to `execution: live`; only drop to
>    shadow when the operator asks for it.
> 2. **Paper/demo accounts always EXECUTE.** A strategy routed ONLY to
>    paper/demo accounts (`ib_paper` IBKR paper, `bybit_1` Bybit demo)
>    must ship `execution: live` — paper accounts exist precisely to TEST
>    strategies by trading them. Shipping such a strategy `shadow` strands
>    it (signals, no trades) and defeats the account's purpose — this is
>    the exact bug that left the MES sleeve dark on `ib_paper`. "Collect
>    live data first" is satisfied by REAL paper execution, not shadow
>    logging, when no real money is at risk.

If you find yourself editing `src/runtime/intents.py::aggregate_intents`,
`compute_execution_delta`, or `src/core/coordinator.py::multi_account_execute`
to make a new strategy work, **stop** — that's a sign the strategy is
trying to bypass an invariant. The right move is almost always to
adjust the strategy's intent fields (priority, target_qty) rather
than the aggregator.

## MANDATORY: per-account compatibility before routing

A new strategy is not "done" when it runs — it's done when you know WHICH
accounts it belongs on. Before routing it to any account (and as part of the PR),
run the per-account compatibility matrix (see the `backtesting` skill +
`docs/integrations/prop-accounts-architecture-DESIGN.md`):

```bash
python scripts/prop/account_compat_matrix.py --strategy <name> --data <feed>
```

Route the strategy only to accounts whose row verdict is **ROUTE** (prop: +EV
under the firm ruleset; standard: positive net performance). For a prop account,
the live route is Tier-3 and additionally requires revalidation on the account's
**real venue data** + operator approval. Prop accounts that route the same
signal aggregate into ONE per-account ticket (`src.prop.multi_account_ticket`)
with a discrepancy banner — never assume a single account.

## Inputs the operator should give you before starting

- **Strategy name** in `snake_case_with_timeframe`, e.g. `ict_scalp_5m`.
  Naming convention: append the primary timeframe so a future 1m
  variant slots in as a sibling block (`ict_scalp_1m`) without
  collision.
- **Signal logic** — at minimum: which timeframe, what fires the
  entry, how SL/TP are computed. If unclear, ask before coding.
- **Risk** — DO NOT set a per-strategy risk level. Removed 2026-06-29: a
  strategy carries no risk; sizing is the RiskManager's sole job (the
  account-level `risk_pct` basis × an internal confidence scalar). Adding a
  `risk_pct:` to `config/strategies.yaml` (or a `strategy_risk_pct` in `src/`)
  trips the `strategy-risk-guard` CI check. Trade-level differentiation is via
  the order package's `confidence`, which the RiskManager modulates centrally.
- **Priority for conflict resolution** — integer; the current roster
  uses turtle_soup=50, vwap=40, ict_scalp_5m=30, trend_donchian=20,
  fade_breakout_4h=10 (squeeze_breakout_4h=5, pending merge). Higher
  wins ties. Pick a deliberately low value for an untested strategy so
  a wiring slip can't let it override an established member.
- **Execution mode** — `live` or `shadow` (S9 per-strategy gate).
  Default to `live`. Only ship `shadow` with EXPLICIT operator
  permission (and an inline `# shadow-guard: allow — <reason>` marker so
  CI passes — see the amendment above). A strategy routed only to
  paper/demo accounts MUST be `live`. See "The execution gate" below.
- **Which accounts route this strategy** — a strategy validated only on
  backtest goes to **bybit_1 (demo) first**. On the demo account it runs
  `execution: live` (paper money — it executes, which is the point of a
  demo account). Adding it to `bybit_2.strategies` (the funded live
  linear-perp account) is the REAL-money activation and is the
  operator-gated Tier-3 step; a strategy may legitimately run `shadow` on
  the real account first (with the operator's `shadow-guard: allow`
  marker) to collect real-money-context data before it executes there.

If any of these are missing, **ask first**. Do not invent values.

## The execution gate (`execution: live | shadow`) — S9

Two declared, default-permissive execution gates govern whether an
enabled strategy actually trades (see CLAUDE.md § Prime Directive):

- **Per-account** — `config/accounts.yaml::mode: live | dry_run`
  (operator-controlled via `set-account-mode`).
- **Per-strategy** — `config/strategies.yaml::execution: live | shadow`.
  `live` (default) = eligible to execute on accounts that route it.
  `shadow` = runs + LOGS order packages everywhere (data collection)
  but never sends a live order (treated as dry on every account).

The strategy itself stays a pure signal generator and knows nothing
about the gate — `execution: shadow` is enforced in
`Coordinator.multi_account_execute`, folded into the same
`effective_dry` resolution as `mode:` (it reuses the dry-run
short-circuit; no new order path). The gate **fails OPEN on a
registry-read error** (treats the strategy as shadow / dry), which is
why a shadow strategy's safe home is **bybit_1 (demo)**.

**Lifecycle of a new strategy:** ship `enabled: true` + `execution:
live` → route to **bybit_1 (demo)** / `ib_paper` (paper money — it
EXECUTES there to build a real-fill track record) → let live paper data
confirm the backtest (days–weeks) → add to `bybit_2.strategies` (REAL
money, Tier-3, operator-approved). A strategy goes `shadow` ONLY with
explicit operator permission — e.g. to log on the REAL-money account
before it executes there. This is the path `trend_donchian` took (now
`live` on bybit_2).

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

### 1b. Live-trade monitor — `def monitor(cfg, candles_df, open_pkg)`

A strategy **owns the trade it opens**. The same module MUST expose a
module-level `monitor(cfg, candles_df, open_pkg)` — the order-monitor
calls it once per tick while the trade is open
(`src/runtime/order_monitor.py::_call_strategy_monitor`) to get the
strategy's live-management **verdict**. Without it the position runs
blind on the static entry SL/TP backstop alone (the orphan-MHG gap), and
the CI guard `tests/test_strategy_monitor_unit_resolution.py` fails.

`monitor()` returns a **schema-valid verdict** — see the canonical schema
+ validator in `src/runtime/strategy_verdict.py` (`validate_verdict`).
A verdict is exactly one of:

- `None` — no action this tick (the common case; always valid).
- `{"sl": <positive float>}` — move the live stop-loss to this price.
- `{"tp": <positive float>}` — move the live take-profit to this price.
- `{"action": "close", "reason": <str>, ...}` — close now. Optional:
  `"close_qty_pct"` in `(0, 1]` for a partial scale-out (omitted/`1.0` =
  full close), `"exit_price"` (positive float, the decided price), and
  `"next_tp"` (positive float, the rolled-forward TP for the runner after
  a partial). `sl`/`tp` adjust keys and `action` are **mutually
  exclusive** — a verdict either adjusts or closes, never both.

For the standard "trail SL to break-even after 1R" rule, delegate to
`_base.monitor_breakeven_sl(open_pkg, candles_df, one_r_threshold=..., be_offset_bps=...)`
(returns `{"sl": ...}` or `None`) and layer any earlier exit checks
(SL/TP-cross close, time-decay, partial-roll) on top — the pattern in
`trend_donchian` / `fade_breakout_4h` / `turtle_soup`. `monitor()` must
**never raise** (the order-monitor catches and treats a raise as a blind
tick); on bad/missing candles return `None`.

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

c) NOTHING to add for risk. The per-strategy `STRATEGY_RISK_PCT` map was
   removed 2026-06-29 — a strategy carries no risk level. Position sizing is
   the RiskManager's sole responsibility: the account-level `risk_pct` basis
   (uniform 1.5%) × an internal confidence scalar driven by the order
   package's `confidence`. Do NOT add a `risk_pct:` to the strategy's YAML or
   a `strategy_risk_pct` anywhere in `src/` — the `strategy-risk-guard` CI
   check fails the PR if you do.

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
    "turtle_soup":      50,
    "vwap":             40,
    "ict_scalp_5m":     30,
    "trend_donchian":   20,
    "fade_breakout_4h": 10,
    "<name>":           <priority>,
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
  enabled: true               # S9 shadow-first: ship enabled so the
                              # strategy RUNS and collects live data…
  execution: shadow           # …but data-only — logs order packages
                              # everywhere, never sends a live order.
                              # Promote to `live` after shadow proves it.
  # NO risk_pct — removed 2026-06-29; sizing is account-level (RiskManager
  # basis × confidence). Adding it here trips the strategy-risk-guard.
  timeframe: "5m"             # primary timeframe
  symbols:
    - BTCUSDT                 # the instrument(s) this strategy trades —
                              # load-bearing: a strategy only evaluates/emits
                              # on its declared symbols (per-strategy scope).
                              # Supported: BTCUSDT, MES, MGC, MHG.
  # ... strategy-specific parameters ...
  shadow_model_ids: []        # keep a fresh data-collector's signal log
                              # clean of ML predictions until it has a
                              # track record
```

**S9 path:** ship `enabled: true` + `execution: shadow`. The strategy
runs and logs on live ticks immediately but never risks money; you
promote `shadow → live` only after the live shadow data confirms the
backtest. (The legacy `enabled: false` "fully inert" pattern is still
valid if you want zero signals/logging, but shadow is preferred — it
collects the comparison data that justifies the eventual go-live.) The
runtime builder honours `enabled` as the single source of truth (see
step 2); `execution` is read from the registry and enforced in the
coordinator.

This is a **Tier-3** file per CLAUDE.md — open the PR as draft, ping
the operator. Never merge to main without explicit approval.

### 6. Description — `config/strategy_descriptions.json` *(Tier-1)*

Every strategy MUST carry a human-readable description so the dashboard
Strategies page (and anyone reading the API) can explain what it does.
Descriptions live in `config/strategy_descriptions.json` — a sibling of
`config/strategy_changelog.json`, deliberately OUTSIDE the Tier-3
`config/strategies.yaml` so prose metadata is a Tier-1 edit that doesn't
gate on strategy-logic approval. The `/api/bot/strategies` endpoint reads
this file (`src/web/api/routers/strategies.py::_load_descriptions`); there
is **no hardcoded fallback** — a strategy missing here renders with an
empty description.

Add a block keyed by the strategy name:

```json
{
  "<name>": {
    "short": "One-line summary — what it trades + timeframe + symbol.",
    "how_it_works": "2-4 sentences: entry trigger, stop, profit-exit, any HTF/regime gate, and the per-trade risk."
  }
}
```

Write the `how_it_works` from the same facts you put in the strategy
module and the `config/strategies.yaml` comment block — entry trigger,
SL/TP rule, gates, risk_pct. Keep it accurate to the *current* config.

**Updating on changes:** whenever a later PR changes how the strategy
behaves (a new gate, a different exit, a timeframe migration, a risk
change), update this `how_it_works` in the SAME PR so the description
never drifts from the live behaviour — and add the matching
`config/strategy_changelog.json` entry. The description is the "what it
does now"; the changelog is the "how it got here".

### 7. Account routing — `config/accounts.yaml` *(Tier-3, separate PR)*

Add the strategy name to the relevant account's `strategies:` list.

**Shadow strategy (the S9 default for a new member) → bybit_1 (demo):**

```yaml
bybit_1:
  strategies: [turtle_soup, vwap, ict_scalp_5m, fade_breakout_4h, <new>]
```

Routing a `execution: shadow` strategy to bybit_1 (demo) begins shadow
data collection at zero risk — the gate keeps it data-only on every
account, and demo is the safe home since the gate fails open on a
registry-read error.

**Live strategy (after shadow proves the edge) → bybit_2 (real money):**

```yaml
bybit_2:
  strategies: [trend_donchian, <proven-new>]
```

Adding a `execution: live` strategy to bybit_2 is the **live
activation**. Open as a separate draft PR from the wiring PR so the
activation is a clearly distinguished commit the operator can revert
with a single `pull-and-deploy` if anything misbehaves.

Tier-3 file — same draft + operator-approval rule as step 5. Do not
open the bybit_2 (live) routing PR until the operator has explicitly
authorized live activation.

### 8. Tests — `tests/test_<name>.py` and the intent test files

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

### 9. Activation — the shadow-first path (S9)

1. Land the wiring PR (steps 1–4, 6, 8).
2. Land the strategies.yaml PR with `enabled: true` + `execution:
   shadow` (step 5). A passing offline backtest + an audit doc under
   `docs/audits/` should already justify this — you don't ship a new
   signal even to shadow without evidence.
3. Land the bybit_1 (demo) routing PR (step 7) and fire
   `pull-and-deploy`. The strategy now RUNS + LOGS order packages on
   live ticks (data collection) without risking money. Confirm
   `<name>_eval` rows in the audit log and the coordinator logging
   `execution:shadow … NOT executing`.
4. Let the live shadow data mature (days–weeks); confirm the live
   signals match the backtest.
5. Promote `shadow → live`: flip `execution: live` in strategies.yaml
   and add the strategy to `bybit_2.strategies` (separate draft Tier-3
   PRs, operator-approved). Fire `pull-and-deploy`. The strategy now
   trades real money on the next tick.

(The legacy flow — ship `enabled: false`, backtest via `/test <name>`
per `docs/runbooks/strategy-testing.md`, then flip `enabled: true` —
still works for a strategy you want fully inert first. Prefer shadow:
it gathers the live comparison data that makes the go-live decision
evidence-based.)

## Files you should NOT need to edit

- `src/runtime/intents.py::aggregate_intents` — strategy-agnostic.
- `src/runtime/intents.py::compute_execution_delta` — strategy-agnostic.
- `src/core/coordinator.py::multi_account_execute` — strategy-agnostic.
- `src/core/coordinator.py::_build_intent_legs` — strategy-agnostic.
- `src/units/accounts/risk.py::RiskManager` — strategy-agnostic; sizes off
  the account `risk_pct` basis × an internal confidence scalar (no
  per-strategy risk input as of 2026-06-29).
- `src/units/accounts/execute.py::execute_pkg` — strategy-agnostic.

If you're editing any of these, you're either fixing a bug in the
execution layer (a separate sprint) or you've taken a wrong turn.

## Multi-symbol support + the per-strategy symbol scope

Intent-layer symbol validation is **config-driven** (PR #3358,
2026-06-11): `StrategyIntent` validates through
`src/runtime/intents.py::supported_symbols()`, which unions the static
base `SUPPORTED_SYMBOLS` (`{"BTCUSDT", "MES", "MGC", "MHG"}`) with every
symbol declared in the `symbols:` list of an account in
`config/accounts.yaml`. Per-symbol open-position state is wired (the
aggregator/delta + the strategy-monocle open-package gates are
symbol-scoped). To add a brand-new symbol you do **NOT** edit
intents.py — declare it on the account that trades it in
`config/accounts.yaml`, add the `config/instruments.yaml` profile
(exchange routing), and — for an IB futures symbol — a `ContFuture`
branch in `src/units/accounts/ib_client._build_contract`. See the
`mgc_pullback_1d` / `mhg_pullback_1d` wiring (PR #2634) for a worked
non-BTC example cloned from the `mes_trend_long_1d` sleeve (its
`SUPPORTED_SYMBOLS +=` step is the part #3358 made obsolete).

**Per-strategy symbol scope (2026-06-02, PR #2643).** A strategy
evaluates/emits ONLY on the symbols it declares in `config/strategies.yaml
::symbols:` — `intent_multiplexer._collect_intents` skips a strategy whose
declared symbols don't include the current tick symbol (permissive when a
strategy declares no `symbols`). So `mgc_pullback_1d` (symbols `[MGC]`)
never runs on MES/BTC, and a BTCUSDT-only strategy never runs on a metal.
Set each new strategy's `symbols:` to exactly the instrument(s) it should
trade — that field is now load-bearing, not just metadata. Do NOT widen an
account's symbol list expecting a strategy to stay scoped by anything
other than its own `symbols:`.

## When you're done

Report back with:
1. The PR URL for the wiring (steps 1–4, 6, 8).
2. The PR URL for `config/strategies.yaml` (step 5, draft, Tier-3).
3. Whether the strategy passed unit tests + the existing intent
   regression suite.
4. The recommended priority + risk_pct (justified against the
   existing roster).
5. The next-action checklist for the operator: backtest, then flip
   `enabled: true`, then add to accounts.yaml, then `pull-and-deploy`.
6. A `pending` row for the new leg added to the **exit-refinement
   coverage matrix** (`docs/research/exit-refinement-coverage.json`) in
   the wiring PR — every new strategy gets exit-processed via the
   `exit-refinement` skill; the leg isn't finished until its exit-lever
   columns carry verdicts (M20 system, operator directive 2026-07-12).

Do **not** open the accounts.yaml PR (step 7) until the operator has
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

Activation (steps 5 → 9) is operator-gated and pending the backtest
result at the time of this skill's introduction. The wiring itself
is fully landed — the strategy will flow through the same
intent → aggregator → delta → dispatch pipeline as Turtle Soup and
VWAP the moment `enabled: true` and `bybit_2.strategies` are flipped.

## Worked example — shadow-first (fade_breakout_4h, S9 PRs #1884 + #1885)

Reference for the S9 `execution: shadow` data-collector path — what a
new member looks like before it has earned real money:

- Strategy module: `src/units/strategies/fade_breakout_4h.py` (+ the
  shared Chandelier `monitor()`)
- Signal builder: `fade_breakout_4h_signal_builder` in
  `src/runtime/strategy_signal_builders.py`
- Pipeline + intent registration: `_STRATEGY_BUILDERS` /
  `STRATEGY_RISK_PCT` (pipeline), `_default_intent_builders`
  (multiplexer), `DEFAULT_PRIORITIES` (`fade_breakout_4h: 10`)
- Config: `config/strategies.yaml::fade_breakout_4h` block —
  `enabled: true` / `execution: shadow` / `shadow_model_ids: []`
- Routing: `bybit_1.strategies` (demo) — NOT bybit_2 (PR #1885)
- Tests: `tests/test_fade_breakout_4h.py` + roster-pin bumps
- Evidence: `docs/audits/fade-breakout-complement-2026-05-24.md`

`squeeze_breakout_4h` (PRs #1907 + #1908) is the same flow,
priority 5. Both are `shadow` data-collectors on bybit_1; neither
sends a live order. Promotion to `execution: live` + `bybit_2` is a
later Tier-3, operator-approved step once the live shadow data
confirms the backtest — see the single-account decider design in
`docs/sprint-plans/DECIDER-SINGLE-ACCOUNT-2026-05-24.md`.
