# Open-source model inventory (S-AI-WS6)

> **Authority:** [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md)
> ([`docs/sprint-plans/ai-traders/ws6-open-source-models.md`](../sprint-plans/ai-traders/ws6-open-source-models.md)).
>
> **Status:** Inventory established 2026-05-10 (S-AI-WS6-PART-1).
> No model in this inventory is yet integrated. **Each model
> family must clear the approval criteria below** before a PR
> introduces it.

## Scope

WS6 adds open-source models — primarily from Hugging Face, but
the framework is provider-agnostic — *after* baseline systems
(WS5-A..F) are stable. Open-source models are auxiliary, not
replacement, for the deterministic baselines.

## Non-negotiable rules

1. **No model is added because it is fashionable or large.** A
   model joins the inventory because a documented use case
   demands it. Vanity additions are rejected at review.
2. **No model influences live trading without staged promotion.**
   Same 3-stage ladder applies — `candidate` → `shadow` →
   `advisory` — regardless of where the model came from. (Ladder
   collapsed 7→3 on 2026-06-16; legacy names alias via
   `ml.manifest.canonical_stage`: `research_only`/`backtest_approved`
   → `candidate`; `limited_live`/`live_approved` → `advisory`.)
3. **No model runs on the live trader VM.** Inference for any
   external model runs either on the training-center VM
   (S-AI-WS9) or off-VM via HTTP API. The live trader stays
   deterministic; rotation policy stays as policy.
4. **PEFT / LoRA / adapter-style tuning is preferred over full
   fine-tuning** when adaptation is needed. Full fine-tuning
   requires a separate operator approval and a documented
   resource budget.
5. **No model is auto-published to HF.** (Pre-existing rule
   from S-AI-WS3; reaffirmed here.) Trained adapters stay in
   the registry; pushing artifacts public is a separate
   operator action.

## Approval criteria (gate before adding ANY new model family)

A pull request adding a model to this inventory MUST document:

| Criterion | What to provide |
|---|---|
| **Use case** | Concrete user-visible benefit. "Sentiment scoring for news headlines feeding strategy X" — not "we should have an LLM". |
| **Measurable gain over baseline** | Vs. WS5 baselines or vs. the deterministic strategy without the model. Use the same evaluator family the baselines use (`ml/evaluators/`). |
| **Latency budget** | Per-call latency on the runtime that will host it. p50 + p99. If hosted off-VM, include round-trip from the live trader's perspective. |
| **Infra cost** | Cost per call, cost per training run, cost per active deployment. Operator-readable estimate; no hidden cloud burn. |
| **Rollback path** | How to disable the model without redeploying. Default: same `shadow_model_ids` YAML toggle WS7 uses. |
| **Provider lock-in** | Note any APIs that don't have open-source equivalents. Prefer architectures where switching providers is a config change, not a refactor. |

## Inventory by use case

> Models listed here are **candidates**, not approved
> dependencies. A model becomes an approved dependency only
> after a PR documents the approval criteria above + ships the
> integration (provider, version pin, registration in the
> model registry).

### Text — news / journaling / operator notes

Open-source candidates for processing free-form text that flows
into the trading pipeline (news headlines, operator's trade
journal, dashboard alerts that need summarization).

| Family | Candidates | Notes |
|---|---|---|
| Lightweight encoder (sentiment, classification) | DistilBERT, MiniLM, RoBERTa-base | ~100–250M params; fast on CPU; well-suited to news headline sentiment. |
| Encoder with longer context | DeBERTa-v3 base/large | Stronger reasoning on multi-sentence text. |
| Instruction-tuned small LLM | Llama-3.1-8B-Instruct, Mistral-7B-Instruct, Qwen2.5-7B-Instruct | Operator-readable summaries. Needs GPU or quantization for latency budget. |

### Embedding — retrieval / similarity

For "find similar past trades" / "find similar market conditions"
lookups against `trade_journal.db` + `market_features`.

| Family | Candidates | Notes |
|---|---|---|
| General-purpose sentence embedding | bge-large-en-v1.5, gte-large, e5-large | ~300–500M params; CPU-tractable; good cross-domain. |
| Domain-tuned (finance) | FinBERT-derived, bge-finance | Smaller domain corpus; useful only if a vendor or community ships one we trust. |

### Tabular / time-series — market prediction

Most of the WS5 baselines fit here. Open-source additions in this
family are likely **only** when one of the following holds:

- A baseline plateaus on `setup_quality` or `r_multiple`
  evaluator metrics, AND a candidate model (e.g. gradient-boosted
  trees from `xgboost` / `lightgbm`) shows measurable lift in a
  walk-forward backtest.
- A transformer architecture suited to OHLCV time-series (e.g.
  PatchTST, TimesFM) shows useful generalisation on our windows.

| Family | Candidates | Notes |
|---|---|---|
| Gradient-boosted trees | xgboost, lightgbm | Open-source, on-prem, CPU-friendly. The standard non-deep baseline beyond linear models. Filed but not prioritised — the WS5-C setup-quality scorer hasn't yet plateaued. |
| Time-series transformers | TimesFM, PatchTST | Higher cost; revisit once trade history + OHLCV history are large enough to justify. |

### Reasoning / orchestration — research, offline

Models that don't touch the live trader at all — they support
research workflows, code generation for new strategies, or
operator decision-grade composition.

| Family | Candidates | Notes |
|---|---|---|
| Open-weights reasoning | Llama-3.1-70B-Instruct, Mixtral-8x22B, Qwen2.5-72B-Instruct | Off-VM inference (HF Inference Endpoints / vLLM / local lab). Operator-facing only; never invoked from the runtime. |

## How a new model gets approved

1. Open a PR that:
   - Edits this file: add a row under the correct use case.
   - Documents the approval criteria above (PR body).
   - Ships an `ExternalPredictor` subclass (see
     [`ml/predictors/external.py`](../../ml/predictors/external.py))
     OR a thin trainer/registrant if the model is pre-trained
     and just needs to land in the registry.
   - Pinning: includes the exact model identifier + revision
     (`org/model-name@sha-or-tag`).
   - Ships a paired manifest under `ml/configs/` if the model
     produces predictions for one of our standard families.
2. The PR enters the same review path as any WS5 baseline.
   No special fast-track for "AI" PRs.
3. Once merged, the model lands in the registry at
   `target_deployment_stage: candidate`. Promotion follows
   the 3-stage ladder (`candidate → shadow → advisory`).

## What's deliberately NOT in this inventory yet

- **No specific HF model integration.** PART-1 ships the
  framework + the rules. PART-2 (filed) integrates the FIRST
  specific model — chosen against a real use case, not
  speculative — once one of the WS5 baselines plateaus or a
  text-feedstock workflow needs sentiment scoring.
- **No vendor APIs.** OpenAI / Anthropic / Cohere / etc. APIs
  are out of scope for WS6. The framework
  (`ExternalPredictor`) doesn't preclude them, but adding a
  paid provider needs its own approval gate beyond the
  open-source criteria above.

## Related

- [`docs/architecture/ai-model-platform.md`](ai-model-platform.md) —
  five-layer AI architecture; the layer this inventory sits in.
- [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md) —
  master plan; non-negotiables.
- [`ml/predictors/external.py`](../../ml/predictors/external.py) —
  provider-agnostic `ExternalPredictor` ABC + `ProviderError`.
