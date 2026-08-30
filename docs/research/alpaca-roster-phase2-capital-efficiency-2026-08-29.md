# Phase 2: the roster graded on capital efficiency — measured, walk-forward

**2026-08-29 · Tier-1 research.** Answers the operator's requirement that a
"winner" include the exit work: *"not holding trades for ridiculously long
periods of times through churn, which is just tying up capital that can be used
for more effective trades."*

That property has exactly one definition in this repo —
`scripts/capital_efficiency.py::net_r_per_capital_day` — and its own docstring
quotes the same complaint. This grades against it rather than against a
re-derived metric.

## What was run

`m20-exit-lever-sweep` run
[33248507062](https://github.com/benbaichmankass/Metis-Insights/actions/runs/33248507062),
**all 19 `alpaca_paper` legs**, `tp_cap_pct=0.099` (LIVE PARITY), `split_mode=oos-trades`,
harness fee default 7.5 bps roundtrip. 21/21 jobs green; corpus merged **19 runs,
312 new rows, 21 superseded, 0 malformed** → 1670 rows on
`claude/m20-sweep-corpus`. Every leg returned a book (`base_book_present: true`).

## ⚠️ First, a methodology correction I made mid-analysis

My first pass picked each leg's **best cell by its OOS delta** and reported
18 of 19 legs "improving". That is selecting on the test set, and it inflates
the result. Redone as a walk-forward — **cell chosen on IS, its OOS reported,
selection never touching OOS** — the honest number is:

| | legs |
|---|---|
| IS-chosen lever still improved capital efficiency OOS | **12 of 19** |
| sign-flipped IS→OOS (the overfit signature) | **7 of 19** |

`gdx_pullback_1d` · `iaum_pullback_1d` · `qld_trend_long_1d` ·
`qqq_pullback_1h` · `scha_trend_long_1d` · `slv_pullback_1d` ·
`splg_trend_long_1d` all looked good in-sample and reversed out-of-sample.
**18/19 vs 12/19 is the size of the selection bias**, and it is the difference
between a roster built on a real edge and one built on the best of sixteen
coin flips. Every number below is the walk-forward one.

## The measured Path B distribution

`base_cap_day_OOS` — the leg's own baseline capital efficiency at live parity.
**Population: 19 of 19 legs returned a measured value.**
min **−0.2258** · median **0.0203** · max **0.4119** · positive **18 of 19**.

⚠️ **The spread is dominated by TIMEFRAME, and that is the metric working, not an
artifact — but it is not a quality ranking.** The five 1h legs sit at
0.15–0.41; the fourteen 1d legs at 0.004–0.047, roughly 10–20× lower. Their
`net_R` is comparable (1h 14.9–26.8, 1d 3.2–26.8). **Same R, far less capital-time**
— which is precisely the property the operator asked to optimise. It does *not*
mean 1h strategies are better strategies, and reading it that way would be the
same substitution error this repo's provenance rules exist to stop.

## The roster table

`capday` = `base_cap_day_OOS` · `dOOS` = the IS-chosen lever's OOS delta ·
`gate` = Path-A gate (net_R **and** maxDD both non-worse) on OOS.

| leg | sym | capday | netR | OOS | dOOS | gate | verdict |
|---|---|---|---|---|---|---|---|
| slv_trend_1h | SLV | **0.4119** | 17.09 | 48 | +0.26750 | False | **CANDIDATE** |
| uso_trend_1h | USO | 0.4103 | 14.87 | 49 | +0.11600 | True | EXCLUDED (operator) |
| gld_pullback_1h | GLD | 0.3773 | 19.48 | 48 | +0.25370 | True | UNAFFORDABLE |
| qqq_pullback_1h | QQQ | 0.2924 | 26.75 | 48 | −0.09740 | False | UNAFFORDABLE |
| spy_pullback_1h | SPY | 0.1549 | 15.15 | 54 | +0.03040 | True | UNAFFORDABLE |
| iaum_pullback_1d | IAUM | 0.0471 | 6.36 | **4** | −0.01020 | False | UNGRADEABLE |
| gdx_pullback_1d | GDX | 0.0264 | 12.13 | 42 | −0.01220 | False | CANDIDATE |
| qqq_trend_long_1d | QQQ | 0.0231 | 24.68 | 40 | +0.00120 | True | UNAFFORDABLE |
| qld_trend_long_1d | QLD | 0.0229 | 10.11 | 28 | −0.00870 | False | CANDIDATE |
| gld_pullback_1d | GLD | 0.0203 | 19.05 | 49 | +0.00860 | True | UNAFFORDABLE |
| slv_pullback_1d | SLV | 0.0201 | 11.63 | 48 | −0.00220 | False | CANDIDATE |
| **tqqq_trend_long_1d** | TQQQ | 0.0199 | 7.35 | 36 | **+0.01840** | **True** | **CANDIDATE** |
| splg_trend_long_1d | SPLG | 0.0177 | 17.30 | 37 | −0.00070 | False | BLOCKED (data) |
| ief_pullback_1d | IEF | 0.0142 | 26.84 | 39 | +0.00010 | False | CANDIDATE |
| iwm_trend_long_1d | IWM | 0.0111 | 7.61 | 35 | +0.00150 | True | UNAFFORDABLE |
| spy_trend_long_1d | SPY | 0.0098 | 9.86 | 29 | +0.00650 | True | UNAFFORDABLE |
| tlt_pullback_1d | TLT | 0.0043 | 7.75 | 48 | +0.00930 | False | CANDIDATE |
| scha_trend_long_1d | SCHA | 0.0042 | 3.25 | 33 | −0.00680 | False | CANDIDATE |
| tlt_pullback_1h | TLT | **−0.2258** | −4.83 | 46 | +0.19450 | False | CANDIDATE |

**Affordability is the REAL SIZER**, not arithmetic: `RiskManager.position_size`
at `equity 200.0`, `whole_units=True`, `available_usd=200.0`, stop = ATR14 as a
declared proxy. **9 of 13 symbols size**: SCHA · IAUM · SLV · TQQQ · TLT · QLD ·
IEF · GDX · USO. Refused: IWM $299.81 · GLD $422.60 · QQQ $721.11 · SPY $771.10.
**The tradeable SET is identical at `risk_pct` 0.02 and 0.05** — only TQQQ's qty
moves (1→2). Prices from run 33248814407, all 13 `state=sound`.

## The finding: the account size is the binding constraint, not strategy quality

**The four most capital-efficient legs are all unreachable** — three priced out
of a $200 cash account (GLD/QQQ/SPY at 1h), one excluded on the operator's
standing non-financial ground (USO). Nine legs survive every filter, and of
those exactly **one** clears every test at once:

> **`tqqq_trend_long_1d`** — affordable (TQQQ $73.30, sizes 1 share), OOS 36
> trades (clears `MIN_OOS_TRADES=25`), base 0.0199, and its IS-chosen lever
> `rrfloor1` **held out-of-sample at +0.01840 AND cleared the Path-A gate** —
> nearly doubling its capital efficiency without costing net_R or maxDD.

**`slv_trend_1h` is the other one worth the operator's attention**: at **0.4119**
it is the single most capital-efficient leg of all 19, affordable at $62.77, with
net_R **+17.09 over 48 OOS trades**. Its lever gains a large +0.2675 OOS but
**fails the Path-A gate**, so the reading is *ship the leg, not the lever*.

⚠️ **Two rows that must not be misread:**
- **`iaum_pullback_1d` has 4 OOS trades.** Its 0.0471 is the best of the 1d legs
  and it is **meaningless** — far below the `MIN_OOS_TRADES=25` floor.
- **`tlt_pullback_1h` is the one leg with NEGATIVE baseline capital efficiency**
  (−0.2258, net_R −4.83). Its lever's +0.1945 is a large improvement *to a
  losing leg*, and it does not clear the gate. It should not be wired.

## What this does NOT establish, and what is owed

- **Nothing here is wired.** `alpaca_live` still carries `strategies: []`.
  Wiring any leg is **Tier-3** and needs an explicit operator OK, one leg at a time.
- **The two Path B thresholds are still UNSET** and I have deliberately not
  invented them — the workflow's own contract is that they come from the operator
  against a measured distribution. That distribution now exists (above), so this
  is the point at which the decision is actually informed.
- `scripts/prop/account_compat_matrix.py` is owed for any leg before it is called
  gate-cleared.
- The 1h legs' data depth is unaffected by the daily-lane result in
  `alpaca-roster-data-sources-2026-08-29.md`; that finding is daily-only.
