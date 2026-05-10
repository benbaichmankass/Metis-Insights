# WS2 — Canonical trade pipeline

**Master plan:** [`docs/AI-TRADERS-ROADMAP.md`](../../AI-TRADERS-ROADMAP.md)
**Milestone:** M9
**Status:** 📋 Not started — blocked on WS1 landing.

## Objective

Formally define the trade lifecycle so model work can plug in cleanly.

## Pipeline stages (default)

1. Market and account ingest
2. Normalization
3. Context assembly
4. Setup detection
5. Opportunity scoring
6. Risk gating
7. Execution packaging
8. Broker routing
9. Post-trade capture
10. Review and feedback

If repo inspection in WS1 reveals a safer naming scheme, adopt that and
record the deviation in the AI-platform doc.

## Tasks

1. Add typed schemas (dataclass / pydantic) for stage inputs and outputs in
   a shared module (proposed: `src/pipeline/types.py`).
2. Define canonical `TradeCandidate` and `ExecutionIntent` objects.
3. Mark which stages are deterministic and which may consume model scores.
4. Document and **enforce in code** that risk and broker validation may
   reject outputs from any model. Models cannot place or modify orders
   outside the validated execution-packaging path.
5. Per-stage logging requirements (fields + log levels).
6. Test scaffolding for stage contracts under `tests/pipeline/`.
7. Update `docs/architecture/ai-model-platform.md` with the final stage
   list and per-stage owner files.

## Acceptance

- Every pipeline stage has a documented owner and I/O contract.
- Repo contains shared types or schemas for trade candidates and execution
  intents.
- Explicit rule encoded in code and docs: models cannot bypass risk gating
  and execution packaging.

## Out of scope

- Implementing any new model.
- Changing live order-routing behavior.

## Risks

- Schema churn. Mitigation: keep first version minimal, mark optional
  fields, version via doc rather than code rename until stabilized.
