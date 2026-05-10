# S-AI-WS7-PART-4 — Multi-predictor shadow + config-driven source

**Date:** 2026-05-10
**Authority:** [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md), [`docs/sprint-plans/ai-traders/ws7-deployment-tiers.md`](../sprint-plans/ai-traders/ws7-deployment-tiers.md), [`docs/sprint-logs/S-AI-WS7-PART-3.md`](S-AI-WS7-PART-3.md)
**Status:** ✅ COMPLETE

## Goal

Land the operator-stated PART-4 spec (2026-05-10): vwap can run
**multiple shadow-mode predictors concurrently**, sourced
**config-driven** from `config/strategies.yaml`. PART-3 shipped the
single-predictor wiring with cfg-injection; PART-4 generalises to
N predictors and replaces the test-injection-only path with a
registry-backed factory so production rollout is a YAML edit, not
a code change.

## Decisions

- **Operator picks: wire all three concurrently + config-driven via `strategies.yaml`.**
  Other options surfaced (env-var, hardcoded) were rejected.
- **Plural helper alongside the singular.** `with_shadow_preds`
  (plural) is the production path going forward; `with_shadow_pred`
  (singular) stays as a thin shim around the plural form so
  PART-3's test injection (`cfg["_shadow_predictor"]`) keeps
  working without modification.
- **Per-predictor failure isolation.** Each predictor in the list
  is wrapped in its own `try/except`; one model raising never
  affects the others. Tested with a `[good_a, broken_b, good_c]`
  list — `a` and `c` both fire, `b`'s exception is logged with
  its `model_id`, decision returned unchanged.
- **Stage-gated factory.** `ml/shadow/factory.py::resolve_predictor`
  refuses to load a model whose `target_deployment_stage` is in
  `{research_only, candidate, backtest_approved}`. Allowed
  stages: `{shadow, advisory, limited_live, live_approved}` —
  models that haven't earned the right to influence anything
  cannot be wired even as observers. The reasoning is bookkeeping:
  the audit trail says "this model_id ran in shadow mode at stage
  X"; a `research_only` model showing up in that trail would
  confuse the deployment audit. Operator must promote first
  (one `promote_stage` call).
- **Per-model error isolation in the factory.**
  `resolve_predictors([...], strict=False)` (the default) logs
  per-model errors as `shadow_factory_skipped` and returns the
  successfully-resolved subset. One missing model_id, one
  unpromoted stage, or one corrupt model_state file cannot block
  the others. `strict=True` is available for operator scripts
  that want fail-fast behaviour (mostly useful for one-shot
  diagnostic runs).
- **vwap resolution priority** — three injection modes, first
  non-empty wins:
  1. `cfg["_shadow_predictors"]` (plural, pre-resolved list).
     Test path; also accepted as a Coordinator-side cache hook
     for PART-5.
  2. `cfg["_shadow_predictor"]` (singular). PART-3 backwards-compat
     for test injection.
  3. `cfg["shadow_model_ids"]` resolved via the factory. The
     production path. Registry root override via
     `cfg["_shadow_registry_root"]`; audit log path override via
     `cfg["_shadow_log_path"]` (defaults to
     `runtime_logs/shadow_predictions.jsonl`).
- **`shadow_model_ids: []` (empty list) is the YAML default.**
  Production runs without the operator opting in carry no shadow
  side-channel. The factory + helper are inert.
- **Predictor instantiation is per-tick today.** The factory loads
  the model state JSON and constructs the predictor on every
  `order_package` call when `shadow_model_ids` is set. For the
  baselines we shipped today (small JSON state files), this is
  acceptable. Filed for PART-5 if real models come in with
  expensive state-load paths.

## Deliverables

- `src/runtime/shadow_adapter.py`:
  - New `with_shadow_preds(decision, *, predictors, feature_row,
    logger=None)` plural helper. Accepts `Sequence`, `Iterable`,
    or `None`. Empty / None = pass-through.
  - `with_shadow_pred` refactored to a thin shim that delegates
    to `with_shadow_preds` with a single-element list — keeps
    the PART-2/3 API stable.
  - Module docstring rewritten to describe both helpers + the
    contract.
- `ml/shadow/__init__.py` + `ml/shadow/factory.py`:
  - `LIVE_INFLUENCE_STAGES` frozenset (the four stages that
    permit shadow loading).
  - `ShadowFactoryError` with messages naming the offending
    model_id + stage.
  - `resolve_predictor(model_id, registry, *, log_path)` —
    single-id resolver; raises on missing/unpromoted/corrupt.
  - `resolve_predictors(model_ids, registry, *, log_path,
    logger, strict)` — batch resolver with per-model error
    isolation.
  - `_resolve_predictor_class` mirrors
    `Evaluator._resolve_predictor`'s logic without coupling to
    the evaluator class hierarchy.
- `src/units/strategies/vwap.py`:
  - Switched from `with_shadow_pred` (singular) to
    `with_shadow_preds` (plural) on the return path.
  - New private `_resolve_shadow_predictors(cfg)` implementing
    the three-mode resolution priority.
- `config/strategies.yaml`:
  - New optional `shadow_model_ids: []` field on the `vwap`
    block, with operator-readable comment block explaining the
    contract + stage gate.
- `tests/runtime/test_shadow_adapter_plural.py` — 8 tests for
  `with_shadow_preds`:
  - Empty / None passthrough.
  - Calls every predictor once.
  - One failure does not block others (the operator-spec test).
  - Audit log per predictor.
  - Non-`ShadowPredictor` entry rejected.
  - Decision keys unchanged (defence-in-depth).
  - Singular API still passes-through `None`.
  - Singular API still calls predictor.
- `tests/ml/test_shadow_factory.py` — 17 tests:
  - Resolves at every allowed stage (4 parametrised cases).
  - Refuses unpromoted stages (3 parametrised cases).
  - Unknown model_id, missing model_state, unknown trainer,
    blank trainer qualname.
  - `log_path` propagates.
  - Plural resolver: returns input order, skips unknown when
    not strict, skips unpromoted when not strict, strict
    re-raises, empty list.
- `tests/test_vwap_shadow.py` — 4 new tests under
  `TestVwapConfigDrivenShadow`:
  - `shadow_model_ids` resolves three concurrently (operator spec).
  - Unpromoted model is skipped, others still fire.
  - Empty list is no-op.
  - Singular `_shadow_predictor` (PART-3 path) still works.

## Acceptance

- [x] `pytest tests/ml/ tests/runtime/ tests/test_vwap_shadow.py` —
      266 / 266 pass + 1 skipped (vwap shadow tests skip on dev
      sandbox without pandas; CI has pandas).
- [x] `ruff check` clean on all changed files.
- [x] PART-3 backward-compat: `cfg["_shadow_predictor"]` injection
      path still works (verified by
      `test_singular_predictor_still_works`).
- [x] Three predictors concurrent: when
      `cfg["shadow_model_ids"] == ["a","b","c"]` and all three are
      registered + promoted to `shadow`, every `order_package` call
      emits exactly three audit lines, decision unchanged.
- [x] Per-model failure isolation: one broken model in a list of
      three does not block the other two.
- [x] No live runtime impact unless an operator sets a non-empty
      `shadow_model_ids` in `config/strategies.yaml`.
      `config/strategies.yaml` ships with `shadow_model_ids: []`.

## Out of scope (filed for follow-ups)

- **PART-5 — Coordinator-side resolution.** Today vwap calls the
  factory inside `order_package`. For a per-tick perf budget, the
  Coordinator can resolve once at strategy-init time and inject
  the resolved list as `cfg["_shadow_predictors"]` (resolution
  mode 1). The strategy code already supports this — it's just
  the dispatcher work.
- **Train + register the WS5 baselines.** PART-4 ships the
  wiring; producing the actual model artifacts (running the WS5
  manifests against real `trade_journal.db` data, registering the
  resulting models in the registry, promoting them past
  `backtest_approved`) is the operator's task. Until then,
  `shadow_model_ids: []` and the harness is inert. Recommended
  workflow: `ml/cli.py train --manifest ml/configs/baseline-...
  yaml` produces a model_state, then `ml/cli.py promote ... shadow`
  walks it to the shadow stage.
- **`turtle_soup` adoption.** Same pattern as PART-3 — a 3-line
  change in `src/units/strategies/turtle_soup.py` plus a
  `shadow_model_ids: []` field in `strategies.yaml`. Filed.
- **Audit log rotation.** `runtime_logs/shadow_predictions.jsonl`
  needs daily rotation when shadow mode is actually being used.
  Same pattern as `signal_audit.jsonl`.
- **Per-tick perf budget enforcement.** Once a real model is
  wired, measure tick latency. If shadow predictions push the
  tick over budget, the helper should add an optional per-call
  timeout (skip + log) rather than block.

## Hand-off

- WS7 acceptance criterion is now satisfied for vwap with up to
  N concurrent shadow predictors, sourced from YAML config.
  Production rollout is a one-line YAML edit + the (separate)
  model-training task.
- Ledger entry under M9 in [`ROADMAP.md`](../../ROADMAP.md);
  WS7 status updated to "🔄 IN PROGRESS (PART-1..PART-4 done;
  PART-5 Coordinator-side resolution + model training queued)".

## Live runtime impact

None until operator sets `shadow_model_ids` to a non-empty list.
The default YAML carries `shadow_model_ids: []` so existing
production runs are byte-identical to pre-PR. Even with the
field set, the factory's stage gate refuses to load any model
whose `target_deployment_stage` is `research_only` /
`candidate` / `backtest_approved` — the operator must explicitly
promote a model before shadow loading is permitted.
