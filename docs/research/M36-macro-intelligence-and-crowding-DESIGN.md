# M36 Track C — Unified Macro-Intelligence + the Positioning/Crowding-Aware Thesis Lifecycle (design of record)

> **Status:** DESIGN — for operator review (operator-directed 2026-07-27, M36
> Track C). **Tier:** Tier-1 throughout (design + pure code + observe-only soak +
> the point-in-time backtest gate). Any wiring into a live path
> (`src/units/strategies/macro_thesis/thesis_tick.py`, sizing, `c_macro` into the
> shared blend) stays a **draft PR + operator approval, backtest-gated (Tier-3)**.
> **Composes with:** M28 (`M28-macro-value-speculation-DESIGN.md`), M29
> (`M29-ai-system-dynamics-DESIGN.md`), M9 news, M16 unified-confidence, M24 net-R.
> **Anchor:** `MB-20260727-M36-CONSOLIDATION-INTEGRATION` (Track C).

## The operator's ask (verbatim intent, 2026-07-27)

Two directives, one program:

1. **Merge M28 and M29** — "those are sort of working together to create the same
   idea." M28 (thesis/value/event) and M29 (AI system-dynamics scenarios) should
   be **one macro-intelligence program**, not two parallel efforts.
2. **Model other traders, not just the world→price link** — "we also need to think
   about how and when other traders are trading those moves… if investors have
   already priced that in, maybe even overvalued before the week is over, then we
   might need to move up the exit. We need to take other traders' behavior into
   account in how we structure and enter and exit trades."

## The honest prior this design MUST respect

The **M28 signal-research program CONCLUDED (2026-07-25)**: standalone
crowding/positioning **directional** signals on free data are **exhausted** —
CFTC-COT spec-positioning (level/change/divergence/cross-section), crypto
funding/OI/basis (level + momentum), and the rising-OI "crowding-builds → fade"
gate all graded **null** out-of-sample after cost (`M28-signal-research-ledger.md`
entries 2/3/5/7/9/10/11/15). **Positioning does not predict direction on these
proxies.**

So Track C does **not** build another directional crowding signal — that path is
closed. The operator's actual ask is **different and untested**: use
positioning/crowding to **condition the timing and size of an ALREADY-FORMED
thesis** (advance an exit when the move is spent / over-owned; size an entry down
when it's over-crowded). The null results are about *generating a direction*; this
is about *managing a bet the value/event thesis already justified*. That
distinction is the whole design — it keeps us on the right side of the evidence.

## Move A — Merge M28⊕M29 into one macro-intelligence program

M28 and M29 already interlock by design (M29's own P4 asks "does adding the SD
scenario signal to `thesis_conviction` improve calibration vs the M28 baseline?").
Track C makes the seam concrete and unifies the framing:

- **M29 is the world-MODEL; M28 is the thesis ENGINE that bets on it.** M29's
  `src/sysdyn/` runs a calibrated stock/flow model forward (`engine.simulate` →
  `Trajectory`) to produce a **scenario distribution** for a driver (e.g.
  `gas_storage_price_v1`: current NG-storage state → forward MNG-price
  distribution). M28 forms theses; M29 tells it *what the system model expects*.
- **The contract — a pure `scenario_read` adapter (C1).** M29 scenario summary
  (modal move, dispersion, P(up)) → three thesis inputs: (i) a **`c_scenario`**
  conviction lens (thesis direction aligned with the modal scenario → conviction
  lift; fighting the distribution → cut or a tighter invalidation); (ii) an
  informed **`target`** (the scenario's expected move) and **`horizon_days`** (when
  the model expects it to play out); (iii) a `macro_context.scenario` snapshot on
  the `TradeThesis` (point-in-time, traceable). This is exactly the
  `macro_context` + `c_macro` seam M28 §7 / M29 P4 already reserve — nothing new
  invented, the two programs become one flow with a shared conviction + exit spine.

## Move B — the positioning/crowding-aware thesis lifecycle

Two pure reads that condition an existing thesis. Both are **observe-only** first,
scored by the M28 `thesis_backtest` calibration instrument before any live effect.

### B1 — Thesis-progress exit ("priced in early → move the exit up")

The operator's exact example, and the buildable core — **price/valuation-derived,
no new external data.** A thesis already carries a `target`, `invalidation`,
`horizon_days`, `max_hold_until`. Define, at each slow scan of an **active** thesis:

```
progress = realized_move_toward_target / expected_move_to_target
         (or: valuation convergence — how far fair-value gap has closed vs entry)
```

A new **`on_progress` decision-rule class** on the thesis (a sibling of the
existing `watched_events.on_outcome:{if→action}`, action ∈ {trim, exit, extend,
hold}):

- `progress ≥ 1` **before** `max_hold_until` (target reached / fair value
  converged early) → **advance the exit / trim** — the move is spent; the
  remaining hold is uncompensated risk. *(the operator's "move up the exit".)*
- `progress` overshoots (price past target / valuation now *rich*) → **exit /
  consider flip-to-flat** — the market over-priced the thesis.
- `progress` stalls near 0 deep into the horizon → invalidation review (the thesis
  isn't playing out).

This is a **calendar-aware exit condition** — precisely the "price **and**
event-outcome exit" M28 §1 says the sleeve needs and no mechanical harness has.
It reuses the existing `valuation.py` fair-value read + `market_data` price; the
scenario `target` from Move A makes `expected_move` principled rather than ad-hoc.

### B2 — Crowding / over-extension conditioner (reductive only)

Layer the "how & when other traders trade the move" read **on top of** B1, used
**reductively** (never to enlarge a bet, never as a standalone direction call —
respecting the null). A pure **`crowding_read`** from **already-wired free feeds**:

- **Over-extension of the move itself** — velocity/stretch of the realized move
  vs its own recent path (how fast/far it already ran — a price-derived
  "over-owned" proxy).
- **Positioning extremity** — COT spec-net **percentile extremity** (not its
  direction — the null was about direction; *extremity as a mean-reversion-risk
  conditioner* is a different use), crypto funding magnitude, VIX level.
- **Sentiment intensity/velocity** — the M9 news layer's aggregate
  sentiment **magnitude and rate-of-change** on the thesis theme (crowded
  narrative → fragile).

Used two ways, both reductive: **(entry)** an over-crowded/over-extended setup
sizes **down** toward a floor (the same shape as `news_influence.py`'s reductive
downsize — reuse it); **(exit)** a high crowding read **tightens B1's exit trigger**
(exit sooner when the move is both near-target *and* over-owned). It is a
*conditioner on conviction/timing*, never an input that flips a thesis's side.

## Why this is honest (the guardrail)

| Concern | How Track C stays clean |
|---|---|
| Standalone crowding signal is exhausted | Track C never uses positioning to *pick a direction* — only to *size/time an already-justified thesis*. The null (direction) doesn't test this (conditioning) use. |
| Low-n weeks-horizon | Validated by **calibration + net-R** on the M28 `thesis_backtest`, never by significance from a handful of wins (M28 §8). |
| Point-in-time leakage | Every read (progress, crowding, scenario) is a strict past-only as-of computation; the backtest reconstructs state as-of each date (M28 §8, the #1 rule). |
| Enlarging risk from a "hot" read | Crowding is **reductive-only** (downsize / exit-sooner), mirroring the news-influence contract — never enlarges. |

## Phased plan (all Tier-1 observe→advise→gate; live effect is Tier-3)

- **C0 — this design (Tier-1).** Merge framing + the two conditioner contracts.
- **C1 — `scenario_read` adapter + M29→M28 wiring (Tier-1 pure; draft PR for the
  tick hook).** Pure `sysdyn`-scenario-summary → `{c_scenario, target, horizon}` +
  `macro_context.scenario` on the thesis; observe-only in the thesis soak. Scored
  in C4.
- **C2 — thesis-progress read + `on_progress` rule (Tier-1 pure).** Pure
  `thesis_progress(thesis, price, valuation)` + the rule class; the would-be
  advanced exit is **logged observe-only** in the thesis soak (never acts yet).
- **C3 — `crowding_read` conditioner (Tier-1 pure).** Pure read from the existing
  free feeds (price over-extension + COT extremity + funding/VIX + M9 sentiment
  intensity); observe-only reductive annotation on entry size + exit timing.
- **C4 — the gate (Tier-1, decisive). ✅ RAN 2026-07-27 → NULL on net edge.**
  Built `thesis_conditioned.py` (`conditioned_exit_on_path` drives the shipped C2
  `thesis_progress` + C3 `crowding_read` over the realized price path — exits only
  ever *earlier* than the baseline, no look-ahead) + `equity_and_maxdd`
  (`thesis_backtest.py`) + the runner `scripts/macro/thesis_c4_run.py` (full grid
  over `expected_move_pct × {crowding on/off}`, no in-sample cell selection).
  **Ran on the committed 21yr point-in-time history** + real off-VM candles
  (SPY/TLT/GLD/SLV/IEF), 1,104 theses — the "backtest history first" rule, no
  accrual wait. **Result** (`M36-C4-conditioned-lifecycle-run-2026-07-27.md`,
  scorecard `comms/macro/thesis_c4_scorecard.json`): the conditioned lifecycle
  **does NOT beat the value-only baseline on net return** (Δnet −0.0010…+0.0004,
  noise-of-zero) → **the C4 gate is NOT cleared, nothing graduates.** It DOES
  deliver a modest *reductive* win — up to ~19% maxDD reduction at the widest
  target without hurting net, + a weak positive calibration shift — so the
  conditioner is validated as **safe + mildly risk-reducing**, but **pointless
  without an edge-positive base thesis**. The blocker is the **thesis
  construction** (the value sleeve is itself OOS-null), not the exit lifecycle.
  **Re-run trigger:** whenever an M28 construction beats its own P4 baseline,
  re-run the C4 runner to test whether the conditioned lifecycle adds net edge on
  top of a base thesis that actually has one. (Note: the "wait for the forward
  FRED producer to accrue" framing was stale — the historical backfill
  `scripts/macro/valuation_snapshot_backfill.py` reconstructs 2005→2026 in one
  shot; the M29/EIA/NG seed is available the same way.)
- **C5 — apply (Tier-3, operator + backtest gated).** The scenario-conditioned
  conviction + progress/crowding exit go live **in the sleeve, paper first**
  (`alpaca_options_paper`); then the `c_macro` global overlay per M28 §7 /
  M29 P5. Reductive-only; kill-switched; honors the two execution gates.

## Code touch-points (when the phases build)

- New pure modules under `src/units/strategies/macro_thesis/`:
  `scenario_read.py` (C1, imports `src.sysdyn`), `thesis_progress.py` (C2),
  `crowding_read.py` (C3) — all pure, unit-tested with synthetic inputs, layer-safe.
- `thesis.py` — add the `on_progress` rule field + `macro_context.scenario`
  (schema-additive).
- `thesis_tick.py` — call the three reads in the slow scan, **log observe-only**
  (draft PR; it's the live-path hook).
- `thesis_backtest.py` — the C4 conditioned-lifecycle vs baseline scorer.
- Reuse (do not rebuild): `valuation.py`, `src/news/` sentiment aggregate,
  `news_influence.py`'s reductive downsize, `ml/datasets/adapters/` COT/funding
  readers (as *extremity* features), `src/sysdyn/engine.simulate`.
- `c_macro` into `conviction.py`/`conviction_inputs.py` — **C5 only**, Tier-3.

## Non-goals / honesty

- **Not a new crowding signal.** Direction-from-positioning is closed; this is a
  timing/sizing conditioner on an already-formed thesis.
- **Not a live launch on theory.** Every read soaks observe-only and must beat the
  baseline on the point-in-time backtest (C4) before any live effect.
- **Not enlarging.** The crowding conditioner is reductive-only, by contract.
