# M26 P1 — Conflict taxonomy + timeframe-aware policy design (2026-07-22)

> **Status: Tier-1 design doc.** Defines the conflict matrix and the *exact*
> proposed `intents.py` semantics from the P0 evidence. **No order-path code
> ships from this doc** — the policy arms are backtested in P3 and must beat
> `hold` out-of-sample before any live behaviour change (P4, Tier-3). This is
> the design of record the P2 detector and P3 harness build against.
>
> Inputs: `M26-P0-conflict-bleed-findings-2026-07-19.md` (full-coverage rerun,
> 121 measured pairs) + `M26-regime-transition-conflict-DESIGN.md`.

## 1. What P0 established (the empirical basis, not theory)

The full-coverage P0 rerun (121 trade-conflict pairs) split the conflict bleed
cleanly along the **two axes the design proposed**, and sharpened one:

- **Timeframe ratio.** Cross-clock conflicts (fast signal vs slow position,
  ≥4× clock ratio) are **benign-to-positive** to hold through (+$4.7k held).
  Same-or-near-clock conflicts (<4×) **lose money BOTH ways** (held −$3.0k AND
  flip −$7.1k; close beat hold 65.8%). → The right response to a same-clock
  conflict is **go flat / tighten**, not hold and not reverse.
- **Held-strategy class.** The bleed concentrates by exit style:
  `htf_pullback_trend_2h` held-worse **93.6%** (−$6.0k, the fix-me cell);
  `trend_donchian` held-BETTER (+$10.1k, flipping it would have burned −$21k —
  the never-touch cell). → Any policy must be **per-class**, never global.
- **Blanket flip stays dead** — flipping burned ~$17.3k paper, ~$10.2k even in
  the benign cross-TF stratum. The May walk-forward that chose `hold` over
  `reverse` was right *on average*; it is displaced only conditionally.

So the taxonomy is a **2-key lookup** — `(timeframe_relationship, held_class)`
— not a single threshold.

## 2. Where conflict is resolved today (the code the taxonomy modifies)

`src/runtime/intents.py::compute_execution_delta` (the pure per-symbol delta
function). When a new desired intent **opposes** an open position, control
reaches the flip-policy block (`intents.py:1507`): `policy = resolve_flip_policy()`
(`hold` default), then:

- `hold` → `noop` with `flip_suppressed_hold_policy` (unless the dormant
  `FLIP_CONFIDENCE_THRESHOLD`/`FLIP_MIN_POSITION_AGE_HOURS` override fires);
- `flat` → `close`, no re-open;
- `reverse` → `flip` (close + reopen).

The resolution is **purely net-direction per symbol** — there is no notion of
the *clock* of either trade, nor of *which strategy owns* the position being
held. Both keys the P0 data proved decisive are **absent** from the decision.
That is exactly the gap P1 designs to fill.

**What metadata is already available at decision time** (so P1 adds no new
plumbing on the read side):

| Key needed | Source that already exists |
|---|---|
| New intent's strategy + clock | `StrategyIntent.strategy` (`intents.py:493`); timeframe ← `config/strategies.yaml::<name>.timeframe` |
| New intent's confidence / regime | `StrategyIntent.confidence` / `.regime` / `.vol_regime` (already threaded) |
| Held position's owner strategy | the open `trades` row's `strategy` (coordinator has it when it computes the delta) |
| Held position's clock | same → `config` timeframe of the owner |
| Held position's age / entry confidence | `existing_age_hours` / `existing_confidence` already passed into `compute_execution_delta` (`intents.py:395`) |

The one new input `compute_execution_delta` needs is the **held position's owner
strategy name** (to derive its class + clock), threaded the same way
`existing_confidence`/`existing_age_hours` already are.

## 3. The conflict taxonomy (the 2-key matrix)

Let `r = clock(new) / clock(held)` expressed as the ratio of the **slower to
faster** bar seconds (always ≥1; direction-agnostic — a fast signal against a
slow hold and vice-versa both map to the same `r`). Threshold **K = 4** (from
P0: the ≥4× stratum is where hold turned positive; <4× is where it bled).

### 3a. Axis 1 — timeframe relationship

| `r` | Class | Meaning | P0 evidence |
|---|---|---|---|
| **≥ K (=4)** | **cross-clock / coexistence** | The fast trade and slow position are different bets on different clocks. The conflict is *not* a transition vote. | +$4.7k held; benign |
| **< K** | **same-clock / transition vote** | Genuine opposition on one horizon — real evidence the tape is turning. | −$3.0k held AND −$7.1k flip; close wins |

### 3b. Axis 2 — held-strategy exit class

Classify the **held** strategy by exit style (config-driven, see §4b):

| Class | Members (today) | P0 hold behaviour | Design intent |
|---|---|---|---|
| **trend-rider** | `trend_donchian`, `squeeze_breakout_4h`, `*_trend_*` | hold is the edge (+$10.1k) | **never touch** — pure `hold` |
| **mean-reversion / pullback / scalp** | `htf_pullback_trend_2h`, `ict_scalp_*`, `*_pullback_*`, `vwap`, `fvg_*` | hold bleeds (93.6% worse) | **transition-tighten candidate** |
| **other / unknown** | pairs, fade, unmapped | small-n, mixed | default `hold` (conservative) until evidence |

### 3c. The combined matrix → proposed *action intent* (design, P3-gated)

The cell yields a **proposed action** — what P3 will backtest against `hold`.
It is NOT enacted by this doc.

| held class \ TF rel | cross-clock (`r ≥ 4`) | same-clock (`r < 4`) |
|---|---|---|
| **trend-rider** | `hold` (+ allow fast leg to coexist*) | `hold` (donchian edge; P0 −flip catastrophic) |
| **mean-rev / pullback / scalp** | `hold` (cross-clock benign) | **`transition_tighten`** ← the primary P3 arm |
| **other / unknown** | `hold` | `hold` (until per-pair evidence) |

`*coexist` = the design's arm (b): rather than suppressing the fast counter-signal
against a slow trend position, allow it to run as its **own** position, sized so
combined gross exposure stays inside caps. This is a *separate* P3 arm from the
tighten arm and touches the multiplexer's opportunity-set admission, not
`compute_execution_delta`'s flip branch — kept explicitly out of scope for the
`compute_execution_delta` semantics in §4 (it is an admission-side change).

`transition_tighten` = fire an M20 exit-tighten lever on the **held** position
(pull the stop toward break-even / to the transition-implied level) instead of
either holding to the original stop or flipping. "Ride the wave" = get off the
wrong side fast; the new side, if it has its own merit, enters as its own trade
under normal admission. This is the arm P0 most strongly supports (the 93.6%
cell + the "same-clock loses both ways" finding).

## 4. Proposed `intents.py` semantics (exact, for P3 wiring — not merged here)

### 4a. Signature change (additive, backwards-compatible)

`compute_execution_delta(...)` gains two optional keyword args, mirroring the
existing `existing_confidence` / `existing_age_hours` pattern (default `None` →
**byte-for-byte current behaviour**, so nothing changes until a caller populates
them AND a `*_MODE` selector is on):

```python
def compute_execution_delta(
    desired, current_signed_qty, *,
    ...,
    existing_confidence: Optional[float] = None,
    existing_age_hours: Optional[float] = None,
    held_strategy: Optional[str] = None,        # NEW — owner of the open position
    conflict_policy_mode: str = "off",          # NEW — off | annotate | apply
) -> ExecutionDelta: ...
```

### 4b. Classification helpers (pure, config-driven — no hardcoded name lists on the order path)

Two small resolvers, both reading config so a new strategy needs no code edit:

- `_strategy_clock_seconds(name) -> Optional[float]` — bar seconds from
  `config/strategies.yaml::<name>.timeframe` (reusing the existing timeframe
  parser). `None` when unknown → treated as "unknown TF rel" → conservative `hold`.
- `_strategy_exit_class(name) -> str` — returns `trend | mean_rev | other`.
  Backed by a NEW `exit_class:` field on each strategy in
  `config/strategies.yaml` (or a small `config/strategy_exit_classes.yaml` map),
  **not** an inline literal — same anti-drift discipline as
  `strategy_descriptions.json`. Unmapped → `other` → `hold`.

The TF-ratio: `r = max(c_new, c_held) / min(c_new, c_held)` for
`c_* = _strategy_clock_seconds(...)`; `r = 1.0` (→ same-clock) when either clock
is unknown (conservative: an unknown clock is treated as potentially same-clock,
so we never wrongly grant coexistence).

### 4c. The opposite-direction branch (the only behaviour change, gated off)

Inside the `# Opposite-direction` block, BEFORE the `resolve_flip_policy()`
resolution, insert the taxonomy — but only when `conflict_policy_mode != "off"`:

```
if opposite_direction and conflict_policy_mode != "off":
    cell = taxonomy_cell(
        tf_ratio = r,
        held_class = _strategy_exit_class(held_strategy),
    )                              # -> "hold" | "transition_tighten" | "coexist"
    annotate delta.meta with {conflict_cell, tf_ratio, held_class, proposed_action}
    if conflict_policy_mode == "apply" and cell == "transition_tighten":
        return ExecutionDelta(action="tighten_stop", ...)   # M20 lever handle
    # "coexist" is handled at admission (multiplexer), not here — see §3c note
    # otherwise fall through to the existing flip-policy block (hold/flat/reverse)
```

- `off` (default) → the block is skipped entirely; **identical to today**.
- `annotate` → the cell + tf_ratio + proposed action are written to the delta
  meta / audit only; the actual returned delta is unchanged (this is the P2
  soak surface — the observe-only evidence trail).
- `apply` → only the `transition_tighten` cell changes the returned delta (to an
  M20 tighten action on the held position); every other cell still falls through
  to the incumbent `hold`. **`apply` is Tier-3, P3-gated, kill-switched**, and
  never enacts `coexist` from here.

`ExecutionDelta` gains an `action` value `tighten_stop` (or reuses the M20 lever
delta shape) — the coordinator maps it to the existing M20 exit-lever actuator
rather than a new order path.

### 4d. Why `annotate` first (the M25 parity-first discipline)

P2 ships this at `conflict_policy_mode="annotate"` so the taxonomy's
classification runs live against real conflicts and writes a soak row — but the
**edge is proven in P3's offline backtest**, not by waiting on the soak. The
soak exists to verify the *mechanics* (does the live classifier agree with the
offline one on the same conflicts?) at the first review, per M25. No multi-week
soak gate.

## 5. Proposed P3 arms (what the taxonomy makes testable)

The taxonomy turns the P0 findings into concrete, targeted `backtest_system.py`
arms (each must beat BOTH always-hold AND always-close **per class**,
walk-forward, net-of-cost, per M24 net-R labels):

1. **A_hold** — incumbent baseline (`conflict_policy_mode=off`).
2. **A_tighten_meanrev** — `transition_tighten` on same-clock conflicts for the
   mean-rev/pullback/scalp class only (the 93.6% cell). *Primary candidate.*
3. **A_coexist_crossclock** — admit the fast counter-signal as its own position
   when `r ≥ 4` (admission-side; sized within caps). *Secondary.*
4. **A_confgap_flip** — the dormant `FLIP_CONFIDENCE_THRESHOLD` knobs, same-clock
   only. *Demoted by P0 (same-clock flip lost −$7.1k) — included only as the
   documented control that should LOSE.*
5. **A_donchian_untouched** — assert `trend_donchian`/trend class stays pure
   `hold` under every arm (the guardrail: no arm may touch the +$10.1k cell).

## 6. Non-goals / honesty

- No order-path behaviour ships from P1. §4 is the design the P3 harness and the
  P2 annotate-soak implement; `apply` is Tier-3 and gated on P3 beating `hold`
  out-of-sample.
- Not a new strategy; not a re-run of M18 allocator selection (coexist sizing is
  reductive/cap-bounded admission, not EV-ranked selection).
- K=4 is the P0-observed knee, not a tuned optimum — P3 sweeps K ∈ {3,4,6,8} as
  part of arm 3's grid rather than freezing it here.
- `exit_class` mapping is small and human-authored; it must live in config with
  the same review discipline as the strategy roster, and any unmapped strategy
  defaults to the conservative `hold` (never silently granted a tighten/coexist).

## 7. Anchors

- Backlog: `MB-20260719-M26-TRANSITION-CONFLICT` — P1 delivered (taxonomy +
  exact semantics); **P2 next** (build the annotate-only soak per §4c) then
  **P3** (the decisive backtest gate).
- Composes with: M20 (the tighten actuator), M24 (net-R grading of arms), M25
  (parity-first soak discipline), M14 regime program (the transition axis feeds
  the same cell vocabulary).
- Code touch-points when P2 builds: `src/runtime/intents.py`
  (`compute_execution_delta` signature + taxonomy helpers, gated `off`),
  `config/strategies.yaml` or `config/strategy_exit_classes.yaml` (the
  `exit_class` map), the coordinator (thread `held_strategy` +
  `conflict_policy_mode`), and a new `conflict_taxonomy_soak` audit log.
