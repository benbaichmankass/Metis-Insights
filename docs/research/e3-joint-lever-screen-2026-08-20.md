# E3 — the joint lever screen: one positive cell, and it dies on cost

**Date:** 2026-08-20 · **Step:** [`exit-mechanism-construction-PROCESS.md`](../design/exit-mechanism-construction-PROCESS.md) § E3
· **Precondition:** [`e3-barrier-geometry-2026-08-20.md`](./e3-barrier-geometry-2026-08-20.md)
· **Tool:** [`scripts/research/e3_joint_lever_sweep.py`](../../scripts/research/e3_joint_lever_sweep.py) (`--selftest` 8/8)
· **Files:** `BL-20260820-EXIT-LEVER-BREAKEVEN-IS-BELOW-THE-REPO-OWN-FEE-CONSTANT`

E3 specifies levers over the features E2 named — `dist_to_stop_atr`, `upnl_r`,
`running_mae_r` — swept **jointly**, with the falsifier that *a combined cell must
beat the best single cell by more than the added degrees of freedom buy.*

---

## 0. Two framing corrections, both structural

**The screen is not the M20 gate.** It replays levers over the existing trade set:
entries are fixed and only the exit moves, so both arms take exactly one exit per
trade and the per-exit fee **cancels**. That makes it cost-*neutral*, not
net-of-cost, and it cannot see the turnover an earlier exit creates. The asymmetry
is the basis for reading it: **a negative is strong** (it failed under assumptions
that flatter it); **a positive is provisional** and must go through
`m20_fleet_exit_sweep.py` before it is evidence.

**A decision-time lever screen is HORIZON-INVARIANT — verified, not argued.** The
horizon moves only the *label*; a lever acts on the *trade*. Running the identical
screen on the XRP h12 and h48 panels returns `5.467111` OOS delta on both, to full
float equality, with identical baselines. So "E3 at h=48", as the licence was
worded, is the same screen as E3 at any other rung. **The horizon lives entirely in
the label — which § 2 of the precondition measured to be barrier composition.** The
horizon dependence and the lever are in different places, and no lever inherits the
h=48 licence.

## 1. Method

`ict_scalp` 15m, Binance public archive 2021-08-16 → 2026-08-19. XRPUSDT **503
trades**, SOLUSDT **567 trades** (full populations). A cell exits at the **first**
row whose condition holds and realises that row's `feat_upnl_r` — the bar's own
mark, the same anchoring rule `src/runtime/exit_anchor.py` enforces live. Untriggered
trades keep `trade_realized_r`.

Grid: `bank ∈ {0.25, 0.50, 0.75, 1.00, 1.25}` (`upnl_r ≥ a`), `mae ∈ {0.30, 0.50,
0.70}` (`running_mae_r ≥ b`), `near ∈ {0.25, 0.50, 1.00}` (`dist_to_stop_atr ≤ c`) —
**11 singles**, plus every OR- and AND-composition of two and three families for
**179 total**. Selection is in-sample on 4 anchored, strictly-forward walk-forward
folds; every number reported is **out-of-sample**.

## 2. Result

| leg | arm | pool | OOS ΔR | folds + | selected |
|---|---|--:|--:|--:|---|
| XRPUSDT | singles | 11 | **+5.467** | **4/4** | `bank0.5`, then `bank0.75` ×3 |
| XRPUSDT | joint | 179 | **+5.467** | 4/4 | *the same single cells* |
| SOLUSDT | singles | 11 | **−9.912** | 2/4 | `near1.0` ×2, `mae0.3` ×2 |
| SOLUSDT | joint | 179 | **−3.121** | 3/4 | `OR(bank1.25+near1.0)` ×2, `OR(bank1.25+mae0.3)` ×2 |

Baselines: XRP **+69.20 R** (mean +0.1376 R/trade), SOL **+66.19 R** (+0.1167 R).

### 2.1 The falsifier: FAILED on both legs, for different reasons

- **XRP — the joint grid buys exactly nothing.** Given 16.3× the cells, it selected
  the *same single cell* in every fold: **+0.000 R**. There is no interaction to find.
- **SOL — the comparison does not apply.** The joint arm "beats" the single arm
  (−3.121 vs −9.912), but **both lose money**, and beating a worse negative is not
  evidence of an interaction. The tool reports this as
  `falsifier_applicable: false` rather than letting a `joint_beats_single: true`
  field be read as a pass.

SOL's fold 4 is the instructive one: `mae0.3` scored **+11.61 R in-sample** and
**−13.57 R out-of-sample**, firing on 66% of trades. That is the overfit the
walk-forward exists to catch, and it caught it.

## 3. The one positive cell dies on cost

`bank0.75` on XRP is the only cell that survives to here. A break-even probe —
charging a per-firing penalty to the **lever arm only**, which is a break-even
question, not a net-of-cost model:

| extra cost per early exit | OOS ΔR | folds + |
|---|--:|--:|
| 0.00 R | **+5.467** | 4/4 |
| 0.02 R | +2.207 | 3/4 |
| **0.05 R** | **−2.683** | 1/4 |
| 0.10 R | −5.574 | 0/4 |

**Break-even is between 0.02 R and 0.05 R.** Against the repo's *own* shared
constant — `src/runtime/execution_costs.py`, `DEFAULT_FEE_BPS_ROUNDTRIP = 7.5`,
`fee_r = (fee_bps/1e4)·avg_price/risk` — and a measured ATR(14)/close median of
**0.4592%** on this feed (p25 0.3528%, p75 0.7009%, n=351 samples over 175,200 bars):

| R assumption | fee_r |
|---|--:|
| 1.0 × ATR | 0.163 R |
| 1.3 × ATR | 0.126 R |
| 1.5 × ATR | 0.109 R |
| 2.0 × ATR | 0.082 R |

**Every value is above the break-even**, so the conclusion does not depend on pinning
R exactly — and this is **fee only**, before the slippage and perp-funding terms the
same module models and the harness defaults to zero.

⚠️ **R here is inferred from ATR, not read from the harness's per-trade
`risk = abs(entry − sl)`.** That direct measurement is the resolution criterion on
the filed row; the range above is what makes the reading robust to the gap, not a
substitute for closing it.

## 4. The second-order observation, which matters more than the lever

The same arithmetic applies to the **baseline**. The panel's `trade_realized_r` comes
from the in-process `run_backtest` path, which the harness's own comment says the cost
policy does not touch (*"run_backtest never touches these … the CLI `main()` applies
the mandatory venue-aware policy"*), so it is **gross**. The legs' fee-free means are
**+0.1376 R** (XRP) and **+0.1167 R** (SOL). A round trip of **0.082–0.163 R** is *of
the same order as that entire mean edge*.

If that holds, then a fee-free R comparison — the established M20 lever-gate basis —
is grading levers on a book whose own net profitability is unestablished. That would
explain a great deal about why lever deltas of a few R have been so hard to interpret.

**This is a flag with arithmetic behind it, not a finding.** R is inferred, the fill
side (maker vs taker) is not established, and the venue-aware resolver may not return
the 7.5 bps default for this symbol. It is filed as
`BL-20260820-EXIT-LEVER-BREAKEVEN-IS-BELOW-THE-REPO-OWN-FEE-CONSTANT` with a
resolution criterion that measures it directly, and it explicitly routes the
baseline half to `/performance-review` if it survives — it is a question about the
fleet, not about exits.

## 5. Disposition

1. **E3 returns an honest negative**, and § 3.1's conditions are recorded: the
   constructs are three decision-time levers over the features E2 named, singly and
   in 179 combinations, over 503 + 567 trades on a 5-year archive, 4 anchored
   walk-forward folds, screened cost-neutrally and then probed for break-even.
2. **The joint hypothesis is specifically refuted on XRP** — 16.3× the grid bought
   +0.000 R, which is a cleaner negative than a marginal one.
3. **Nothing here licenses a Tier-3 declare**, and no cell should enter the coverage
   matrix as a candidate: the one positive cell fails the cost check by 2–8×.
4. The substantive next step is unchanged from the precondition: the **bracket
   geometry** (`tp_at_r`, stop distance, `timeout_bars`), graded net of fees through
   the existing M20 fold structure — because the measurement in § 3–4 says cost is
   the binding term, and the bracket is the parameter that sets how often it is paid.
