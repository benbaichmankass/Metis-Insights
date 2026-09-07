# MI-169 — the approved cancel names an order in the group I was forbidden to touch. HALTED.

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Unit: MI-169. Lane: `CY-20260906-TRADING-TRUTH`.
Dispatched by the manager (`session_01HrmZ1RRNM4UnEUaFdrPEjj`) 2026-09-07 to execute the
operator-approved Option 3 of [`MI-167`](MI-167-pipeline-integrity-pass-2.md).

---

## The one-line finding

**I cancelled nothing and repaired nothing.** The dispatch told me to cancel
`oca-protect-t5531`'s two legs and named them **548** and **551**. Order **548 is not in
that group** — it is the resting 11-lot stop of `oca-protect-t5353`, the group the same
dispatch explicitly forbade me to touch. The 43-lot legs of `oca-protect-t5531` are
**550** and **551**. Cancelling as instructed would have stripped trade 5353's real
protection off a real 11-lot position **and left the dangerous 43-lot stop resting** —
reproducing `BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG`
almost exactly.

---

## Population and provenance

All reads mine, live, through `scripts/ops/diag_fetch.sh` (direct HTTPS,
`served by https://ict-bot.duckdns.org`).

| read | endpoint | state | captured |
|---|---|---|---|
| IB resting orders | `ib_open_orders?account_id=ib_paper` | ⚠️ **`read_state: "could_not_look"`**, `orders: null` | 19:30:30Z |
| IB resting orders | same | **`read_state: "orders_read"`**, 8 orders | 19:30:54Z |
| IB resting orders | same | **`orders_read`**, 8 orders, byte-identical | 19:30:55Z |
| IB resting orders | same | **`orders_read`**, 8 orders, byte-identical | 19:30:56Z |
| IB positions | `exchange_positions?account_id=ib_paper` | 3 positions, **`error: null`** | 19:30:58Z |
| journal `trades` | `journal?table=trades&limit=1000` | rows 5531 and 5353 both present | 19:31Z |
| running SHA | `version` | `git_sha 40ebd40b`, on-disk `054650e3`, **`restart_pending: true`** | 19:30:58Z |

⚠️ **The first read returned `could_not_look`.** Had I treated that as an empty book I
would have concluded the legs were already gone. It is reported here as its own row, not
folded into the successful reads, because *we could not look* is not *nothing rests*.
I re-read until I had a real `orders_read` and then took it three times.

⚠️ **`restart_pending` is now `true`** (MI-167 had it `false` at `40ebd40b`/`40ebd40b`).
`054650e3` is on disk and not running. Stated as a state change since MI-167; it does not
affect any finding here, because every finding is a venue read, not a code read.

---

## Finding 1 — the order ids in the approved action are wrong (BLOCKING)

Live venue, MGC, `ib_paper`, 19:30:54–56Z, three identical `orders_read` reads:

| order | type | qty | price | **oca_group** | status |
|---|---|---|---|---|---|
| **548** | STP | **11** | 4210.7 | **`oca-protect-t5353`** | PreSubmitted |
| 549 | LMT | 11 | 4807.5 | `oca-protect-t5353` | Submitted |
| **550** | STP | **43** | **4394.5** | **`oca-protect-t5531`** | PreSubmitted |
| **551** | LMT | **43** | **4485.0** | **`oca-protect-t5531`** | Submitted |

The dispatch said: *"order ids **548** (STP 43 @ 4394.5) and **551** (LMT 43 @ 4485.0),
the 548→551 group."* **That description is self-inconsistent against the venue.** No order
548 with shape `STP 43 @ 4394.5` exists. The order carrying that exact shape is **550**.
Order 548 carries `STP 11 @ 4210.7` and sits in `oca-protect-t5353`.

The journal corroborates the split independently:

| journal row | status | size | `stop_loss` | matches resting stop |
|---|---|---|---|---|
| **5531** | `open` | 43 | **4394.47142857** | order **550** (4394.5) |
| **5353** | `open` | 11 | **4210.70714286** | order **548** (4210.7) |

**Order 548 is the leg that protects trade 5353** — the position the dispatch called
*"real, correct protection over a real 11-lot position"* and ordered me not to touch.

### Where the error comes from — MI-167 contradicts itself

This is not the manager mistyping. MI-167's own timeline is correct:

> `11:49:36.538` re-armed GTC OCA … on broker-naked trade_id=**5353** → orders **548/549**
> `11:55:20.017` RE-ASSERT both_legs_resting … trade=**5531** … → orders **550/551**, **43 lots**

…but MI-167's **operator-decision section** then writes the option as
*"Cancel `oca-protect-t5531`'s two legs (**548→551** group)"*. The dispatch relayed that
shorthand faithfully. **The defect is in MI-167's options block, and it propagated into an
operator approval.** The operator approved a correctly-named group with a wrongly-named
pair of ids.

### Why I stopped instead of substituting 550

The dispatch's own gate is explicit: *"Confirm the two ids still exist and still belong to
`oca-protect-t5531` before acting; if the venue state has moved … stop and report rather
than cancelling something you have not re-identified."* One of the two ids does not belong
to that group. I could have resolved the group name to 550/551 and acted on intent — the
group is unambiguous and the shapes match exactly — but:

1. This is **Tier-3 on the order path**, where my latitude to reinterpret an approved
   action is minimal, and the approval's text names a specific order.
2. The id that is wrong points **precisely at the forbidden group**. The one prior attempt
   at remediating this exact class cancelled the journal-matching leg
   (`BL-20260820`). A discrepancy pointing at the same trap is the moment to stop, not
   the moment to improvise.
3. **There is no clock.** `ib_paper` is paper money — no real capital is at risk — and
   MI-167 records the `sl_cross` verdict as `None (no action)` on every tick since 11:45Z
   because price recovered above 4394.47. The cost of stopping is one confirmation
   round-trip; the cost of guessing wrong is a real position left naked.
4. The operator's approval was given over a description that is factually wrong about
   what it would do. **Re-confirming against corrected ids is not re-litigating the
   decision** — it is the same decision, correctly addressed.

I also did **not** perform the journal repair alone: the approved order is cancel-then-
repair, and the dispatch states a repair alone leaves 43 resting sell lots. Doing the
second half of an ordered pair whose first half is blocked is not a partial success.

---

## Finding 2 — the PnL, re-derived from the venue rather than copied

Method, stated because the dispatch required it. MGC multiplier is **10** (Micro Gold,
10 troy oz per contract → $10 per 1.0 of price); MI-167 read `multiplier='10'` off the
contract, and the venue's own position read is consistent with it — `entry_price
4423.021925925` is `avgCost 44230.21925925 / 10`.

IB does not rewrite `avgCost` on a reduction, so the 43 lots' entry is the delta between
the book before and after the fill:

```
book before : 11 @ 43860.0509091 / 10 = 4386.00509091
book after  : 54 @ 44230.21925925 / 10 = 4423.021925925
entry(43)   = (54 × 4423.021925925 − 11 × 4386.00509091) / 43 = 4432.491348835…
exit        = 4392.9                     (orderStatus 547, avgFillPrice, 43/43 filled)
PnL         = 43 × (4392.9 − 4432.491349) × 10 = −17024.2799994  →  −$17,024.28
```

**I reproduce MI-167's −$17,024.28 exactly.** Independent corroboration that the 54 → 11
transition was a *reduction* and not a re-entry: the surviving 11 lots still carry the
blended 54-lot average, and the venue read at 19:30:58Z still shows MGC
`size 11.0, entry_price 4423.021925925` — unchanged.

**This number is verified and ready to write. It was not written.**

---

## What whoever proceeds must do

1. **Re-confirm the target with the operator as `oca-protect-t5531` = orders 550 and 551**
   (43 lots each). Do not carry the string "548" forward.
2. **Cancel 550 and 551** via the `cancel-ib-order` system-action. Re-read
   `ib_open_orders` and require `read_state: "orders_read"` **before and after** — the
   surface served `could_not_look` once inside this session's own working window.
3. **Leave 548 and 549 resting.** They are trade 5353's correct protection.
4. **Then** repair row 5531: `closed_at 2026-09-07T11:45:06Z`, `exit_price 4392.9`,
   `pnl −17024.28`, `status closed`.

### Provenance for the repaired row

Per `src/runtime/provenance.py`: the exit **price** is broker truth — a real
`avgFillPrice` off a real fill — so it belongs in the `MEASURED` bucket, and
**`exchange_fill`** is the existing `MEASURED_SOURCES` member that fits (*"exit price
built from ACTUAL exchange fills on a venue that serves no per-fill realised PnL"*;
`ib_execution` is for `CommissionReport.realizedPNL` read back from the fills store,
which is not what this is).

⚠️ **But `MEASURED` alone would make this row indistinguishable from one the pipeline
booked correctly, and it is not one.** The value is measured; the *row* is a
reconstruction written by a session hours after the fact, on a close the live path graded
a failure. `provenance.py` has no source expressing "session repair" —
`MEASURED_SOURCES` is broker-truth vocabulary and the file's own warning
(`BL-20260812-MEASURED-PROVENANCE-CANNOT-SEE-MISATTRIBUTION`) is that a `MEASURED` grade
speaks to a value's source and **not** to how the row came to exist. **Deciding how a
repaired row is marked as repaired is a real gap, not an implementation detail**, and it
should be settled before the write, so that a future audit can find this row. I did not
invent a source string to fill it.

---

## The root cause remains open — MI-168

**This remediation, even completed, fixes nothing causal.** The defect is that
`ib_client.py:2636`'s close confirmation is **symbol-scoped** while the close is
**trade-scoped**, so any account holding other lots of the same symbol can never confirm a
close. That re-scoping is **MI-168** (`session_01BpU7rfqWKir4rQrowT4ZQd`), a separately
dispatched Tier-3 proposal, and it is **still open**. A repaired row with the defect live
will simply recur on the next trade that closes while a sibling row is open — a
precondition MI-167 measured as **13 `(account, symbol)` pairs** in a 28-day window.

## `ict_scalp_mgc_15m` aggregates

MI-167 graded this strategy's expectancy / R / win-rate **FABRICATED** while row 5531
stands open. That is **still true** — the repair did not happen, so nothing is
un-fabricated yet. Once the row is repaired they become *computable*. **I have not
recomputed them and they should not be quoted until a review session does.**

## Scope discipline

I found **no second instance** and therefore filed none — and that is *"I did not sweep
for one"*, not *"I swept and there are none."* My population was this one OCA pair on
`ib_paper`/MGC. The approval covers this trade only; nothing else was touched, on any
account, on any symbol.
