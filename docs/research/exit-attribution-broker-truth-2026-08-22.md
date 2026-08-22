# Item 1.1 re-measured on broker truth: the brackets fire ~3× more often than the labels say

**Session `s3-exitpath`, 2026-08-22.** Tier-1 measurement. No `execution:` change, no
Tier-3 gate, no order-path change. Everything below is a read.

## The question

Workplan item 1.1 says *"exits do not happen as declared"* — on the main order path
only **15.2%** of closes carry a decision-driven `exit_reason`, `tp_cross` is **3 rows
= 0.9%**, and the stop:target ratio is **10.3 : 1**. Item 1.5 established the field to
read (`trades.exit_reason`, never `order_packages.close_reason`).

Those are all statements about the **label**. They do not distinguish two very
different worlds:

| | what is broken | what the fix is |
|---|---|---|
| **mechanism** | the bracket never fires; the position is closed some other way | make brackets fire — an order-path change |
| **attribution** | the bracket fires and the row records a generic reason | fix the record — a bookkeeping change |

The distinction decides what item 1.1 *is*, so it is worth measuring rather than
assuming.

## Population — stated, because it is the load-bearing caveat

`/api/bot/db/table/trades`, `status='closed'`, newest 500 by id, **`filter_state`
asserted `applied`** before reading (an ignored filter silently returns the whole
table). Window **2026-07-15 → 2026-08-22**, of **1,293** closed rows. Backtest rows
excluded. Pairs excluded from the main-path figures throughout — it is an isolated
2-leg order path and it **flatters the aggregate**.

- main order path: **323**
- ...with a **broker-truth exit price** (`exit_price_source` ∈ `exchange_fill`,
  `bybit_closed_pnl`, `ib_execution`): **78 — 24.1%**

⚠️ **The other 245 rows carry an estimated or fabricated exit price and are UNKNOWN.**
Not favourable, not unfavourable. `candle_at_close` alone is 30.7% of main-path closes.
Geometry measured against a reconstructed price is not evidence, so those rows are
excluded rather than counted — this is the `pnlCoverage` problem showing up in a second
place, and it bounds everything below to a quarter of the path.

⚠️ **Selection, named.** Having a broker-truth price requires the fills store to hold a
fill, which over-selects exits that *were* real venue orders. What that conditioning
does **not** select is the **price** of the fill — a fill could have been at any level.
So the result below is conditional on a fill existing, and within that population the
fill price is unbiased evidence.

## Method — the production classifier's own inequality, not a picked tolerance

`order_monitor._classify_broker_exit` already exists and already does this
classification. It is **conservative**: it requires the fill to be *through* the level.

```
long : exit <= sl -> 'sl'      exit >= tp -> 'tp'
short: exit >= sl -> 'sl'      exit <= tp -> 'tp'
else -> None, and the row keeps 'reconciler_filled'
```

⚠️ **A first pass of this analysis used a symmetric tolerance (|exit − level| ≤ 0.10
risk-units) and reported 72.7% at a bracket. That number is wrong and is retracted
here.** A symmetric band counts a fill that stopped *short* of the stop as a stop hit,
which it is not. Every figure below uses the production inequality. The corrected
answer is materially smaller — and it is the one that matches what the system itself
would decide.

## Result

Of the **78** main-path exits with a broker-truth price:

| | n | share |
|---|--:|--:|
| fill **crossed the declared stop** | 40 | 51.3% |
| fill **crossed the declared target** | 6 | 7.7% |
| fill strictly **between** the two | 32 | 41.0% |
| **reached a declared level, either side** | **46** | **59.0%** |
| **labelled** with a decision-driven `exit_reason` | **15** | **19.2%** |

**The under-attribution gap is 31 rows — the brackets reached a declared level about
three times as often as the labels record it.**

**So item 1.1 is, on this population, substantially an ATTRIBUTION defect, not a
mechanism one.** The declared bracket is doing its job far more than the 15.2% headline
suggests; the journal is not saying so.

### What survives from the original framing

**The stop:target skew is real.** Among fills that reached a level it is **40 : 6 =
6.7 : 1**. Less extreme than the label-derived 10.3 : 1, and still heavily
stop-weighted. Targets are genuinely reached far less than stops — that half of item
1.1 is not an artifact and is unaffected by the correction above.

**And 41.0% of measurable exits landed between the brackets** — a real, large residue
of closes that were neither a stop nor a target.

## Where the mis-attribution comes from — partly diagnosed, honestly incomplete

Restricting to `reconciler_filled` rows with a broker-truth price (**n = 44**),
**21 crossed a declared level** and still carry the generic label. Two candidate
causes were tested:

**REFUTED — the classifier reads the wrong levels.** It reads `order_packages.sl/tp`,
not the `trades` row. Joined against the packages: **0 of 44** had a null package stop,
and the two sources give the **identical** verdict on **43 of 44** rows (21 hit / 23
none either way). The level source is not the cause.

**REFUTED — a different close path.** `closed_by` is `monitor_reconciler` on **all 44**,
and `closed_reason` is the identical string *"reconciler — Bybit reports order filled
and position flat"* on both the crossed and the non-crossed group. Same path. The
difference is the data, not the route.

**CANDIDATE, and ⚠️ UNDERPOWERED — a price backfill left the derived reason stale.**
18 of the 21 carry a `backfill` marker (`run_id: bf-20260808T151707Z`,
`prior_exit_price_source: local_markprice` → `new_exit_price_source: exchange_fill`).
The shape is coherent: the classifier ran at close time against a *mark price*,
correctly returned `None`, and the 2026-08-08 repair then corrected the **price**
without re-running the **classification that depends on it**.

**But the control does not carry it.** The 2×2:

| | backfilled | not backfilled |
|---|--:|--:|
| crossed a level | 18 | 3 |
| did not cross | 15 | 8 |

`P(crossed | backfilled)` = 0.545 vs `P(crossed | not backfilled)` = 0.273 — the right
direction, but **Fisher exact one-sided p = 0.111 at n = 44**. **That is not a
finding and is not quoted as one.** 15 backfilled rows did not cross, and 3
non-backfilled rows did.

### The 3-row residue, narrowed to one named candidate

Those 3 rows — crossed, no backfill marker — are the cleanest lead, and they share a
striking shape: **all three crossed the STOP by a hair** (0.0159 / 0.0074 / 0.0210
risk-units past it), all carry a tracked `sl_order_id`, all report
`close_exec_type: Trade`, and **two are real money** (`bybit_2`).

| trade | symbol | strategy | account | closed_at | overshoot |
|---|---|---|---|--:|--:|
| 4928 | AVAXUSDT long | `ict_scalp_avax_5m` | `bybit_1` (paper) | 2026-08-22T13:39Z | 0.0159 R |
| 4733 | BTCUSDT long | `ict_scalp_5m` | `bybit_2` (**real money**) | 2026-08-18T05:14Z | 0.0074 R |
| 4180 | BTCUSDT long | `ict_scalp_5m` | `bybit_2` (**real money**) | 2026-07-29T14:31Z | 0.0210 R |

Four candidates were checked and **three are eliminated**:

- **A later price refinement left the reason stale.** ❌ **REFUTED for these rows.** No
  call site writes `exit_price` without `exit_reason`, and at the site that closed them
  (`order_monitor.py:5913`) the classifier is handed `avg_exit_price` — the *same* value
  that is then stored. Classification and price are set together.
- **A reduce leg** (the classifier returns `None` by design). ❌ Neither
  `notes.intent_reduce` nor `setup_type == 'intent_reduce'` is set on any of the three.
- **The netted-sibling cascade**, which classifies separately. ❌ That path stamps
  `closed_by: monitor_reconciler_netted_cascade` and an `exit_reason_source` field; all
  three carry plain `closed_by: monitor_reconciler` and no such field.
- **The level lookup fell back.** ✅ **The surviving candidate.** `_classify_broker_exit`
  resolves levels via `_resolve_linked_package_id(db, row['id'])` and, on a miss, falls
  back to `_resolve_protective_levels(db, symbol, direction)` — which resolves from *the
  most recent matching order package for that symbol+direction*, **not this trade's**. On
  a netting account with concurrent same-symbol packages that can grade a trade against
  **another trade's bracket**, and a bracket the fill did not cross returns `None`. All
  three are `ict_scalp` legs on netting Bybit accounts, which is exactly the population
  where several journal rows share one symbol.

⚠️ **This is a candidate, not a finding.** All three rows carry a populated
`order_package_id` *today*; whether it was populated at close time is the open question,
and answering it needs the trader journal around those three timestamps — which this
session did not pull. **The next session should start there**: three ids, three
timestamps, one hypothesis to confirm or kill.

## Consequences for two other workplan entries

**G.2's premise needs re-examining, and possibly inverting.** It reads *"the grader
calls a reconciler close a take-profit … the grades currently flatter exactly the
defect 1.1 is about."* On this evidence a reconciler close **often genuinely was** a
bracket hit — so the grader may be closer to right than the `exit_reason` it is being
checked against. G.2 should not be actioned on the assumption the grader is wrong.

**G.1 is upstream of all of this.** Every number here is bounded by the 24.1% of the
main path that has a broker-truth exit price. Raising `pnlCoverage` widens this
measurement more than any change to the classifier would.

## What was NOT done

No change to `_classify_broker_exit`, no re-classification of historical rows, no
order-path change. A repair that rewrites `exit_reason` on 31 historical money-DB rows
is a **Tier-2/3 data mutation** and needs the residual diagnosed first — re-stamping
rows on a hypothesis at p = 0.111 would be exactly the fabrication class this repo
already pays for.

Row: `BL-20260822-EXIT-ATTRIBUTION-UNDER-REPORTS-BRACKET-HITS`.
