# How wrong is the backtest instrument about the live exit? — measured, 2026-09-08

> **Doc status:** `live` · category `research` · MI-193 (LANE 2) ·
> `session_012cVryifpv359yAWWVkrmnk`

Joins three filed rows —
`BL-20260906-NO-LEG-WENT-LIVE-ON-A-DISPOSITIONED-BACKTEST`,
`BL-20260906-BACKTEST-HARNESSES-RUN-NO-TAKE-PROFIT-BY-DEFAULT`,
`BL-20260906-FIDELITY-CALIBRATOR-CANNOT-GRADE-EXIT-LOCATION` — and asks the
question none of them answers: **how far apart are the backtested exit and the
live exit, and can that be quantified?** Cycle `CY-20260906-TRADING-TRUTH`:
repair the measurement before acting on what it says.

Research only. Nothing enacted, no config changed, no matrix cell re-graded.

## Populations

| id | definition | complete? |
|---|---|---|
| **P-LEG** | the 44 `config/strategies.yaml` entries with `enabled: true` and `execution: live`, permissive defaults | **complete** |
| **P-HARNESS** | the 15 files matching `scripts/backtest_*.py` + `src/backtest/run_backtest*.py` | **complete** |
| **P-TRADE** | newest **1000** rows of `/api/diag/journal?table=trades` — `limit=2000` requested, 1000 returned, so **the route capped**. Window `2026-08-10T18:36Z → 2026-09-08T06:01Z` | **a WINDOW, not the table** |

`is_backtest = 0` on all 1000; `entry_price` and `stop_loss` usable with
`risk > 0` on all 1000, so nothing was dropped for unreadable geometry.

---

## 1. Which harnesses model the venue cap — verified by reading, not from the row

| | count |
|---|---|
| reference `tp_cap_pct` at all | **4 / 15** |
| default it to a **non-zero** value | **0 / 15** |

`backtest_fade.py`, `backtest_pullback.py`, `backtest_squeeze.py`,
`backtest_trend.py` all carry `tp_cap_pct: float = 0.0` in the signature —
default off, which sets `tp_price = None`, i.e. **a book with no take-profit
exit path at all**. The other 11 never mention it.

**Independently reproduces `BL-20260906-BACKTEST-HARNESSES-RUN-NO-TAKE-PROFIT-BY-DEFAULT`.**
Read from the signatures, not the docstrings, as that row's own resolution
criteria require.

---

## 2. A CORRECTION that narrows where the divergence actually is

The row lists `backtest_ict_scalp.py` among the non-modelling harnesses and
notes it covers **8 of the 44** live legs. That is factually true and, read as
evidence of *backtest-vs-live divergence*, **it does not hold for those 8 legs**.

`src/units/strategies/ict_scalp.py` references the cap **zero times**
(`grep -c 'tp_venue_cap\|TP_VENUE_CAP\|take_profit'` → `0`). Its target is
`tp_at_r * risk` with `tp_at_r = 1.5`. And nothing downstream re-applies it: a
grep for the constant across `src/` outside the unit files returns only
`target_expectation.py`, `bracket_calibration.py`, `position_telemetry.py` and
`research/bracket_quantile.py` — **all measurement/telemetry, none on the order
path**.

So on the cap axis the ict_scalp harness and the ict_scalp live unit **agree**:
both are uncapped. The divergence this row is about lives on the **36 clamping
legs**, not on these 8.

The split is visible in the config schema itself: **36 legs declare `tp_r`**
(the clamping families) and **8 declare `tp_at_r`** (ict_scalp) — disjoint, no
leg carries both.

⚠️ This narrows the claim, which makes it **more** actionable, not less: porting
the cap to `backtest_ict_scalp.py` would move that harness *away* from its live
counterpart. The row's own partial-close clause proposes exactly that as
covering "8 of the 44 legs" — **on this evidence it should not be done.**

---

## 3. How much the cap actually moves the live target — and the headline flips on the split

`cap_r = 0.099 × entry / |entry − stop|`; effective live target
`min(cap_r, tp_r)`. Computed per trade over P-TRADE.

### A · 24 clamping legs carrying the `tp_r: 50` sentinel — 203 trades

```
cap_r          min 1.07 | median 10.13 | max 724.59
cap BINDS      198/203 = 97.5%      (the CAP sets the live target, not tp_r)
shortfall vs declared tp_r   median 39.87 R | max 48.93 R
```

### B · 12 clamping legs carrying a real finite `tp_r` — 60 trades

```
cap_r          min 0.69 | median 2.87 | max 172.03
cap BINDS      33/60 = 55.0%
shortfall vs declared tp_r   median 0.27 R | max 3.35 R
```

### C · 8 ict_scalp legs — 298 trades — NOT APPLICABLE

The unit applies no clamp, so a `cap_r` for these legs is a **hypothetical, not
a measurement**, and is not reported as one.

> ⚠️ **Pooled over A+B the figure reads 87.8%, and that number should not be
> quoted.** It is dominated by A, where the cap binding is the *design* — a 50R
> sentinel exists precisely so the clamp sets the target. The number that says
> something is **B's 55.0%**: on legs where somebody chose a real target, the
> venue clamp overrides that choice on **more than half** of live trades, by a
> median of 0.27R and up to 3.35R.

### A small live note, from the same computation

An uncapped `ict_scalp` target exceeds the 9.9% venue boundary on **1 of 298**
trades (`ict_scalp_avax_5m`). 255 of those 298 routed to `bybit_1`, 19 to
`bybit_2` (**real money**), 19 to `bybit_portfolio`, 5 to `ib_paper`. So the
clamp is very nearly non-binding for this family — which is why its absence
went unremarked — but *"very nearly"* is not *"never"*, and the one case is the
shape that returns ErrCode 10001. Recorded, not acted on.

---

## 4. Can the gap be quantified? NOT TODAY — and the blocker is RETENTION, not measurability

This is the part worth having.

### The live half is computable now

Over P-TRADE, closed trades on an enabled+live leg carrying an `exit_price`: **266**.
Graded through `src/runtime/provenance.py::classify_pnl` — `UNVERIFIED` is never
folded into `MEASURED`:

```
measured   89  (33.5%)   exchange_fill 61 · bybit_closed_pnl 25 · exchange 2 · ib_execution 1
estimated 173            candle_at_close 143 · recorded_exit_price 23 · verdict 7
unverified  4
```

Exit location in R, **MEASURED rows only**, n = 89:

```
min -1.17 | p25 -1.01 | median -0.95 | p75 0.94 | max 18.73
at or beyond +1R: 22.5%
```

### The backtest half is COMPUTED and DISCARDED

`backtest_trend.py` computes `exit_price, exit_idx` per trade internally
(lines ~496-631). Its `--emit-trades` JSONL writes:

```
strategy entry_time symbol exit_time entry sl exit_reason direction
gross_r net_r mfe_r net_r_fee_only cost_fee_r
```

**`exit_price` is not in it** — but `gross_r` **is exit location, in R**, which
is the same unit the live half above is expressed in. So the quantity is not
missing from the instrument; it is already emitted, under a different name.

What is missing is that it is **kept nowhere**. Per
`BL-20260906-FIDELITY-CALIBRATOR-CANNOT-GRADE-EXIT-LOCATION`, across the full
4277-commit history **no `backtest_fidelity_*` output artifact has ever been
committed**, and `comms/research/` has received one unrelated file in the repo's
life. `--emit-trades` writes to a path the caller supplies; nothing retains it.

### Therefore

**Both halves of the comparison are expressible in the same unit (R) with the
data that already exists.** The comparison is blocked on *no retained backtest
run*, not on a missing instrument and not on new data collection.

### Which makes the calibrator's remedy cheaper than its row assumes

That row's option (a) requires the calibrator to *"read an exit LOCATION
column"*. On the evidence here it needs neither a new column nor a new
instrument:

- **live side** — `entry_price`, `stop_loss`, `exit_price` are already on
  `trades` and already give exit-R (demonstrated above, n=89);
- **backtest side** — `gross_r` already *is* exit-R.

The calibrator's `SELECT pnl, notes, direction, timestamp FROM trades` simply
reads the wrong columns. **A different query, not a different instrument.**

⚠️ Two things this does NOT establish, stated rather than implied: it does not
show the two distributions differ (no backtest run exists to compare against),
and a 33.5% MEASURED provenance rate means **two thirds of the live window
cannot be used** for the comparison at all — that, not the query, is the binding
constraint on statistical power.

---

## Verdicts

| question asked | answer |
|---|---|
| which harnesses model the cap | **MEASURED**, 4/15 reference it, 0/15 default it on — row reproduced by reading |
| how far apart a backtested and a live exit are, for a leg where both exist | **NOT COMPUTABLE TODAY, and why: no backtest run has ever been retained.** The live half is computable (n=89 MEASURED); the backtest half is emitted as `gross_r` and kept nowhere |
| whether the fidelity calibrator can be made to grade exit location | **YES, with a different query.** Both sides already carry exit-R; a new instrument is not required |
