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

## SUPERSEDING EVIDENCE (2026-08-09, same day) — the retune was tested and FAILED

> **This section overrides the proposal below it.** The operator approved option A
> **conditional on testing it first**. It was tested. **The recommendation is now
> option C — hold at 2.5, no config change.** Options A/B are preserved verbatim
> beneath as the record of what was proposed and why, because the reasoning that
> produced them is the thing that turned out to be insufficient, not wrong.

The sweep above ranks cells by aggregate deltas. It never reports **how many
trades the ranking rests on** — and `+7.1999R` built from four trades and from
sixty are the same number and completely different evidence.

Instrument: [`scripts/research/m20_trail_attribution.py`](../../scripts/research/m20_trail_attribution.py).
Trainer-diag **#8672** (first run), **#8676** (maxDD reconciliation), **#8677**
(re-run on the corrected cost basis).

### 1 — the denominator: 17 of 250 trades, and 3 of 75 out of sample

*Same population as above; `arm_r` 6.49, CLI cost basis (fee 7.5bps + slippage
5.0bps + funding 1.0bps/8h).*

| window | trades | armed (`mfe_r ≥ 6.49`) | differing | Δ net R |
|---|--:|--:|--:|--:|
| ALL | 250 | **17** (6.8%) | 17 | **−1.8395** |
| IS | 175 | 14 | 14 | **−2.4395** |
| OOS | 75 | **3** (4.0%) | 3 | +0.6000 |

### 2 — where the lever acts, 2.0 is WORSE

| | count | total |
|---|--:|--:|
| gains | 14 × **+0.2R** | +2.8000 |
| losses | 3 (−1.7176, −2.4505, −0.4714) | −4.6395 |
| **net on trades touched** | 17 | **−1.8395** |

It wins small and often, loses big and rarely — the wrong shape for a
trend-following exit, whose profit comes from the few trades that run.

The uniform `+0.2R` is an **internal consistency check**, not a coincidence:
when both arms stop on the same bar, the tighter trail sits `(2.5−2.0)×ATR`
closer and risk is `atr_stop_mult×ATR = 2.5×ATR`, so the gain is exactly
`0.5/2.5 = 0.2R`. The instrument reproduces the arithmetic the engine must
produce. The three losses are the bars where the tighter stop fired *earlier*
and cut a run.

### 3 — the aggregate edge is a SEQUENCING artifact, not better exits

On the CLI basis the headline says 2.0 beats 2.5 by **+0.2960R** net
(39.4485 vs 39.1525). But the lever's own contribution is **−1.8395R**. The
residual **+2.1355R** comes from three trades that exist in only one book —
`unique_to_live` 2023-06-21 16:00 and 2023-06-23 15:00; `unique_to_proposed`
2023-06-21 01:00. Shifting one exit moved `next_idx = exit_index + 1 +
cooldown_bars`, so the two configs took **different subsequent trades** over one
week in June 2023.

An aggregate that is dominated by which trades a config happened to take next is
not an edge. This is precisely why the arms are joined on `entry_time` rather
than zipped positionally — a positional compare would have folded that +2.14R
into the per-trade deltas and reported 2.0 as a clean winner.

### 4 — not a knife-edge; negative at every arm with a real sample

| `arm_r` | armed | Δ net R on trades touched |
|--:|--:|--:|
| 3.0 | 54 | **−1.3089** |
| 4.0 | 38 | **−4.1322** |
| 5.0 | 26 | **−6.1243** |
| 6.49 (live) | 17 | **−1.8395** |
| 8.0 | 7 | +1.4000 — `TOO_THIN`, below the 10-trade bar |

There is no arm setting at which tightening to 2.0 is justified on the trades it
changes.

### 5 — the maxDD basis, reconciled (the gate verdict rests on it)

The "+0.4220 IS maxDD" finding above was measured on a **date-restricted** run;
a first attribution pass measured the full tape and saw an identical maxDD across
all three arms, which read as a contradiction. Two separate things were going on:

- **The restriction hypothesis was WRONG.** Date-restricted and
  full-tape-split-by-`entry_time` agree *exactly* — OFF 14.7968, live 15.2188,
  proposed 14.7968 on both (`bases_agree: true`).
- **The real cause was COST BASIS**, and it was a defect in the attribution tool.
  `scripts/backtest_trend.py` holds its cost terms in **module globals** and
  `main()` resolves symbol-specific slippage/funding into them when the CLI flags
  are left at their `None` default; the module's own initial values are `0.0`. So
  a caller that imports the harness and calls `run_backtest()` directly runs
  **fee-only**, while every CLI/sweep run is fee+slippage+funding. Same engine,
  same 175-trade book, maxDD 13.2595 vs 14.7968.

Corrected, the reconciliation **reproduces the sweep exactly**:

| arm | IS maxDD | Δ vs OFF |
|---|--:|--:|
| OFF | 14.7968 | — |
| tight **2.5** (live) | 15.2188 | **+0.4220** |
| tight **2.0** (proposed) | 14.7968 | +0.0000 |

So the gate failure is **real and robust to cost basis** (fee-only gives
+0.3474 — same sign, same conclusion). And the per-trade deltas are
**identical on both bases** (−1.8395 either way), since a shared trade's gross R
does not move with the cost model. Both facts measured, neither assumed.

The generalised trap is filed as
`BL-20260809-INPROCESS-HARNESS-RUNS-FEE-ONLY-SILENTLY` — this tool is fixed
(`--cost-basis`, default `cli`, effective terms printed beside the population),
the trap is not.

### Verdict

**Option C — hold at `trail_decay_tight_mult: 2.5`. No config change.**

The live cell does fail its gate on drawdown (+0.4220R IS), and that finding
stands. But the proposed replacement is **not better**: it is worse on the 17
trades it touches, at every arm with a usable sample, and its aggregate
advantage is a one-week sequencing accident. Swapping a value that fails a gate
for one that fails on the merits is not an improvement, it is churn on a live
strategy.

Two bounds that keep this in proportion, and that argue against option B as
well: the lever versus OFF is **+9.53R** (live) / **+9.83R** (proposed) at
identical full-tape maxDD (27.3002 across all three arms), so the lever itself
is earning its place — it is only the 2.5→2.0 *move* that is unsupported. And
**nothing can change until a trade reaches 6.49R peak**, which has not happened
in 2026 (max 4.593 over 35 trades).

`BL-20260808-TRAIL-LEVER-TUNED-ON-NON-LIVE-FAITHFUL-TRAIL` closes as
`resolved_hold` — the tuning-basis defect is now *measured* rather than
suspected, and the measurement says the current value is the better of the two.

---

## PROPOSAL (Tier-3 — superseded by the section above; kept as the record)

**~~Recommended: option A.~~** — see the superseding evidence. Option A was
approved conditionally, tested, and withdrawn; the recommendation is option C.

### Option A — retune `tight_mult` 2.5 → 2.0 (~~recommended~~ WITHDRAWN)

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

### Option C — hold (no change) — **THIS IS WHAT WAS CHOSEN**

~~**Not recommended.** It is the only option that leaves a value in place which is
now *measured* to fail its own gate on the engine the live monitor matches, and
which is strictly dominated by a neighbouring value.~~

**The "strictly dominated" claim was the error.** Dominance was read off
aggregate net_R and maxDD; per-trade attribution showed the dominance is a
sequencing artifact and that 2.0 is *worse* where the lever acts. See the
superseding section at the top.

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
