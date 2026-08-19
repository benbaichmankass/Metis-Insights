# The measured `observed_p` — and it reverses how XRP 4163's loss should be read

`src/runtime/hold_vs_cash.py` computes the hit rate a position **requires** and refuses to
grade without a **measured** rate to compare it against. This is that measurement for
`xrp_pullback_2h`, and the answer changes the conclusion I reached earlier the same day.

## P(the take-profit fills | the trade already reached X of its cap)

`scripts/research/peak_banking_basis.py::conditional_hit_rate`, n=284 harness trades
(2021-09-14 → 2026-08-19, `--tp-cap-pct 0.099`), hit basis **`exit_reason`**:

| X of cap | n | hits | p |
|---:|---:|---:|---:|
| 0.00 | 284 | 85 | 0.299 |
| 0.50 | 134 | 80 | 0.597 |
| 0.60 | 114 | 71 | 0.623 |
| 0.70 | 98 | 68 | 0.694 |
| 0.75 | 92 | 67 | 0.728 |
| 0.80 | 77 | 59 | 0.766 |
| **0.8712** | **58** | **46** | **0.793** |
| 0.90 | 46 | 38 | 0.826 |

**Monotonically increasing.** The closer a trade gets to its venue ceiling, the *more* likely
it reaches it. That is the **opposite** of the banking thesis, and it agrees independently
with `peak_banking_basis`'s pooled verdict (`refuted` at every threshold).

## Applied to XRP 4163

It peaked at `peak_pct_of_cap = 87.12`, where the measured rate is **0.793**:

| point in the trade's life | r_to_target | r_to_stop | required p* | observed p | edge | EV | verdict |
|---|--:|--:|--:|--:|--:|--:|---|
| as I quoted it (rr 0.71) | 0.7100 | 1.0000 | 0.585 | 0.793 | **+0.208** | +0.36R | **HOLD** |
| final telemetry 15:10Z | 2.3463 | 0.1590 | 0.063 | 0.793 | **+0.730** | +1.83R | **HOLD** |

**The framework says HOLD at every point, and holding lost 2R.** XRP landed in the 20.7% that
did not reach the cap.

## What this changes

Earlier today I wrote that the give-back vindicated the giveback lever and that
`gb1R_afterMFE1R` "would have exited near +2.42R". That counterfactual is arithmetically true
for this trade and **selects on the outcome**. Applied to the whole population it surrenders
the 79.3% majority that *do* reach the cap. On this evidence the 2R give-back is **variance,
not an exit-mechanism failure** — which is a different finding from either of the two I stated
during the day, and it is the one with a denominator behind it.

That does **not** restore the earlier "every lever books less than holding" claim either. That
one was a point estimate on an open position and was wrong to state as settled. The defensible
version is narrower: *at 87% of cap, this leg's measured base rate does not support banking.*

## Two caveats that bound it

1. **Population.** Measured on the **backtest harness** population, not live fills. The live
   leg has 3 closed trades; the harness has 284. They are not the same book.
2. **The lookup is approximate at the edges.** `reached` uses the harness's `mfe_r / cap_r`;
   XRP's 87.12% comes from `position_telemetry`'s close-basis `peak_r / cap_r`, where `peak_r`
   is a declared **lower bound**. Similar quantities, **not identical definitions** — so the
   row selected may be one step conservative.

## How the number nearly went the other way

The first version of `conditional_hit_rate` inferred the hit from `mfe_r >= cap_r`, on the
argument that a take-profit is a resting limit and fills on touch. **Measured, that is false
for this harness**: `mfe_r` excludes the fill bar, so on `take_profit` rows `net_r ≈ cap_r`
while `mfe_r` sits below it (entry 1.083 → cap_r 3.290, net_r 3.235, mfe_r 2.783). The
predicate was **unsatisfiable by construction** and returned `p = 0.0` at every level over a
corpus containing 85 take-profits — a confident number, indistinguishable from a real one,
which fed to `hold_vs_cash` would have said **LIQUIDATE on every position with maximum edge**.

The fix reads `exit_reason`, and adds **`proxy_agreement`** — the share of recorded
take-profits the old rule also finds — so two ways of computing the same thing cross-check
instead of one failing silently. It reports **0.0** here with a `proxy_note`, which is the
guard working. A regression control in the self-test reproduces the exact `p=0.0` shape.
