# E3 precondition — the `label_hold` signal is barrier COMPOSITION, and the bracket it composes was never chosen

**Date:** 2026-08-20 · **Step:** the E3 precondition named in
[`e2-horizon-arm-2026-08-20.md`](./e2-horizon-arm-2026-08-20.md) § 8.2
· **Process:** [`exit-mechanism-construction-PROCESS.md`](../design/exit-mechanism-construction-PROCESS.md) § E3, § 3.1
· **Files:** `BL-20260820-E2-LABEL-BARRIER-DOES-NOT-MATCH-THE-LIVE-EXIT-POLICY`,
`BL-20260820-TP-LEVEL-IS-THE-ONE-EXIT-PARAMETER-NEVER-SWEPT`

The horizon arm licensed E3 on `label_hold` at a long horizon and attached a limit
to the licence:

> Part of this may be barrier geometry rather than edge. At a longer horizon more
> trades reach a barrier, and `dist_to_stop_atr` mechanically bears on *which* …
> Whether that is an exploitable lever or a restatement of the triple-barrier
> structure **is not a question E2 can answer.**

It is answered here, before any lever is built. **It is composition.** And chasing
that down surfaced two things about the substrate that condition every E2 number
published so far.

---

## 1. Population

`ict_scalp` 15m, Binance public archive 2021-08-16 → 2026-08-19, rebuilt locally and
reproducing the horizon arm's substrate **exactly**: XRPUSDT **9,761 rows / 503 trades**,
SOLUSDT **10,724 rows / 567 trades**, cross-asset block joined at `row_coverage` 1.0,
27 dense features. Panels differ across rungs **only** in the label
(`expected_hold_bars` pinned at 24). Every figure below is over the **full**
population of a panel, never a sample.

## 2. `label_hold` is mostly a statement about which barrier gets hit

`label_hold = 1[advantage_r > 0]`, and `advantage_r` is dominated by the barrier the
trade runs into. Measured as the share of `label_hold`'s entropy that the terminal
barrier alone accounts for:

| leg | h=12 | h=24 | h=48 |
|---|--:|--:|--:|
| XRPUSDT | **13.9%** | **27.0%** | **46.6%** |
| SOLUSDT | — | — | **47.4%** |

**Monotone in the horizon on a single leg, and the two legs agree to 0.8 pp at the
matched rung.** The conditional base rates show why: P(hold) is **0.017** when the
stop is touched, **0.995** when the target is, and **0.556** at the time stop. Two of
the three strata are nearly deterministic, so predicting `label_hold` is very largely
predicting the barrier.

**This is the mechanism behind the horizon arm's flip.** The rung at which E2 starts
finding hits is the rung at which the barrier's share of the label passes ~45%.

## 3. The pooled association REVERSES inside every stratum

The features E2 named are not merely diluted by composition — pooled and
within-stratum disagree on the **sign**. Spearman vs `label_hold`, with the
size-weighted mean of the per-stratum values beside the pooled value:

| leg · rung | feature | pooled | within | `time` | `sl` | `tp` |
|---|---|--:|--:|--:|--:|--:|
| XRP h48 | `dist_to_stop_atr` | **+0.117** | **−0.194** | −0.256 | −0.189 | −0.024 |
| XRP h48 | `upnl_r` | **+0.106** | **−0.219** | −0.275 | −0.189 | −0.113 |
| XRP h48 | `running_mae_r` | **−0.097** | **+0.168** | +0.210 | +0.188 | +0.004 |
| SOL h48 | `dist_to_stop_atr` | **+0.128** | **−0.185** | −0.194 | −0.223 | −0.088 |
| SOL h48 | `upnl_r` | **+0.144** | **−0.194** | −0.198 | −0.223 | −0.126 |
| SOL h48 | `running_mae_r` | **−0.130** | **+0.113** | +0.079 | +0.222 | −0.008 |

**5 of 27 features on each h48 panel flag a full sign reversal**, and they are exactly
the features the horizon arm reported as clearing FWER. This is Simpson's paradox: the
pooled sign is produced by *between-stratum composition*, and inside any barrier class
the relationship runs the other way.

Note what is stable and what is not. The **within**-stratum value for `upnl_r` is
−0.19 to −0.22 on **every leg at every rung measured** (XRP h12 −0.193, h24 −0.189,
h48 −0.219; SOL h48 −0.194). The **pooled** value swings from −0.022 to +0.144 across
the same cells. The pooled number — the one E2 scores, and the one the E3 licence
rests on — is the unstable one, and it moves with the barrier mix.

### 3.1 ⚠️ This does not license the within-stratum number either

`touch` is the barrier the trade **later** reached. Conditioning on it conditions on
the future, and no live lever can. A within-stratum coefficient is a **mechanism
diagnostic, not a tradeable effect size**, and this document does not propose acting
on one.

Both readings in fact carry large mechanical components, and the honest statement is
that neither decomposition can separate mechanism from edge:

- **Pooled** is composition — a statement about where price sits relative to two
  fixed levels.
- **Within the time stratum**, "already far up ⇒ holding does not improve on exiting
  now" is close to arithmetic: conditioning on `touch == time` conditions on *the
  target was never reached*, so a trade currently far up must by construction give
  some back.

**The consequence for E3 is therefore a refusal, not a redirection:** no lever may be
licensed from an E2 information score on this label. The only thing that can license
one is a **decision-time-only, net-of-cost** run — which is what the M20 gate is, and
where limit 8.2 said this routes.

## 4. Two substrate findings surfaced on the way

### 4.1 The label scores a counterfactual the fleet cannot execute

Every E2 artifact ran `--tp-r 2.0`. That sets the **label's** upper barrier. The
**trade** loads live YAML (`build_backtest_panel.py:163`), and all eight `ict_scalp`
legs declare **`tp_at_r: 1.5`**.

Measured over the full trade population, `trade_realized_r` caps at **exactly +1.500**
(XRP: 97/503 = 19.3% of trades), floors at −1.000 (105/503 = 20.9%), and **no trade
realises above +1.95R**. So `label_hold` asks *"would holding to a 2R barrier beat
exiting now"* about a strategy that would itself have exited at 1.5R — the hold branch
runs a different exit policy from the one under study.

And the 2R bound barely binds: only **4.2%** (XRP) and **4.8%** (SOL) of trades ever
*touch* 2.0R as maximum favourable excursion. Filed as
`BL-20260820-E2-LABEL-BARRIER-DOES-NOT-MATCH-THE-LIVE-EXIT-POLICY`. No published
verdict is known to be wrong — E2 faithfully labels the barrier it is handed — but no
write-up states the mismatch, and it conditions all three.

### 4.2 The exit-quality census § 1.5.1 specified, finally computed

| | XRPUSDT (503 trades) | SOLUSDT (567 trades) |
|---|--:|--:|
| MFE p50 / p75 / p90 / p95 | 0.72 / 1.27 / 1.75 / 1.95 R | 0.69 / 1.27 / 1.71 / 1.95 R |
| MAE p50 / p75 / p90 | 0.51 / 0.87 / 1.17 R | 0.49 / 1.00 / 1.19 R |
| reaches 0.50R MFE | 62.0% | 61.2% |
| reaches 1.00R MFE | 36.4% | 34.2% |
| **reaches the live 1.50R target** | **19.7%** | **19.2%** |
| reaches the label's 2.00R barrier | 4.2% | 4.8% |
| MAE never reaches 0.50R | 49.3% | 50.3% |

**MFE capture rate — state the population, the four denominators span 0.14 to 0.69:**

| population | XRP | SOL |
|---|--:|--:|
| aggregate (Σ realised R / Σ MFE R) | 0.159 | 0.140 |
| all trades, losses clipped to 0 (mean) | 0.318 | 0.330 |
| trades whose MFE reached 0.25R (mean) | 0.386 | 0.415 |
| **winners only, mean / median** | **0.603 / 0.683** | **0.627 / 0.693** |

The E-lit survey's tier-B healthy band is 0.65–0.80. **Winners land just under it;
the aggregate is nowhere near it.** Two independent legs agree to within 0.04R at
every MFE quantile.

## 5. What this says about where the leverage is

E0 measured that **78.5%** of exits are decided by a level fixed at entry. § 2 here
measures that **~47%** of the hold/exit label is decided by which of those levels gets
hit. § 4.2 measures the distribution those levels are set against: **MFE p50 is 0.70R
and the target sits at 1.5R, reached by one trade in five**, while **half of all trades
never use half the risk the 1.0R stop books for them.**

And the target level has **never been swept.** `exit-refinement-coverage.json`
declares eight lever columns — `trail_geometry`, `stale_stop`, `giveback_stop`,
`exit_ladder`, `exit_head_ml`, `trail_decay`, `regime_flip_exit`, `vol_trail` — every
one a post-entry override on a bracket fixed at entry. The matrix's `tp_geometry` field
is a *correctness qualifier* on the substrate, not a sweep of the level. The nearest
thing, `m27/ict_scalp_exit_sweep.py`, takes `tp_at_r` as a **fixed input** and sweeps
ladder rungs as fractions beneath it, explicitly skipping any rung `>= tp_at_r`
(":77, never emit a no-op cell").

So twenty lever cells searched for an override on a bracket nobody had evaluated.
Filed as `BL-20260820-TP-LEVEL-IS-THE-ONE-EXIT-PARAMETER-NEVER-SWEPT`.

**This is a hypothesis about where to look next, not a result.** That 1.5R is reached
by 19% of trades is not by itself evidence that a different level is better — a lower
target banks more often and gives up the tail, and which side wins is exactly what a
sweep is for. It must be graded **net of fees**, because a lower target raises turnover
and the existing sweeps' fee-free R basis would flatter it precisely where it is most
likely to pass.

## 6. Disposition

1. **E3 is NOT licensed by E2's `label_hold` hits.** They are composition; the
   licence has to come from a decision-time-only net-of-cost run.
2. The joint lever sweep the process specifies is still worth running as a bounded
   negative — cheap, and the decomposition above predicts what it will find.
3. The substantive next step is the **bracket-geometry sweep** (`tp_at_r`, stop
   distance, `timeout_bars`) through the existing M20 fold structure and Path A/Path B
   gate: a new *dimension* for the coverage matrix, not a new gate. Any resulting
   config change is **Tier-3**.

§ 3.1 of the process governs: this is a negative about *the constructs tried over the
substrate available*, with a date and a corpus attached. It records that the substrate
had two defects nobody had stated, and it does not close the thread.
