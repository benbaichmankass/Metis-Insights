# WS6 — Open-source model layer

**Master plan:** [`docs/AI-TRADERS-ROADMAP.md`](../../AI-TRADERS-ROADMAP.md)
**Milestone:** M9
**Status:** 📋 Not started — blocked on at least one baseline in WS5.

## Objective

Add open-source models through Hugging Face only after baseline systems are
stable.

## Tasks

1. Author `docs/architecture/model-inventory.md` listing candidate
   open-source models by task.
2. Separate model families by use case:
   - text — news, journaling, operator notes,
   - embedding — retrieval, similarity,
   - tabular / time-series — market prediction,
   - optional larger reasoning — research / offline orchestration.
3. Prefer PEFT, LoRA, or adapter-style tuning before full fine-tuning.
4. Approval criteria for any new model family:
   - measurable gain over baseline,
   - manageable latency,
   - acceptable infra cost,
   - safe rollback path.
5. Keep the training interface provider-agnostic even if Hugging Face is
   the first implementation target.

## Acceptance

- Documented open-source model inventory.
- Every added model family has a defined task and measurable success
  criteria.
- No model is added only because it is fashionable or large.
