# Signal-research framework — DESIGN (2026-06-30)

> **Tier-1 research/observability tooling.** Nothing in this design touches the
> live order path, `config/strategies.yaml`, `config/accounts.yaml`, or any unit
> the live VM consumes. Every layer is *measure-only*: read the journal, score,
> propose. A survivor wires to **demo** through the normal Tier-3 PR + readiness
> ladder, exactly like the WS-C alt cells (PR #3941) and the recombination sweep.
>
> Status: **DESIGN — for operator review before build.** Layer 1a is buildable
> immediately over existing journal history; the rest are phased behind it.
>
> Origin: operator direction 2026-06-30 — *"a framework for testing the signals
> and researching new ones — score how well each signal generator is working,
> how effective each signal is for driving PnL, and think of new ones we might
> want to incorporate."*

## 1. Why this exists — the missing rung on the scoring ladder

The system already scores trading quality at two granularities:

| Granularity | Tool | What it answers |
|---|---|---|
| **Strategy** | M7 review gate (`scripts/ml/strategy_review_packet.py`), M8 sweeps, readiness ladder (`classify_strategy_tier.py`), `/performance-review` aggregates | "Is `ict_scalp_5m` worth running? KILL / DEMOTE / TUNE / HOLD / PROMOTE." |
| **Decision** | `/performance-review` A–F grades (`comms/claude_strategy_scores.jsonl`), the three soaks (allocator/exit-ladder/conviction-sizing), the conviction blend | "Was *this* order package a textbook entry? How much EV did routing leave on the table?" |

There is **no signal-component granularity.** A strategy is a *bundle of
predicates* — `ict_scalp_5m` = liquidity sweep + displacement + FVG +
mitigation + HTF-bias + a confidence threshold (`src/units/strategies/ict_scalp.py`).
We score the bundle, but we have never asked **which predicate in the bundle
carries the edge and which is dead weight** (or actively hurting). That is the
unit you actually iterate on when you "improve a signal," and the unit you need
to *score a brand-new one*. This framework adds that rung.

A confirmation pulled before writing this (two read-only code surveys,
2026-06-30): grepping for ablation / feature-importance / conditional-edge over
the *strategy* layer returns nothing — model feature-importance exists for the
ML predictor heads, never for the rule-based entry predicates. Scoring stops at
strategy/decision level today. That is the gap, stated as a fact, not a guess.

## 2. Relationship to existing work — extend, don't duplicate

This framework is the **depth** complement to the recombination sweep's
**breadth**, and it reuses the same gate.

- **`docs/research/strategy-primitives-recombination-DESIGN.md`** decomposes a
  strategy into four orthogonal primitives (entry / regime-filter / exit /
  timeframe) and runs a *combinatorial search over already-coded primitives* to
  find new **cells**, tiered whole by the k-fold ladder. It explicitly excludes
  "generating new geometry" and it scores at **cell** granularity. **It tells us
  which *combinations* win; it never tells us which *component* of an existing
  live strategy earns its keep.** That is Layer 1 here.
- The two **chain**: Layer 1/2 (attribution + scorecard) *ranks and prunes the
  primitive pool* before the recombination Cartesian blow-up — directly
  attacking the recombination doc's stated #1 risk (multiple-comparisons over a
  few-hundred-tuple sweep). Layer 3 (new-primitive discovery) *produces new pool
  members* for the sweep to recombine. Pipeline:
  **attribute → prune/rank pool → recombine → tier-ladder → demo.**
- **The gate is already built and is shared.** Nothing here invents a new
  pass/fail rubric. A new or re-weighted signal graduates through the *existing*
  ladder: `scripts/ops/m15_ws_b_fold_report.py` (anchored k-fold, net-of-fee
  7.5/15 bps) → `scripts/ops/classify_strategy_tier.py` (reject / paper_ready /
  live_ready) → `docs/claude/strategy-refinement-queue.json` → Tier-3 demo PR.
- **It feeds the conviction layer.** The c_setup / c_reg / c_wr lenses
  (`src/runtime/conviction.py`) blend *whole-package* sub-scores. A
  per-component edge map is exactly the labelled data a learned c_setup wants:
  "when predicate X is strong, is realized R higher?" Layer 1's output is a
  conviction-model feature source, not a parallel scorer.

## 3. The unit of research, and what is recoverable today

**Unit = a signal component:** one predicate or graded feature that gates or
shapes an entry. Three kinds, which behave very differently for attribution:

1. **Graded features** — continuous, *vary across the traded set*: sweep depth,
   `displacement_body_to_range`, `fvg_size`, VWAP `deviation_std`, the
   confidence sub-scores, the per-model `model_scores`. **These yield edge
   analysis directly from live history.**
2. **Hard gates** — boolean conditions that *must* be true for the signal to
   fire (FVG-present, in-killzone, HTF-bias-aligned). They are true for ~100% of
   *traded* rows, so their marginal edge is **invisible in the journal alone** —
   you cannot measure what a gate filtered out by looking only at what passed.
3. **Regime context** — `regime`, `adx_14`, `vol_regime` stamped on every
   signal; already the axis the live router gates on.

What the journal already persists per closed trade (verified 2026-06-30):
`order_packages.signal_logic` (JSON, capped 8000 chars, written once at decision
time in `src/core/coordinator.py::_log_new_order_package`) carries the
strategy-specific component values — e.g. for `ict_scalp`: `sweep_level`,
`sweep_extreme`, `sweep_idx_from_end`, `displacement_body_to_range`, `fvg_low`,
`fvg_high`, `fvg_size`, `mitigation_mode`, `atr`, `htf_filter_active` — plus the
regime stamp and `model_scores`, linkable to realized PnL/R via
`order_package_id` (→ `trades`) and to `account_context_snapshots` (equity /
drawdown / open-trade-count at entry). So:

> **For a closed trade, can we recover which ICT conditions were true at entry
> and attribute its PnL to each?** — **Yes for graded features and regime
> context, today, in SQL. No for hard gates from the journal alone** (need the
> `*_eval` audit near-misses or backtest ablation). And the schema is
> **strategy-specific, not standardized** — each builder writes its own keys
> (`src/runtime/strategy_signal_builders.py`), so a clean cross-strategy rubric
> needs a canonical adapter (§7, the one prerequisite).

## 4. Layer 1 — Component edge attribution

**Question:** within a strategy that traded, which component drove the PnL?

**Evidence basis (per operator's per-layer choice):** **1a = live-logged
primary; 1b = backtest ablation** (+ eval-audit where present).

### 1a. Graded-component edge — live, buildable now
For each strategy, pull the closed-trade × `signal_logic` join (§3 query) and,
per graded component, compute:
- **Conditional expectancy-R by bucket** — bucket the component (e.g. displacement
  body/range into terciles) and report win-rate + expectancy-**R** per bucket.
  R-normalised so cross-symbol trades compare on one axis (the `/performance`
  R-metric basis). Monotone lift = the component carries edge; flat = dead weight.
- **Marginal lift** — does the component add edge *after* controlling for the
  others (a small regularised logit / gradient-boosted importance of realized-win
  on the component vector). Anti-spurious: cite the **M18 finding** — per-trade
  outcome was ~coin-flip OOS from decision-time features. So the honest prior is
  **expect weak entry-feature edge**; a "no edge here" result is a *finding*
  (it says the edge lives in exit/risk/regime, not the entry predicate), and the
  tool must report it plainly, never manufacture signal.
- **Decay** — the same bucket edge computed over rolling windows, to catch a
  component whose edge has faded (the signal analogue of model drift).

Output: a read-only **component-edge report** per strategy (markdown + JSON),
written under `runtime_logs/signal_research/` and surfaced like the M7 packet.

### 1b. Hard-gate marginal edge — backtest ablation (needs new knobs)
A hard gate's value = what it *refused*. Two routes:
- **Eval-audit route (cheap, partial).** `signal_audit.jsonl` logs per-bar
  `*_eval` rows incl. declines. Where a decline records *which* gate failed, we
  can compare fired vs gate-declined cohorts directly. Gap: today the audit row
  records top-level conditions, not always the precise decline predicate —
  scope a small writer addition (a `decline_reason` / per-gate bitset) as the
  enabling step.
- **Ablation route (authoritative).** Add `--disable-<condition>` knobs to the
  per-strategy backtest harnesses (the survey confirmed the harness exposes only
  *strategy-level* knobs today — `--flip-policy`, `--regime-router`, … — and a
  strategy emits one `order_package()` call per bar with no per-predicate
  toggle). Run paired full-vs-ablated backtests; ΔPnL / ΔmaxDD / Δexpectancy-R is
  the gate's marginal contribution. This is the same machinery the recombination
  sweep needs, so build it once and both use it.

## 5. Layer 2 — Per-generator scorecard

**Question:** one comparable score per signal generator — the rule-based
analogue of **RG4** for regime heads.

**Evidence basis:** **both** — live for the realized columns, backtest for the
cost/counterfactual columns.

The scorecard (one row per generator: the 6 live strategies, the 4
shadow/research units, and any Layer-3 candidate):

| Column | Source | Meaning |
|---|---|---|
| **Frequency** | live + backtest | signals/day — is it firing enough to matter? |
| **Hit-rate** | live | win-rate on resolved trades |
| **Expectancy-R** | live | mean R per trade — the headline edge |
| **Regime-conditional edge** | live | expectancy-R per `(trend,vol)` cell — does it only work in some cells? (feeds `regime_policy.yaml`) |
| **Decay** | live | rolling expectancy-R slope — is the edge fading? |
| **Redundancy** | live | correlation of its entry timing / PnL with the rest of the book — two always-co-firing signals add no diversification |
| **Cost-sensitivity** | backtest | net edge at 7.5 vs 15 bps — how much of the edge is fee-fragile (the ict_scalp fee-bleed class) |
| **Calibration** | live | are the generator's own confidence stamps calibrated to realized win (Brier/ECE; reuses `fit_confidence_calibrators.py`) |

Roll-up: a single **GenScore** (weighted, regime-aware, cost-aware) so generators
sort on one axis, with the columns as the explainer. Lands as a packet beside the
M7 review (and is a natural `/performance-review` subsection), never a YAML write.

## 6. Layer 3 — New-signal research ladder (observe → score → graduate)

**Question:** propose, measure, and graduate *net-new* ICT primitives — the
geometry the recombination sweep explicitly does not generate.

**Evidence basis:** **backtest discovery + live observe-only soak**, then
backtest-gate before any wire-to-trade — mirroring the ML `shadow → advisory`
ladder exactly.

**Candidate primitive shortlist** (each is a coded, gated ICT idea not yet in the
pool; ordered by expected value-to-cost):
1. **Breaker / mitigation blocks** — order-block inversion after a sweep; a
   natural extension of `ict_scalp`'s existing sweep+FVG geometry.
2. **Inversion FVG (IFVG)** — an FVG that fails and flips polarity; high-quality
   continuation trigger, reuses the existing FVG detector.
3. **SMT divergence** — cross-asset (BTC/ETH, or ES/NQ) liquidity-sweep
   divergence; pairs with the cross-asset peer-feature plumbing
   (`config/cross_asset.yaml`, `cross_asset_live.py`).
4. **Equal-highs/lows liquidity pools** — relative-equal-extreme draws on
   liquidity; a cleaner sweep-target than the raw swing extreme.
5. **Session opening-range** — killzone-anchored OR breakout/fade; reuses the
   session/killzone gate already in `ict_scalp` + the hf candidates.
6. **PD-array / daily-bias** — premium-discount array bias as a regime/direction
   filter rather than an entry — a c_reg input as much as a trigger.

**The ladder:**
1. **Code the primitive as a pure detector** behind the same
   `order_package()`/`SignalPackage` seam — *observe-only*, never wired to an
   account.
2. **Per-bar soak** — compute the predicate every bar and log it to
   `runtime_logs/signal_research/candidate_<name>.jsonl`, the way
   `regime_bar_scoring.py` accrues ML-head predictions without trading. Surfaced
   read-only (a `/api/bot/signal-research/*` Tier-1 endpoint, like the other soaks).
3. **Score standalone + marginal** — run it through Layer 1/2: its own
   expectancy-R *and* its lift on top of the existing book (does it fire where the
   book is silent, and is that incremental edge real?).
4. **Backtest-gate** — the *existing* k-fold + tier ladder (§2). `reject` dies
   with evidence; `paper_ready` opens an `SRQ-…` row and **enters the
   recombination pool** as a new primitive *and* wires to `bybit_1` demo via a
   Tier-3 PR. `live_ready` is an operator-gated real-money proposal.

The symmetry is the point: the ML side already has shadow→advisory + RG4 +
gate-check + calibrators. The rule-based side has scoring only at strategy
granularity. Layer 3 gives new signals the **same observe-and-graduate
discipline** the models already have.

## 7. The one prerequisite — a canonical component vector

`signal_logic` is strategy-specific (§3). Rather than force every builder to a
rigid schema (brittle, and the recombination doc warns against forking unit
logic), add a thin **read-side adapter**: `src/research/component_vector.py`
with one `extract(strategy_name, signal_logic) -> {component: value|bool}` per
strategy, mapping each unit's idiosyncratic keys to a canonical component
namespace (`sweep_depth_r`, `displacement_strength`, `fvg_size_r`,
`htf_bias_aligned`, `in_killzone`, …). Pure, table-driven, unit-tested against
fixture rows. Every layer consumes the canonical vector, so adding a strategy =
one adapter entry, not a schema migration. This is the only new *standardisation*
the framework needs; everything else reads existing columns.

## 8. Data model / where outputs live

| Output | Path | Surface |
|---|---|---|
| Component-edge report (L1) | `runtime_logs/signal_research/component_edge_<strategy>.json` + `.md` | like the M7 packet; `/performance-review` subsection |
| Generator scorecard (L2) | `runtime_logs/signal_research/scorecard_<date>.json` | packet beside M7; dashboard Strategies tab |
| Candidate soak (L3) | `runtime_logs/signal_research/candidate_<name>.jsonl` | `/api/bot/signal-research/soak` (Tier-1, read-only) |
| Ablation results (L1b) | reuse the backtest `--emit-trades` log + fold-report | existing tier pipeline |

No new money-DB tables; the journal is read, never written. Soak logs follow the
existing best-effort append-only, never-raise writer convention.

## 9. Phased build order

- **P0 — Component-edge report (L1a), read-only.** The canonical adapter (§7) +
  the live graded-component edge report. Zero new trading, immediate insight,
  validates the whole premise on real data. *First deliverable.*
- **P1 — Generator scorecard (L2).** The rubric + GenScore over P0's output +
  the cost-sensitivity backtest column.
- **P2 — Ablation knobs (L1b).** `--disable-<condition>` in the per-strategy
  harnesses (shared with recombination) + the eval-audit `decline_reason` writer.
- **P3 — New-signal soak (L3).** One candidate primitive end-to-end through the
  observe→score→graduate ladder as the proof, then fan the shortlist.

Each phase pings the operator at its result (standing cadence); P0/P1 are
autonomous Tier-1; a graduating signal is an operator-gated Tier-3 demo PR.

## 10. Risks / guardrails

- **Multiple-comparisons / false discovery.** Many components × strategies ×
  regime cells inflates false positives. Mitigations: pre-register the
  hypotheses, gate on the *every-fold* k-fold ladder (not a single split), the
  2×-fee headroom bar, a per-fold min-trade floor, and an out-of-pool
  symbol/period holdout before any real-money proposal — the same controls the
  recombination doc adopts.
- **The M18 humility prior.** Entry-feature edge was ~coin-flip OOS. Build the
  tool to *report null edge as a result*, not to keep slicing until something is
  "significant." A consistent null is itself the steer (edge is in exit/regime).
- **Hard-gate invisibility.** Never infer a gate's value from traded rows alone
  (§3/§4.1b) — it is a censored sample. Ablation or eval-audit only.
- **Redundancy, not diversification.** A new signal that always co-fires with the
  book adds risk-concentration, not edge — the redundancy column (§5) and the
  portfolio pass (`backtest_system.py`) are where that is caught.
- **Schema drift.** The canonical adapter (§7) is a *reference* to live unit
  keys; a unit logic change must update its adapter entry. Unit-test the adapter
  against captured fixture rows so drift fails loudly.

## 11. Open questions for the operator

1. **Scorecard home** — a standalone `/signal-review` packet, or a subsection of
   the existing `/performance-review`? (Recommend: subsection — it already grades
   decisions anchored on `signal_logic`, so the component view is one level down.)
2. **Candidate shortlist priority** — confirm the §6 ordering, or front-load a
   specific primitive (breaker blocks and IFVG reuse the most existing code).
3. **Autonomy line for L3** — the soak + scoring are Tier-1; confirm that a
   `paper_ready` survivor wiring to `bybit_1` demo follows the standard Tier-3
   PR (as recombination does), with `live_ready` operator-gated.
