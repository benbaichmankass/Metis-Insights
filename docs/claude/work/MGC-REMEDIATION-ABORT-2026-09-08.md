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

## Landing

Proposed as **PR #11328**, opened ready (not draft) per the 2026-09-03 operator
ruling — the hold is carried by
`.github/pr-landing/mgc-remediation-abort-attribution-contradiction.json`
(`tier: 3`, `landing: hold`, `hold_reason: tier_2_3_needs_approval`), and **no
`.github/pr-automerge-requests/` file is written**, so nothing self-lands.

The relay opened it as `github-actions[bot]`, so GitHub's recursion prevention
fired no workflows and `get_check_runs` read `total_count: 0` — **blocked, not
green**, exactly as `pr-opener.yml`'s header documents. This commit is the
ordinary push that arms CI.

## Addendum — the `ib_paper` venue read went unreadable at 05:05Z, and this is a worse signature than MI-177 measured

The final confirming read for this session **could not be taken**. Stated as
*we could not look*, never as agreement.

**What happened.** Reads at 04:47:38Z–04:51:00Z were healthy and consistent:
`ib_open_orders` `read_state: orders_read` ×3, `exchange_positions` populated
with `error: null` ×2. From **05:05:11Z** both IB endpoints degraded together
and stayed there:

| endpoint | attempts | result |
|---|---|---|
| `exchange_positions?account_id=ib_paper` | **6** | `positions: null`, `count: null`, `error: null` — all six |
| `ib_open_orders?account_id=ib_paper` | **2** | `read_state: could_not_look` — both |

**It is not a general outage, and that is the point.** Measured in the same
minutes, same session:

* `/api/diag/version` → **200**, serving.
* `exchange_positions?account_id=bybit_1` → **read fine**, 3 positions with live
  unrealised PnL. So the route works and the executor is not globally stuck.
* `/api/diag/ib_state` → clients **497** and **9779** `connected: true`,
  `account_data_ready: true`, `breaker_open: false`, `likely_wedged: false`,
  `consecutive_failures: 0`, last OK 1.1s and 11.1s prior. (Client 498 shows
  `disconnected` with `last_ok_age 46.6s`.)

**So the null is not explained by a disconnected, breakered or wedged IB
client** — the clients say they are healthy while the account read returns
nothing. `error: null` alongside `positions: null` means the failure carries no
reason at all: a collapsed state of exactly the kind this repo keeps filing
(`BL-20260826-OPEN-TRADES-COLLAPSES-A-READ-FAILURE-INTO-AN-EMPTY-BOOK`).

**Why it is worse than what MI-177 recorded.** MI-177 measured *1 failure in
~8* reads, on the **fleet-wide** call, and explicitly found the **per-account**
path succeeding three times in a row seconds later — which is why its
recommendation was to read per-account. Here the **per-account path itself
failed 6/6 and 2/2**, on both endpoints, while another account on the same
route succeeded. That is a different signature, and the per-account remedy
MI-177 proposes does not cover it.

`/api/diag/version` also reports `restart_pending: true` (`git_sha 17377a9d`
serving, `92d9f127` on disk). **Not offered as the cause** — the correlation was
not tested and no attempt was made to establish one.

**What this does and does not change.** It changes nothing about the abort: the
evidence above was gathered while reads were confirmed healthy, and **no action
was taken, so nothing depends on the current read**. What it does mean is that
the done-condition's *"fresh per-account read shows journal-open matching the
venue"* branch **cannot be exercised by anyone right now** — a further reason
this object completes on its ABORT branch rather than its resolution branch.

Worth its own object; not filed from here, because filing one is a write this
session did not establish it should make on a Tier-3 object it does not own.
