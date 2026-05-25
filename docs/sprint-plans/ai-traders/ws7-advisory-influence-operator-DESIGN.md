# WS7 — Advisory influence operator (DESIGN — operator sign-off required)

**Status:** 🔄 **Rollout step 1 BUILT (default-off, not wired)** — the
operator + config contract + gate + invariant tests shipped as
`src/runtime/advisory_influence.py` (2026-05-25). It is **inert**: with
`ADVISORY_MODE` off (the default) and no strategy supplying an
`advisory_policy`, no order is ever touched. **Wiring it onto a real
model/strategy (rollout step 2+) is the live switch and still needs
operator sign-off** on the contract + the open questions below. Tier-3.

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

## Influence modes (v1 ships downsize-to-floor; operator decision 2026-05-25)

| Mode | Effect | v1? |
|------|--------|-----|
| `downsize` | If a quorum of advisory models score below `bearish_threshold`, scale position size to `size_floor × qty` (never below the floor, never above 1.0). | ✅ first |
| `annotate` | Attach score to the package for the journal; no order effect. | ✅ (free) |
| `veto` | Special case of `downsize` with `size_floor = 0` (full suppression). Available, not defaulted. | via floor=0 |

The operator chose **downsize-to-floor over a hard veto** (2026-05-25):
the worst a bearish quorum can do is shrink the position to the floor, not
zero it out. A hard veto remains reachable as `size_floor: 0.0`.

## Config (per operator decisions)

```yaml
# config/strategies.yaml  (per strategy)
vwap:
  advisory_model_ids: ["trade-outcome-winrate-baseline-v0"]   # [] = opt out
  advisory_policy:
    mode: downsize             # off | annotate | downsize
    bearish_threshold: 0.35    # a model is "bearish" when its score < this
    size_floor: 0.5            # smallest fraction of intended size a downsize leaves
    quorum: majority           # majority (default) | <int>  — how many bearish models trigger
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
- **Quorum logic.** Downsize fires only when ≥ `quorum` advisory models
  are below the bearish threshold; `quorum: majority` resolves to
  `n_scored // 2 + 1`.
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

1. ✅ **DONE (2026-05-25)** — `apply_advisory_influence` (downsize +
   annotate) + `AdvisoryPolicy`/`parse_policy` + 15 invariant tests, in
   `src/runtime/advisory_influence.py`. Default off; not wired to any
   strategy.
2. Wire onto **whichever model+strategy clears `gate-check` first** (see
   open-question 1 resolution); soak ≥ 7d on the demo/paper account;
   review `advisory_decisions.jsonl` (how often it downsized, and the
   realized outcome of downsized-vs-full via attribution).
3. Operator approves first live `advisory` (downsize) on that strategy.
4. `limited_live` / `live_approved` are later, separately-gated steps.

## Open questions — RESOLVED (operator, 2026-05-25)

1. **First model + strategy** → **data-driven, not predetermined.** Wire
   whichever model+strategy *first accumulates enough live data to clear
   `gate-check`* — `stage-guard` surfaces the first `ready: true` model.
   (Given current data, the `trade-outcome` family on the highest-volume
   BTCUSDT strategy is the likely first to cross the ≥200-trade bar.)
2. **Influence semantics** → **downsize-to-floor, NOT hard veto.** A
   bearish quorum shrinks the position to `size_floor × qty`; never zero
   (unless `size_floor` is explicitly set to 0).
3. **Quorum** → **majority.** A majority of the wired advisory models must
   be bearish before a downsize applies (`quorum: majority` →
   `n_scored // 2 + 1`), not a single model.
4. **Demo vs paper** → **same thing here** (both are non-real-money). Soak
   runs on the non-real-money account; no separate choice.
