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

**Lifecycle of a new strategy** — governed by
[`docs/CLAUDE-RULES-CANONICAL.md`](../../../docs/CLAUDE-RULES-CANONICAL.md)
§ "Promotion evidence — **offline edge, live mechanics**" (binding; a
soak-doctrine CI guard enforces this). Read that rule before wiring anything.

The **edge is decided OFFLINE, before the soak** — a live-faithful backtest
(config-exact, live-exit-faithful) plus the backfill / live-simulator over deep
history must already show the leg meets our standard. That offline gate carries
the promote decision. THEN:

1. ship `enabled: true` + `execution: live` → route to **bybit_1 (demo)** /
   `ib_paper` (paper money — it EXECUTES there);
2. the paper run is a **MECHANICS check ONLY** — confirm the live executions
   match the simulator (entry vs the logged signal, fill/fee/slippage, SL/TP
   placement, whole-unit sizing). This needs **1–2 live executions** and
   accrues in **HOURS (not calendar time)**;
3. add to `bybit_2.strategies` (REAL money, Tier-3, operator-approved).

**A paper soak is never a calendar-time wait to re-establish the edge — the edge
was already decided offline. A handful of early losing paper trades is variance,
NOT a demotion signal, as long as the mechanics match.** If a leg reaches soak
without an adequate offline proof, the gap is the
missing backtest (go run the live-faithful backtest / backfill), not more soak
time. The only legitimate reason a soak blocks a promotion is a **mechanics
divergence** — the live executions don't match what the simulator produced (bad
fills, wrong sizing, SL/TP not placed) — which you fix, not wait out.

A strategy goes `shadow` ONLY with explicit operator permission — e.g. to log on
the REAL-money account before it executes there. This is the path
`trend_donchian` took (now `live` on bybit_2).

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

### 6b. Regime coverage — `config/regime_policy.yaml` (or the debt register)

**This is the step that was silently skipped for 35 of 39 live strategies**
(the roster grew 6 → 44 but the regime layer did not), so it is now a **hard CI
gate**: `scripts/check_strategy_coverage.py` (workflow `strategy-coverage-guard`)
FAILS the PR if a new `execution: live` strategy has neither a `regime_policy`
cell nor an explicit exemption. You cannot merge a new live strategy without
making a regime decision for it. Do ONE of:

1. **Author a real regime cell** in `config/regime_policy.yaml` for the
   strategy (Tier-3 — the OFF cells are backtest-gated; propose them in the
   `config/strategies.yaml` draft PR). This is the right answer for a directional
   trend/pullback strategy — decide, per regime, whether each direction should
   trade. Prefer a **direction-aware** cell (the 2026-07-16 root cause was that
   ADX measures trend *strength*, not *direction*, so long-only pullbacks fired
   into downtrends).
2. **Add a reasoned `exempt:` entry** in `config/regime_coverage_exemptions.yaml`
   *(Tier-1)* — only if regime gating genuinely does not apply (e.g. a
   market-neutral sleeve). Requires a `reason`.

You may **not** park a new strategy in `coverage_debt:` — that list is a
ratcheting-down register of the pre-guard grandfathered roster; the guard's
`debt_ceiling` blocks adding to it. Regenerate the matrix in the same PR:
`python scripts/check_strategy_coverage.py --matrix` and commit
`docs/strategy-coverage-matrix.md`.

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
4. **Mechanics check (HOURS, not calendar time).** Confirm the live executions
   mechanically match the simulator — **1–2 executed trades are enough** to
   verify entry-vs-signal, fill/fee/slippage, SL/TP placement, and whole-unit
   sizing parity. Per the canonical rule (§ "Promotion evidence — offline edge,
   live mechanics") the EDGE was already decided by the offline live-faithful
   backtest before this point, so this step does **not** wait for live
   performance to accrue; a couple of early losing paper trades are variance if
   the mechanics match. If the live executions DIVERGE from the simulator, fix
   that mechanics gap before promoting.
5. Promote `shadow → live`: flip `execution: live` in strategies.yaml
   and add the strategy to `bybit_2.strategies` (separate draft Tier-3
   PRs, operator-approved). Fire `pull-and-deploy`. The strategy now
   trades real money on the next tick.

(The legacy `/test <name>` flow is **GONE** — the M5 consumer was removed
2026-08-20 because it ran one hardcoded engine regardless of the strategy
named and wrote fabricated `0.0` metrics. Shipping `enabled: false` to keep a
strategy fully inert still works; get the evidence from the harnesses in the
`backtesting` skill or a trainer sweep. Prefer shadow either way: it gathers
the live comparison data that makes the go-live decision evidence-based.)

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
7. **Regime coverage decided (step 6b):** a `regime_policy.yaml` cell OR a
   reasoned `exempt` entry, with the `strategy-coverage-guard` CI check green
   and `docs/strategy-coverage-matrix.md` regenerated. The leg is NOT done
   until the guard passes for it — no new debt.

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

---

## Definition of done — a capability is not shipped until something RUNS it

*(Operator directive 2026-08-20, binding on every build skill: "we don't keep
building things out half way and then leaving them to rust while the system
chugs along with bad structure.")*

Merging is not shipping. Before you call any capability from this skill done,
all four must hold — and the ones you cannot satisfy get **said out loud**, not
left implied:

1. **A RUNNER exists.** A workflow, a systemd unit, a call site in `src/`, an
   entry in `run_guards.py`, or a documented cadence. A tool that is genuinely
   manual-only declares it in its own file:
   `# wiring: manual-only — <who runs it, when>`. Verify with
   **`python3 scripts/ci/check_unwired_artifacts.py`** — if your new file
   appears in its output, it is not done.
2. **A CONSUMER exists.** Anything the capability *writes* must be *read* by
   something that acts on it. A signal written and never read is worse than a
   missing one — reviewers see the field and assume something acts on it
   (`provenance-consumer-guard` exists for exactly this).
3. **A DETECTOR exists.** Something fails if this silently stops working. A
   test, a guard, an alert, or an invariant in
   `scripts/ops/system_invariants.py`. "We'll notice" is not a detector.
4. **It has been OBSERVED working on real data** — not only in a test. Cite the
   evidence (a diag pull, a log line, a row) or state plainly that it has not
   yet been observed and what would settle it.
5. **The LIVE environment matches the repo's declaration.** If your change adds
   or depends on an env var, a service, a timer, a path or a routing entry,
   **read it back from the VM** (`get-env`, `/api/diag/services`,
   `/api/bot/config`, the relay) and confirm the running value is the declared
   one. *"The repo says X"* is not evidence that the VM does X — the two drift,
   and this repo has the scars: a `FLIP_CONFIDENCE_THRESHOLD` running live for a
   day with no record behind it, a `DIAG_BASE_URL` still pointing at a VM
   terminated 2026-06-16 while the doc-coherence guard passed (it checks the
   docs, not the environment), and a `BYBIT_TPSL_MODE` "flip" that was a no-op
   re-assertion of a value already live.
6. **The change is CONCENTRATED.** Count the files you had to touch. If a
   *routine* addition of this kind cost more than the source-of-truth files plus
   tests and docs, say so — every hand-maintained registry you had to update in
   lockstep is a place the next person half-applies the change. Measured
   2026-08-20: wiring one strategy leg touched **17 files**, of which three were
   `src/` maps holding facts `strategies.yaml` already contains. **A file you
   edited only to keep a derived map in sync is a design finding, not a chore** —
   record it (audit skill § 3.7 MODULARITY) even when you cannot fix it here.

7. **A parameter shared with production has ONE definition, and you asserted
   it.** If your work reads a value that also lives in a config file —
   `risk_pct`, a fee, a cap, a threshold — do not re-derive its units. Import the
   resolver; if there is no resolver, that is the finding. Then state which
   branch you are on: **SWEEP** the parameter, or **FIX** it at the live value
   and assert that equality in the run's own output. A default that merely
   *looks* live is the failure. Measured 2026-08-20 (audit F-37..F-40):
   `accounts.yaml::risk_pct: 0.015` is a FRACTION while five research/prop files
   compute `rpct / 100.0` as a PERCENT, so `--risk-pct 0.015` means 1.5% in one
   research script and 0.015% in another — **100× apart under one flag name** —
   and every harness default sits **5×** below the live basis.
   ⚠️ **"It's R-normalized so risk doesn't matter" does NOT discharge this.**
   That claim assumes the trade SET is invariant to the parameter, and
   production quantizes: futures floor to whole contracts and **refuse
   sub-1-contract outright**, Alpaca floors to whole shares, `min_qty` and the
   margin cap bite. Below a threshold the trade does not shrink — it does not
   happen. Unless your harness models refusal, it cannot test its own
   independence premise, and it errs flatteringly (small risk reads as safe when
   it means the leg does not trade).

**The measured cost of skipping this:** 161 of 384 tools under `scripts/` have
no runner (2026-08-20). `scripts/ops/trainer_dataset_gc.py` — the retention
tool for a 12 G dataset tree — had no caller, no timer and **0 mentions across
7,442 cycle-log rows** while the disk it was written for reached **93 %**.
`exchange_fills_ib.closed_pnl_from_fills` has **zero production callers**, so
IBKR's own realized PnL is pulled hourly and never read. Every one was found by
accident, months later.

`/system-review` now enumerates everything shipped since the previous review and
grades each `running` / `wired_not_yet_exercised` / **`UNWIRED`** /
`unverifiable` (`review_coverage.since_last_build_verification`, enforced by
`render_system_report.py --strict`). **Your work will be graded against this
list.** Leave it wired, or leave it declared.
