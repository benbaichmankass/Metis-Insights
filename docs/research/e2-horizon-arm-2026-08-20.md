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

