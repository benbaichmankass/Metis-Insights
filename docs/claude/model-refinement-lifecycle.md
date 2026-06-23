# Underperformer refinement lifecycle (models) — 2026-06-23

> **Principle.** An underperforming model is **refined, not abandoned.** Every
> model that degrades (an `advisory` model losing its edge) or stalls (a
> `shadow` model that keeps failing the promotion gate) is routed through a
> **tracked refine → re-evaluate → re-shadow-or-retire loop**, owned by
> [`/ml-review`](../../.claude/skills/ml-review/SKILL.md) and logged in
> [`ml-review-backlog.json`](./ml-review-backlog.json). It ends in exactly one
> of two terminal states — **restored** (back to influencing) or **retired**
> (turned off) — and the decision is recorded. No model is left silently
> drifting on the live order path, and no failing model is left to spin in the
> registry with no plan.

This is the **model** half. The **strategy** half already exists and is owned by
[`/performance-review`](../../.claude/skills/performance-review/SKILL.md): the
[`strategy-refinement-queue.json`](./strategy-refinement-queue.json)
(`paper_ready` cells) + the **M7 review gate**
([`strategy-review-gate.md`](../strategy-review-gate.md):
`KILL`/`DEMOTE_SHADOW`/`TUNE`/`HOLD`/`PROMOTE`). The two mirror each other; this
doc is the model-side spec. The unified rule across both: **detect → refine →
re-gate → restore or retire, with the decision in the review log.**

## The loop

```
            ┌──────────────────────── /ml-review (every session) ───────────────────────┐
            │  DETECT: python -m ml stage-guard  (+ gate-check + shadow-drift)           │
            └───────────────┬───────────────────────────────────────┬───────────────────┘
                            │ demote proposal (advisory degrading)   │ promote proposal (shadow gates pass)
                            ▼                                         ▼
        1. DEMOTE advisory→shadow (Tier-3, on operator OK)     propose shadow→advisory (Tier-3)
        2. open MB-… refinement item (status: open)            — normal promotion path
                            │
                            ▼
        REFINE (model-training skill): retrain on fresh data / repair or add features /
        new target horizon / recalibrate / fix the input pipeline (if the score collapsed).
        Each attempt appended to the item's `updates[]`.
                            │
                            ▼
        RE-EVALUATE (next /ml-review): python -m ml gate-check <id>
           ├─ recovered (live_agreement ≥ 0.55, drift clean, non-degenerate, oos_edge)
           │     → re-propose shadow→advisory; resolve item `restored`.
           └─ still failing AND (attempts ≥ N=3  OR  fundamentally broken: degenerate /
                 no oos_edge / inverted live discrimination with no repair hypothesis)
                 → RETIRE; resolve item `retired`.
```

## Detection — the engine

`python -m ml stage-guard --db <journal>` (read-only;
[`ml/promotion/stage_guard.py`](../../ml/promotion/stage_guard.py)) emits a
`promote | demote | hold` proposal per model from the canonical triggers — run
it **every `/ml-review`**:

- **Demote** an `advisory` (order-influencing) model when any
  `_demote_triggers` fire: score-distribution **drift verdict `significant`**,
  **live score collapse** (output spread ≤ `score_spread_eps` — the degenerate
  "stuck at one value" case), **`brier_lift < 0`** (calibration worse than base
  rate), or **`AUC < 0.5`** (live discrimination inverted).
- **Promote** a `shadow` model when **all** advisory gates pass.
- **Hold** otherwise (records the blocking gate).

`stage-guard` only *proposes*; the operator runs `promote-stage` to act (Tier-3).
`/ml-review` is responsible for turning each non-`hold` proposal into a backlog
item and driving it.

## "Turn off" — two depths

| Depth | Mechanism | Effect |
|---|---|---|
| **Soft off** | demote `advisory → shadow` (`promote-stage <id> --new-stage shadow`) | keeps observing + logging predictions; **no order influence** |
| **Hard off (retire)** | demote `→ candidate` (shadow factory **refuses** `candidate`, so it emits **no predictions**) **+** mark the manifest deprecated so it drops from the training rotation | fully off — neither influences nor accrues a track record |

A degrading live model goes **soft off first** (immediate safety), then through
refinement; only an unrecoverable model is **retired** (hard off).

## Backlog contract (the log)

Each lifecycle item lives in [`ml-review-backlog.json`](./ml-review-backlog.json)
using the standard item schema, with these conventions:

- **`title`** prefixed `[refinement]` and **`source`** citing the `stage-guard`
  trigger + the gate/drift numbers.
- **`trigger_condition`** = the measurable thing that re-opens evaluation
  (e.g. "next trainer cycle retrains <id>" / "≥N live scored trades accrue").
- **`resolution_criteria`** = the explicit restore-or-retire bar (e.g. "re-gate
  clears live_agreement ≥ 0.55 + drift clean → restore to advisory; else after
  3 refinement attempts → retire to candidate").
- **`updates[]`** = one entry per refinement attempt (what changed, the new
  metric, the re-gate verdict).
- **`status`** ∈ `open | resolved | invalid | snoozed`; on resolve, the final
  `updates[]` entry records `restored` or `retired` + the `promote-stage`
  command that was run.

## What `/ml-review` does each session (added 2026-06-23)

1. Run `python -m ml stage-guard` and record every non-`hold` proposal.
2. For each **new** demote/underperformer → demote soft-off (propose, Tier-3)
   and open a `[refinement]` backlog item with a concrete hypothesis.
3. Drive each **open** refinement item **one step**: log a refinement attempt,
   or re-gate, or resolve (`restored` / `retired`).
4. Surface the proposals in `promotion_recommendations[]` and the in-flight
   refinement state in the response's `backlog_drain[]`.

The promotion of a recovered model past `shadow`, and the retire decision, are
both **Tier-3** — `/ml-review` proposes with evidence; the operator approves the
`promote-stage` flip.
