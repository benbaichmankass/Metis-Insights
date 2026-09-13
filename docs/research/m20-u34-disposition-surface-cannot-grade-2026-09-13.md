# MI-278 U34 — the M7 disposition gate reads the offline edge record and still cannot act on it

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Subject: [`scripts/ml/strategy_review_packet.py`](../../scripts/ml/strategy_review_packet.py) `::decide()` (**Tier-1 path, read only, unmodified**) ·
producer run: [`scripts/ops/build_strategy_evidence.py`](../../scripts/ops/build_strategy_evidence.py) ·
records: [`comms/strategy_evidence/`](../../comms/strategy_evidence/) (52, committed by this unit)

## Why this unit exists

`PB-20260821-SLV-TREND-1H-ZERO-WINS-IN-13` has one `next_action`: *"Run the M7
strategy-review packet for slv_trend_1h, eth_pullback_2h, htf_pullback_trend_2h
and tlt_pullback_1h … and bring the resulting KILL/DEMOTE_SHADOW badges to the
operator."* **Three sessions have re-measured this row by hand and refused to
close it. None ran the packet.** Its headline has gone stale three times
(0/13 → 2/9 → 2/8) while its actual instruction sat undone.

The packets exist. A daily cron has committed one index per day since
2026-09-01, and they are in the repo.

## 1. Eleven consecutive committed indexes. `actionable: 0` on every one.

| date | graded | actionable | below_floor | `offline_evidence_states` | `by_action` |
|---|--:|--:|--:|---|---|
| 2026-09-01 | 52 | **0** | — | *(field absent)* | `{hold: 52}` |
| 2026-09-02 … 09-09 | 52 | **0** | 52 | *(field absent)* | `{hold: 52}` |
| 2026-09-10 | 52 | **0** | 52 | `{absent: 51, approximate: 1}` | `{hold: 1, no_offline_evidence: 51}` |
| 2026-09-11 | 52 | **0** | 52 | `{absent: 51, approximate: 1}` | `{hold: 1, no_offline_evidence: 51}` |

The MI-216/MI-217 wiring landed on 09-10 and **swapped one non-actionable label
for another**: `hold` → `no_offline_evidence`, with `actionable` unmoved at 0.

**The one leg that has ever carried offline evidence is `eth_pullback_2h`** — the
leg the builder's own docstring names as its end-to-end verification run. Two
days on, coverage was still **1 of 52**.

## 2. So this unit ran the producer for the fleet

`scripts/ops/build_strategy_evidence.py`, all 52 enabled legs. Records committed
to `comms/strategy_evidence/` (216 KB, Tier-1 surface).

**19 measured** (12 `faithful`, 7 `approximate`) · **10 `no_harness`** ·
**23 `harness_failed`**.

### ⚠️ WHAT `net_r_oos` IS, BEFORE ANY OF THESE NUMBERS ARE READ

**It is the POOLED total over the whole 365-day window, not a held-out result**,
and the number below is quoted throughout this memo on that understanding.
Verified against the records rather than assumed: on **all 19 measured records,
without exception**, `net_r_oos` equals the exact sum of every entry in
`fold_detail` and `n_trades_oos` equals the sum of every fold's `trades`. There
are 4 folds and **none is withheld**.

**The builder says so itself and is not at fault** — it discloses this in the
plainest terms and its field names are accurate:

- `basis` is **`harness_timefolds`, never `purged_walkforward`** — its own
  docstring refuses that term as *"exactly the unprovenanced-diagnostic failure
  this repo has a guard family for"*.
- *"The folds are out-of-sample with respect to EACH OTHER. If a leg's
  parameters were tuned on this same history, **the pooled number is
  optimistic** and no field here can detect that."*
- Every record carries `param_selection_provenance: not_established` —
  recording that it was not established rather than implying it was.

So a positive `net_r_oos` is **not** evidence a leg's edge survives out of
sample. It is a whole-window total on a harness whose fidelity is stated
per leg. Read it beside `fidelity`, `basis` and `param_selection_provenance`,
never alone.

### ⚠️ AND A `critical` OPEN ROW MEASURED SIX OF THESE LEGS AND GOT THE OPPOSITE SIGN

`BL-20260818-EVERY-CRYPTO-PULLBACK-LEG-IS-OOS-UNPROFITABLE` (severity
**critical**, open) reports **five of six crypto pullback legs OOS-negative**.
Every one of those six is positive here:

| leg | `BL-20260818` OOS net_R (n) | this unit's `net_r_oos` (n) |
|---|--:|--:|
| `htf_pullback_trend_2h` | −14.6089 (48) | **+2.7261** (79) |
| `eth_pullback_2h` | −6.8043 (49) | **+1.1402** (53) |
| `eth_pullback_prop_2h` | −11.7846 (49) | **+0.5087** (62) |
| `sol_pullback_2h` | −2.5783 (49) | **+8.7755** (16) |
| `xrp_pullback_2h` | −0.5915 (48) | **+21.3820** (45) |
| `ada_pullback_2h` | +3.4926 (49) | **+21.5841** (42) |

**NEITHER REFUTES THE OTHER AND THIS MEMO DOES NOT CLAIM IT DOES.** They are
different measurements of different things:

| | `BL-20260818` | this unit |
|---|---|---|
| window | 2023-08-14 → 2026-08-18, 13,206 bars (**~3 years**) | **365 days**, 2025-09-13 → 2026-09-13 |
| basis | per-leg derived IS/OOS split, a **held-out** arm | `harness_timefolds`, **4 folds pooled, none held out** |
| take-profit | `--tp-cap-pct 0.099` imposed | live declared params |
| corpus | trainer 2h corpora | `data.binance.vision` |

A three-year window with a genuine held-out arm and a one-year pooled total are
not the same quantity, and the shorter window is the more recent regime. **The
`critical` row's verdict stands; this unit's records do not overturn it.**

⚠️ **But the disagreement matters for the decision below.** If `decide()` is
re-pointed at these records — option 1 — it would grade six legs on a 365-day
pooled figure while a `critical` open row holds the opposite sign from a longer
window with a real held-out arm. **Whichever record the gate reads, it must
read the other one too.** Filed as
`BL-20260913-TWO-OFFLINE-EDGE-MEASUREMENTS-OF-THE-SAME-SIX-LEGS-DISAGREE-IN-SIGN-AND-THE-M7-GATE-WOULD-CONSUME-ONLY-ONE`.

| leg | fidelity | `net_r_oos` | n_oos | folds+ |
|---|---|--:|--:|--:|
| `trend_donchian` | approximate | -7.1079 | 75 | 1/4 |
| `trend_donchian_sol` | approximate | -1.0697 | 56 | 1/4 |
| `trend_donchian_sol_prop` | faithful | +0.5072 | 63 | 2/4 |
| `eth_pullback_prop_2h` | faithful | +0.5087 | 62 | 2/4 |
| `squeeze_breakout_4h` | faithful | +0.5994 | 9 | 2/4 |
| `eth_pullback_2h` | approximate | +1.1402 | 53 | 2/4 |
| `htf_pullback_trend_2h` | faithful | +2.7261 | 79 | 1/4 |
| `trend_donchian_avax_4h` | faithful | +4.0668 | 47 | 2/4 |
| `fade_breakout_4h` | approximate | +6.1101 | 25 | 3/4 |
| `sol_pullback_2h` | faithful | +8.7755 | 16 | 2/4 |
| `trend_donchian_sol_4h` | faithful | +8.9882 | 33 | 2/4 |
| `trend_donchian_eth_prop` | faithful | +9.1601 | 169 | 3/4 |
| `trend_donchian_ada_4h` | faithful | +11.0648 | 36 | 2/4 |
| `trend_donchian_eth_4h` | faithful | +15.2528 | 28 | 3/4 |
| `avax_pullback_2h` | approximate | +15.6777 | 46 | 3/4 |
| `trend_donchian_xrp_4h` | faithful | +19.8509 | 16 | 3/4 |
| `xrp_pullback_2h` | approximate | +21.3820 | 45 | 3/4 |
| `ada_pullback_2h` | faithful | +21.5841 | 42 | 3/4 |
| `trend_donchian_eth` | approximate | +23.4351 | 99 | 1/4 |

**The 23 failures split cleanly, and only one half is a defect:**

- **23 `harness_failed`, of which 13 are a FEED THIS ENVIRONMENT CANNOT REACH.**
  Positive control run in the same breath: `data.binance.vision` → **HTTP 200**;
  `query1.finance.yahoo.com` → **HTTP 429**, and `yfinance`'s own path returns
  `SSLError / curl (35) Recv failure: Connection reset by peer`. So every
  equity/ETF and futures leg (`SLV`, `TLT`, `GLD`, `IWM`, `IEF`, `IAUM`, `GDX`,
  `ES=F`, `GC=F` …) is unbuildable **from this sandbox**. That is an environment
  fact, not a verdict about the builder.
- **10 `no_harness`** — `regime_debt_matrix.classify()` routes nothing. Known
  and named in the builder's own docstring (42/52 route, 29/52 grade
  `faithful`).
- ⚠️ **`yfinance` is in `requirements-backtest.txt` and `requirements-test.txt`,
  NOT in `requirements.txt`** — so on a runtime install the equity feed is
  absent outright, with a bare `ModuleNotFoundError` and no hint that a second
  requirements file is needed. **19 of 52 enabled legs are equity/ETF**, and one
  of them, `tlt_pullback_1h`, is routed to **real-money `alpaca_live`**.

## 3. ⚠️ AND SUPPLYING THE EVIDENCE CHANGES NOTHING ACTIONABLE

This is the finding, and it is the reason eleven days read `actionable: 0`.

`decide()` reads the offline record **only to choose between two non-actionable
labels**:

```python
if n == 0:
    if offline is not None and not offline.has_record:
        decision.action = NO_OFFLINE_EVIDENCE
    else:
        decision.action = "hold"          # a record present -> "hold"
elif n < MIN_CLOSED_FOR_ACTION:            # 20
    if offline is not None and not offline.has_record:
        decision.action = NO_OFFLINE_EVIDENCE
    else:
        decision.action = "hold"          # a record present -> "hold"
elif n < 30:
    ...  the KILL / DEMOTE matrix, reachable only here  ...
```

**The KILL/DEMOTE matrix is gated entirely on `n_closed >= 20` LIVE closes.** The
offline edge number never enters it.

**PROVEN BY RUNNING IT, not by reading it.** With the new records in place:

| leg | offline fidelity | offline `net_r_oos` | n_oos | `decide()` |
|---|---|--:|--:|---|
| `htf_pullback_trend_2h` | **faithful** | **+2.7261** | 79 | **`hold`** |
| `trend_donchian` | approximate | **−7.1079** | 75 | **`hold`** |

**A leg with a negative offline edge of −7.11 over 75 out-of-sample trades grades
`hold`.** The record is read, its state is recorded in the packet, and the
verdict is unchanged.

⚠️ **This contradicts the doctrine the packet itself cites.**
`docs/CLAUDE-RULES-CANONICAL.md` § "Promotion evidence — offline edge, live
mechanics" has been binding since 2026-07-26: edge is proven OFFLINE, live data
proves MECHANICS, and **no gate may require calendar-time accrual to prove
edge**. `build_strategy_evidence.py`'s own docstring quotes that rule as its
reason for existing. `decide()` still requires 20 live closes.

⚠️ **MI-216 shipped the PRODUCER and said so.** Its docstring: *"Operator-approved
2026-09-09 (MI-216), with the sequencing explicitly approved too: **the producer
comes FIRST**, because re-pointing `decide()` beforehand yields 52/52 `null`."*
The producer landed. **The consumer was never re-pointed.**

⚠️ **So committing these 52 records makes the surface look HEALTHIER while
changing nothing**, and that is stated rather than hidden: 18 legs will move
`no_offline_evidence` → `hold` on the next cron run, and `actionable` will stay
**0**. `hold` reads as *graded, nothing to do* — which `decide()`'s own comment
calls *"wrong in the dangerous direction"*. The records are committed anyway,
because withholding real evidence to keep a label pessimistic is worse; but the
next reader of that index must know the `hold` is not a verdict.

## 4. What this means for `PB-20260821`'s four legs

| leg | `execution` | accounts | offline edge | packet verdict |
|---|---|---|---|---|
| `slv_trend_1h` | shadow | paper ×3 | **ungradeable** (equity feed) | `no_offline_evidence` |
| `htf_pullback_trend_2h` | shadow | `bybit_1` paper | **+2.7261**, n=79, faithful | `hold` (was `no_offline_evidence`) |
| `eth_pullback_2h` | **live** | `bybit_2` **REAL MONEY** | **+1.1402**, n=53, approximate | `hold` |
| `tlt_pullback_1h` | **live** | `alpaca_live` **REAL MONEY** | **ungradeable** (equity feed) | `no_offline_evidence` |

⚠️ **TWO OF THE FOUR ARE ON REAL MONEY AND THE ROW NEVER SAYS SO.** It was filed
2026-08-21 as a class of *paper* losers and singles out `slv_trend_1h` because it
was *"the only large loser whose loss is well MEASURED"*. `alpaca_live` began
routing real money on 2026-08-31. The row's own framing now points at the one
leg that is paper-only **and already in shadow**, while two live-money legs sit
unexamined.

⚠️ **AND THE TWO LEGS THAT CAN NOW BE GRADED BOTH SHOW A POSITIVE OFFLINE EDGE** —
`htf_pullback_trend_2h` at **+2.73 over n=79 at `faithful` fidelity**, the
highest evidence standard the builder emits. That leg was **demoted to shadow on
2026-08-23**, the disposition this row proposes. This is the first evidence the
mechanism has ever been able to offer about it, and it points the other way. It
is **not** a promotion proposal — that is Tier-3 and is not made here — but a
kill argued on this row's premise would now be arguing against the only edge
number that exists.

## 5. Shadow is a one-way door for this mechanism, and nothing says so

The packets grade both shadow legs `structurally_ungradeable`, in their own
words:

> *"execution=shadow with n_filled=0 over 7d — a shadow leg does not reach the
> order path by design, so it accumulates NO closed-trade evidence at any window.
> **Waiting cannot grade it; a different disposition mechanism is required.**"*

`days_to_floor_point`, `_optimistic` and `_conservative` are all **`null`**. So
demoting a leg to shadow — this row's own proposed remedy, applied to 2 of its 4
legs — removes it permanently from the only surface that could ratify or reverse
the decision. The 2026-09-02 drain note spotted the accrual-clock consequence for
two *other* rows and did not connect it to this one.

⚠️ And for the two **live** legs the floor is barely better: the packet's own
horizon says `n_closed=1` over 7d projects **140 days** to reach n=20, with a
95% interval of **29.5 to 2729.4 days**.

## What this does NOT do

- **No Tier-3 change is proposed and none may be.** No kill, no demote, no
  promote, no parameter.
- **`decide()` is not modified.** Re-pointing it is the consumer half of MI-216
  and is a decision, not a side effect of a research unit.
- **It does not establish that any leg is good or bad.** `net_r_oos` is a single
  time-fold split on a harness whose fidelity is stated per leg; the builder's
  own docstring warns it is *"NOT a promise that params were chosen out of
  sample"*.
- **The 13 feed-blocked legs are blocked HERE.** Whether they build from an
  environment that can reach Yahoo is untested and is not claimed either way.

## The decision this puts to the operator

The M7 gate cannot produce a disposition for any of the 52 legs, and running the
producer its own error message instructs cannot change that. Three options, and
**doing nothing keeps `actionable: 0` indefinitely**:

1. **Re-point `decide()`** so a `faithful` offline record with sufficient
   `n_trades_oos` can carry a KILL/DEMOTE without 20 live closes — the consumer
   half of MI-216, and what the promotion-evidence doctrine already says.
2. **Decide the legs on a different basis** and record it, accepting that the M7
   badge will never arrive.
3. **Record that the gate is live-close-gated by choice** — a legitimate answer —
   and stop emitting a `reasons` line that instructs sessions to run a producer
   which cannot move the verdict.
