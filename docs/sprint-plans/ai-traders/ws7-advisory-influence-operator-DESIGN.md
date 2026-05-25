# WS7 — Advisory influence operator (DESIGN — operator sign-off required)

**Status:** 📋 Proposal — **not implemented.** This is the design for the
one missing piece that lets an ML model actually change a live trade.
Nothing in this document is built yet; it needs operator sign-off on the
contract before any code lands. Tier-3 (live order path).

**Companion (already shipped, this PR):** the *decision-support* half —
`model-attribution`, `gate-check`, `stage-guard` (read-only evidence +
proposals). Those tell you *whether* a model has earned influence. This
doc is the *mechanism* by which a model, once you approve it, exerts that
influence.

## Problem

Today the deployment ladder's "act" half does not exist:

- `Coordinator.log_advisory_scores()` is explicitly *"No order action
  taken… the live order path is completely unaffected."*
- The live signal path calls the observe-only `with_shadow_preds`;
  `with_shadow_preds_advisory` is referenced only in a docstring.
- No code anywhere converts a model score into a size change, a veto, or
  a confidence adjustment.

So promoting a model to `advisory` changes nothing about a trade — it
just starts writing a second log file. WS7 Task 3 ("model outputs
annotate or veto") and the acceptance criterion "every live-influencing
model has a fallback / disable path" are unmet.

## Goal

A **bounded, reversible** mechanism by which an `advisory`+ model
influences an `OrderPackage`, with these non-negotiables:

1. **Reductive-only in v1.** A model may make the bot trade *less*
   (veto a trade, or shrink size) — never *more*. It can never create a
   trade, enlarge size, widen risk, or loosen a stop. This bounds the
   downside of a bad model to "missed/over-trimmed trades," never
   "amplified losses."
2. **RiskManager stays the final gate.** Influence is applied to the
   package *before* the existing per-trade risk checks; those checks are
   never bypassed or relaxed.
3. **Deterministic fallback.** Model unavailable / errors / returns
   garbage → the deterministic package passes through unchanged (reuse
   the existing per-predictor `try/except` contract).
4. **Default off, declared in YAML.** Gated by `ADVISORY_MODE` (already
   exists, default false) AND per-strategy opt-in. Omitting config =
   today's behavior, byte-for-byte.
5. **Fully audited.** Every influenced decision logs intended-vs-final
   package so the effect is reconstructable.

## Where it plugs in

The strategy `order_package()` path already builds a deterministic
package then threads it through `with_shadow_preds`. v1 adds a sibling
step, active only when the gates above are satisfied:

```
package = _build_deterministic_package(cfg, candles_df)         # unchanged
feature_row = _build_shadow_feature_row(package)               # unchanged
package, scores = with_shadow_preds_advisory(package, ...)     # observe (existing)
package = apply_advisory_influence(package, scores, policy)    # NEW (reductive-only)
return package
```

`apply_advisory_influence` is a pure function: `(package, scores,
policy) -> package`. It is the only new code on the order path, and it
can only ever return a package that is "smaller or equal" to its input.

## Influence modes (v1 ships veto-only; size-scale behind a second gate)

| Mode | Effect | v1? |
|------|--------|-----|
| `veto` | If a quorum of advisory models score below `veto_threshold`, the package is suppressed (status `advisory_veto`, logged, no order). | ✅ first |
| `size_scale` | Multiply position size by `f ∈ [size_floor, 1.0]` derived from the score. Never > 1.0. | gated 2nd |
| `annotate` | Attach score to the package for the journal; no order effect. | ✅ (free) |

`veto`-only is the safest first step: the worst a broken model can do is
stop a trade that would otherwise have happened. We soak that on a demo
account before enabling `size_scale`.

## Config (proposed)

```yaml
# config/strategies.yaml  (per strategy)
vwap:
  advisory_model_ids: ["trade-outcome-winrate-baseline-v0"]   # [] = opt out
  advisory_policy:
    mode: veto                 # veto | size_scale | annotate
    veto_threshold: 0.35       # quorum of advisory scores below this → veto
    quorum: 1                  # how many advisory models must agree
    size_floor: 0.5            # only used by size_scale; never below this
```

Global enable stays `ADVISORY_MODE` (env / settings, default false).
Both gates must be on; either off = pass-through.

## Invariants & tests (defence-in-depth)

- **Never increases risk.** Property test: for any score, `apply_advisory_influence`
  output size ≤ input size, SL no wider, TP unchanged, direction unchanged.
- **Flag off = identity.** With `ADVISORY_MODE` off (or empty
  `advisory_model_ids`), output is byte-identical to input.
- **Fallback determinism.** A raising predictor leaves the package
  unchanged.
- **Quorum logic.** Veto fires only when ≥ `quorum` advisory models are
  below threshold.
- **Stage gate.** Only `advisory`+ models influence; `shadow` models are
  observe-only (existing `ShadowPredictor` stage gate).

## Promotion / demotion (both operator-gated)

- **Promote** to advisory only when `gate-check` reports `ready: true`
  (this PR's gates) **and** the operator runs `promote-stage`.
- **Demote** is **also operator-gated** (operator policy, 2026-05-25).
  `stage-guard` surfaces a demote *proposal* with evidence (drift /
  degeneracy / live underperformance); the operator runs `promote-stage
  <id> --new-stage shadow`. No automatic demotion.

## Rollout

1. Land `apply_advisory_influence` (veto-only) + config + tests. Default off.
2. Enable on **one** strategy on a **demo** account; soak ≥ 7d; review
   `advisory_decisions.jsonl` (how often it would have vetoed, and the
   realized outcome of vetoed-vs-taken via attribution).
3. Operator approves first live `advisory` (veto-only) on one strategy.
4. Add `size_scale` behind its own gate; repeat soak.
5. `limited_live` / `live_approved` are later, separately-gated steps.

## Open questions for sign-off

1. **First model + strategy.** Which model and strategy get the first
   veto-only advisory wire? (Recommendation: `trade-outcome-winrate` on
   the highest-volume strategy, once it has ≥ 200 live trades and clears
   `gate-check`.)
2. **Veto semantics.** Hard suppress, or downsize-to-floor instead of
   full veto? (Recommendation: hard veto in v1 — simplest to reason about.)
3. **Quorum default** when multiple advisory models are wired (any-1 vs
   majority).
4. **Demo vs paper** for the soak account.
