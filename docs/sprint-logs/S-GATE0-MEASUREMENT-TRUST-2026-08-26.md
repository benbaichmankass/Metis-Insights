# Sprint Log: S-GATE0-MEASUREMENT-TRUST-2026-08-26

## Date Range
- Start: 2026-08-26T10:30Z
- End: 2026-08-26T13:00Z

## Objective
Operator-directed, after the MHG maintenance window, in their stated priority
order:

1. *"If the trade journal is not a trustworthy source of data, that is priority
   number 1."*
2. *"We need to deploy the MHG fix now, and keep a LOUD backlog item for EVERY
   SESSION to check and report on it till it's implemented correctly and the
   mechanism has been verified."*
3. *"If there are other pipeline issues, those also need to be fixed
   immediately — we can't measure strategies of trades that are firing
   incorrectly."*
4. *"We need to ensure that all our workflow failures are fixed so that we don't
   keep making the same mistakes over and over again. We aren't using the
   backlog/lessons learned logs correctly if we still keep running into the same
   fuck ups."*

Plus, separately: *"there needs to be some sort of log that new sessions know to
check to see what open items they need to be aware of."*

The operator's framing on all of it: **"ALL OF THESE ARE LOOSE ENDS THAT YOU
ATTEMPTED TO PASS THE BUCK ON."** That was accurate.

## Tier
**Tier 1** throughout. Four commits, one PR (#10339). No config file, no order
path, no live-order behaviour. The one change that touches an order-path module
(`IBClient.cancel`) corrects a *report*: the same call is sent to the venue, and
its single consumer is an ops wrapper — verified by grep across `src/` and
`scripts/`, not assumed.

## Starting Context
The MHG disjoint-OCA over-cover had just been cleared by hand (#10332–#10338):
a 29-lot `ib_paper` position carrying **58 lots of resting stop across two
disjoint OCA groups**, i.e. 200%. OCA cancels only *within* a group, so one stop
firing would have flattened the position and left the other group resting to
sell 29 more into a **naked short**.

## What was done

### 1. IBKR error 202 read as a refusal (pipeline bug, item 3)

The window itself exposed it. `cancel-ib-order` reported the **successful**
cancel as `refused_by_venue` and told the operator a retry would not help:

| run | result |
|---|---|
| #10335 | `action: refused_by_venue` · `verify_state: still_present` · `refusal: {code: 202}` |
| #10336 (fresh process) | `lookup_state: not_found` · `orders_on_account: 6` (was 8) |

IBKR delivers acceptance and rejection down **one** event channel keyed to the
same `orderId`; only the code distinguishes them, and `202` is
`"Order Canceled - reason:"` — the venue confirming the cancel landed. The
capture filed every event as a refusal.

Fixed with an **allowlist** of confirmation codes, so an unrecognised code stays
`refused` and a rejection this repo has never seen fails loud rather than being
swallowed as success. Two contributing shapes fixed alongside: the refusal note
asserted the 10147 story under *every* code, and a confirmed cancel still
showing on the re-read is now `cancelled_readback_contradicted` rather than a
failure (that read is stale by construction).

### 2. The broker-truth ledger reaches the journal read path (item 1)

`comms/broker_truth_ledger.json` has recorded since 2026-07-13 that `bybit_2`'s
per-row journal `pnl` under-records — **−$262.52 wallet-truth against a per-row
sum roughly 8× smaller** — and its only consumer was its own read-only route.
Nothing on the journal READ path consulted it.

Which is why, earlier in this session, a query of that account's closed BTC
trades returned `+$0.88` and was reported as a flat book. The operator corrected
it from the venue UI. **Every component was individually correct.** The ledger
recorded the divergence, the journal returned its rows, the aggregate summed
them faithfully. The defect is at the *seam*.

`journalTrust` now ships per row on `/api/bot/trades/closed` (mirroring
`pnlProvenance`, no shape change) and as an envelope block on
`/api/bot/performance` scoped to the accounts actually in the window.

### 3. `OPEN-ITEMS.json` — the capped cross-session register

The three review backlogs *are* the standing to-do list and are far too large to
read at session start — health alone is **951 rows / 5.1 MB**. Follow-ups were
lost not because nothing recorded them, but because the recording surface is
unreadable at the moment a session plans.

Twelve rows, hard-capped, `open-items-guard` enforcing. **The cap is the
mechanism:** a register that can grow becomes a second backlog and stops being
read, and one nobody reads is worse than none because it *looks* like the
follow-up mechanism exists. Three rows today, including the **loud** MHG one the
operator asked for.

### 4. A backlog that refuses a row restating one already filed (item 4)

The id check that existed catches only an *exact* repeat, which never happens —
ids carry the filing date. So the backlogs were write-only in practice.
`append_row` now raises above 0.65 token overlap and **prints the candidates**.

Verified against the real case from this session: the two rows I duplicated
score **0.80 and 0.70**, the top two hits.

## Validation
- `run_guards.py` → **PASS 53 · FAIL 0 · SKIP 0** on committed code.
- **Both fixes break-tested.** Reverting the 202 classification reproduces the
  #10335 payload byte-for-byte; reverting `journalTrust` fails three wiring tests.
- Two new self-tests that prove they can find a positive before their silence is
  trusted (9 cases and 5 cases).
- A test pins that the **committed** ledger still flags `bybit_2`, so emptying
  it cannot make the flag silently inert.

## What went wrong — three findings against my own work

**The `unreadable` state I wrote was UNREACHABLE.** `load_ledger` funnels a
missing file, an unparseable file and a file listing nothing into one identical
empty envelope, so a deliberately corrupted ledger graded every account merely
`no_record` — the exact collapse the three states exist to prevent, one layer
down, *inside the fix for that class*. Caught only because the test asserted the
state rather than the code path.

**The test schema was wrong twice, in the two documented ways.** Hand-rolled
missed `trades.exit_price`; lifting the `CREATE TABLE` text missed the
migration-added `reconcile_status`. It now builds through `Database(...)`. This
is `BL-20260810-PAIRS-MAX-HOLD-BARS-NOT-ENFORCED`'s lesson catching itself.

**`diagnostic-provenance-guard` flagged my own output.** `format_hits` printed a
bare `0.80` that reads as a confidence when it is token overlap normalised by
the query. Now labelled at the point of print.

The pattern across all three: **the checks found what review would not have.**
That is the argument for GATE 0 being mechanical rather than exhortative.

## Docs updated
- `CLAUDE.md` — the two API rows; "Every session" now routes to `OPEN-ITEMS.json`
  at both ends and to the backlog pre-check.
- `docs/claude/WORKPLAN-2026-08-26.md` — **G2 and G6 shipped**; G1/G3/G4 open,
  G5 blocked on G1.
- `docs/claude/health-review-backlog.json` — 2 rows filed resolved, with tiers.

## Follow-ups
- **G2's live-data half** — the wiring is tested; "verified against live data"
  needs `journalTrust: known_divergent` coming back from the deployed route.
- **G1** is the next session's first item: it gates B2 and the 08-21 headline.
- **`OI-20260826-MHG-OVER-COVER-MECHANISM-UNVERIFIED`** — loud, every session,
  until the page fires on a real event AND `cancel-ib-order` runs against the
  real gateway.
