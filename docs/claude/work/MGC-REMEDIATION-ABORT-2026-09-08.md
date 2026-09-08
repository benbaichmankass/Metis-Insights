# MGC remediation — ABORTED, and the attribution the approval rests on is contradicted by the venue

> **Doc status:** `live` · category `evidence` · last verified `2026-09-08` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md)

Session object: [`WO-20260908-RE-DISPATCH-THE-MGC-REMEDIATION-AGAINST-THE`](objects/WO-20260908-RE-DISPATCH-THE-MGC-REMEDIATION-AGAINST-THE.yaml).
Under `IN-20260903-TRADING-SYSTEM-HEALTH`.

**MEASURE ONLY. Nothing was cancelled, modified, re-armed, closed or written to
the journal. No order-path code was touched. No order was sent to any venue.**

## Verdict

**ABORTED — and this is the object's own sanctioned completion**, not a failure
to finish. Three independent grounds, any one of which is sufficient:

1. **The approval's factual premise is false.** It says order `548` is "stale
   and is NOT what you are authorised against". `548` is **not** stale: it rests
   live at the venue right now, `PreSubmitted`, as the stop leg of a *second,
   disjoint* OCA group. The approval describes a world in which `550`/`551`
   replaced `548`. They did not — they were added beside it.
2. **The attribution the remediation depends on is contradicted by the venue's
   own average cost.** The direction of the fix inverts depending on which
   journal row owns the venue's 11 real lots, and the evidence points at the
   *opposite* row from the one MI-166 assumed. See § The contradiction.
3. **This class is deliberately detect-only.** `CLAUDE.md` on
   `IB_BROKER_NAKED_CHECK_SECONDS`: the over-cover side "pages and cancels
   nothing, because the one attempt to remediate this class automatically
   cancelled the leg that MATCHED the journal"
   (`BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG`).

## What actually rests at the venue

`/api/diag/ib_open_orders?account_id=ib_paper`, per-account `read_state`
**`orders_read`** on all three reads. Captured `04:47:38Z`, re-read `04:50:52Z`
— **identical**. Population: all 8 resting orders on the account; the 4 MGC ones
are shown.

| order | type | side | qty | stop | limit | OCA group | status |
|---|---|---|---|---|---|---|---|
| **548** | STP | SELL | 11 | 4210.70 | — | `oca-protect-t5353` | PreSubmitted |
| **549** | LMT | SELL | 11 | — | 4807.50 | `oca-protect-t5353` | Submitted |
| **550** | STP | SELL | 43 | 4394.50 | — | `oca-protect-t5531` | PreSubmitted |
| **551** | LMT | SELL | 43 | — | 4485.00 | `oca-protect-t5531` | Submitted |

`550` and `551` do rest, exactly as the approval describes them (STP 43 @
4394.50, LMT 43 @ 4485.00). **The abort is not because those ids moved again.**
It is because the two orders the approval does *not* mention are the ones that
change what cancelling them means.

**The OCA group names carry their owning journal trade id** (`oca-protect-t<id>`),
so attribution of *protection* is self-identifying and exact:

| journal row | qty | entry | declared stop | declared TP1 | its OCA group | its legs |
|---|---|---|---|---|---|---|
| **5353** `mgc_pullback_1d`, opened 09-02 | 11 | 4374.40 | 4210.707 | 4807.466 | `oca-protect-t5353` | 548 / 549 |
| **5531** `ict_scalp_mgc_15m`, opened 09-07 | 43 | 4430.70 | 4394.471 | 4485.043 | `oca-protect-t5531` | 550 / 551 |

Every resting leg matches its journal row to the tick on both sides. **MI-166's
"protection is FAITHFUL" holds and is re-confirmed here.** The defect is in the
journal's *quantities*, upstream — exactly where MI-166 put it.

## The live risk, stated plainly

Venue MGC position: **long 11** (`/api/diag/exchange_positions?account_id=ib_paper`,
two independent reads `04:47:52Z` and `04:48:02Z`, `positions` populated and
`error: null` on both — this endpoint emits no `read_state` key, its
fail-safe signal is `positions: null`, and it was not null).

Resting **stop** quantity: 11 + 43 = **54 against a position of 11 — 491%
stop over-cover across two disjoint OCA groups.**

OCA cancels only *within* a group. Whichever stop fires first flattens the 11
and the other group's legs are still resting to sell. This is the
`BL-20260816-IB-STOPS-OVER-COVER-IN-DISJOINT-OCA-GROUPS` shape, and it is real
and live. **The abort does not make this safe — it leaves it exactly as
found.** What it avoids is making it worse by cancelling the wrong group.

## The contradiction

[`WO-20260907-ROOT-CAUSE-THE-43-PHANTOM-MGC-LOTS`](objects/WO-20260907-ROOT-CAUSE-THE-43-PHANTOM-MGC-LOTS.yaml)
records MI-166's reading: *"Trade 5531 … claims 43 lots it does not have."* That
implies the venue's 11 real lots belong to trade **5353**, and so the
remediation is to retire the **5531** side — i.e. cancel `550`/`551`.

**The venue's average cost says the opposite.** Venue MGC reports `entry_price`
**4432.696** on 11 lots. Testing every possible split of those 11 lots between
the two journal entries:

| lots from 5353 (@4374.40) | lots from 5531 (@4430.70) | blended | vs venue 4432.696 |
|---|---|---|---|
| 0 | 11 | 4430.700 | **−1.996 (−0.045%)** |
| 5 | 6 | 4405.109 | −27.587 |
| 11 | 0 | 4374.400 | **−58.296 (−1.333%)** |

**Control.** Journal `entry_price` is a signal price and venue `entry_price` is
a realised average, so the comparison is only worth as much as its calibration.
`ib_paper`/MHG is an exactly-reconciling pair (journal 30, venue 30, single
journal row 5258) and gives the scale of the normal gap: **+0.199%**.

* MGC attributed wholly to **5531**: **+0.045%** — *inside* the control's gap.
* MGC attributed wholly to **5353**: **+1.333%** — **6.7× the control**, and it
  would require the journal's recorded entry to be wrong by 1.3%.

The MES control (row 4350) was **not available**: it sits below the
`journal?table=trades` id window (returned ids 5248–5547), the same truncation
MI-177 documents. So this calibration rests on **one** control pair, not two.
Say that, don't round it up.

**Read straight, the evidence says the venue's 11 lots came from trade 5531 —
whose protection is `550`/`551` — and that trade 5353 holds *nothing*.** Under
that reading the phantom decomposes as 11 (all of 5353) + 32 (of 5531's 43) =
**43**, which reconciles with the measured divergence just as exactly as
MI-166's reading does. Both readings produce 43. The sum cannot separate them.
**MI-177 says so itself**: per-trade attribution "is a different question this
instrument does not answer, and it is MI-173's."

**So cancelling `550`/`551` would, on this evidence, strip the protection off
the only real MGC position at the venue and leave `548`/`549` resting against a
position that does not exist.** That is
`BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG`
happening a second time, by the same mechanism, on the same account.

## What this does and does not establish

* It **does** establish that the two readings are not distinguishable by the
  quantity sum, and that the one price-based discriminator available points
  away from the assumed one.
* It **does not** establish that trade 5353 is the phantom. An average cost is
  suggestive, not a fill record. **We did not look at the execution records** —
  no fills/executions surface was consulted, and `broker_order_id` 530 (5353)
  and 538 (5531) were not traced to IBKR's actual answers. That trace is
  precisely `WO-20260907`'s `done_condition` and it remains unmet.
* Nobody has looked at whether this class touches `bybit_2` or `alpaca_live`
  (**both real money**) — also `WO-20260907`'s, also unmet.

## What should happen next, in order

1. **Do not cancel anything yet.** Neither pair, on current evidence.
2. **Settle attribution from execution records, not from sums** — trace
   `broker_order_id` 530 and 538 to IBKR's actual fills. That is
   `WO-20260907-ROOT-CAUSE-THE-43-PHANTOM-MGC-LOTS`, whose `done_condition`
   already says *"No leg cancelled and no journal row edited — Tier-3 proposals
   only."* **This object is blocked on that one**, and that is a true edge —
   the first one either object has.
3. **Only then** decide the end state. For the record, the two candidates are
   opposites, which is the whole point:
   * *If 5531 is the phantom* — retire `oca-protect-t5531` (550/551), keep
     548/549 at 11.
   * *If 5353 is the phantom (what the price evidence suggests)* — retire
     `oca-protect-t5353` (548/549), and **reduce** 550/551 from 43 to 11.
     Reduce, not cancel: the position they cover is the real one.
4. Either way the journal row for the phantom side needs correcting, and that
   is a separate Tier-3 write nobody has scoped.

## Governance note — this object was never actually armed

The dispatch says *"You own"* this object and calls it *"OPERATOR-APPROVED,
Tier-3"*. The object on `main` says otherwise, and per the **Code-First
Verification Rule** the file wins over a conversational summary:

```
lifecycle: dormant
owner: null
blocked_on_basis: >-
  NOT_ASSESSED. ⚠️ This empty list is NOT the claim that nothing blocks this
  object — nobody has looked.
review_trigger: >-
  ... A captured idea is PARKED, not queued — it becomes work only when it is
  given an owner and moved out of `dormant`.
```

An object that is `dormant` with `owner: null` is parked by its own terms.
Cancelling a live resting protective leg is the **live order path**, which
`docs/CLAUDE-RULES-CANONICAL.md` § Permission Tiers puts at **Tier-3** and
hard-blocks from self-merge: *analyze, test, prepare docs, and propose exact
code changes* — propose, not enact. This document is that proposal.

**Coordination board (#6927) is at GitHub's 2500-comment cap and writes 403, so
no START claim could be posted. Known and parked as MI-182; not retried.**
