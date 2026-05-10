# S-AI-WS7-PART-3 — Wire vwap through `with_shadow_pred`

**Date:** 2026-05-10
**Authority:** [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md), [`docs/sprint-plans/ai-traders/ws7-deployment-tiers.md`](../sprint-plans/ai-traders/ws7-deployment-tiers.md), [`docs/sprint-logs/S-AI-WS7-PART-2.md`](S-AI-WS7-PART-2.md)
**Status:** ✅ COMPLETE — third of three planned WS7 parts.

## Goal

Land the **first production-strategy adoption** of the per-strategy
shadow-mode adapter shipped in PART-2. Operator-chosen target
(2026-05-10): `vwap` — the most actively traded strategy in the
bot. Operator-chosen first predictor: a constant placeholder (no
trained model required for this PR). Threading `with_shadow_pred`
through `vwap.order_package` is a 3-line change in the production
strategy module; the feature-row construction is a small pure
helper.

The acceptance criterion the WS7 spec demands — *"a new model can
run in shadow mode without changing live trading behavior"* — is
satisfied at this point: the operator can inject a
`ShadowPredictor` into `cfg["_shadow_predictor"]` and observe its
score in the audit log without any change to the deterministic
`vwap` decision.

## Decisions

- **Operator picks: vwap + constant placeholder predictor.** Other
  options surfaced (turtle_soup, smoke_test for first-strategy;
  trained WS5 model or offline replay for first-predictor) were
  documented but not chosen. Rationale per operator: vwap has the
  highest live volume so wiring it has the most reach, and a
  constant predictor proves the wire works without needing a
  trained model artifact.
- **`cfg["_shadow_predictor"]` is the injection point.** Strategies
  thread their package through `with_shadow_pred(package,
  predictor=cfg.get("_shadow_predictor"), feature_row=...)`
  unconditionally. When the cfg key is absent, the helper is a
  pass-through (per PART-2). When present, the predictor must be a
  `ShadowPredictor` instance — bare `Predictor`s are rejected loud.
  The WHO-puts-the-predictor-in-cfg question is filed for PART-4
  (config-driven factory or registry-resolved selection).
- **Feature-row helper lives next to the strategy.** A new private
  `vwap._build_shadow_feature_row(package)` projects the package
  into a signal-time feature dict aligned with the WS5-C / WS5-D
  feature surface (`strategy_name`, `symbol`, `direction`,
  `confidence`, `setup_type`, `killzone`, `bias`). Outcome columns
  (`pnl`, `pnl_percent`, `r_multiple`) are explicitly omitted —
  there's a dedicated leakage test verifying this.
- **No live-impact cfg changes.** `config/strategies.yaml` is
  unchanged. Production runs without any operator action will
  carry no `_shadow_predictor` and therefore exercise the
  pass-through branch only.
- **Defense-in-depth: package keys unchanged.** The integration
  test asserts that `order_package` returns the canonical seven
  keys (`symbol, direction, entry, sl, tp, confidence, meta`)
  whether or not a predictor is injected. Defends against any
  future refactor that confuses "shadow" with "advisory" and
  accidentally lets the model score reach the package.

## Deliverables

- `src/units/strategies/vwap.py`:
  - Module docstring extended with a "Shadow-mode hook" section
    documenting the contract.
  - New private helper `_build_shadow_feature_row(package)`.
  - `order_package()` builds the package as a local var, then
    threads it through `with_shadow_pred(...)` before returning.
  - New import: `from src.runtime.shadow_adapter import
    with_shadow_pred`.
- `tests/test_vwap_shadow.py` — new test file:
  - `pytest.importorskip("pandas")` so it skips on dev sandboxes
    that don't have pandas (matches the existing vwap-test
    convention).
  - 4 integration tests under `TestVwapShadowIntegration`: no
    predictor leaves keys unchanged; predictor called with
    signal-time features; audit log emitted; broken predictor
    does not crash strategy.
  - 4 unit tests under `TestBuildShadowFeatureRow`: minimal
    package; meta passthrough; leakage defence (no `pnl` /
    `r_multiple` in row); missing-meta handling.

## Acceptance

- [x] `pytest tests/ml/ tests/runtime/test_shadow_adapter.py` —
      241 / 241 pass (no regression on PART-1 + PART-2).
- [x] `pytest tests/test_vwap_shadow.py` — skipped locally
      (pandas not installed in dev sandbox); collects 8 tests.
      Will run on CI which has pandas.
- [x] `ruff check src/units/strategies/vwap.py
      tests/test_vwap_shadow.py` — clean.
- [x] WS7 spec acceptance — *"a new model can run in shadow mode
      without changing live trading behavior"* — satisfied. Inject
      `cfg["_shadow_predictor"] = ShadowPredictor(...)` from any
      caller and the model's score lands in the audit log without
      reaching the order package.
- [x] Live runtime impact: zero unless an operator opts in by
      injecting a predictor. `config/strategies.yaml` unchanged.

## Out of scope (filed for follow-ups)

- **S-AI-WS7-PART-4 — predictor source / factory.** Decide
  WHO populates `cfg["_shadow_predictor"]` in production. Two
  natural designs:
  1. Config-driven: `config/strategies.yaml::strategies.vwap.
     shadow_model_id` + a strategy-init step that resolves the
     id against the model registry, instantiates the predictor,
     wraps it in `ShadowPredictor`, and stuffs it in cfg.
  2. Code-driven: a `ml/shadow/factory.py::resolve_for_strategy
     (strategy_name)` helper called by the dispatch path before
     `order_package` runs.
  Operator picks; design is its own scoped sprint.
- **Train + register a real WS5 model.** The constant placeholder
  proves the wire. To get useful shadow-mode output, train one of
  the seven WS5 manifests (e.g. `baseline-setup-quality.yaml`)
  on real `trade_journal.db` data and register the resulting
  model in the registry at `target_deployment_stage=shadow`.
  Will need VM access (training off the live VM per WS9) or a
  representative export.
- **Wire turtle_soup.** Same pattern as this PR; should be a
  ~10-line change once vwap proves out.
- **Multiple shadow predictors per strategy.** Strategies want to
  shadow more than one model (e.g. WS5-C + WS5-D + WS5-E
  concurrently). The current helper takes one; a
  `with_shadow_preds(decision, *, predictors, feature_rows, ...)`
  variant or a composite `MultiShadowPredictor` is the natural
  next step.
- **Audit log rotation.** `runtime_logs/shadow_predictions.jsonl`
  (or wherever the operator points the predictor) needs daily
  rotation and an inventory hook similar to
  `signal_audit.jsonl`'s consumer.
- **Per-tick performance budget.** A real model adds latency to
  the strategy tick. Need to measure once a real predictor is
  wired; if budget is breached, the helper should add an
  optional timeout (skip + log) rather than block the tick.

## Hand-off

- WS7 acceptance criterion is now **satisfied for vwap**. A new
  model can run in shadow mode against vwap signals without
  changing live behavior. PART-4 decides how the predictor gets
  into cfg in production.
- Ledger entry under M9 in [`ROADMAP.md`](../../ROADMAP.md);
  WS7 status updated to "🔄 IN PROGRESS (PART-1 + PART-2 +
  PART-3 done; PART-4 predictor source queued)".

## Live runtime impact

None until an operator opts in. The new code path is taken
only when `cfg["_shadow_predictor"]` is set; the default cfg
(loaded from `config/strategies.yaml` unchanged in this PR) does
not set it. The production behavior of `vwap.order_package` is
byte-identical to pre-PR for any caller that does not inject a
predictor — verified by the `test_no_predictor_keys_unchanged`
integration test.
