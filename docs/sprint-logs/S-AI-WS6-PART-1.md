# S-AI-WS6-PART-1 — Open-source model layer scaffolding

**Date:** 2026-05-10
**Authority:** [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md), [`docs/sprint-plans/ai-traders/ws6-open-source-models.md`](../sprint-plans/ai-traders/ws6-open-source-models.md)
**Status:** ✅ COMPLETE (framework + inventory + rules). PART-2 (first concrete model integration) gated on a real use case.

## Goal

Stand up WS6's framework without committing to any specific
open-source model. PART-1 ships the **inventory document + rules
+ provider-agnostic interface**. PART-2 follows later when a
real use case justifies a specific HF integration; until then
the framework exists but produces nothing in production.

## Decisions

- **Inventory document, not integration.** Per the WS6 sprint
  plan, "no model is added because it is fashionable or large."
  PART-1 ships the rubric so a future PR adding a specific model
  has explicit criteria to meet. Adding the first concrete
  model is a separate PR with its own approval trail.
- **Provider-agnostic ABC, not HF-first.** `ExternalPredictor`
  doesn't import `transformers` or any HF type. Concrete
  subclasses can wrap HF, vLLM, local Ollama, or vendor APIs —
  the interface treats them identically. Avoids the trap of
  designing the framework around the first integration.
- **`ProviderError` distinct from generic exceptions.** Lets
  callers (and future routing logic) distinguish "external
  system is flaky" from "logic bug in our code". Subclasses raise
  `ProviderError(message, provider=..., model_identifier=...)`;
  the shadow harness still catches it via its bare
  `except Exception` so behavior is unchanged today, but the type
  is available for log routing / alerting later.
- **No model auto-promotion.** Models integrated through this
  framework land at `target_deployment_stage: research_only` by
  default. Same WS7 stage ladder as in-house baselines — the
  fact that a model came from Hugging Face doesn't earn it any
  fast-track to shadow / advisory / live.
- **Provider, version, and use case all required.** The inventory
  doc + the approval criteria require `org/model-name@sha-or-tag`
  pinning. No floating-version dependencies in the registry.
- **Live trader VM stays out.** Per WS9, any open-source model
  inference runs on the training-center VM or off-VM via HTTP
  API. The live trader VM stays deterministic. The framework
  doesn't enforce this — it's policy, surfaced in the inventory
  doc.

## Deliverables

- `docs/architecture/model-inventory.md` (new) — non-negotiable
  rules, approval criteria, candidate models by use case (text,
  embedding, tabular/time-series, reasoning/orchestration), and
  the workflow for adding a new model.
- `ml/predictors/external.py` (new) — `ExternalPredictor` ABC +
  `ProviderError` exception. ~110 LOC, stdlib-only.
- `ml/predictors/__init__.py` — re-exports `ExternalPredictor` +
  `ProviderError` alongside existing predictor types.
- `tests/ml/test_external_predictor.py` (new) — 11 tests:
  - ABC cannot be instantiated.
  - Subclass missing `predict` or `describe` cannot be
    instantiated (validates abstract methods).
  - Concrete subclass works as a `Predictor` (drop-in
    compatibility).
  - `ProviderError` carries `provider` + `model_identifier`
    metadata.
  - `ProviderError` catchable as `RuntimeError` so the shadow
    harness' bare `except` continues to work.
  - **`ExternalPredictor` composes with `ShadowPredictor`** —
    drop-in for the WS7 shadow harness, including the "broken
    predictor doesn't crash" property via `with_shadow_preds`.

## Acceptance

- [x] `pytest tests/ml/ tests/runtime/` — 361 / 361 pass
      (350 prior + 11 new).
- [x] `ruff check` clean.
- [x] No HF / vendor SDK dependencies pulled in.
- [x] `ExternalPredictor` registered in `ml.predictors`
      namespace (re-exported from `__init__`).
- [x] Inventory doc documents the approval criteria + rules.
- [x] Live runtime impact: ZERO — no concrete model integrated.

## Out of scope (filed for follow-ups)

- **WS6-PART-2 — First concrete model integration.** Gated on a
  real use case + the approval criteria. Most likely candidates:
  - **News headline sentiment** scoring (text — DistilBERT-class)
    if a news feed is wired in.
  - **xgboost / lightgbm** on `setup_labels` family if the WS5-C
    setup-quality scorer plateaus.
- **Trainer / loader adapter for HF pretrained models.** The HF
  family includes both fine-tunable and zero-shot use cases. The
  zero-shot path needs a "register-existing-weights" helper that
  lands in the registry without invoking the WS4 trainer
  framework. Filed.
- **PEFT / LoRA / adapter-tuning workflow.** Required by the WS6
  rules but not implemented. Lands when an actual fine-tuning
  use case appears.
- **Inference cost / latency benchmarking harness.** Per the
  approval criteria, every model needs p50/p99 latency + cost
  numbers. A reusable benchmark script would lower the bar to
  bringing a new model forward.

## Live runtime impact

None. PART-1 is documentation + an abstract class + tests. No
runtime code path imports the new module; no model has been
integrated. The framework exists waiting for PART-2.

## What success looks like for PART-2

A PR that:

1. Picks a specific model (e.g. `prosusai/finbert@v0.1`).
2. Documents the WS6 approval criteria (use case, gain, latency,
   cost, rollback, provider lock-in) in the PR body.
3. Ships a concrete `ExternalPredictor` subclass (e.g.
   `ml/predictors/hf_text_sentiment.py`).
4. Lands a registry entry at `target_deployment_stage:
   research_only`.
5. Updates `docs/architecture/model-inventory.md` from "candidate"
   to "approved dependency" with the integration commit hash.
