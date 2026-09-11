# The three accounts MI-177 could not read — 2026-09-08

> **Doc status:** `live` · category `evidence` · last verified `2026-09-08` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md)

**MI-189b**, standing pipeline-review lane pass 5, under
`IN-20260903-TRADING-SYSTEM-HEALTH` / `CY-20260906-TRADING-TRUTH`. Object
`WO-20260908-PIPELINE-REVIEW-LANE-PASS-5-JOURNAL-VS`.

**MEASURE ONLY.** Nothing was closed, cancelled, repaired, re-armed or
reconciled. No order-path code was touched. No live position was touched.

## What this pass was for

[`JOURNAL-VENUE-FLEET-AUDIT-2026-09-08.md`](JOURNAL-VENUE-FLEET-AUDIT-2026-09-08.md)
(MI-177, pass 4) compared journal-open rows against venue truth and read
**8 of 11** declared accounts. It reported the other three as *not looked at* —
correctly, and that discrimination is the reason that audit is trustworthy. But
it derived each account's REASON by **reading `clients.py`**, not by attempting
the read. *An account nobody has looked at is "we did not look", never "clean"* —
and a reason nobody tested is a claim, not an observation.

This pass attempted each read by every path that could plausibly serve it.

## Verdict, with the denominator

**3 accounts attempted · 3 reached a determination · 0 left unknown.**

| account | money | venue side | journal side | **verdict** |
|---|---|---|---|---|
| `ib_live` | **REAL MONEY** | **could_not_read** — dry-mode gate; **and the endpoint it would dial does not exist** | flat — 0 `trades` rows, ever | **could_not_read** |
| `oanda_practice` | paper | **could_not_read** — dry-mode gate | flat — 8 rows, all refusals, 0 open | **could_not_read** |
| `breakout_1` | **PROP, live** | **READ** — operator-reported `prop_account_status` | flat — **0 open prop positions** | **reconciled**, as of a **stale** snapshot |

**The headline correction: `breakout_1` is not unreadable, and it reconciles.**
MI-177's row title says it *"can never have"* a venue read. That is true of
`/api/diag/exchange_positions` and false of the account: a prop account's venue
analogue is the operator's own reported balance, and read that way its journal
and its venue **agree — both flat**.

Every number below carries the population it was measured over.

## Positive control, first

A clean verdict is worth nothing unless the probe is shown finding a positive.
On the same route, same session, seconds apart:

* `bybit_2` (REAL MONEY) → `positions: [ETHUSDT 0.04, XRPUSDT 58.5]`, `count: 2`
* `ib_paper` (**same exchange and same gateway as `ib_live`**) → `[MHG 30, MGC 11, MES 15]`, `count: 3`

So the nulls below are **account-specific**, not an IB-wide or fleet-wide
outage. On the journal side the control is `bybit_2` = **1656** `trades` rows
against `ib_live`'s 0, with `filter_state: applied` asserted on every count.

## `ib_live` — could_not_read, and a latent defect MI-177's note would have hidden

Venue read reproduces MI-177 exactly: `positions: null`, **`error: null`**.
The gate is `clients.py:1278-1280` — `mode != "live"` returns `None` before any
dial. Deliberate and documented.

**The new finding is what MI-177's table says next.** It records `ib_live` as
*"resolves on its own? **yes, on promotion to live**"*. **That is false**, and
false in the dangerous direction — it describes a real-money account as one
health-check away from readable when its configured endpoint is dead:

| | `ib_paper` | `ib_live` |
|---|---|---|
| `ib_host` | `10.0.0.251` (gateway VM) | **`127.0.0.1`** |
| `ib_port` | `4002` | **`7496`** |

Three independent measurements, this session:

1. **Every live IB client is on `10.0.0.251:4002`.** `/api/diag/ib_state` shows
   3 connected clients (ids 498, 9762, 497), all `10.0.0.251:4002`.
2. **The trader VM runs no gateway.** `/api/diag/services` lists 40 units and
   none is an IB gateway; `ict-ib-gateway-watchdog` is `inactive` there (it is
   role-gated to the gateway VM). `127.0.0.1:7496` on the trader VM has nothing
   behind it — the address predates the 2026-06-10 gateway isolation, which
   moved the gateway off the trader box and updated `ib_paper` but not this.
3. **`7496` is not this deployment's live port anyway.** `deploy/ib-gateway.compose.yml`
   maps **only** `127.0.0.1:4002:4004` and its own comment states *"(Live would
   be host 4001 → container 4003.)"* `7496` is TWS's live port, not IB Gateway's,
   and no `7496` or `4001` mapping exists anywhere in `deploy/`.

So promoting `ib_live` would dial a loopback port on the wrong host for the
wrong client. It fails **closed** — a connect failure, not a bad order — and it
is Tier-3 to change, so it is **filed, not fixed**. Recorded as
`BL-20260908-IB-LIVE-ENDPOINT-IS-STALE-AND-PROMOTION-WOULD-NOT-CONNECT`.

The journal side is unambiguous: **`ib_live` has 0 rows in `trades`, ever**
(`filter_state: applied`, `total: 0`). It has never traded, so "assumed flat"
and "measured flat on the journal" coincide here.

## `oanda_practice` — could_not_read, and genuinely dormant

Venue: `positions: null`, `error: null`; gate at `clients.py:1536-1538`, the
same dry-mode rule. Deliberate.

Journal, measured three independent ways:

* `trades` filtered on `account_id`: **8 rows**, `filter_state: applied` —
  6 `rejected`, 2 `exchange_rejected`, **0 open, 0 filled**, all between
  2026-06-11 and 2026-06-15, all `is_backtest: 0`.
* `/api/diag/exposure`: `measured: true`, `open_gross_notional: 0.0` — an
  **uncapped** `SUM(ABS(position_size*entry_price))` over open non-backtest rows.
* config: `strategies: []`, so it is out of the dispatch loop.

**Nothing is at stake on this account** and nothing needs doing. Stated
explicitly so a later pass does not spend the effort again.

## `breakout_1` — READ, and it RECONCILES

This is the account MI-177 said could never be read. The reason it looked
unreadable is that the audit asked the wrong surface: `account_open_positions`
supports four exchanges and `breakout` is not one of them, so
`/api/diag/exchange_positions` returns `null` **by construction and always**.

But `breakout_1` is a *manual bridge*. It has no broker feed by design — its
venue truth is the operator's reported terminal state, journalled in
`prop_account_status`. Read there:

**Venue side — `/api/bot/prop/status`, latest snapshot `2026-08-30T19:33:29Z`:**

```
balance 4787.34 · equity 4787.34 · unrealized 0.00   →  FLAT
```

`equity == balance` and `unrealized == 0.00` is the operator's own terminal
saying the account holds nothing.

**Journal side — 0 open positions**, over the complete `prop_fills` table
(**41 rows returned = 41 total**, so not truncated), grouped by
`prop_position_identity.position_key` exactly as `find_open_prop_positions`
does:

| measurement | value |
|---|---|
| fill ROWS with `status ∈ {open, filled}` | **18** |
| distinct position keys | **3** |
| **open positions** (newest row per key) | **0** |

Both sides say flat. **`breakout_1` reconciles.**

### The caveat that is the whole point

It reconciles **as of 2026-08-30T19:33Z**, a snapshot the system itself grades
`status_freshness: "stale"` at **203.9 hours — 8.5 days** — against its own
24-hour threshold. This is `reconciled_as_of_2026-08-30`, **never**
`reconciled_now`. The account is `mode: live` with 3 strategies routed, and on
that last snapshot sat **$87.34 above its $4,700 static drawdown floor** — 1.7%
of a $5,000 evaluation account.

**And the machinery is not broken — I checked, because it looks broken.**
`BL-20260814-PROP-BALANCE-REQUEST-ONLY-ASKS-WHILE-A-POSITION-IS-OPEN` is marked
`resolved` and describes this exact symptom, so a 8.5-day-stale balance reads as
that fix having failed. **It has not.** The cadence file
`runtime_logs/prop_status_request.json` reads
`{"breakout_1": "2026-09-07T19:46:07Z"}` — **~11.6 h before this audit, against
a 12 h cooldown.** The ask is firing on schedule and has been for days.

So this is **not a recurrence**: the code asks correctly, roughly 17 times over
these 8.5 days, and the human half of the bridge has not answered. The gap is
that an unanswered ask never escalates — it re-asks at identical volume forever,
which is how an alarm gets walked past. Filed as
`BL-20260908-PROP-BALANCE-ASK-NEVER-ESCALATES-WHEN-UNANSWERED`.

A session that had filed "recurrence of BL-20260814" would have sent someone to
fix working code.

## What I could not establish

**Whether `breakout_1` is flat RIGHT NOW.** Nothing in the repo can answer
this. Both sides of the reconciliation above descend from operator reports; the
newest is 8.5 days old, and no independent instrument exists. If the operator
has traded since 2026-08-30, the journal cannot know and neither can this audit.
That is a property of a manual bridge, not a defect to fix — but it means
`breakout_1` cannot ever be graded `reconciled` on a live basis, only as-of.

**Whether the SL was crossed on 2026-08-26.** See below; the daily candle's low
straddles the entry time and the intraday ordering is unknown. The TP crossings
on 08-27 and 08-28 are unambiguous. Neither changes a verdict.

## Two false findings this pass nearly filed — and what caught them

Recorded because both traps are still armed for the next session.

**1. "9 open prop fills, one open 13 days."** Filtering `prop_fills` on
`status='open'` returns 9 rows; adding `filled` gives 18. It is a natural read
and it is **wrong by 18 to 0**. `prop_fills` rows are *report records*, not
position state: `find_open_prop_positions` groups by
`(account_id, symbol, canonical_direction)` and **only the newest row per key
decides**. The SOLUSDT 25 @ 99.94 row (id 36) still reads `status: open`
forever; id 41 (`SOLUSDT`, `closed`, `2026-08-30T19:33:17Z`) is the newest row
for that key and closes the position. Caught by replicating the code's own
grouping instead of trusting the status column.

**2. "Detector B failed to fire on a $33.34 balance move."** The two most recent
snapshots move `4754.00 → 4787.34` = **+$33.34**, over
`PROP_FILLS_STALENESS_BALANCE_DELTA_USD`'s $25 threshold, while
`prop_fills_staleness_state` records `findings: {}`. That looks exactly like a
dead detector. **It is the detector working.** Fill id 41 was reported at
`19:33:17.466Z` — **12 seconds before** the `19:33:29.584Z` snapshot — so a fill
*was* reported in the interval and `assess_balance_move` correctly grades
`explained`. Caught by checking `created_at` on the fills instead of assuming
the interval was empty.

The arithmetic that made both legible, and that independently corroborates the
operator's reports:

* Snapshot `13:37:39Z`: balance 4754.00, equity 4840.00 → unrealized **+86.00**.
  For the then-open SOL long 25 @ 99.94 that implies SOL = **103.38**, and
  SOL's actual 2026-08-30 range was **100.30–107.46**. It fits.
* Snapshot `19:33:29Z`: unrealized **0.00**, balance **+33.34**. The position
  closed for a ~$33 gain, and fill 41 says so.

Two operator reports, a price series and a fill row, agreeing to the dollar.
That agreement — not any single surface's say-so — is what makes "flat" an
assertion here rather than a hope.

## Findings filed

| id | severity | what |
|---|---|---|
| `BL-20260908-IB-LIVE-ENDPOINT-IS-STALE-AND-PROMOTION-WOULD-NOT-CONNECT` | **high** | Real-money `ib_live` declares `127.0.0.1:7496`; no gateway there, wrong port for this deployment. Promotion would not connect. |
| `BL-20260908-EXPOSURE-WITHHOLDS-THE-JOURNAL-NOTIONAL-WHEN-ONLY-EQUITY-IS-UNREADABLE` | medium | An unreadable **equity** suppresses the **journal-side** notional, which needs no venue call — so both sides go dark together on exactly the accounts that most need one to be readable. |
| `BL-20260908-PROP-BALANCE-ASK-NEVER-ESCALATES-WHEN-UNANSWERED` | medium | The ask fires correctly every 12 h and has gone unanswered ~17 times over 8.5 days at identical volume. |
| `BL-20260908-PROP-FILL-ROW-STATUS-READS-AS-POSITION-STATE-AND-IS-WRONG-18-TO-0` | low | The trap above, filed so the next session does not re-derive it. |

`BL-20260908-THREE-OF-ELEVEN-ACCOUNTS-HAVE-NO-VENUE-POSITION-READ-AND-BREAKOUT-1-CAN-NEVER-HAVE-ONE`
(MI-177) is **updated, not duplicated** — its route-level claim stands, its
account-level claim is narrowed by the reconciliation above.

## The `/api/diag/exchange_positions` null, restated precisely

MI-177 filed that this route returns `positions: null` with `error: null` for
three accounts and three different reasons. Measured more precisely here:
`account_open_positions` has **17 distinct `return None` statements** (lines
1269–1571; 22 textual matches, 5 of which are comments), spanning at least six
semantic classes — bad argument · deliberate dry-mode non-dial · missing creds ·
gateway unreachable · empty-but-unverifiable snapshot · unsupported exchange —
plus a catch-all `except`. All 17 arrive at the caller as one `null` with
`error: null`.

A *designed* non-read and a *gateway outage* are therefore indistinguishable on
the wire. This is the same shape as the open
`BL-20260821-IB-OPEN-ORDERS-COULD-NOT-LOOK-CARRIES-NO-REASON` on a sibling
route. Folded into MI-177's existing row rather than filed again.

## What the next pass should check

1. **Put the fleet comparison on a cadence.** MI-177 said this and it is still
   true: `scripts/ops/journal_venue_audit.py` exists and nothing re-runs it. A
   divergence appearing tomorrow is found by the next accident.
2. **Decide `ib_live`.** Either repair the endpoint so a real-money account is
   verifiable, or record that it is trusted flat because it is dry and never
   traded. The finding above makes the third option — assuming promotion will
   just work — unavailable.
3. **Attribution, not quantity**, on the four multi-row symbols MI-177 named.
   Still open, still MI-173's, and still the class both passes are blind to.
