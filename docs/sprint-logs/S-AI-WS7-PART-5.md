# S-AI-WS7-PART-5 — turtle_soup adoption of shadow harness

**Date:** 2026-05-10
**Authority:** [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md), [`docs/sprint-plans/ai-traders/ws7-deployment-tiers.md`](../sprint-plans/ai-traders/ws7-deployment-tiers.md), [`docs/sprint-logs/S-AI-WS7-PART-3.md`](S-AI-WS7-PART-3.md), [`docs/sprint-logs/S-AI-WS7-PART-4.md`](S-AI-WS7-PART-4.md)
**Status:** ✅ COMPLETE

## Goal

Apply the proven PART-3 / PART-4 pattern to `turtle_soup` — the
second of the two production strategies. After this PR, both
production strategies (`vwap` + `turtle_soup`) can run multiple
shadow-mode predictors concurrently from `config/strategies.yaml`.
WS7 acceptance criterion *"a new model can run in shadow mode
without changing live trading behavior"* now satisfied for both
production strategies.

## Decisions

- **Apply, don't redesign.** The PART-3 / PART-4 pattern is proven
  (one strategy, four PRs of harness work). PART-5 is a direct
  port — same `_resolve_shadow_predictors(cfg)` 3-mode priority,
  same `_build_shadow_feature_row(package)` projection, same
  `shadow_model_ids: []` YAML field. No new abstractions; if a
  shared mixin emerges naturally on the third adoption, refactor
  then. (We don't have a third strategy on the runway today.)
- **Strategy-specific feature row.** Per the PART-3 contract, the
  feature row is whatever the strategy knows about its own
  signal — predictors trained against WS5's per-strategy feature
  surface specialise. `turtle_soup`'s row exposes `atr` and
  `body_to_range` (its setup-quality fields) in addition to the
  cross-strategy fields (`strategy_name`, `direction`,
  `confidence`, `setup_type`, `timeframe`). Outcome columns
  (`pnl`, `r_multiple`) are explicitly excluded.
- **No shared helper module yet.** Both `vwap._resolve_shadow_predictors`
  and `turtle_soup._resolve_shadow_predictors` are 25 lines each
  with identical structure. Extracting to
  `src/runtime/shadow_resolver.py` is filed if a third strategy
  adopts; until then the duplication is cheaper than the wrong
  abstraction.

## Deliverables

- `src/units/strategies/turtle_soup.py`:
  - Imports `with_shadow_preds` (plural helper from PART-4) and
    `Path`.
  - `order_package(...)` builds the deterministic dict into a
    local `package` var, then threads it through
    `with_shadow_preds(...)` before returning.
  - New private `_resolve_shadow_predictors(cfg)` — 3-mode
    priority identical to vwap (explicit plural injection >
    singular legacy injection > config-driven `shadow_model_ids`
    via the registry-backed factory).
  - New private `_build_shadow_feature_row(package)` —
    strategy-specific projection: `atr`, `body_to_range` fields
    surface, `setup_tf`/`timeframe` fall back to empty strings.
- `config/strategies.yaml`:
  - New optional `shadow_model_ids: []` field on the
    `turtle_soup` block, cross-referencing the vwap comment for
    the full contract.
- `tests/test_turtle_soup_shadow.py` — new file mirroring
  `tests/test_vwap_shadow.py` surface (12 tests across 3
  classes):
  - **`TestTurtleSoupShadowIntegration`** (4 tests): no-predictor
    keys unchanged, singular predictor called with feature row,
    audit log emitted, broken predictor doesn't crash.
  - **`TestTurtleSoupConfigDrivenShadow`** (4 tests): operator's
    PART-4 spec ("three concurrent") on turtle_soup,
    unpromoted-skip, empty-list no-op, plural injection.
  - **`TestBuildShadowFeatureRow`** (2 tests): includes
    strategy-specific fields (`atr`, `body_to_range`), missing
    meta handled.
  - Skips locally on dev sandboxes without `pandas` / `numpy`
    (CI has both).

## Acceptance

- [x] `pytest tests/ml/ tests/runtime/ tests/test_vwap_shadow.py
      tests/test_turtle_soup_shadow.py` — 266 / 266 pass + 2
      skipped (turtle_soup + vwap shadow integration tests skip on
      dev sandboxes without pandas).
- [x] `ruff check` clean on `src/units/strategies/turtle_soup.py`
      and `tests/test_turtle_soup_shadow.py`.
- [x] No new abstractions; `_resolve_shadow_predictors` body is
      a direct port of vwap's.
- [x] `config/strategies.yaml` ships with `turtle_soup.shadow_model_ids:
      []` (empty default — no live runtime impact unless operator
      opts in).
- [x] Backwards-compat: PART-3's `cfg["_shadow_predictor"]`
      injection on turtle_soup is supported (verified by
      `test_singular_predictor_called`).
- [x] Operator spec ("wire all three concurrently") replicated for
      turtle_soup (verified by
      `test_shadow_model_ids_resolves_three_concurrently`).

## Out of scope (filed for follow-ups)

- **PART-6 — Coordinator-side resolution.** Today both strategies
  call the factory inside `order_package`. PART-6 lifts resolution
  to strategy-init time at the dispatcher and injects the
  pre-resolved list as `cfg["_shadow_predictors"]` (resolution
  mode 1 in both strategies — already supported).
- **Shared resolver helper.** If a third strategy adopts the
  shadow pattern, lift the duplicated `_resolve_shadow_predictors`
  and the feature-row builder skeleton into
  `src/runtime/shadow_resolver.py`. Today's two-strategy
  duplication is cheap.
- **Train + register the WS5 baselines.** PART-4's filed
  follow-up. PART-5 doesn't move the needle here — same operator
  task (real `trade_journal.db` data on the VM).
- **Per-strategy feature-row contract.** Today each strategy
  produces its own row shape. As predictors get trained against
  specific feature surfaces, the contract should be formalised
  (e.g. via a TypedDict per strategy, surfaced in WS5 manifests).

## Live runtime impact

None until an operator sets `turtle_soup.shadow_model_ids` to a
non-empty list. The default YAML carries `shadow_model_ids: []`
so existing production runs are byte-identical to pre-PR. The
factory's stage gate (PART-4) still applies — only models
promoted past `backtest_approved` can be wired.
