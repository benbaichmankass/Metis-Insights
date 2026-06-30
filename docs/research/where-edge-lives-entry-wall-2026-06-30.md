# Where does the edge live? Entry-prediction is at the M18 wall (2026-06-30)

> **Tier-1 research finding.** Read-only analysis; no live-path, config, or
> money-DB change. Records a decision-grade conclusion from a half-day of
> autonomous, zero-risk experiments so it doesn't have to be re-derived.
>
> Origin: operator direction 2026-06-30 — the signal-research framework
> ([`signal-research-framework-DESIGN.md`](signal-research-framework-DESIGN.md))
> P0 build, plus the "free read" on whether an end-to-end learned **entry**
> policy is worth building.

## The question

Does **entry timing / direction** carry attributable, exploitable edge in this
system — measured two independent ways: through the **hand-coded ICT
predicates** (P0 component-edge attribution) and through **flexible ML models**
(the existing direction + meta-label heads)? This is the precondition for two
proposed investments: P1 of the signal-research framework (a per-generator
scorecard built on entry components) and a new end-to-end learned-entry-policy
model.

## The dominant prior — M18

The standing prior is the **M18 finding**: per-trade outcome from decision-time
features was ≈ coin-flip out-of-sample. Everything below either confirms or
refutes it with fresh data. The honest framing throughout: an end-to-end model
does not manufacture signal that isn't in the data — it searches the policy
space more flexibly (and overfits more easily). So "can a model do it?" and
"do the rules capture it?" are the same question from two ends.

## The three reads

### 1. Hand-coded predicates — live journal (P0, real + paper)

`scripts/research/component_edge_report.py` over `trade_journal.db`
(real-money + paper cohorts, R-coverage 100%):

- The real-money book is **too thin per strategy** to attribute entry-component
  edge — only `vwap` has volume (318 closed); everything else ≤ 15 → `insufficient`.
- The one well-sampled component anywhere — `vwap`'s deviation magnitude
  (`vwap_deviation_std`) — verdicts **`weak`**, not `edge`. Its `confidence`
  saturates at the cap (no discrimination).
- The paper cohort spreads across 32 strategy cells but each is still thin, and
  paper rows largely **lack `signal_logic` detail** (artifact-dominated closes,
  per `PB-20260626-ARTIFACT-BUCKETS`).

**Read: no attributable entry-component edge from the live journal; the one
measurable component is only weak.**

### 2. Hand-coded predicates — backtest volume (P0 `--backtest-log`)

`ict_scalp_5m` backtested over **full-history ETHUSDT 5m** (2021–2026, 553k bars
→ **1,289 trades**, `--min-confidence 0` for the full range; the harness calls the
**live** `order_package()`, so identical component meta. ETH not BTC, but the ICT
predicates are ATR-normalized / symbol-agnostic, so this is a valid volume test of
whether the predicates discriminate winners). Win-rate 51.8%, mean R +0.18 (gross —
the ict_scalp harness has no fee model, so net is lower).

**Every graded component verdicts `NONE` at n = 1,289:**

| component | AUC | verdict | bucket win% (low→high tercile) |
|---|---|---|---|
| `confidence` | 0.500 | none | 51.6 / 53.1 / 50.7 |
| `sweep_depth_atr` | 0.507 | none | 52.1 / 49.2 / 54.2 |
| `displacement_strength` | 0.516 | none | 50.5 / 52.0 / 53.0 |
| `fvg_size_atr` | 0.487 | none | 53.9 / 50.1 / 51.4 |

All AUCs sit in **0.49–0.52** (coin-flip); buckets are flat / non-monotone;
standardised-logit marginal lifts are negligible (|coef| ≤ 0.21, `fvg_size` even
slightly *negative*). **No ICT entry predicate discriminates winners from losers,
even at 1,289 trades.** Notably the strategy is mildly gross-profitable (+0.18R)
yet *none of its entry predicates explain which trades win* — the small edge comes
from elsewhere (exit / R-structure or noise), which **reinforces** the thesis.
(The "decay" line in the per-component report is not meaningful for a 2021–2026
backtest — its 30-day windows catch only the last ~30 trades.)

**Read: hand-coded entry predicates carry no attributable edge at volume — the
M18 wall, confirmed from the rules side at scale.**

### 3. Flexible ML — the existing entry heads (the decisive read)

These two registry heads **already implement exactly the supervised approach a
learned-entry-policy would use** — LightGBM, de Prado triple-barrier /
meta-labeling, cross-asset features, leakage-guarded OOS evaluation. Their
out-of-sample numbers (pulled 2026-06-30, runs `20260630T0120*`):

**Direction head** — `eth-direction-1h-lgbm-v1` / `-xasset-v1`
(target `direction_label` ≈ binary up/down, base rate ~50/50, `time_aware_holdout`
0.2, `n_eval = 8760`):

| Variant | OOS accuracy | f1_up | f1_down |
|---|---|---|---|
| bars only (`-v1`) | **0.5074** | 0.514 | 0.501 |
| + cross-asset peers (`-xasset-v1`) | **0.5156** | 0.528 | 0.503 |

≈ **coin-flip.** Cross-asset features add ~0.8pp — a sliver of structure, nowhere
near usable (and far below any net-of-fee threshold). The gate-check's
`std(macro_f1)=0.0` across 10 retrains is the fixed seed, not degeneracy — it's
a real model that genuinely can't predict direction.

**Meta-label "is this entry worth taking?" head** — `setup-candidates-metalabel-v1`
(target `won` = triple-barrier outcome; evaluator **`live_holdout`** — train on
synthetic candidates, **evaluate on 354 REAL closed trades**; the closest
existing proxy to the exact "should we take this setup" question):

| accuracy | brier | f1 | precision | recall | n_eval |
|---|---|---|---|---|---|
| 0.681 | 0.222 | 0.137 | 0.209 | 0.102 | 354 |

The 0.681 accuracy is the **lose-class base rate** (the model almost never says
"win"): near-zero positive recall (0.10), f1 0.137, and a **Brier ≈ the no-skill
baseline**. **No usable skill on real trades** — and it fails the gate's
min-sample (354 < 1000).

**Gate-check (both heads):** `ready: false`; neither has ever accumulated a
measurable live track record (`live_regime_discrimination` / `live_agreement`
both `insufficient_data`) despite weeks of shadow soak.

**Read: a flexible model with bar + cross-asset features + proper
triple-barrier/meta-labeling predicts entry direction at ~51.6% OOS and
entry-quality at no-skill on real trades. The M18 wall, confirmed from the
model side.**

## Verdict

**Entry-prediction edge is at the wall — rules and flexible ML agree.** A new
end-to-end triple-barrier entry head would **reproduce these existing heads** and
hit the same wall. Therefore:

- **Do NOT build the dedicated learned-entry-policy model yet.**
- **Do NOT build signal-research P1 (the per-generator scorecard) on entry
  components yet** — it would render mostly `insufficient`/`none`.
- **Fold both into "edge lives in exit / regime / sizing / selection."**

## The constructive half — this is NOT "ML doesn't work here"

The one ML head that **earns its keep** is the **regime / vol head**: it is live,
A/B-validated as beating the frozen vol label
(`docs/research/A-vol-gating-AB-evidence-2026-06-27.md`), and gates real-money BTC
(`REGIME_ML_VERDICT_MODE=use`). The pattern is specific and useful:

> **ML adds value on *regime / context*, not on *entry timing / direction*.**

So the high-value redirect is to point the *same* rigor — triple-barrier
labeling, the promotion gate, the shadow ladder, all of which already exist — at
**exit-timing and sizing/selection**, where the wall is **untested** and where the
regime head's success suggests there is structure to find. Scoped in
[`exit-management-ml-experiment-DESIGN.md`](exit-management-ml-experiment-DESIGN.md).

## Honesty / caveats

- Symbols/timeframes tested are ETH/BTC at 1h (direction) + the live book — not
  exhaustive. The pattern is consistent with M18, not a universal proof.
- Both the entry-**filter** framing (meta-label "is this setup good?") and the
  entry-**trigger** framing (direction) were tested; both fail. That breadth is
  what makes the verdict robust.
- "Deprioritize entry ML" ≠ "stop trading the rule strategies." The rule
  strategies' *net* performance is a separate question (per-strategy review +
  the M7 gate); this finding is specifically about **attributable entry-signal
  edge**, the thing P1 / a learned entry head would have monetized.

## Provenance (autonomous trainer-vm-diag relays, 2026-06-30)

| Read | Source |
|---|---|
| Live-journal P0 (real) | relay #5202 |
| Live-journal P0 (+paper, 32 cells) | relay #5205 |
| Backtest-volume P0 | relay #5222 (ETHUSDT, 1,289 trades) |
| ML gate-check (both heads) | relay #5213 |
| ML offline OOS metrics | relay #5217 |

Tooling: [`signal-research-framework-DESIGN.md`](signal-research-framework-DESIGN.md)
(P0, merged #5199) + `--backtest-log` mode (merged #5207). ML stack:
`ml/datasets/labeling/triple_barrier.py`, `ml/configs/{eth-direction-1h-lgbm-v1,setup-candidates-metalabel-v1}.yaml`,
`ml/promotion/gates.py`.
