# M23 Phase 1 — variant C design: outcome/R-aware meta-label (2026-07-17)

**Why:** the exact-R EV gate (`M23-phase1-ev-gate-2026-07-17.md`) established the binding
defect precisely — the pooled meta-label ranks **P(win)** well (real-trade win-rate
0.263 → 0.318 under selection) but does **not** rank **realized R**: per-trade net R is
non-monotone and mostly ≈/worse than take-all as the threshold rises, so selection lowers
loss *frequency* but not loss *size*. Since this book's EV is dominated by fat-tailed
losses (mean −0.43R/trade, R ∈ [−14.4, +16.1]), a win/lose classifier structurally can't
manufacture positive EV. Variant C changes the **target** so the head learns to avoid big
losses / prefer big wins, not just to be right more often.

This is now buildable exactly because the **live rows carry real R** (the
`MB-20260717-M23-LIVEROW-REALIZED-R` fix, `c314141`; 359/376 = 95.5% coverage) — the
R-aware target and the R-based eval are both well-defined on train (backtest R = pnl) and
eval (live real R) rows.

## The three sub-variants (do in order; each is Tier-1 / offline)

### C1 — R-thresholded classification target (cheapest first cut)
Replace the binary `won = pnl>0` **training** target with `won_r = 1[r_multiple ≥ τ]`
(τ a small positive R, e.g. 0.25–0.5R), computed on **all** row types (backtest rows'
`r_multiple = pnl`; live rows' reconstructed real R; synthetic rows' barrier R). Keep the
real `won` (pnl>0) column untouched for reporting. The head then ranks P(trade clears τR),
i.e. P(a *materially* good trade), which should push the selected subset toward higher
realized R, not just higher win-rate.

- **Build:** add an optional `r_label_threshold` param to `SetupCandidatesBuilder.iter_rows`;
  when set, emit an additional `won_r` (int) column (schema-declared, so it validates;
  emitted only when the param is set). New manifest `setup-candidates-metalabel-backtest-c1-v1.yaml`
  pinning its own `dataset.version` (avoid the v001 pin footgun — the build MUST write the
  version the manifest declares) with `target: won_r`.
- **Gate:** the SAME EV-at-threshold gate (`scripts/ml/m23_ev_gate.py`, already real-R) —
  does selecting high-P(≥τR) trades beat take-all on **net R** at usable volume? Sweep τ.
- **Risk / discipline:** a shared builder family — a wrong label transform silently
  corrupts every downstream analysis. Add a family unit test asserting `won_r` matches
  `r_multiple ≥ τ` on seeded rows before any trainer run; `ruff` + `py_compile` locally.

### C2 — regress net R (more principled, if C1 is promising)
Train a **regressor** on `r_multiple` (expected-R head) and select on predicted R; this
optimizes EV directly rather than a thresholded proxy. Requires a regression manifest/
trainer path (the current metalabel manifests are classification — confirm the framework
supports a regression target + an R-MSE/rank metric before committing). Escalation from C1,
not a replacement.

### C3 — barrier-vs-live faithfulness relabel (orthogonal; layer onto C1/C2)
The design's known ~0.6R gap: harness/backtest trades use idealized costs/exits, so their
R over-states live R. Re-simulate the backtest trades with realistic costs + the live exit
model (fees/monitor/flip/reconciler), or use the auxiliary-pretrain framing (backtest
labels pretrain, real labels fine-tune the head), so the **training** R distribution
matches live. This attacks the label-quality axis; combine with C1/C2's target change.

## Expected outcome + honesty bar

C1 is a real test, not a guaranteed win: if the book's positive-R trades aren't separable
in the feature space, an R-aware target won't help either — and that would itself be an
important negative (it would localize the wall to *features/labels present*, not the
target framing). Pre-registered gate: **the R-aware selection must beat take-all on net R
at ≥ 10% coverage (n ≥ ~40)** — the same usable-volume floor the exact-R gate uses. No
live wiring regardless; shadow soak only if it clears, behind the operator gate.

## Artifacts / provenance
- Prior leg (the finding that motivates this): `docs/research/M23-phase1-ev-gate-2026-07-17.md`.
- Live-row R fix that makes it buildable: `c314141`, `MB-20260717-M23-LIVEROW-REALIZED-R` (closed).
- Follow-up backlog: `MB-20260717-M23-SELECTION-GATE` (variant C).
- Design parent: `docs/research/M23-decision-label-wall-DESIGN.md`.
