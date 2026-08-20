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

## 1. Question (1) — "why is it SOL?" — no defect large enough to explain it

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
make.

### 1.1a The matched-horizon comparison the row actually asked for

Criterion (1) specifies *"compare the negative control's null distribution between the
SOL and XRP panels at matched horizons."* Both h48 panels, `label_hold`, 400 controls:

| panel | clear-rate | mean *p* | median *p* | null sd | `cv_length` | binomial *p* |
|---|--:|--:|--:|--:|--:|--:|
| XRPUSDT h48 | **0.0375** | 0.5008 | 0.4910 | 0.00687 | 0.4256 | 0.901 |
| SOLUSDT h48 | **0.0700** | 0.4921 | 0.4745 | 0.00655 | 0.4503 | 0.048 |

**Do not read this as a clean bill either.** SOL's control null is ~5% **narrower** and
its clear-rate ~1.9× XRP's at the matched cell — *the direction the block-length
mechanism of § 1.2 predicts*, and SOL's `cv_length` is indeed the higher of the two.

But it is **marginal and does not carry the finding**: binomial *p* = 0.048 on a single
cell, at α = 0.05, with three targets examined — not significant under even minimal
multiplicity correction, nowhere near the gate's 0.01, and far too small to produce four
discards. On the same SOL panel the other two targets measure 0.0375 (`advantage_r`) and
0.0475 (`forward_r`), so the elevation is not a stable property of the leg.

**The honest statement is therefore narrower than "refuted":** there is no SOL-specific
defect *large enough to explain the discard pattern*, and the arithmetic in § 1.3 already
explains it without one — but a small difference in the predicted direction is present
and should not be waved away. The 24-cell re-run's banks give 1,536 control draws and are
the properly-powered read on it.

> ⚠️ **AND THAT READ REVERSES THIS ONE — do not stop at this table.** Pooled over 768
> draws per leg (§ 3.6), **XRP is the anticonservative leg at 2.27× α and SOL measures
> textbook at 0.99× α** — the opposite of what this single 400-column cell suggests. The
> two are not in conflict so much as differently powered: § 3.7 shows the same panel and
> target swings 0.047–0.141 across six seeds, so *any* single-cell reading of this
> quantity is noise. **The per-leg pooled figure is the one to quote; this table is kept
> because it is what criterion (1) literally asked for, not because it is the answer.**

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

So there is no SOL-specific factor large enough to matter. The finding is that **the
sweep's own independence assumption was wrong** — a shared control column was counted as 24
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

## 2.4 A substrate fact the manifests settle, recorded here because it bears on § 2

`label_config.cost_r` is **0.0** on every panel: the `advantage_r` / `label_hold` labels
are computed **fee-free**. And `run_info.ignored_yaml` is **false**, so the *trade* loaded
live YAML (`tp_at_r: 1.5`) while the *label* used `tp_r: 2.0` — the mismatch filed as
`BL-20260820-E2-LABEL-BARRIER-DOES-NOT-MATCH-THE-LIVE-EXIT-POLICY`, established from the
artifact rather than inferred. `expected_hold_bars` is pinned at 24.0 on all eight panels
and `time_stop_bars` is the only field that varies, so the horizon arm's two confounds are
verifiable from the manifests without re-deriving them.

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

## 3.5 THE RE-RUN — and it moves the horizon arm's headline

The full 24-cell sweep was re-run under the new rule (not the four cells patched, as the
row requires). Same panels, same seed, same 1,000 shuffles.

**Every admissible cell reproduces its published FWER/min-p/pointwise counts EXACTLY.**
The gate decides admissibility, not verdicts, and it shows: nothing moved except which
cells are allowed to speak. What moved is the *set*.

| target · cell | published | re-run |
|---|---|---|
| `advantage_r` SOL h24 / h48 / h96 | `harness_invalid` ×3 | **none 0/0/1 · 0/0/1 · 0/0/2** — recovered |
| `label_hold` **SOL h48** | `harness_invalid` | **HIT 3/5/6** — recovered, *and it is a hit* |
| `label_hold` **XRP h48** | HIT 2/3/5 | **`harness_invalid`** |
| `label_hold` **XRP h96** | HIT 3/4/7 | **`harness_invalid`** |

**The admissibility holes swapped legs**, and § 3.6 measures why.

Two consequences for the horizon arm, both material:

1. **`advantage_r` is now negative on 8 of 8 cells**, not 5 of 8. Its claim — the horizon
   buys the decision's SIGN, not its MAGNITUDE — is *stronger* than published.
2. ⚠️ **The `label_hold` flip is now carried by SOL ALONE**, at h48 (HIT 3/5/6) *and*
   h96 (HIT 4/5/6). The horizon arm's central defence — *"two independent legs tracing
   near-identical trajectories"* — **must be withdrawn for `label_hold`.** Its § 7
   monotonicity table remains a valid description of the statistics, but XRP's half of it
   is measured against a null now known to be anticonservative, so the "gap to the bar"
   column for XRP is not reliable.

   The lament that SOL h48 survived "by luck, not design" is resolved in the other
   direction: **the hole at the decisive rung was the gate's error, and the cell is a hit.**

⚠️ **`legacy_pointwise_gate_would_invalidate` is NOT a replay of the original sweep.**
It reads bank column 0 — but adding K = 64 columns changed the RNG stream, so column 0
is *not* the column the original single-control run drew. On this run the old rule would
have discarded **0 of 24**; the original discarded 4. Both are single draws from the same
Bernoulli(α), and that they disagree so completely is the clearest possible illustration
of why the rule was replaced. **Do not quote 4 → 0 as a like-for-like improvement.**

## 3.6 The bank found a bigger defect than the one it was built for

Pooled over the 24-cell sweep — **1,536 control draws**, the properly-powered read the
row's criterion (1) called for:

| population | cleared | rate | vs α | sd | binomial *p* | mean permutation *p* |
|---|--:|--:|--:|--:|--:|--:|
| **SOLUSDT** (12 cells) | 38/768 | **0.0495** | 0.99× | −0.1 | 0.55 | 0.4956 |
| **XRPUSDT** (12 cells) | 87/768 | **0.1133** | **2.27×** | **+8.0** | **2.3 × 10⁻¹²** | 0.4707 |
| pooled | 125/1536 | 0.0814 | 1.63× | +5.6 | — | — |

**The original suspicion was exactly backwards.** The row hypothesised that *SOL's*
control null was too narrow. Measured on 768 draws per leg: **SOL's null is textbook**
(0.99× α, mean permutation *p* 0.4956 against a theoretical 0.5) and **XRP's is
decisively anticonservative** — 2.27× α at *p* ≈ 2 × 10⁻¹².

That is a defect **in the null**, not in the gate, and it is more consequential than the
row that started this: an anticonservative null lowers the FWER threshold too, so **XRP's
E2 hits across every target are inflated by an unknown amount**. Filed as
`BL-20260820-TRADE-BLOCK-NULL-IS-ANTICONSERVATIVE-ON-XRP`.

⚠️ **The mechanism is NOT established.** § 1.2's cycling/truncation distortion is a real
structural candidate and predicts severity scaling with block-length inequality — but the
`cv_length` ordering runs the **wrong way** (SOL 0.4503 > XRP 0.4256), so it cannot be the
whole story and may not be any of it. Naming it as the cause here would be the same
unearned mechanism claim this document withdrew in § 1.1a.

## 3.7 ⚠️ Checking my own result: the per-cell verdicts are seed-dependent, the leg-level defect is not

The finding in § 3.6 confirmed what § 1.2 had guessed, which is exactly when a result
needs the hardest look. Two hypotheses were tested and one survived.

**Refuted — E2's observed and null paths are consistent.** `observed` is computed by
`_fold_stat` and the null by `_prepare_fold_feature` → `_corr_from_centered`. If those
disagreed, observed and null would not be comparable and the whole rate would be an
artifact. Measured over 48 (fold, column) cells on the XRP h48 panel: worst
`|observed_path − null_path|` = **6.9 × 10⁻¹⁸**. They agree.

**Survived, but with a limit that must travel with § 3.6.** All 64 columns in a run share
the same 1,000 shuffled label sets, so their verdicts could be correlated and the
`Binomial(K, α)` reference the gate uses could be wrong. Re-running the *same panel and
target* under six seeds:

| seed | 20260820 | 11 | 22 | 33 | 44 | 55 |
|---|--:|--:|--:|--:|--:|--:|
| bank cleared | **9**/64 | 6/64 | 6/64 | 5/64 | 3/64 | **9**/64 |
| `harness_state` | **invalid** | valid | valid | valid | valid | **invalid** |

- **No material overdispersion.** Observed sd of the counts is **2.34**; the binomial sd
  *at the observed rate* is **2.39**. The shared-shuffle correlation is not large enough
  to break the reference distribution.
- **The leg-level defect replicates.** Mean rate **0.0990 = 1.98× α** across six
  independent seeds (range 0.047–0.141). § 3.6's XRP finding stands.
- ⚠️ **But the per-cell verdict does not.** The gate fired on **2 of 6** seeds. So
  *"XRP h48 `label_hold` is inadmissible"* is **not a stable verdict** — it is a
  coin-flip at this effect size, and the same is true of the h96 cell.

**This is the gate behaving exactly as its own power curve predicted**, not misbehaving:
at K = 64 the power against a 2× inflated null is **0.19**, and 2/6 is consistent with
that. The curve was computed before the rule shipped and is in § 2.1.

**So read § 3.5 this way.** The reliable statement is the **leg-level** one — XRP's null
is anticonservative at ~2× α on 768 draws (*p* ≈ 2 × 10⁻¹²) — and the per-cell firings
are a noisy manifestation of it, not independent evidence about those two cells. The
correct conclusion is *"every XRP `label_hold` verdict at long horizon rests on a null
measured hot"*, which is **stronger** than the two firings and does not depend on them.
It also says what the gate should read next: the **per-leg** pooled rate, not only the
per-cell one — recorded in the filed row's criterion (4).

## 4. Disposition

The rule above is fixed. The **full 24-cell sweep is re-run under it** — not the four
cells patched, as the row requires — and both gates' verdicts are reported side by
side so the change's effect on the sweep is visible rather than asserted.

---

## Appendix — the full re-run matrix (24/24 cells, new gate)

## `forward_r`  (verdict · FWER/min-p/pointwise)

| symbol | h=12 | h=24 | h=48 | h=96 |
|---|---|---|---|---|
| `SOLUSDT` | **HIT** · 5/5/7 | **HIT** · 5/5/7 | **HIT** · 5/5/6 | **HIT** · 5/6/6 |
| `XRPUSDT` | **HIT** · 6/7/9 | **HIT** · 5/5/11 | **HIT** · 5/7/10 | **HIT** · 5/8/10 |

## `advantage_r`  (verdict · FWER/min-p/pointwise)

| symbol | h=12 | h=24 | h=48 | h=96 |
|---|---|---|---|---|
| `SOLUSDT` | none · 0/0/0 | none · 0/0/1 | none · 0/0/1 | none · 0/0/2 |
| `XRPUSDT` | none · 0/0/0 | none · 0/0/1 | none · 0/0/1 | none · 0/0/2 |

## `label_hold`  (verdict · FWER/min-p/pointwise)

| symbol | h=12 | h=24 | h=48 | h=96 |
|---|---|---|---|---|
| `SOLUSDT` | none · 0/0/1 | none · 0/1/3 | **HIT** · 3/5/6 | **HIT** · 4/5/6 |
| `XRPUSDT` | none · 0/0/0 | none · 0/0/1 | **harness_invalid** | **harness_invalid** |
