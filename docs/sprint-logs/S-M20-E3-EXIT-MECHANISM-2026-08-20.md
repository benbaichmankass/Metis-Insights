# S-M20-E3-EXIT-MECHANISM — the E2 gate resolved, and E3 answered with a negative that names its own cause

**Sprint ID:** S-M20-E3-EXIT-MECHANISM-2026-08-20 · **Date:** 2026-08-20
**Milestone:** M20 (exit mechanism) · **Tier:** 1 throughout
**Branch:** `claude/m20-e3-exit-mechanism-r6r266`

## Objective

Three things, in the order the task fixed them:

1. **Decide `BL-20260820-E2-NEGATIVE-CONTROL-GATED-POINTWISE-NOT-FWER` before running
   anything**, chase why 4 of 24 discards landed on SOL, and re-run the **full** sweep
   under whatever rule was chosen rather than patching the four cells.
2. **E3** — levers over `label_hold` at a long horizon, over `dist_to_stop_atr`,
   `upnl_r`, `running_mae_r`, swept **jointly**, with the added-degrees-of-freedom
   falsifier.
3. **Settle edge vs barrier geometry first**, routed through the M20 net-of-cost gate.

## Work completed

### 1. The gate — decided, implemented, and committed BEFORE the re-run

Commit `d5251d1` lands the rule; the sweep was dispatched after it, so the ordering the
row demanded is provable from history rather than asserted.

**Question (1), "why is it SOL?" — REFUTED.** New diagnostic
`scripts/research/e2_null_calibration.py` pushes a **bank** of independent noise columns
through E2's *imported* fold/block/shuffle machinery. On `panel_SOLUSDT_h48` (10,724
rows / 567 trades, reproducing the horizon arm's substrate exactly): clear-rate **0.040**
against α = 0.05, mean permutation *p* **0.517** (a valid null gives 0.5), and a
length-matched comparison null returns 0.045. **The SOL null is calibrated under both
schemes.**

The actual cause: `inject_controls` seeds from the run seed and fills in panel row order,
so **the `__ctrl_noise` column is byte-identical across all three targets of a panel** —
verified directly. The sweep had **8** independent control draws, not 24. Measured
co-discard lift on a 400-column bank: **6.8×–14.0×**, 1.59 cells per affected column.
Re-doing the row's own arithmetic under that dependence: **P(≥4) = 0.086**, not the
0.0298 quoted, and **P(all on one leg | ≥4) = 0.329**, not 0.125. Both published numbers
assumed an independence the runs do not have, and both flip the conclusion.

**Question (2), the rule.** The gate reads a **rate over K = 64** noise columns; refused
only when the Binomial(K, α) upper tail falls below `gate_level` = 0.01. `harness_state`
is four never-collapsed states — `valid` / `invalid_positive_control_dead` /
`invalid_null_miscalibrated` / **`unchecked`** (K = 0: *we did not look*, and it does not
fall back to a bank of one). `legacy_pointwise_gate_would_invalidate` is recorded per run.

**Measured over 40 seeded sound null panels**, as the row required the rate to be a
property of the tool: legacy **2/40 = 5.0%** (α, reproduced exactly), this gate **0/40**,
bank clear-rate 0.052 against 0.05 expected.

**The FWER gate the row proposed was designed, measured and REJECTED.** P(an out-of-family
column clears the family-max bar) is not a constant — it falls with family size, so
P(any of K clears) *rises with K* and the rule gets more trigger-happy the more carefully
you measure it. Measured 2/64 on the module's own 2-feature synthetic null panel against a
clean 3/64 pointwise. Kept as the reported diagnostic `n_cleared_fwer` with no vote. **The
self-test caught this, which is why it was fixed before the sweep rather than after seeing
which cells it rescued.**

### 1b. The re-run — 24/24 cells, and it moved the headline

**Every admissible cell reproduces its published FWER/min-p/pointwise counts EXACTLY.**
The gate decides admissibility, not verdicts. What moved is the *set*, and **the holes
swapped legs**:

| target · cell | published | re-run |
|---|---|---|
| `advantage_r` SOL h24/h48/h96 | `harness_invalid` ×3 | recovered → **negative on 8 of 8** |
| `label_hold` **SOL h48** | `harness_invalid` | **HIT 3/5/6** — the gate's error, not luck |
| `label_hold` **XRP h48 / h96** | HIT 2/3/5 · HIT 3/4/7 | **`harness_invalid`** |

⚠️ **The `label_hold` flip is now carried by SOL alone; the horizon arm's "two independent
legs" defence is withdrawn for that target.**

**The bank found a bigger defect than the one it was built for.** Pooled over 1,536
control draws: **XRP's null is anticonservative at 2.27× α** (87/768, +8.0 sd,
*p* ≈ 2 × 10⁻¹²); **SOL's is textbook** (38/768, 0.99× α, mean permutation *p* 0.4956).
The original suspicion — that *SOL* was the narrow leg — was exactly backwards. Filed as
`BL-20260820-TRADE-BLOCK-NULL-IS-ANTICONSERVATIVE-ON-XRP` (high).

**Self-checked, because the result confirmed a prior guess.** One candidate artifact was
**refuted** (E2's observed and null paths agree to 6.9 × 10⁻¹⁸ over 48 cells). The
shared-shuffle correlation worry **survived with a limit**: six seeds on one cell give
counts 9/6/6/5/3/9, sd 2.34 against a binomial sd of 2.39 at the observed rate — no
material overdispersion, and the ~2× leg defect replicates on all six — **but the gate
fired on only 2 of 6**, so the *per-cell* verdict is a coin flip at that effect size,
exactly the 0.19 power at K = 64 the pre-shipped power curve predicted. **Quote the
leg-level figure, not the two firings.**

### 2. Edge vs barrier geometry — settled, and the E2 licence does not survive it

`docs/research/e3-barrier-geometry-2026-08-20.md`, tool
`scripts/research/e3_barrier_decomposition.py`.

- The terminal barrier alone accounts for **13.9% → 27.0% → 46.6%** of `label_hold`'s
  entropy across h = 12/24/48 **on one leg**, with SOL h48 at **47.4%** agreeing to
  0.8 pp. P(hold) is 0.017 at the stop, 0.995 at the target, 0.556 at the time stop.
  **The rung where E2 starts finding hits is the rung where the barrier's share passes
  ~45%.**
- The pooled association **reverses inside every stratum** — `dist_to_stop_atr` +0.117
  pooled / −0.194 within; `upnl_r` +0.106 / −0.219 — on exactly the features the horizon
  arm reported clearing FWER. **5 of 27 features flag a full reversal on each h48 panel.**
  The *within* value is stable across legs and rungs; the *pooled* value, which E2 scores,
  swings with the barrier mix.
- The stratified view licenses nothing either: `touch` is the barrier the trade **later**
  reached. **So E3 is not licensed by an E2 information score on this label.**

### 3. E3 — run anyway, as a decision-time cost-probed screen. Honest negative.

`docs/research/e3-joint-lever-screen-2026-08-20.md`, tool
`scripts/research/e3_joint_lever_sweep.py`. 11 singles + **179 cells**, 503 + 567 trades,
4 anchored strictly-forward walk-forward folds, every number OOS.

| leg | singles | joint (16.3× the grid) |
|---|--:|--:|
| XRPUSDT | **+5.467 R**, 4/4 folds | **+5.467 R** — *the same cells* |
| SOLUSDT | −9.912 R, 2/4 | −3.121 R, 3/4 — still negative |

**The falsifier fails on both legs.** XRP: the joint grid bought **+0.000 R**. SOL: both
arms lose, so the comparison does not apply (`falsifier_applicable: false`).

**And the one positive cell dies on cost.** `bank0.75` breaks even between **0.02 R and
0.05 R** of extra cost per early exit; the repo's own
`execution_costs.DEFAULT_FEE_BPS_ROUNDTRIP = 7.5` resolves to **0.082–0.163 R** here
across R = 1.0×–2.0× ATR. Fee-only, before slippage and funding.

**A decision-time lever screen is HORIZON-INVARIANT** — verified to full float equality
(`5.467111` on both the h12 and h48 panels). The horizon moves only the *label*; a lever
acts on the *trade*. No lever inherits the h=48 licence.

## Validation

- `e2_feature_information --selftest` **42/42** (was 31/31; +11 covering the measured
  false-invalidation rate, a planted miscalibration the gate must catch,
  `unchecked`-is-not-`valid`, and bank-excluded-from-family)
- `e2_null_calibration --selftest` **12/12** · `e3_barrier_decomposition --selftest` **6/6**
  (incl. a planted Simpson's paradox) · `e3_joint_lever_sweep --selftest` **8/8**
- `tests/test_e2_feature_information.py` **5 passed**
- Panels rebuilt locally reproduce the horizon arm **exactly**: XRP 9,761 rows / 503
  trades, SOL 10,724 / 567, xa `row_coverage` 1.0. `dist_to_stop_atr` at XRP h12 returns
  `0.0087`, matching the published table.

## Filed

- `BL-20260820-E2-NEGATIVE-CONTROL-GATED-POINTWISE-NOT-FWER` — **resolved**
- **`BL-20260820-TRADE-BLOCK-NULL-IS-ANTICONSERVATIVE-ON-XRP`** (high) — found by the new
  bank, and larger than the row it was built to close
- `BL-20260820-E2-LABEL-BARRIER-DOES-NOT-MATCH-THE-LIVE-EXIT-POLICY` (high) — every E2
  artifact scores a 2.0 R label barrier while the live legs trade `tp_at_r: 1.5`, and
  **no harness trade ever realises above +1.5 R** (caps at exactly +1.500, 97/503 trades)
- `BL-20260820-TP-LEVEL-IS-THE-ONE-EXIT-PARAMETER-NEVER-SWEPT` (high) — 8 lever columns,
  all post-entry overrides; `m27/ict_scalp_exit_sweep.py` takes `tp_at_r` as a fixed input
  and skips any rung `>= tp_at_r`. MFE p50 is 0.70 R against a 1.5 R target reached by
  ~19% of trades
- `BL-20260820-EXIT-LEVER-BREAKEVEN-IS-BELOW-THE-REPO-OWN-FEE-CONSTANT` (high) — and the
  same arithmetic puts the legs' fee-free means (+0.1376 R / +0.1167 R) inside their own
  round-trip cost. **Filed as a flag with arithmetic, not a finding**; R is inferred from
  ATR rather than read from the harness's per-trade `risk`, and the baseline half routes
  to `/performance-review` if it survives

## Docs updated

`docs/design/exit-mechanism-construction-PROCESS.md` (E2 gate resolution + E3 outcome),
three new research artifacts, `ROADMAP.md`, the health-review backlog.

## What did NOT happen

- **No Tier-3 anything.** No `config/`, `src/`, `ml/`, unit file, VM mutation or order path.
- **No cell entered the coverage matrix as a candidate** — the one positive cell fails cost.
- **The trainer VM was not used.** CPU-only, run on a free runner lane and locally, per the
  board's routing rule.
