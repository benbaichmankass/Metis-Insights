# Real-money close attribution — what actually closed the losing trades

> **Doc status:** `live` · category `research` · created `2026-09-08` · MI-200 ·
> object `WO-20260908-REAL-MONEY-IS-0-FOR-13-AND` · intent `IN-20260903-TRADING-SYSTEM-HEALTH`
> · cycle priority `CY-20260906-TRADING-TRUTH`

**Operator, 2026-09-08:** *"What are we doing about the losing streak? even on 16
trades, we can't just chalk this up to chance - something isn't working, and we
need to track it down"*

**Reproduce everything here:** `python3 scripts/research/real_money_close_attribution.py`
(reads the live API; `--self-test` runs 20 assertions offline). All figures below
were read **2026-09-08 between 12:16Z and 12:30Z**.

---

## The short answer

1. **The losing streak is real.** 14 consecutive non-wins on real money, the last
   win being trade `5170` at 2026-08-28T16:24Z. Not 13 — and the last win itself
   was booked by the *reconciler*, not by a strategy exit.
2. **"Zero wins" is an artifact of a truncated window, not a property of the
   book.** Over the full real-money population the account is **117 W / 258 L /
   49 flat**, not 0 W. The n=15 that framed this task is the real-money *subset of
   a paper-inclusive page* — a 5-day slice of a 447-row population.
3. **On the arithmetic alone, chance is NOT excluded.** At the lifetime non-win
   rate a run of 14 somewhere in 424 trades has probability **~71%** under an IID
   model. The operator's instinct that "something isn't working" is right, but the
   *streak length itself* is not the evidence for it.
4. **The evidence for "something isn't working" is the instrument, not the
   streak.** 8 of the 14 streak rows were not strategy exits at all, and two
   published API surfaces disagree about the exit mechanism on **174 of 447 rows
   (38.9%)**.
5. **Verdict on every leg currently routed to a real-money account:
   `insufficient_n`.** The largest convictable sample on a live-routed leg is
   **7**, against `MIN_CLOSED_FOR_ACTION = 20`. No promote, demote, kill or
   re-parameterisation is supportable from this population. **This is the finding,
   not an evasion of one.**

---

## 1. The population was wrong, and wrong in the direction that matters

`GET /api/bot/trades/closed` takes `include_paper`, **default `False`** — so the
default response is **already real-money-only** (`not_paper_predicate`,
`src/web/api/_clean_trades.py`).

| call | returns | real-money rows | window |
|---|---|--:|---|
| `?limit=200` | 200 real-money rows | **200** | 2026-05-19 → 09-04 |
| `?limit=200&include_paper=true` | the newest 200 rows of **all** classes | **15** | 2026-08-30 → 09-04 |
| `?limit=200&offset=…` paged to exhaustion | the whole real-money list | **447** | 2026-05-09 → 09-04 |

Passing `include_paper=true` and then filtering to `accountClass == 'real_money'`
does not widen the real-money population — **it narrows it to whatever fraction of
the most recent 200 mixed rows happens to be real money.** That is how n=15 was
produced. The 15 are a strict subset of the 447 (verified by id-set containment).

Two consequences that were reported as facts and are not:

* *"0 wins"* — true of the 5-day slice, false of the book.
* *"`alpaca_live` contributed zero closes"* — true of the slice; `alpaca_live`
  has a closed row in the real-money population.

**Three populations, never collapsed** (the number moves between them, so quoting
one without naming it is not a claim):

| # | population | n | what it is |
|---|---|--:|---|
| 1 | **LIST** — what the endpoint returns | 447 | `not_paper` + `exclude_superseded` only. A transparent list, **not** a KPI set. |
| 2 | **DECISION SET** — (1) minus the canonical KPI exclusions | 431 | drops 9 `intent_reduce` reduce legs + 7 `orphan_adopt` rows |
| 3 | **CONVICTABLE** — (2) ∩ strategy-exit ∩ `pnlProvenance == measured` | 222 | the only rows that can convict a leg |

Arithmetic cross-check: 431 + 16 = 447 ✓; 447 unique ids across 3 pages ✓;
1656 + 337 = 1993 raw journal rows ✓; `filter_state == "applied"` asserted on
every Data-Explorer page before `total` was trusted.

---

## 2. `closeReason` cannot answer the question that was asked of it

`trades_closed._normalise_close_reason` maps `tp`/`sl`/`manual` to themselves,
`reconciler*` → `reconciler`, and **everything else → `other`**. Over the 447 rows
that single `other` bucket holds **fifteen** distinct mechanisms:

| in `other`, and it **is** a strategy exit | n | in `other`, and it is **not** | n |
|---|--:|---|--:|
| `vwap_cross` | 102 | `backfill_closed_pnl_recovery` | 21 |
| `sl_cross` | 53 | `netting_attributed` | 7 |
| `tp_cross` | 15 | `operator_flatten_reconciled` | 3 |
| `exit_head` | 5 | `manual_closeall` | 3 |
| `time_decay` | 4 | `netted_misattributed` | 2 |
| `stale_stop` | 3 | `intent_reduce*` | 5 |
| | | `adopted_orphan_disappeared` | 7 |
| | | `stuck_strategy_watchdog` | 1 |

So **reading `other` as "not a strategy exit" is wrong in both directions.** This
is `UNPROVENANCED DIAGNOSTIC OUTPUT` sub-class A (semantic substitution): the
label names a quantity the code did not compute, and a reader who trusts the label
reaches a confident wrong conclusion. It is what produced the framing this task was
dispatched with.

### The two published surfaces disagree on 38.9% of rows

`src/web/api/routers/strategies.py::_normalise_exit_reason` and
`src/web/api/routers/trades_closed.py::_normalise_close_reason` are **different
taxonomies over the same column**, and the second is strictly cruder:

| raw `exit_reason` | `/api/bot/strategies` says | `/api/bot/trades/closed` says | n |
|---|---|---|--:|
| `vwap_cross` | `vwap_cross` | `other` | 102 |
| `sl_cross` | **`sl`** | `other` | 53 |
| `tp_cross` | **`tp`** | `other` | 15 |
| `time_decay` | `time_decay` | `other` | 4 |

**174 of 447 real-money closed rows (38.9%) carry a different published label
depending on which endpoint you ask.** 53 rows are a *stop-out* on one surface and
an unclassified `other` on the other. Filed as
`BL-20260908-TRADES-CLOSED-AND-STRATEGIES-PUBLISH-DIFFERENT-EXIT-TAXONOMIES` — a
**recurrence** of `BL-20260820-STRATEGIES-EXIT-REASON-ROLLUP-BUCKETS-LEVERS-AS-OTHER`,
which fixed the class in one module and left the sibling untouched.

---

## 3. What actually closed the trades

Over the **decision set (n=431)**, mechanism read from raw `trades.exit_reason`:

| bucket | n | share |
|---|--:|--:|
| **(a) the strategy's own exit fired** | 309 | 71.7% |
| **(b) something else booked the row** | 122 | 28.3% |
| **unknown mechanism** (*we did not look*) | 0 | 0.0% |

The `unknown_mechanism` bucket exists and is empty — every one of the 19 distinct
`exit_reason` values in this population is explicitly classified. An unrecognised
value would land there rather than being folded silently into either real bucket.

### The 14-row streak, row by row

| id | closed | leg | pnl | provenance | raw `exit_reason` | bucket |
|---|---|---|--:|---|---|---|
| 4922 | 08-28 16:25 | `eth_pullback_2h` | −0.2519 | estimated | `netting_attributed` | b |
| 4934 | 08-30 09:46 | `xrp_pullback_2h` | −2.4509 | measured | `operator_flatten_reconciled` | **b — operator** |
| 4904 | 08-30 09:48 | `trend_donchian_eth_4h` | −1.2872 | estimated | `operator_flatten_reconciled` | **b — operator** |
| 5250 | 08-31 18:18 | `trend_donchian_xrp_4h` | −4.8189 | measured | `sl` | a |
| 5312 | 09-01 23:36 | `ict_scalp_5m` | −5.5101 | measured | `reconciler_filled` | b |
| 5316 | 09-02 03:25 | `ict_scalp_5m` | −5.5013 | measured | `sl` | a |
| 5342 | 09-02 18:05 | `trend_donchian_eth_4h` | −1.0126 | measured | `reconciler_filled` | b |
| 5369 | 09-03 01:01 | `ict_scalp_5m` | −3.1858 | measured | `sl` | a |
| 5409 | 09-03 10:12 | `ict_scalp_5m` | −2.7469 | measured | `sl` | a |
| 5359 | 09-03 12:56 | `xrp_pullback_2h` | −4.4766 | measured | `reconciler_filled` | b |
| 5403 | 09-03 13:36 | `eth_pullback_2h` | −0.8391 | estimated | `reconciler_filled` | b |
| 5429 | 09-03 22:13 | `ict_scalp_5m` | −4.2576 | measured | `sl` | a |
| 5450 | 09-04 12:30 | `ict_scalp_5m` | −4.0509 | measured | `sl` | a |
| 5461 | 09-04 12:32 | `xrp_pullback_2h` | −2.9773 | estimated | `netting_attributed` | b |

**8 of 14 were not a strategy exit** — 2 operator flattens, 4 reconciler closes,
2 netting attributions. **6 of 14 were `ict_scalp_5m`**, which is no longer routed
to `bybit_2`. So of the streak that prompted this question, **6 rows cannot recur
on that book and 8 rows are not evidence about any strategy's exit quality.**

The two `operator_flatten_reconciled` rows are the 2026-08-30 hedge-mode flatten
recorded in `BL-20260830-OPERATOR-FLATTEN-LEAVES-NO-MARKER-ON-THE-JOURNAL-ROW`.
**That row's own text is now stale in the helpful direction** — it records both as
`exit_reason=reconciler_filled`; they now read `operator_flatten_reconciled`,
because the retroactive marker (`scripts/ops/mark_operator_flattened.py`) was
applied. ⚠️ Their `notes` JSON still carries `closed_by: monitor_reconciler` and
`closed_reason: "reconciler — Bybit reports order filled and position flat"`, so a
consumer reading `notes` rather than the column still sees the misleading value.
The structural half of that row — the flatten scripts still leave no marker —
is untouched.

---

## 4. Is a 14-run surprising? The arithmetic, stated honestly

Lifetime non-win rate over the graded decision set: **307/424 = 0.7241**.

| question | answer |
|---|--:|
| P(one *specific* block of 14 is all non-wins) | 0.7241¹⁴ = **1.09%** |
| E[# runs of ≥14 anywhere in 424 trades] | **1.24** |
| P(*some* run of ≥14 exists) | **~71%** |

**Under an IID model a run this long is entirely ordinary — it is expected roughly
once per 424 trades, and we have 424.** So the streak length alone does not
falsify "chance", and presenting it as proof of a broken system would be an
unsupported claim in the operator's favour rather than an honest one.

⚠️ **These trades are not IID** — same legs, same three symbols, overlapping
regime — so the real probability of a clustered run is *higher* than 71%, not
lower. The IID number is a **floor on how ordinary the run is**, not a p-value.

**What that does not mean.** It does not mean the book is fine. A 31.2% win rate
that loses money net, on an account with ~$202 of available margin, is a real
problem. It means the *streak* is the wrong instrument to detect it with, and the
question "which leg is broken" needs a population none of these legs has.

---

## 5. The verdict: `insufficient_n`, on every live-routed leg

Convictable rows only — bucket (a) **and** `pnlProvenance == measured`.
Live roster read from `/api/bot/config` (not the YAML) on 2026-09-08:

| leg | account | convictable n | W | sum | routed now? | verdict |
|---|---|--:|--:|--:|---|---|
| `trend_donchian` | bybit_2 | 7 | 2 | −4.29 | **YES** | `insufficient_n` |
| `eth_pullback_2h` | bybit_2 | 4 | 1 | −11.64 | **YES** | `insufficient_n` |
| `xrp_pullback_2h` | bybit_2 | 2 | 1 | −2.28 | **YES** | `insufficient_n` |
| `trend_donchian_xrp_4h` | bybit_2 | 2 | 0 | −6.31 | **YES** | `insufficient_n` |
| `fvg_range_15m` | bybit_2 | **0** | 0 | 0.00 | **YES** | `insufficient_n` — never closed a convictable trade |
| `trend_donchian_eth_4h` | bybit_2 | **0** | 0 | 0.00 | **YES** | `insufficient_n` — never closed a convictable trade |
| `tlt_pullback_1h` | alpaca_live | **0** | 0 | 0.00 | **YES** | `insufficient_n` — never closed a convictable trade |
| `ict_scalp_5m` | bybit_2 | 22 | 10 | −18.26 | no (demoted 09-06) | historical |
| `vwap` | bybit_2 | 180 | 46 | −49.38 | no (off since 06-07) | historical |

**`MIN_CLOSED_FOR_ACTION = 20`** (read from source). **No leg on the current
real-money roster reaches it. The largest is 7.** Three of the seven have *zero*
convictable closes.

Note where the lifetime loss actually sits: **`vwap` carries 180 of the 222
convictable rows and −49.38 of the −69.53** — a leg that has been off since
2026-06-07 and is routed to no account. The lifetime real-money PnL is dominated
by a strategy that cannot trade again.

⚠️ **This is a real answer, not a deferral.** Widening the window until something
becomes gradeable is precisely the low-n hazard the floor exists to prevent
(`OI-20260901-REVIEW-PACKET-CANNOT-PROPOSE-AN-ACTION-…`: 52/52 legs ungradeable at
a 7-day window). Nine clean closes is not enough to convict a leg, and saying so is
the finding.

---

## 6. The instrument problem underneath all of it

Even the 222 "convictable" rows rest on an account the repo has **already
recorded as untrustworthy at the row level**.

* **446 of 447 rows carry `journalTrust: "known_divergent"`.**
* `comms/broker_truth_ledger.json` records `bybit_2` wallet truth as
  **−262.52** over 2026-04-15 → 2026-07-13, against a journal sum of ~−33 for the
  same window — an under-record of roughly **8×** (measured at **11.0×** by
  `BL-20260830-BROKER-TRUTH-LEDGER-STALE-…` on its own population).
* **The ledger is `as_of: 2026-07-13` — 57 days stale.** Every close in the streak,
  and every close since 2026-07-13, has **no wallet-truth counterpart at all.**

So there is **no authoritative real-money PnL for the window containing the
losing streak.** The −69.53 in this document is a journal figure on an account
whose journal figures are known to under-record. It is the best number available
and it is not broker truth. `BL-20260830-BROKER-TRUTH-LEDGER-STALE-…` is updated
with the current split rather than duplicated.

---

## 7. Two live observations made while measuring

**`bybit_2` has written no journal row of any status for 94 hours** (newest:
`5474`, 2026-09-04T14:04Z) while `bybit_1` produced 38 of the newest 40 rows
fleet-wide. **This is explained, not an outage:** the trader is alive (heartbeat
1.3s, `bybit_2` live), two of the six roster legs hold open positions, and the
no-pyramiding guard suppresses re-entry while a strategy holds one. Both positions
are real and **correctly protected** — verified on the venue: `ETHUSDT` 0.04 long
with a resting reduce-only SL at 2451.59 (trailed up; `updated_time` ≠
`created_time`) and a TP; `XRPUSDT` 58.5 long with SL 1.3463 / TP 1.5535, both
`Untriggered`, qty matching position exactly. Recorded because the silence *looks*
like an outage and the next session should not re-derive it.

**`OI-20260906-ICT-SCALP-5M-DEMOTED-OFF-BYBIT2-…` clause (3) — CORROBORATED, not
newly found.** MI-192b (`/system-review`, 2026-09-08 10:29Z) already established
this and went further than I did, so this is a second independent read and **not a
new observation**; that row needs no further re-affirmation from me. My pull agrees
on the mechanism: no new `bybit_2` `ict_scalp_5m` row exists after the demote, but
the required denominator is absent — **`bybit_1` also produced zero `ict_scalp_5m`
rows on or after 2026-09-06**, its newest being 2026-09-04T08:37:48Z against
`bybit_2`'s 08:37:49Z, the *same tick*. The leg went quiet on every account two days
*before* the demote, so the `bybit_2` silence cannot distinguish "the demote worked"
from "the strategy was quiet". MI-192b additionally confirmed via `/api/diag/audit`
that the leg is alive and evaluating on cadence (7 `ict_scalp_eval` rows in 13
minutes, all "no liquidity sweep in last 12 bars") — i.e. nothing is stuck and the
wait is on market conditions. I did not re-derive that and am not restating it as
mine.

---

## What I did NOT do

No Tier-3 action was enacted or proposed for enactment. No `config/` change. No
exit-matrix cell re-graded. `ICT_SCALP_EXIT_HEAD_MODE` and `TP_VENUE_CAP_PCT`
untouched. No promote/demote/retire — the population does not support one, which
is the point.
