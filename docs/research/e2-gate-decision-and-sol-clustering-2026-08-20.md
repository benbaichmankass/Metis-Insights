# The E2 admissibility gate: the decision, and what the SOL clustering actually was

**Date:** 2026-08-20 · **Step:** the E2 loose end that gates E3,
[`docs/design/exit-mechanism-construction-PROCESS.md`](../design/exit-mechanism-construction-PROCESS.md) § E2
· **Closes:** `BL-20260820-E2-NEGATIVE-CONTROL-GATED-POINTWISE-NOT-FWER`
· **Predecessor:** [`e2-horizon-arm-2026-08-20.md`](./e2-horizon-arm-2026-08-20.md)

The horizon arm discarded **4 of 24** runs as `harness_invalid`, all on SOLUSDT, and
filed a row binding the next session to **two separate questions, answered in order,
with the rule decided BEFORE the next run**. This is that answer. The rule below was
committed before the re-run sweep was dispatched; the commit order is the evidence
that it was not chosen after seeing which cells it rescues.

---

## 1. Question (1) — "why is it SOL?" — REFUTED. There is no SOL-panel defect.

The row's hypothesis was that *"SOL's control null is systematically narrower, [so]
the trade-block shuffle is not producing an equally valid null on that panel."*
Measured directly, that is **not what is happening**.

### 1.1 The null is valid on the SOL panel

E2 injects **one** negative control, so a run yields one Bernoulli draw and cannot
distinguish 5% bad luck from a broken null — the ambiguity that produced the row. A
new diagnostic, [`scripts/research/e2_null_calibration.py`](../../scripts/research/e2_null_calibration.py),
pushes a **bank of independent noise columns** through the *imported* fold, block and
shuffle machinery (never a second copy) and reports the empirical rate.

**Population:** `panel_SOLUSDT_h48.jsonl` — `ict_scalp` SOLUSDT 15m, Binance public
archive 2021-08-16 → 2026-08-19, **10,724 rows from 567/567 trades**, 4 purged
folds, embargo 48, 1,000 replicates. (The panel rebuild reproduces the horizon arm's
substrate exactly, row-for-row and trade-for-trade.)

| scheme | controls | cleared pointwise | rate | binomial *p* | mean *p* | verdict |
|---|--:|--:|--:|--:|--:|---|
| `trade_block_cyclic` (the one in use) | 200 | 8 | 0.040 | 0.787 | 0.517 | `null_consistent_with_alpha` |
| `length_matched` (comparison arm) | 200 | 9 | 0.045 | 0.673 | — | `null_consistent_with_alpha` |

A valid permutation *p* is Uniform(0,1), so its mean is 0.5. **Measured 0.517, median
0.507, clear-rate 0.040 against α = 0.05.** The null on the SOL panel is calibrated,
under the scheme in use *and* under a scheme constructed to have no distortion to
make. Whatever caused four discards, it was not a narrow SOL null.

### 1.2 A structural defect in the shuffle exists, and is measurably inert here

Worth recording because it is real and a future session will re-find it.
`block_shuffled_labels` cycles a donor block shorter than its recipient
(`src[pos % len(src)]`) and truncates a longer one, so it is **not a bijection on the
label multiset** when trade lengths differ — asserted, with its equal-length converse,
in `e2_null_calibration --selftest`. The identity assignment alone has no distortion,
which is exactly the asymmetry that would break exchangeability.

On this panel it does not bite: `cv_length` **0.450**, `mobile_fraction` **1.0**, and
the length-matched arm — which has no cycling and no truncation — returns the same
rate. **Stated as a bounded negative:** the distortion is inert *at this panel's
length dispersion*, not harmless in general.

### 1.3 What the clustering actually was: the runs were never independent

`inject_controls` seeds from the run seed and fills columns in panel row order, and
the whole sweep used one seed. **Verified by direct comparison: the `__ctrl_noise`
column is byte-identical across all three targets on a given panel.** The three
targets of a `(leg, horizon)` cell therefore share **one** noise draw — 24 cells, but
only **8** independent control columns.

Measured on SOL h48 with a 400-column bank scored against all three targets:

| pair | both discarded | expected if independent | lift |
|---|--:|--:|--:|
| `forward_r` × `advantage_r` | 10 | 0.71 | **14.0×** |
| `advantage_r` × `label_hold` | 12 | 1.05 | **11.4×** |
| `forward_r` × `label_hold` | 9 | 1.33 | **6.8×** |

A column that trips one target trips **1.59 cells** on average. Re-doing the row's own
arithmetic under the measured dependence, by convolving the empirical per-panel
discard distribution over the sweep's 8 independent panels:

| quantity | assuming 24 independent draws | under the measured dependence |
|---|--:|--:|
| P(≥ 4 discards) | **0.0298** ← the number that was quoted | **0.0860** |
| P(all on one leg \| ≥ 4) | 0.125 | **0.329** |

**Both published numbers overstated the surprise, and both flip the conclusion.**
0.086 is not below α; discards *cluster by panel by construction*, and a leg is four
panels, so four discards sharing a leg is an ordinary outcome rather than a signal.

There is no SOL-specific factor to find. The finding is that **the sweep's own
independence assumption was wrong** — a shared control column was counted as 24
separate trials — and that is what turned a 5% gate misfiring into apparent evidence
of a defect.

---

## 2. Question (2) — which bar should the gate use? THE RULE, decided before the re-run

**The gate reads a RATE over a BANK, not a single draw.** The row named gating on
`informative_fwer` as "the coherent option"; that option was designed, measured, and
**rejected on its own measurement** (§ 2.2). What ships:

- **Positive control** — unchanged. Must reach `informative_fwer`.
- **Negative-control bank** — `n_negative_controls` (default **64**) independent noise
  columns, each scored exactly like a feature and outside the family. Under a valid
  null each clears the pointwise bar with probability α, so the count is
  `Binomial(K, α)`. Refuse only when its upper tail falls below `gate_level`
  (default **0.01**).
- **`harness_state`**, four states, never collapsed: `valid` ·
  `invalid_positive_control_dead` · `invalid_null_miscalibrated` · **`unchecked`**
  (`K = 0` — the bank never ran, so *we did not look*; **not** `valid`, and it does not
  fall back to a bank of one).
- **`legacy_pointwise_gate_would_invalidate`** is recorded per run, so the re-run is
  comparable to the sweep it replaces without re-running the old code.

### 2.1 The rate is measured, not asserted

The row bound the fix to *"a self-test asserting the chosen gate's false-invalidation
rate over many seeded null panels, so the rate is a measured property of the tool
rather than an emergent surprise."* Over **40 seeded sound null panels**:

| gate | discards | rate | designed bound |
|---|--:|--:|--:|
| legacy single-draw pointwise | 2/40 | **0.050** | α = 0.05 — reproduced exactly |
| this gate | 0/40 | **0.000** | `gate_level` = 0.01 |

Bank clear-rate across those panels: **0.052** against an expected 0.05.

`K = 64` was sized on a power curve, not picked: false-invalidation 0.0044, power
0.64 against a null inflated 3× and 0.92 against 4×.

### 2.2 ⚠️ The FWER gate the row proposed was rejected BY MEASUREMENT

Gating the negative control on `informative_fwer` — "the bar the decision uses" —
reads well and is wrong. The rate at which an **out-of-family** noise column clears
the family-max threshold is not a known constant: it falls as the family grows and as
the family's null widths widen, so on a narrow family it is far from negligible, and
P(at least one of K clears) then **rises with K** — the rule would get more
trigger-happy the more carefully you measured. Measured on the module's own 2-feature
synthetic null panel: **2 of 64 columns cleared FWER** while the pointwise rate was a
clean 3/64 (binomial *p* = 0.63). That is the gate inventing a failure, which is the
sin the row was filed about.

It is kept as the reported diagnostic `n_cleared_fwer` with **no vote**. It is also
redundant: the max-statistic construction already guarantees
P(any family member clears | global null) = α *whenever the null is valid*, which is
exactly what the rate test checks.

**This is the process working.** A rule was pre-registered, the self-test measured it,
the measurement refuted half of it, and it was fixed **before** the sweep ran rather
than after seeing which cells it rescued.

### 2.3 A gate that cannot fail is not a gate

`_selftest` plants the one failure that actually breaks a noise bank's calibration —
control columns given trade structure while the null shuffles at **row** level, so the
null no longer preserves the dependence the columns carry — and requires
`invalid_null_miscalibrated`. A row-level shuffle alone does **not** break a bank of
i.i.d.-per-row columns, which is why the plant has to break both halves.

---

## 3. What this changes about the horizon arm's result

**Nothing about the verdicts, and one thing about the argument.** No bad verdict was
admitted: the FWER machinery was correct in all four discarded runs, and the gate
failed safe. What changes:

- The horizon arm's § 9 claim that the discard rate *"points at a SOL-panel-specific
  factor inflating the control's statistic, not at bad luck"* is **withdrawn**. Both
  supporting probabilities assumed independence the runs do not have.
- Its § 9 note that SOL h48 `label_hold` survived only because h96 replicated — *"luck,
  not design"* — **stands**, and the re-run is what removes the hole.
- The `label_hold` flip at 48–96 bars is untouched by any of this and is re-measured
  under the new gate in the sweep this document licenses.

## 4. Disposition

The rule above is fixed. The **full 24-cell sweep is re-run under it** — not the four
cells patched, as the row requires — and both gates' verdicts are reported side by
side so the change's effect on the sweep is visible rather than asserted.
