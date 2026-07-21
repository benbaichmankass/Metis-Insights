# Sprint Log — S-M27-ALTCOIN-PROMOTION-2026-07-21

## Date Range

- Start / End: 2026-07-21 (single session, continuation of the M27 overnight arc)

## Objective

Wire the three ict_scalp_5m alt-variant legs the M27 Batch-1 crypto study
passed (SOL, XRP, AVAX) into the live execution layer at
`execution: live` on the bybit_1 demo/paper account, per operator directive
2026-07-21: *"push the altcoins that passed to live on shadow... promote
them so they can start accruing data toward a real-money decision."*

**Terminology clarification (operator, same directive):** "shadow" in the
operator's vocabulary means *live-executing on the paper/demo account*
(bybit_1) — NOT the codebase's `execution: shadow` gate, which is
signal-log-only and never places even a paper order. The correct flow,
confirmed against `new-strategy` SKILL.md's own hard rule ("paper/demo
accounts always EXECUTE"): new leg → `execution: live` → bybit_1 only →
prove out on real paper fills → promote to `bybit_2` (real money) AND
`bybit_portfolio` (its paper mirror) together in a later, separate
operator-approved Tier-3 step.

## Tier

Tier 3 — `config/strategies.yaml` + `config/accounts.yaml` changes
(new live strategies, account routing). Operator-approved in this session
before any file was touched.

## Starting Context

- M27 P0 Batch-1 crypto findings merged to main (#7220):
  SOL/XRP/AVAX/ETH pass the net k-fold gate, ADA mixed (no leg).
  ETH held for the P1 15m sweep per the findings doc's own recommendation
  (not requested for promotion this session).
- The `new-strategy` skill's worked pattern for per-symbol alt variants:
  `_trend_donchian_variant_builder` (SOL/ETH prop legs) — mirrored exactly.

## Repo State Checked

- Branch `claude/m27-altcoin-scalp-shadow` cut from `origin/main` at
  `d4891a4a` (post M27 Batch-2 merge).
- Read `config/strategies.yaml` (ict_scalp_5m block, execution-gate header
  comment), `config/accounts.yaml` (bybit_1/bybit_2/bybit_portfolio blocks),
  `config/regime_policy.yaml`, `config/regime_coverage_exemptions.yaml`,
  `src/runtime/strategy_signal_builders.py` (ict_scalp_signal_builder +
  `_trend_donchian_variant_builder` precedent), `src/runtime/intents.py`,
  `src/runtime/intent_multiplexer.py`, `scripts/check_strategy_coverage.py`.

## Files and Systems Inspected

- `src/runtime/regime/vol_detector.py::vol_regime_from_spec` /
  `resolve_vol_specs` — confirmed the live vol axis for
  `config/regime_policy.yaml`'s `trend_vol` cells resolves from a
  **registered ML shadow-stage regime head per `(symbol, timeframe)`**, not
  a standalone spec. No such head exists for SOL/XRP/AVAX.
- `src/runtime/regime/detector.py::detect_regime` — pure, registry-free
  ADX-14 classifier; safe to call directly.
- `scripts/check_strategy_coverage.py` — confirmed it gates only
  `execution: live` strategies (shadow strategies need no regime cell), and
  that `coverage_debt` is a capped, ratchet-down-only grandfather list a new
  strategy may never join — `exempt` is the only legal path for a new leg
  without a working `regime_policy.yaml` cell.

## Work Completed

1. **Root-cause finding:** XRP's M27 Batch-1 pass was *conditional* on the
   off-cells regime gate (ungated 2/4 folds fails; gated 4/4 passes,
   +34.3R). Since no live regime head is trained for XRP, wiring it through
   `config/regime_policy.yaml` would silently resolve `vol_regime="unknown"`
   and never fire — shipping XRP that way would actually run the ungated
   FAILING profile, not the evidenced-passing one.
2. **Fix:** implemented the off-cells gate **strategy-locally** —
   `_ict_scalp_variant_builder` (new shared builder,
   `src/runtime/strategy_signal_builders.py`) reads an optional
   `off_cells: [[trend, vol], ...]` + `vol_spec: {...}` from the variant's
   own YAML block, computes `(trend, vol)` via `detect_regime` +
   `vol_regime_from_spec` called directly (no registry dependency), and
   suppresses a signal that matches. XRP's `vol_spec` carries the EXACT
   frozen 5m tercile edges the Batch-1 backtest derived. SOL/AVAX carry no
   `off_cells` (their own evidence passes ungated).
3. **Wiring** (mirrors `_trend_donchian_variant_builder`'s pattern exactly):
   - `ict_scalp_sol_5m_signal_builder` / `_xrp_5m_` / `_avax_5m_` in
     `strategy_signal_builders.py`; each pins its symbol from its own
     `symbols:` config, reuses `src.units.strategies.ict_scalp.order_package`.
   - `monitor_unit` tags → `"ict_scalp"` (reuse the unit's `monitor()`).
   - Registered in `intent_multiplexer.py::_default_intent_builders` and
     `intents.py::DEFAULT_PRIORITIES` (priority `0` — untested-roster floor,
     same as the trend_donchian alt variants). NOT added to
     `pipeline.py::_STRATEGY_BUILDERS` — the alt-variant precedent lives
     ONLY in the intent-layer roster (the production default path).
   - `config/strategies.yaml`: three new blocks, config-exact copies of
     `ict_scalp_5m`'s params (the transfer study's point was testing the
     SAME setup logic, not re-tuning), `execution: live`,
     `shadow_model_ids: []`.
   - `config/accounts.yaml::bybit_1.strategies` — added all three. NOT
     added to `bybit_2` or `bybit_portfolio` (future promotion step).
     bybit_1 already carries all three symbols in its `symbols:` list — no
     account-level symbol change needed.
   - `config/strategy_descriptions.json` — three entries.
   - `config/regime_coverage_exemptions.yaml::exempt` — three reasoned
     entries (the strategy-local-gate rationale above), since neither
     `regime_policy.yaml` cell nor `coverage_debt` (capped, no new entries)
     applies.
   - `docs/strategy-coverage-matrix.md` regenerated
     (`check_strategy_coverage.py --matrix`).
   - `docs/research/exit-refinement-coverage.json` — three `pending` rows
     (all lever columns) per the new-strategy skill's completion checklist.
4. **Tests:** `tests/test_ict_scalp_variants.py` (new, 5 tests) — symbol
   pinning, disabled short-circuit, off-cell suppression on match, pass-through
   on non-match, and confirmation that SOL/AVAX (no `off_cells` configured)
   are NEVER gated regardless of detected regime.

## Validation Performed

- `pytest tests/test_multi_strategy_intents.py tests/test_intent_delta_dispatch.py
  tests/test_ict_scalp_5m.py tests/test_ict_scalp_variants.py
  tests/test_strategy_monitor_unit_resolution.py` — **138 passed** (incl. the
  monitor-unit drift guard, confirming all three new legs resolve to a
  module with a real `monitor()`).
- `python scripts/check_strategy_coverage.py` — PASS: 43 live strategies,
  all covered/exempt/debt; debt stays 35/35 (no new debt added — the three
  new legs are `exempt`, not parked in the capped debt register).
- `python scripts/check_dry_run_in_diff.py` — clean (no `execution: shadow`
  in the diff; correctly ships `live`).
- `python scripts/check_strategy_risk_field_in_diff.py` — clean (no
  `risk_pct` added).
- `python scripts/check_canonical_config_loaders.py` — clean.
- `python scripts/check_writer_conformance.py` — clean.
- `ruff check` + `py_compile` on all touched `.py` files — clean.
- All touched YAML/JSON parse-validated.
- **Gaps not yet verified:** live deploy + first-tick confirmation (next
  step after merge — `pull-and-deploy` + a diag check that the three new
  `<name>_eval` audit rows appear and the coordinator logs
  `execution:live` dispatch, not a live BROKER fill yet since this is a
  fresh demo-soak, zero trade history to inspect).

## Documentation Updated

- `docs/strategy-coverage-matrix.md`, `docs/research/exit-refinement-coverage.json`
  (both regenerated/updated in this PR).
- `docs/claude/performance-review-backlog.json` — new tracking item for the
  paper-soak → real-money decision checkpoint (see below).
- This sprint log.

## Contradictions or Drift Found

None new. (The `_ict_scalp_variant_builder` docstring documents WHY the
global `regime_policy.yaml` mechanism doesn't apply here — not a
contradiction, a scoping note for the next session that touches this code.)

## Risks and Follow-Ups

- XRP's live gate is enforced via a config-local frozen spec, not the
  registry-driven mechanism the rest of the regime system uses — flagged
  clearly in the `regime_coverage_exemptions.yaml` reason and the builder
  docstring so a future session doesn't mistake it for dead code. Migrate to
  a real `regime_policy.yaml` cell once an XRP regime head is registered
  (tracked informally in the exemption's `reason`, not a separate backlog
  item — low urgency while the strategy-local gate works correctly).
- Performance-review backlog item added: check the live paper P&L on all
  three legs against the M27 Batch-1 backtest once meaningful trade count
  accrues, as the gate for the bybit_2 + bybit_portfolio promotion decision.

## Deferred Items

- ETH (M27 Batch-1's weakest passer, fold-4 negative under the gate) — held
  per the findings doc's own recommendation; not requested for promotion.
- ADA — no leg (mixed evidence).
- Migrating XRP's local gate to a registered regime head.

## Next Recommended Sprint

Continue the M27 workplan: Batch-3 equities/ETFs rig, or the P1 15m sweep.
Separately (not urgent): monitor the new legs' first live paper signals via
diag once deployed.

## Wrap-Up Check

- [x] Code inspected directly (builder, registry, coverage guard, config
      loader precedent)
- [x] Docs reviewed/updated (coverage matrix, exit-refinement matrix,
      performance-review backlog, this log)
- [x] TRADE-PIPELINE: new strategy registration only — no pipeline stage
      logic changed
- [x] Roadmap checked (M27 row unaffected — this is a leg-promotion step,
      not a milestone-status change)
- [x] Contradictions recorded (none new)
- [x] Unknowns stated (live deploy verification pending — see Validation
      gaps)
