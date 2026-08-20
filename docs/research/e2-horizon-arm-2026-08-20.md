# E2 × horizon — does the per-feature information verdict depend on the label horizon?

**Date:** 2026-08-20 · **Step:** the horizon arm of E2,
[`docs/design/exit-mechanism-construction-PROCESS.md`](../design/exit-mechanism-construction-PROCESS.md) § E2
· **Predecessor:** [`e2-feature-information-2026-08-20.md`](./e2-feature-information-2026-08-20.md)

E2 returned **`no_feature_beats_control`** on `advantage_r` and `label_hold`, on two
independent legs, at a **12-bar (3 h) vertical barrier**. That write-up recorded the
horizon as a **condition on the answer, not a property of the fleet**, and named a
longer-horizon arm as the cheapest follow-up. This is that arm.

**The question is narrow and worth stating exactly:** the reference measured that no
feature carries information about *whether holding beats exiting now, over the next 3
hours*. It did **not** measure whether one does over a longer hold. Those are different
claims, and only the first was tested.

---

## 1. What varies, and the two things that must not

The arm varies **one** thing: `--time-stop-bars`, the vertical barrier. Two properties
of the substrate would otherwise move with it and make the comparison meaningless.

### 1.1 The feature set must not move with the horizon — `expected_hold_bars` is PINNED

`build_intrabar_exit_panel.py:214` resolves

```
expected = expected_hold_bars if expected_hold_bars else time_stop_bars
```

and `intrabar_features.py:122` computes `bars_in_trade_frac = n / expected`. Left at its
default, that denominator **tracks the horizon** — so each rung would carry a *different*
`bars_in_trade_frac`, and any difference between rungs would confound a label change with
a feature change. Every rung here passes `--expected-hold-bars 24`, so the feature matrix
is identical across the ladder and only the label differs.

### 1.2 The purge buffer must keep its relationship — `embargo_bars` TRACKS the horizon

`analyze_exit_head._grouped_purged_folds` purges a training row whose **own** `label_t1`
reaches within `embargo_bars` of the test block's earliest `label_t0`. Because
`label_t1 = t + touch_offset` — the *actual* barrier touch, not the worst case — the purge
is **already horizon-aware per row**, and only rows that run to the time-stop pay the
longer span. The embargo is an *additional* buffer on top of that. The reference arm ran
embargo 12 at horizon 12; holding embargo fixed while the horizon grew would quietly
shrink that buffer in relative terms, so each rung sets `--embargo-bars` equal to its own
horizon.

## 2. The ladder is anchored on a declared value, not intuited

`build_backtest_panel.py:144` declares the `ict_scalp` adapter's own trade timeout as
`timeout_bars: int = 24`, and `build_intrabar_exit_panel.py` exposes **no override** — so
a trade in this panel runs **at most 24 bars**. (Consistent with the reference substrate:
10,103 rows / 530 trades = 19.06 sampled bars per trade.) The rungs are that value's
multiples:

| horizon | × timeout | what it asks |
|--:|--:|---|
| **12** | 0.5× | the reference configuration — a **replication control**, not a result |
| **24** | 1.0× | hold to the strategy's **own designed limit** |
| **48** | 2.0× | hold **beyond** the design |
| **96** | 4.0× | far past trade close — the honest upper probe, where `forward_r` stops being an exit-timing question and becomes a strategy-change question |

The 96-bar rung is reported with that caveat attached rather than presented as an exit
result.

## 3. Where this substrate DIFFERS from the reference — both differences stated, neither glossed

The reference ran on the trainer's corpus; this runs on **Binance's public archive** from
a GitHub-hosted runner (`data.binance.vision`; `api.binance.com` geoblocks US runners,
which is why the archive is the repo's feed source). Two consequences, and they cut in
opposite directions:

1. **The trades are not the same trades.** Same symbol, same timeframe, same harness,
   different feed — so the 12-bar rung is **not** expected to reproduce the reference
   exactly. What is expected is the same **direction**. If it does not reproduce, the
   longer rungs are not trustworthy either, and that is a finding about the lane rather
   than about the fleet.

2. **The order-flow features are LIVE here, and were dead in the reference.** The
   reference dropped `feat_taker_imbalance` and `feat_taker_imbalance_intrade` as
   **all-null**, because the trainer's candle source carried no taker split — which is why
   that write-up says in terms that *"E2 says nothing about order flow."* The Binance
   archive carries `taker_buy_base`, so both columns are dense here: **27 scored features
   against the reference's 25.** This arm therefore also answers a question the reference
   could not, and the extra two columns are part of the family the FWER threshold is
   calibrated over.

## 4. A structural finding, confirmed before the results were read

`bars_in_trade_frac = n / expected_hold_bars` divides by a **constant**. Division by a
positive constant is **rank-preserving**, so under any rank-invariant method the column is
**the same feature** as `bars_in_trade` — that covers Spearman (what E2 scores) *and*
gradient-boosted trees (which split on order, not magnitude). Measured on a real panel
(XRPUSDT 15m, 973 rows from 48/48 trades), the two returned
`statistic 0.1189399575806653` and `p_empirical 0.054945054945054944` — equal in full
float repr.

The reference run recorded this as an empirical coincidence, *"identical to 16 s.f."*
It is **structural**, holds at every horizon, and means the family carries one fewer
distinct test than its column count suggests. Filed as
`BL-20260820-BARS-IN-TRADE-FRAC-RANK-IDENTICAL`; not a defect in E2, which faithfully
scores the columns it is handed.

## 5. Method, unchanged from the reference

Statistic `abs_mean_of_per_fold_spearman`; null = **trade-block cyclic** label shuffle
(rows within a `trade_id` move together, because the panel's features are autocorrelated
within a trade and a row-level shuffle would score against a null that is too tight);
decision rule = pre-registered **max-statistic FWER** at α = 0.05, with a scale-free
**min-p** companion reported beside it; 1,000 replicates; 4 purged/embargoed grouped
walk-forward folds; positive and negative controls injected **outside** the family and
required to fire, or the run returns `harness_invalid` and is not admissible evidence.
An underpowered run returns **`unmeasured`**, which is **never** read as a negative.

---

## 6. Result — the horizon changes the answer, for the SIGN but not the MAGNITUDE

**Population, on every number below.** `ict_scalp`, 15m, Binance public archive,
2021-08-16 → 2026-08-19. **XRPUSDT: 9,761 labelled rows from 503/503 trades.
SOLUSDT: 10,724 rows from 567/567 trades.** 27 scored features, cross-asset block
`state: joined` at `row_coverage` **1.0** on every rung. 4 folds, 1,000 shuffles,
α 0.05, `tp_r` 2.0, seed 20260820. `expected_hold_bars` pinned at 24, `embargo_bars`
= horizon. **Rows, trades and feature count are IDENTICAL across the four rungs of a
leg** — the label is genuinely the only thing that varies.

Cells read `verdict · FWER/min-p/pointwise`.

### `forward_r` — informative at every rung, on both legs, and still not the question

| leg | h=12 | h=24 | h=48 | h=96 |
|---|---|---|---|---|
| XRP | HIT 6/7/9 | HIT 5/5/11 | HIT 5/7/10 | HIT 5/8/10 |
| SOL | HIT 5/5/7 | HIT 5/5/7 | HIT 5/5/6 | HIT 5/6/6 |

Unchanged from the reference's reading: these are largely arithmetic about where the
trade already **is**, because `forward_r` is measured from entry and shares its
baseline with every path feature.

### `advantage_r` — negative wherever it is admissible

| leg | h=12 | h=24 | h=48 | h=96 |
|---|---|---|---|---|
| XRP | none 0/0/0 | none 0/0/1 | none 0/0/1 | none 0/0/2 |
| SOL | none 0/0/0 | **harness_invalid** | **harness_invalid** | **harness_invalid** |

**The magnitude of the hold-vs-exit advantage stays unpredictable at every horizon
tested.** Note honestly that this is carried by the XRP leg alone: three of SOL's
four `advantage_r` cells are inadmissible (§ 7), so SOL contributes one rung.

### `label_hold` — THE VERDICT FLIPS, and it replicates

| leg | h=12 | h=24 | h=48 | h=96 |
|---|---|---|---|---|
| XRP | none 0/0/0 | none 0/0/1 | **HIT 2/3/5** | **HIT 3/4/7** |
| SOL | none 0/0/1 | none 0/1/3 | **harness_invalid** | **HIT 4/5/6** |

**The reference's negative was horizon-bound.** At 3 h nothing predicts whether
holding beats exiting; at 24 h, on two independent legs, several features do.

## 7. Why this is not a max-statistic reshuffle

The leading feature changes identity between rungs, so the max statistic alone is a
bad witness. Tracking **one** feature — `dist_to_stop_atr` — across the whole ladder,
with its own FWER threshold beside it, because **both sides move** (the null widens
with the horizon, so a rising statistic is not automatically closing on the bar):

| leg | h | statistic | FWER threshold | **gap** | p | fold sign-agreement |
|---|--:|--:|--:|--:|--:|--:|
| XRP | 12 | 0.0087 | 0.0886 | −0.0799 | 0.775 | 0.50 |
| XRP | 24 | 0.0505 | 0.1064 | −0.0559 | 0.136 | 1.00 |
| XRP | 48 | 0.1264 | 0.1164 | **+0.0100** | 0.003 | 1.00 |
| XRP | 96 | 0.1900 | 0.1242 | **+0.0657** | 0.001 | 1.00 |
| SOL | 12 | 0.0179 | 0.0905 | −0.0726 | 0.518 | 0.50 |
| SOL | 24 | 0.0489 | 0.1041 | −0.0551 | 0.144 | 0.75 |
| SOL | 48 | 0.1059 | 0.1127 | −0.0067 | 0.004 | 1.00 |
| SOL | 96 | 0.1573 | 0.1185 | **+0.0388** | 0.001 | 1.00 |

Monotone in the statistic **and** in the gap, on both legs, with fold sign-agreement
rising from 0.50 to 1.00 — i.e. every fold eventually agrees on the direction. Two
independent legs tracing near-identical trajectories is what separates this from the
`advantage_r` column, where the top statistic wandered (0.0397 → 0.0857 → 0.0809 →
0.0995) and the leading feature changed identity without ever clearing.

The h=96 FWER hits overlap across legs — XRP `{dist_to_stop_atr, upnl_r,
running_mae_r}`, SOL `{upnl_r, dist_to_stop_atr, running_mae_r, running_mfe_r}` —
three features shared.

## 8. ⚠️ Three limits, stated before anyone reads this as a lever

1. **EVERY feature that clears is ENDOGENOUS.** No exogenous / peer / order-flow
   feature clears FWER at any rung, on either leg — including the two taker columns
   that are dense here and were dead in the reference. So it is the **horizon** that
   changed the answer, **not E1's widening of the panel**. §0.2's diagnosis (that the
   all-endogenous panel was the problem) is *not* rescued by this result.

2. **Part of this may be barrier geometry rather than edge.** At a longer horizon
   more trades reach a barrier, and `dist_to_stop_atr` mechanically bears on *which*
   barrier: far from the stop ⇒ SL less likely, TP more likely ⇒ P(hold beats exiting
   now) rises. Whether that is an exploitable lever or a restatement of the
   triple-barrier structure **is not a question E2 can answer** — E2 measures
   information, not tradeable edge. It routes to the M20 net-of-cost gate.

3. **h=96 is 4× the harness's own trade timeout.** A `label_hold` measured over 24 h
   on a strategy that closes within 6 h is answering *"should a different strategy
   hold this?"* — a strategy-change question wearing an exit question's clothes. The
   h=48 rung (2×) is the more defensible one, and it is the rung SOL lost.

## 9. Admissibility — 20 of 24, and the four losses are not random

**Four runs returned `harness_invalid` and are excluded from every table above**, all
on SOL: h24 `advantage_r`, h48 `advantage_r`, h48 `label_hold`, h96 `advantage_r`. In
each, the positive control fired correctly and the **negative control did not stay
silent** — a pure-noise feature cleared the *pointwise* bar (p = 0.0410 in the
inspected case) while sitting far below the FWER threshold the decision uses.

E2 gates `harness_valid` on the negative control's **pointwise** verdict, and that
bar is the α = 0.05 quantile of the control's own null — so it misfires ≈ 5% of the
time by construction. But the observed rate is **4/24 = 16.7%**: P(X ≥ 4 |
binomial(24, 0.05)) = **0.0298**, below the same α the gate uses, and **all four
landed on one leg** (P = 0.125 for four independent discards sharing a leg). That
points at a SOL-panel-specific factor inflating the control's statistic, not at bad
luck. Filed as `BL-20260820-E2-NEGATIVE-CONTROL-GATED-POINTWISE-NOT-FWER`.

**These runs were not re-rolled with a different seed.** A pre-registered
admissibility gate that is retried until it passes is not a gate, and the fix — very
plausibly to gate on the FWER threshold instead — must be decided **before** the next
run, not after seeing which cells it would rescue. The finding survived the hole at
SOL h=48 only because h=96 replicated; that is luck, not design, and it is recorded
as such.

## 10. Disposition

§3.1 still governs, but the conditions have changed and must be recorded with the
result: **at a 3-hour barrier the fleet's panel carries no information about the
hold-vs-exit decision; at 12–24 hours it carries information about that decision's
SIGN, on two independent legs, from endogenous path features.** The magnitude
(`advantage_r`) remains unpredictable everywhere it was measured.

That is enough to license **E3 on `label_hold` at a long horizon** — and explicitly
not on `advantage_r`, and not at 3 h. Anyone quoting this must state the horizon: the
same panel, same features, same legs return opposite verdicts three rungs apart.
