# Technical signal-research ledger (M30)

**The compounding record for the technical-side quant-research platform.** Mirrors
the macro `M28-signal-research-ledger.md`: **every study is a row, and every null
is a recorded result** — never a silent drop, never retry-until-significant. This
is the durable deliverable of M30; individual studies may (often will) come back
null, and that is real, recorded knowledge (exactly as the M28 macro program's own
`no deployable standalone edge` conclusion demonstrated the *methodology* was the
asset).

Scope + methodology: [`technical-quant-research-platform-scoping-2026-07-27.md`](technical-quant-research-platform-scoping-2026-07-27.md).
Binding rigor: [`RESEARCH-RIGOR-STANDARD.md`](RESEARCH-RIGOR-STANDARD.md) — honest
negatives, the M18 coin-flip prior, purged/embargoed CV, multiple-comparisons (FDR)
control, non-overlapping windows.

## Row format

| # | Date | Hypothesis (feature × outcome) | Panel / data | Gate (CV + net-of-cost) | Verdict | Learning |
|---|---|---|---|---|---|---|

- **Verdict** ∈ `edge` (survives the shared net-of-cost walk-forward gate OOS) /
  `weak` (signal but not tradeable at our costs) / `none` (coin-flip / null) /
  `insufficient` (underpowered — record the power, don't force a read).
- A `none`/`insufficient` verdict is a **completed** row, not a TODO. Re-open a cell
  only with a *different* construction (a new feature/transform/conditioning), never
  the same one re-run until it clears.

## Standing priors (before the first study)

- **Entries are ~coin-flip OOS.** `where-edge-lives-entry-wall-2026-06-30.md`: ICT
  entry-predicate AUCs 0.49–0.52. Do not re-litigate raw entries; condition on
  regime, or study exit/sizing where edge plausibly lives.
- **The traded book censors the hard gates.** FVG-present / in-killzone / HTF-align
  are ~100% of traded rows, so their marginal value is invisible in the journal —
  measure via backtest ablation (L1b), not the journal.
- **The eval book is small (~376 real rows, the M23 wall).** Discovery on it will
  manufacture spurious correlations without purged CV + FDR control — both are
  mandatory. M30 C1 is coupled to the M23 label-volume work for statistical power.

## Studies

| # | Date | Hypothesis | Panel / data | Gate | Verdict | Learning |
|---|---|---|---|---|---|---|
| 0 | 2026-07-27 | — (platform scoping, not a study) | — | — | — | Inventory: the discovery *analyzer* (`component_edge_report.py`: conditional-edge tables + AUC + IRLS-logistic marginal-lift), the feature/label foundry, purged-CV, and the backtest bridge already exist; what's missing is the **standing integrated loop** (L2 scorecard / L1b ablation / L3 ladder unbuilt; L1a unscheduled/unsurfaced; no cross-strategy importance/redundancy/standing-regression; no ledger). M30 = assembly + completion, not greenfield. |

_(P3 onward appends the first real study — exit-timing edge — here.)_
