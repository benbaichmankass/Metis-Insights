# WS4 — Training center

**Master plan:** [`docs/AI-TRADERS-ROADMAP.md`](../../AI-TRADERS-ROADMAP.md)
**Milestone:** M9
**Status:** 📋 Not started — blocked on WS3 (needs at least one
reproducible dataset family).

## Objective

Repo-native training center that behaves like a repeatable factory for
training, evaluation, registration, and promotion.

## Target structure

The existing `ml/` tree has only `ml/config/` and `ml/src/`. WS4 expands it
to:

```text
ml/
  configs/
  datasets/
  features/
  labels/
  trainers/
  evaluators/
  experiments/
  registry/
  promotion/
  reports/
  src/        # existing
```

## Tasks

1. Define a YAML training-manifest format containing: model family,
   dataset version, feature set, label spec, objective, evaluation suite,
   target deployment stage.
2. Build CLI / Make entry points for the standard lifecycle:
   - `build-dataset`
   - `train`
   - `evaluate`
   - `compare`
   - `register`
   - `promote`
3. Add experiment-tracking metadata. File-based first version is fine
   (`ml/experiments/<run-id>/`).
4. Add a model-registry file or folder (`ml/registry/`) with status
   categories: `candidate`, `champion`, `paper`, `advisory`,
   `live-approved`.
5. Author `docs/ml/model-registry-policy.md` describing transitions and
   approvals.
6. Author `docs/ml/training-center.md` describing the directory layout,
   manifest schema, and command path.
7. Promotion checklist covering: leakage checks, walk-forward checks,
   transaction-cost-aware evaluation, rollback notes.
8. Tie all training artifacts to a specific dataset version + code
   revision.

## Acceptance

- Documented training center exists in the repo.
- At least one model trains and evaluates via a repeatable command path
  (the first WS5 baseline is a good acceptance proof).
- Model-registry metadata supports promotion-state tracking.

## Out of scope

- Building advanced (HF) model families — that is WS6.
- Wiring shadow-mode into the live runtime — that is WS7.

## Risks

- Heavy training drifting onto Oracle VM. Mitigation: WS9 rule —
  `train` / `compare` documented as Hugging Face / external compute by
  default; only smoke-sized runs allowed locally on the Oracle box.
