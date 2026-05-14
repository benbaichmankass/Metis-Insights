# Sprint Log: S-MSE-1

> **Multi-Strategy Execution (Phase 1) — Intent layer + delta-aware dispatch for
> Bybit2 / BTC/USDT.**

## Date Range
- Start: 2026-05-14
- End:   2026-05-14

## Objective
- **Primary goal:** Add a scalable multi-strategy execution structure so Turtle
  Soup and VWAP can run simultaneously on Bybit2 / BTC/USDT through one shared
  execution layer, without double-counting exposure or flipping
  non-deterministically on conflicts.
- **Secondary goals:**
  - Keep the architecture extensible so future strategies (e.g. ICT scalping)
    plug into the same interface with no aggregator changes.
  - Maintain the existing per-account `RiskManager` as the single sizing /
    cap-enforcement site — the new layer must never bypass it.
  - Default the new code path **off** so the merge itself doesn't change live
    behaviour; flip the flag explicitly via a deploy PR after operator approval.

## Tier
- **Tier 3** (live order code: `src/runtime/pipeline.py`,
  `src/core/coordinator.py`, `deploy/ict-trader-live.service`).
- **Justification:** changes touch the strategy-multiplexer + order-dispatch
  hot path. Each PR opened as draft + merged after explicit operator approval
  per CLAUDE.md Tier-3 approval-gated contract.

## Starting Context
- **Active roadmap items:** none directly — surfaced as an operator request to
  run Turtle Soup + VWAP simultaneously on the one funded live account
  (Bybit2, linear perpetuals, 3× leverage).
- **Prior sprint reference:** Pre-this-sprint `multiplexed_signal_builder`
  (PR #1112) was strictly first-wins; the dispatcher's `_has_open_position`
  guard (PR #1100) blocked any subsequent strategy on the same
  `(account, symbol)` for the lifetime of the open trade.
- **Known risks at start:**
  - Touching the order path on the only funded live account.
  - The legacy first-wins multiplexer needed to keep working unchanged so the
    default-off rollout could survive any unexpected fault in the new layer.
  - Bybit-side reduce-only / flip semantics are non-trivial — deferred to
    Phase 2 rather than wired half-way.

## Repo State Checked
- **Branch reviewed:** `main` at `eacb751` (pre-sprint) → `778dbf5` (post #1129)
  → final post-#1130 commit captured below.
- **Deployment state reviewed:** `deploy/ict-trader-live.service` env block.
- **Canonical docs reviewed:** `CLAUDE.md`, `docs/CLAUDE-RULES-CANONICAL.md` §
  Prime Directive + Tier-3 contract.

## Files and Systems Inspected
- **Code files inspected:**
  - `src/runtime/pipeline.py` — strategy selection + multiplexer wiring.
  - `src/core/coordinator.py::multi_account_execute` — order dispatch loop
    including the `_has_open_position` binary guard.
  - `src/runtime/strategy_signal_builders.py` — Turtle Soup + VWAP builder
    surface.
  - `src/units/strategies/_base.py` + `turtle_soup.py` + `vwap.py` — strategy
    purity contract.
  - `src/units/accounts/risk.py::RiskManager.position_size` — single sizing
    site (S-026 G2).
  - `src/units/accounts/execute.py::execute_pkg` — exchange-side dispatch.
  - `src/units/db/database.py` — `trades` table schema (status, direction,
    position_size).
  - `src/runtime/order_monitor.py` — open-trade reconciler queries reused as
    a reference for the position helper.
- **Config files inspected:** `config/strategies.yaml`, `config/accounts.yaml`,
  `config/account_state.yaml` (Bybit2 = live, BTCUSDT, linear perp, 3×).
- **Deployment files inspected:** `deploy/ict-trader-live.service`.

## Work Completed
- **#1125 — Strategy-intent layer (foundation).** New
  `src/runtime/intents.py` with `StrategyIntent` / `DesiredPosition` /
  `ExecutionDelta` dataclasses, pure `aggregate_intents()` (same-direction
  reinforcement picks larger valid target; conflicts resolve deterministically
  by priority → earliest timestamp → strategy-name alphabetical), pure
  `compute_execution_delta()` (six action types: `noop` / `open` / `increase`
  / `reduce` / `close` / `flip`). New `src/runtime/intent_multiplexer.py` with
  `multiplexed_intent_signal_builder()` and `register_intent_builder()` (plug-
  in hook for future strategies). `src/runtime/pipeline.py` adds an opt-in
  branch gated on `MULTI_STRATEGY_INTENT_LAYER` env var; default off.
  `tests/test_multi_strategy_intents.py` — **42 cases** covering netting,
  conflict resolution, delta math, risk-cap non-bypass, future-strategy
  plug-in.

- **#1129 — Wire `compute_execution_delta` into `multi_account_execute`.**
  New `src/runtime/positions.py::current_net_position_qty(account, symbol)` —
  signed net position from the trade journal (longs positive, shorts negative).
  Best-effort: missing DB or read failure returns 0.0 (safe fall-through).
  `src/runtime/intents.py` gains `INTENT_MODE_META_KEY` /
  `INTENT_MODE_META_VALUE` sentinel + `package_is_intent_mode()` detector +
  `compute_execution_delta_for_package()` bridge (effective target =
  `min(aggregated_target_qty, risk_sized_qty)`). `multi_account_execute`
  intent-mode branch: `noop`/`open`/`increase` proceed with delta-sized qty;
  `reduce`/`close`/`flip` refused v1 with `intent_<action>_not_yet_wired_v1`;
  sub-`min_qty` delta becomes `noop` to avoid dust orders. Delta + reason
  stamped on `pkg.meta["execution_delta"]`. Non-intent (legacy) packages
  keep the binary `_has_open_position` block — verified by
  `test_legacy_mode_still_uses_binary_open_guard`. New test file
  `tests/test_intent_delta_dispatch.py` — **24 cases**.

- **#1130 — Enable `MULTI_STRATEGY_INTENT_LAYER=true` on the live trader.**
  One-line `Environment=MULTI_STRATEGY_INTENT_LAYER=true` added to
  `deploy/ict-trader-live.service`. Default in
  `src/runtime/intent_multiplexer.py::intent_multiplexer_enabled()` stays
  false — pinning here is the explicit live opt-in. `pull-and-deploy`
  installs the new unit file + restarts `ict-trader-live.service` on the
  live VM.

## Validation Performed
- **Tests run:** `pytest tests/test_multi_strategy_intents.py
  tests/test_intent_delta_dispatch.py tests/test_s012_pipeline.py
  tests/test_s007_pipeline_rewire.py tests/test_pipeline_refusal_cooldown.py
  tests/test_strategy_registry.py tests/test_s029_pr1_account_strategy_filter.py`
  → 116 passed, 0 regressions from the changed modules.
- **Total new tests:** 66 (42 in #1125 + 24 in #1129).
- **CI:** all 8 PR gates green on #1125, #1129, #1130 (`arch-doc-guard`,
  `pytest-collect`, `secret-scan`, `silent-empty-guard`, `ruff-lint`,
  `repo-inventory`, `env-gate-guard`, `dry-run-guard`).
- **Lint:** `ruff check src/runtime/intents.py src/runtime/intent_multiplexer.py
  src/runtime/positions.py src/core/coordinator.py tests/...` → clean.
- **Baseline regression check:** 13 pre-existing test failures on `main` reference
  removed pipeline attributes — unchanged after this sprint, no new failures.
- **Live verification:** trader restarted via `pull-and-deploy` post-#1130
  merge; runtime status confirmed via `/api/diag/status`.

## Documentation Updated
- **Sprint log:** this file (`docs/sprint-logs/S-MSE-1.md`).
- **Roadmap:** appended an entry to `ROADMAP.md` § Historical Sprint Ledger.
- **Module docstrings:** every new module (`intents.py`,
  `intent_multiplexer.py`, `positions.py`) carries a long-form docstring
  documenting scope (BTC/USDT only), strategies in scope, risk-layer
  invariant, and the future-strategy plug-in pattern.
- **Systemd unit comment:** the new `Environment=MULTI_STRATEGY_INTENT_LAYER`
  line in `deploy/ict-trader-live.service` carries a block comment explaining
  the flag, the revert path, and the link back to PRs #1125 + #1129.

## Contradictions or Drift Found
- None.

## Risks and Follow-Ups
- **Reduce / close / flip wiring (Phase 2).** Same-direction reinforcement is
  live; opposite-side delta actions are refused with
  `intent_<action>_not_yet_wired_v1`. Needs reduce-only flag plumbed through
  `execute_pkg` and the per-account close path. Operator can authorise a
  follow-up PR once Phase 1 has a track record on the live VM.
- **Exchange-side reconciliation.** `current_net_position_qty` reads the trade
  journal, which is what the existing `_has_open_position` guard also treats
  as truth. Cross-VM exchange reconciliation already lives in
  `src/runtime/order_monitor.py` (the `MONITOR_RECONCILE_ENABLED=true` path).
  Both pulling from the same source keeps the intent-mode delta consistent
  with what the legacy block was already doing.
- **Multi-symbol expansion.** `SUPPORTED_SYMBOLS = {"BTCUSDT"}` is enforced
  at the `StrategyIntent` constructor. Adding a symbol requires both an
  append to that frozenset AND wiring per-symbol open-position state —
  intentionally out of scope until at least one more symbol is funded.

## Deferred Items
- **ICT scalping strategy implementation.** The scaffolding is ready
  (`register_intent_builder()` + `DEFAULT_PRIORITIES` row). Strategy
  implementation itself is a separate sprint.
- **Phase 2 — reduce/close/flip delta legs.**
- **Cross-symbol netting / multi-account portfolio logic.** Out of scope until
  a second symbol is on a funded live account.

## Next Recommended Sprint
- **Suggested next sprint:** Phase 2 — wire reduce-only orders into
  `execute_pkg` and the per-account close path, then unblock `reduce` /
  `close` / `flip` deltas. Should be paired with a Bybit-side dry-run on the
  reduce flag to confirm the executor handles partial fills cleanly.
- **Why next:** Phase 1 leaves opposite-side delta actions refused at the
  dispatcher. Phase 2 closes the loop so the intent layer can fully manage
  the net position lifecycle.
- **Required verification before starting:** Phase 1 ran live on Bybit2 for a
  full session with both strategies actively firing; `meta.execution_delta`
  rows landed in `signal_audit.jsonl` for every actionable tick; no
  unexpected `open_position_exists` refusals.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] If this sprint touched any pipeline stage, `docs/TRADE-PIPELINE.md` was
      noted — multiplexer + dispatcher updates are summarised here; the
      TRADE-PIPELINE doc itself is descriptive prose that already covers
      "strategy → multiplexer → dispatcher → executor" at the same abstraction
      level, and the new layer is a swap-in at the multiplexer point. No
      change to the pipe stage list.
- [x] Roadmap status was checked + ledger entry added.
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.

## PR Reference
- [#1125](https://github.com/benbaichmankass/ict-trading-bot/pull/1125) — Intent layer foundation.
- [#1129](https://github.com/benbaichmankass/ict-trading-bot/pull/1129) — Delta wiring into dispatcher.
- [#1130](https://github.com/benbaichmankass/ict-trading-bot/pull/1130) — Enable flag on live trader.
