# M20 — `trend_donchian` trail-decay re-sweep on the converged engine (2026-08-09)

**Tier-3 PROPOSAL. Nothing here is merged.** `config/strategies.yaml` is untouched
by the PR carrying this memo.

Closes the evidence half of
`BL-20260808-TRAIL-LEVER-TUNED-ON-NON-LIVE-FAITHFUL-TRAIL`.

---

## The question

`config/strategies.yaml::trend_donchian` declares, **live and armed, on real
money**:

```yaml
trail_decay_arm_r: 6.49
trail_decay_tight_mult: 2.5
```

Those values were fitted (P4.4, 2026-07-13, #6273) against
`scripts/research/backtest_trend.py`, whose trail multiplies the **current**
bar's rolling ATR. `trend_donchian.monitor()` trails off the **frozen** entry-bar
ATR. Trail-decay's whole job is to tighten the trail once peak open profit
reaches `arm_r`, and both the R path that reaches 6.49 and the stop the tightened
mult then produces depend on the baseline trail's distance — the very thing that
differed. So the lever was tuned on a trail that behaves unlike the live one.

PR #8633 ported all 15 levers onto the live-faithful
`scripts/backtest_trend.py`, so the sweep can finally run on the engine the
monitor matches. This is that sweep.

## Population — state it before any number

| | |
|---|---|
| instrument | BTCUSDT, **1h**, resampled from 124,684 native 15m bars (`/home/ubuntu/m27_data/BTCUSDT_15m.csv`) |
| span | **2023-01-01 → 2026-07-22** (3.55 years) |
| engine | `scripts/backtest_trend.py` @ trainer HEAD `10990013` (the converged copy) |
| driver | `scripts/research/m20_trail_resweep.py` @ `548bfee8` |
| config | **config-exact**: donchian 20 · atr_period 14 · atr_stop_mult 2.5 · trail_mult 5.0 · min_confidence 0.7 · long_only |
| split | IS = through 2025-06-30 · OOS = 2025-07-01 onward |
| n | **250 baseline trades** — 175 IS / 75 OOS |
| folds | 4 per-year (2023 / 2024 / 2025 / 2026-to-date) |
| grid | `arm_r ∈ {3, 4, 5, 6.49, 8}` × `tight_mult ∈ {2.0, 2.5, 3.0}` = 15 cells + a lever-OFF arm |
| runs | 16 cells × 7 windows = 112 harness invocations |
| evidence | trainer-diag #8662 (sweep) + #8664 (gate table); raw JSON `/tmp/m20_trail_resweep.json` |

**n = 250 is small, and 75 OOS trades is smaller.** Everything below is a
direction, not a precise magnitude.

## Baseline — lever OFF

| window | n | net_R | maxDD_R | max_mfe_R |
|---|--:|--:|--:|--:|
| full | 250 | **+29.6202** | 27.3002 | 14.217 |
| IS | 175 | +42.3766 | 14.7968 | 14.217 |
| OOS | 75 | **−12.5137** | 27.3002 | 10.357 |
| y2023 | 76 | +31.1876 | 14.7968 | 14.217 |
| y2024 | 69 | +9.6965 | 13.6514 | 9.646 |
| y2025 | 68 | +1.2717 | 12.0784 | 10.357 |
| y2026 | 35 | −13.9728 | 14.8125 | **4.593** |

**The leg is OOS-negative before any lever is applied.** Nothing below changes
that; the levers only move a losing book by a couple of R.

## Result — the live cell FAILS the gate

Gate (`exit-refinement` skill P2/P4): beat lever-OFF on **net_R AND maxDD in
BOTH IS and OOS**. Positive `net_R` delta = better; **negative `maxDD` delta =
better**.

| cell | dIS_netR | dIS_dd | dOOS_netR | dOOS_dd | folds | gate |
|---|--:|--:|--:|--:|:--:|:--:|
| **arm6.49_tight2.0** | +7.1999 | 0.0000 | **+2.6284** | 0.0000 | 3/4 | **pass\*** |
| arm4_tight2.5 | +1.7027 | +0.4220 | +2.1625 | −1.0091 | 2/4 | fail |
| arm5_tight2.5 | −0.2194 | +0.4220 | +2.1514 | 0.0000 | 1/4 | fail |
| **arm6.49_tight2.5 ← LIVE** | +7.5033 | **+0.4220** | +2.0290 | 0.0000 | 2/4 | **FAIL** |
| arm5_tight3 | −4.2466 | +0.4220 | +1.5674 | 0.0000 | 1/4 | fail |
| arm6.49_tight3 | +5.4146 | +0.4220 | +1.4296 | 0.0000 | 2/4 | fail |
| arm4_tight3 | +4.4111 | 0.0000 | +1.3786 | −0.8093 | 3/4 | pass\* |
| arm8_tight2.0 | +0.8668 | +0.4220 | +1.1988 | 0.0000 | 2/4 | fail |
| arm8_tight2.5 | −0.3318 | +0.4220 | +0.9990 | 0.0000 | 2/4 | fail |
| arm8_tight3 | −0.8223 | +0.4220 | +0.7992 | 0.0000 | 2/4 | fail |
| arm4_tight2.0 | +4.7642 | +0.4220 | +0.5784 | −1.2089 | 3/4 | fail |
| arm5_tight2.0 | −0.4722 | 0.0000 | +0.3676 | 0.0000 | 0/4 | fail |
| arm3_tight2.0 | −3.2812 | −0.7772 | −5.9712 | +1.1557 | 1/4 | fail |
| arm3_tight2.5 | −3.6866 | −0.5774 | −7.9783 | +1.3555 | 1/4 | fail |
| arm3_tight3 | +1.8402 | −0.7995 | −9.9766 | +1.5553 | 1/4 | fail |

**\* "pass" is on a non-worsening reading of the maxDD axis.** Both passing cells
are drawdown-**neutral** (0.0000), not drawdown-improving, in at least one
window. Under a strict *beats-on-both-axes* reading, **zero of 15 cells pass.**
That distinction is load-bearing and is why the recommendation below is modest.

### Four findings, in order of how much they should change behaviour

**1. The live cell fails, and it fails on drawdown.** `arm6.49_tight2.5` improves
net_R in both windows but makes **IS maxDD worse by +0.4220R** (14.7968 →
15.2188). The gate is conjunctive precisely so that a net_R gain cannot buy a
drawdown regression.

**2. The original justification does not reproduce.** The coverage matrix records
the P4.4 pass as *"IS 51.8→55.4 net_R dd 21.8→21.0; OOS −24.5→−20.8 dd
31.9→30.6 — improves both axes"*. On the converged engine the **baselines** are
different books (IS net_R 42.38 not 51.8; OOS −12.51 not −24.5; IS dd 14.80 not
21.8) and, decisively, **the sign of the drawdown effect flips**: the claim that
made it shippable was "improves both axes", and on the live-faithful engine the
IS drawdown axis gets *worse*. This is the defect the backlog row predicted,
measured.

**3. The live cell is strictly dominated by its own neighbour.** Holding
`arm_r = 6.49` and moving `tight_mult` 2.5 → 2.0 is better or equal on **every
measured axis**: IS drawdown 15.2188 → 14.7968 (back to baseline), OOS net_R
−10.4847 → −9.8853, folds 2/4 → 3/4. The only cost is IS net_R +7.5033 → +7.1999
(−0.30R, 0.7% of the IS book). No trade-off judgement is needed to prefer it.

**4. The lever has been INERT all year.** In 2026-to-date (35 trades) the largest
peak-R any trade reached is **4.593**, below the 6.49 arm — so the lever
**provably cannot have fired** in 2026 on this data. Whatever is decided, it
should not be expected to change 2026 behaviour. (Verified two ways: `max_mfe_r`
per window, and an exact identity check against the OFF arm, which reports every
`arm ≥ 5` cell as inert in y2026.)

### What this does NOT say

- It does not say the leg is fixed. **OOS stays negative** (−12.51 → −9.89 at
  best). This is a tuning-basis correction, not a profitability result.
- It does not establish an edge for the lever family. 2 of 15 cells clear a
  lenient reading of the gate; at 15 comparisons that is close to what selection
  noise produces, and the `regime-selectivity` no-cosmetic-cell rule applies in
  spirit.
- The per-year folds here are **4** (2023–2026); the original P4.4 claim was
  "wf 4/6" over a **6**-fold scheme. The two fold counts are not comparable and
  should not be read as 3/4 vs 4/6.

---

## PROPOSAL (Tier-3 — operator decision, not merged)

**Recommended: option A.** It is the minimal change, it is strictly dominant over
the status quo on every axis measured, and it removes the specific defect the
backlog row is about (a value fitted on the wrong trail).

### Option A — retune `tight_mult` 2.5 → 2.0 (recommended)

```diff
--- a/config/strategies.yaml
+++ b/config/strategies.yaml
@@ trend_donchian:
     trail_mult: 5.0
     trail_decay_arm_r: 6.49
-    trail_decay_tight_mult: 2.5
+    trail_decay_tight_mult: 2.0
```

- Keeps the arm where it is (6.49 is still the best-performing arm in the grid).
- IS drawdown returns to the lever-OFF baseline; OOS net_R improves; folds 3/4.
- Reductive either way — a tighter `tight_mult` tightens the stop sooner, so the
  risk direction is unchanged and a bad outcome is a suboptimal exit, never an
  unprotected position.
- Rollback is deleting one line (the lever is absent-means-off).

### Option B — remove the lever entirely

```diff
--- a/config/strategies.yaml
+++ b/config/strategies.yaml
@@ trend_donchian:
     trail_mult: 5.0
-    trail_decay_arm_r: 6.49
-    trail_decay_tight_mult: 2.5
```

Defensible, and the operator may prefer it: no cell clears a strict both-axes
gate, the whole family is worth ~2R of OOS improvement on a book that stays
negative, and the lever is inert on 2026 data. The cost of B is giving up the
+7.2R IS / +2.6R OOS net_R that option A retains.

### Option C — hold (no change)

**Not recommended.** It is the only option that leaves a value in place which is
now *measured* to fail its own gate on the engine the live monitor matches, and
which is strictly dominated by a neighbouring value.

### If A or B is approved

1. Tier-3 PR with exactly the diff above, no other change.
2. Merge on explicit approval; deploy; verify the live HEAD carries it.
3. Per `exit-refinement` P7, the next `/health-review` checks the mechanics of
   the first real lever-driven exit after the flip. Note this may take a long
   time to observe — the lever cannot fire below 6.49R peak, and nothing in 2026
   has reached that.
4. Update `docs/research/exit-refinement-coverage.json` to `shipped` with this
   memo as the ref.

## Reproducing this

```bash
# on the trainer
.venv/bin/python scripts/research/m20_trail_resweep.py \
    --data /home/ubuntu/m27_data/BTCUSDT_15m.csv --resample 1h \
    --split 2025-07-01 --years 2023,2024,2025,2026 \
    --json /tmp/m20_trail_resweep.json
```

The instrument reports inertness and arm-reachability **before** ranking cells,
because "does the lever fire at all" outranks "which cell wins" — finding 4 above
is the one that would have been missed by a cell ranking alone.
