# M26 — Regime-transition & directional-conflict intelligence (design of record)

> **Status:** 📋 PROPOSED 2026-07-19 (operator-directed). Evidence-gathering and
> backtests are Tier-1 autonomous; anything that changes routing, sizing, or an
> exit is Tier-3, walk-forward-gated, operator-approved.
> **Operator framing (verbatim intent):** (a) when new trades bet AGAINST the
> direction of existing trades, that is a strong signal of a change in market
> direction — we should be set up to ride the wave instead of getting crushed by
> it; (b) different strategies work on different timeframes, so "conflicting"
> trades can be legitimate when their timescales are vastly different (a
> multi-day long can coexist with minutes/hours-scale shorts inside it); (c) we
> bleed capital whenever the market isn't moving in one clear direction — the
> decision system needs a milestone dedicated to fixing this.

## The problem, in system terms

Today's conflict handling is a single blunt rule: `FLIP_POLICY=hold` (intent
layer, 2026-05-31) — an opposing new intent is dropped and the existing
position rides its own exits. That policy beat `reverse` in the 24-cell
walk-forward, but it throws away two kinds of information:

1. **The transition signal.** An opposing signal — especially a *cluster* of
   them across strategies — is evidence the tape is turning. Today that
   evidence is logged (`hold` deltas in the intent audit) and then ignored: the
   existing position neither tightens its exit nor resizes, and the new signal
   is discarded entirely. Worst case we sit through the full adverse move to
   the original stop (get crushed), and never take the other side (don't ride).
2. **The timeframe axis.** The intent layer resolves conflicts on net direction
   per symbol with no notion of holding horizon. A 1h/multi-day trend long and
   a 5m/15m short are not actually in conflict — they are different bets on
   different clocks — but today the shorter one is simply suppressed.

And the chop bleed: the regime router gates per-cell on (trend, vol) *level*
classification, but transitions between regimes — exactly where opposing
signals cluster and where chop lives — have no first-class representation
anywhere in the decision path (regime heads classify the current bar's state,
not the turn). M23's meta-label program independently measured the cost: the
book's take-all is deeply net-negative in non-directional stretches.

## What already exists to build on (do not rebuild)

| Piece | Where | Reuse |
|---|---|---|
| Conflict events, already logged | intent audit rows (`hold` policy deltas, `signal_audit.jsonl` + signals dual-write) | P0's raw material — no new instrumentation needed to mine history |
| Dormant flip-override knobs | `FLIP_CONFIDENCE_THRESHOLD` / `FLIP_MIN_POSITION_AGE_HOURS` (built, default-off, Tier-3) | P3 policy arm (b) — already implemented, never validated |
| Flip-policy backtest arms | `scripts/backtest_system.py --flip-policy` | P3 harness — extend with new arms rather than writing a new harness |
| Regime router + cells | `config/regime_policy.yaml`, `intents.py` hard gate | P2 integration point for a transition axis |
| Exit levers (tighten/trail) | M20 exit-refinement lever library + exit-head soaks | P3 arm (d) — transition-triggered exit tightening |
| Reductive sizing shape | `NEWS_INFLUENCE_MODE`-style `*_MODE` selectors (off/annotate/apply) | P4 rollout shape for allocator-level throttling |
| Allocator soak plumbing | M18 P0b/P0c intent-multiplexer opportunity set + `allocator_soak` | P4 gross-exposure throttle hook (M18 selection stays parked) |

## Phased plan (observe → advise → gate → apply)

**P0 — Quantify the bleed (Tier-1, first session).** Mine the journal +
intent audit for every conflict event (new intent opposing an open position,
per symbol): frequency, clustering, and the realized PnL of the held position
from conflict-time to close vs three counterfactuals (close-at-conflict,
flip-at-conflict, tighten-stop-at-conflict), stratified by (timeframe ratio,
strategy pair, regime cell, confidence gap). Deliverable: a findings doc that
puts a $ / R number on "hold ignored the warning" and identifies WHERE the
bleed concentrates (same-TF conflicts? chop cells? specific strategy pairs?).
This is the go/no-go evidence for everything downstream — if hold is actually
fine, we stop here honestly.

**P1 — Conflict taxonomy + timeframe-aware policy design (Tier-1).** From P0
data, define the conflict matrix: `timeframe_ratio ≥ K` (e.g. ≥4–8×) →
**legitimate coexistence** (the short-clock trade is allowed to run against the
long-clock position, sized so combined exposure stays inside caps — an
intentional partial hedge); `timeframe_ratio < K` → **transition vote** (the
conflict is real evidence, feeds P2). Design doc + exact proposed
`intents.py` semantics; no code on the order path yet.

**P2 — Transition detector (observe-only soak, Tier-1 to build).** A
transition score per symbol from: opposing-signal cluster density (the
operator's core insight), regime-head label flips, and vol/trend cell boundary
crossings. Ships as an **annotate-only** audit field + soak log (the same
observe-first shape as every other layer), with the M25 parity-first
discipline: mechanics verified at the first review, edge proven offline in P3
— NOT by waiting on the soak.

**P3 — Backtest the policy arms (Tier-1 harness, the decisive gate).** Extend
`backtest_system.py` with arms over the full multi-year history: (a) `hold`
(incumbent baseline), (b) confidence-gap flip (existing dormant knobs), (c)
TF-aware coexistence per P1 matrix, (d) transition-triggered exit-tighten
(M20 levers fired by the P2 score — "ride the wave" = get out of the wrong
side fast + let the new side run), (e) transition-triggered gross-exposure
throttle (chop defense: when the transition score is high and direction is
unclear, cut new-entry size toward a floor — reductive only). Walk-forward,
per-cell, net-of-cost. Winner(s) must beat `hold` out-of-sample.

**P4 — Tier-3 rollout (operator-gated).** The winning arm ships behind a
`*_MODE` selector (`off`/`annotate`/`apply`), reductive/defensive defaults,
kill-switch, exact YAML/env diff in the packet, live-verified post-deploy.
Allocator-level throttling lands here as sizing influence — NOT as allocator
selection (M18 P2 stays parked pending a proven P_win input).

## Non-goals / honesty

- Not a new strategy and not a re-run of the M18 allocator-selection question.
- No live behaviour change before P3 beats `hold` out-of-sample — `hold` won
  its walk-forward fairly in May; it is displaced by evidence, not by theory.
- P2's soak follows the M25 reframe: mechanics-parity within days; the edge
  case is made offline. No multi-week soak gates.

## Anchors

- Backlog anchor: `MB-20260719-M26-TRANSITION-CONFLICT` (ml-review backlog).
- Composes with: M20 (exit levers are the "ride the wave" actuator), M23 (the
  chop-bleed measurement), M24 (net-R labels grade the arms honestly), M25
  (parity-first soak discipline), M14 regime program (transition axis).
