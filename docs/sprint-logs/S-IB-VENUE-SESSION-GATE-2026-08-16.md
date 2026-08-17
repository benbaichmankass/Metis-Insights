# Sprint Log: S-IB-VENUE-SESSION-GATE-2026-08-16

## Date Range

2026-08-16 → 2026-08-17 (one session, spanning the Sunday Globex close and reopen).

## Objective

Operator-directed, arrived at by investigation rather than assignment. The session
began on `BL-20260816-EXIT-EVAL-INTERVAL-AT-60S-REQUIREMENT`, was redirected to
"dig into why it won't fill" after a deliberate `ib_paper`/MGC flatten was
accepted and never filled, and landed on **"let's fix the market hours gap for
IB."**

Deliverable: give the IB close the market-hours treatment the Alpaca close has
had since `BL-20260716-ALPACA-MARKET-HOURS-EXIT`.

## Tier

**Tier 2** — live order path (`IBClient._locked_close`). Operator approved the
direction ("let's fix the market hours gap for IB") and the merge ("merge 9693
once green"). Rollback is one env flip, no redeploy.

One **Tier-3** item was found and deliberately NOT actioned
(`BL-20260816-IB-PROTECTIVE-STOPS-NEVER-SET-OUTSIDERTH`).

## Starting Context

A `ib_paper` MGC flatten (system-action #9687) returned accepted, sat
`PreSubmitted` with `filled 0`, and the confirm window expired. An earlier one
(#9648, order 6) had done the same 90 minutes before. Neither was explained.

## Repo State Checked

- `origin/main` at session start; merged forward three times during the session
  (main moved under this branch on each).
- Live VM read via the diag relay throughout; direct egress is blocked at the
  Trusted network level (`diag_fetch.sh` exit 3).

## Files and Systems Inspected

- `src/units/accounts/ib_client.py` — `_locked_close`, `place_protective`,
  `_cancel_own_close_order`, `protection_coverage`, `_build_contract`.
- `src/units/accounts/alpaca_client.py` — the `us_equity_session()` precedent.
- `src/runtime/market_hours.py` — confirmed it models `fx` / `us_equity` /
  `crypto` only; **no futures calendar exists in the repo**.
- `src/runtime/order_monitor.py` — the defer contract (string-matched, not
  retCode-matched).
- `scripts/deploy_pull_restart.sh`, `src/web/runtime_status.py` — the deploy
  verification path.
- Live: `/api/diag/ib_open_orders`, `/api/diag/ib_state`,
  `/api/diag/exchange_positions`, `/api/diag/journalctl`, `/api/diag/version`.

## Work Completed

**Shipped (#9693, merged 15:09Z, deployed — trader PID 3082570 → 3093061):**

- `src/runtime/ib_trading_hours.py` (new) — a pure parser for IBKR's
  `tradingHours` / `liquidHours` + `timeZoneId`. Asks the broker rather than
  modelling a calendar. Handles both shipped IBKR formats. Three states
  (`open`/`closed`/`unknown`), registered with `collapsed-state-guard` as
  `ib_venue_session.state`.
- `IBClient._contract_hours` / `_venue_session` / `venue_session` — bounded
  (`IB_CONTRACT_DETAILS_TIMEOUT_S`, 5s) and cached (`IB_SESSION_CACHE_S`, 900s).
  **The cache holds the raw hours STRING, never the verdict.**
- `_locked_close` — consults the gate **before** the Step-1 bracket cancel;
  `closed` → `retCode 2` defer with the bracket left armed.
- `outsideRth` split by instrument (`_close_wants_outside_rth`): FUT transmits
  `True` and is graded on `tradingHours`; STK keeps the default and is graded on
  `liquidHours`, so an equity defers rather than firing into a thin extended book.
- `src/web/api/routers/exit_interval.py` (new) — the read surface #9627 shipped
  without.
- Docs: `CLAUDE.md` env table, `docs/api-tier-policy.md` (93 → 94 routes).

**Also merged:** #9741 (deploy-gate finding), #9750 (Sunday retraction), #9843
(the decisive cancel result).

## Validation Performed

- 82 tests (16 new parser, 13 new close-wiring). **Every load-bearing property
  falsified**: removing the gate fails 3; moving it after the bracket cancel
  fails the bracket assertion; dropping `outsideRth` fails 1; caching the verdict
  instead of the string fails 1; collapsing unknown-outside-span into `closed`
  fails the headline parser test; dropping the tz alias map fails 12 of 16;
  treating equities like futures fails 2.
- `scripts/ci/run_guards.py`: PASS 32 · FAIL 0. CI green on merge.
- **Live: the gate is DEPLOYED and UNEXERCISED.** ~14.5h post-deploy, an 8h
  journal window spanning the Globex open shows zero `venue session`, zero
  `close_open_position`, zero `flatten` — no close was attempted, so it has never
  run. The probe finds positives in the same window, so the search works.
  **Absence of an `UNKNOWN` warning is not evidence of health.**

## Documentation Updated

- `CLAUDE.md` — the four new env knobs, with the ordering, cache and defer-string
  constraints stated as load-bearing.
- `docs/api-tier-policy.md` — new route row + the coverage claim (93→94, Tier-1
  69→70 as a delta argument, not a recount).
- `scripts/ci/check_collapsed_states.py` — new contract, with its coverage caveat
  stated rather than papered over.
- Backlog: 7 rows filed/updated (below).

## Contradictions or Drift Found

- **A caveat I authored and repeated was wrong.** Two backlog rows and merged PR
  #9693 recorded that market hours were *not* shown to explain the MGC non-fill,
  because order 6 sat `PreSubmitted` at 08:26 ET which "does not fit an 08:20
  open." **2026-08-16 is a Sunday**; there is no 08:20 open. Retracted in #9750.
- **A tracking id cited three times in `scripts/deploy_pull_restart.sh`
  (`BL-20260714…`) is filed in no backlog.** Filed as
  `BL-20260817-DEPLOY-SCRIPT-CITES-AN-UNFILED-TRACKING-ID`; the missing row is
  deliberately NOT invented.
- **`/api/diag/version` reports the working tree, not the running process**, so
  the deploy assertion in `deploy_pull_restart.sh` compares `POST_SYNC_HEAD` to
  `POST_SYNC_HEAD` and cannot fail. Folded into
  `BL-20260816-DEPLOY-VERSION-ASSERTION-CANNOT-FAIL` — a row **this same session
  filed at 08:40Z**, lost to context compaction, and re-derived seven hours later.

## Risks and Follow-Ups

| Item | Row | Tier |
|---|---|---|
| Do futures stops trigger outside RTH with `outsideRth=False`? | `RS-20260817-DOES-OUTSIDERTH-FALSE-MAKE-A-FUTURES-STOP-INERT` | research → 3 |
| Every IB protective stop carries the library default | `BL-20260816-IB-PROTECTIVE-STOPS-NEVER-SET-OUTSIDERTH` | 3 |
| Deploy gate cannot fail | `BL-20260816-DEPLOY-VERSION-ASSERTION-CANNOT-FAIL` | 2 |
| `venue_session` has no read surface (the cheap way to settle tzdata) | `BL-20260817-VENUE-SESSION-HAS-NO-READ-SURFACE` | 1 |
| Cancel reports `cancelled` on acceptance | `BL-20260816-IB-CANCEL-REPORTS-CANCELLED-ON-ACCEPTANCE` | 2 |
| No partial-fill state at cancel time | `BL-20260816-IB-CLOSE-CANCEL-IGNORES-PARTIAL-FILL` | 2 |
| Unfiled id cited by the deploy script | `BL-20260817-DEPLOY-SCRIPT-CITES-AN-UNFILED-TRACKING-ID` | 1 |

**Resolved by observation, not by code:** the stuck cancels. Orders 6 and 378 sat
`PendingCancel` for hours while COMEX was shut and **both cleared on their own at
the Globex open**, with MGC unchanged at long 105 (so they cancelled, they did not
fill). Two clients, two implementations, same behaviour — the venue, not the code.
The defect is reporting-only.

## Deferred Items

The `outsideRth` question on protective legs is the one item where deferral was a
judgement call rather than a scope limit. It is deferred because **this repo holds
no evidence either way** about IBKR's trigger semantics, and changing when a live
stop can fire on an unverified inference is worse than leaving a documented
unknown. Hence the research row.

## Next Recommended Sprint

Run `RS-20260817-DOES-OUTSIDERTH-FALSE-MAKE-A-FUTURES-STOP-INERT` first — it is
the only open item where the downside is money rather than record-keeping, and its
answer decides whether a Tier-3 change is needed at all. Then
`BL-20260817-VENUE-SESSION-HAS-NO-READ-SURFACE`, which is small and settles the
one thing about this sprint's own change that remains unverified.

## Wrap-Up Check

- Tests + guards green; CI green on every merge. ✅
- Docs reconciled; `canonical-doc-coherence` and the mechanical scans clean. ✅
- Every open item filed with severity, tier and resolution criteria. ✅
- ✅ **VERIFIED LIVE 2026-08-17T17:01Z** (superseding this line's earlier
  "not verified live" caveat, which stood while the gate had never executed).
  Verification did NOT come from a close — four checks over ~24h found zero
  closes attempted, which is structural, not bad luck. It came from
  `GET /api/diag/venue_session`, shipped in #9884 for exactly this purpose,
  read through the diag relay against the live VM. Verbatim, both IB futures:

  | field | MGC | MES |
  |---|---|---|
  | `state` | `open` | `open` |
  | `tz_source` | `zoneinfo` | `zoneinfo` |
  | `time_zone_id` / `tz_resolved_name` | `US/Eastern` / `US/Eastern` | `US/Central` / `US/Central` |
  | `graded_field` | `tradingHours` | `tradingHours` |
  | `close_would_send_outside_rth` | `true` | `true` |
  | `read_state` | `session_read` | `session_read` |

  MGC `reason`: `tradingHours: inside 20260816 18:00-20260817 17:00 US/Eastern`.

  ⚠️ **THIS REFUTES A PREMISE STATED THROUGHOUT THIS SPRINT.** The sprint argued
  the live risk was that `US/Eastern`/`US/Central` are tzdata legacy links absent
  from slim installs, measured by `zoneinfo` raising for both in the repo's
  sandbox, with `pytz` expected to carry the VM. On the VM it does not need to:
  `tz_source` is `zoneinfo` and `tz_resolved_name` equals the RAW id for both, so
  the **first** rung resolved directly and the pytz fallback was never consulted.
  The sandbox measurement was correct about the sandbox and was wrongly
  generalised to the VM. The alias map and the pytz rungs stay — they are
  defensive, they are load-bearing in the sandbox (dropping the alias map fails
  12 of 16 parser tests), and a future host may well be slim — but they must not
  be described as what makes the live gate work. They are not.

  Also confirmed by the same read: the parser handles a real IBKR overnight span
  (Sunday 18:00 ET → Monday 17:00 ET) against live data, and `graded_field` is
  `tradingHours` with `outsideRth` true for FUT — each instrument graded on the
  field its own order acts on. ✅
- **Nine corrections were issued this session**, three of them caught only because
  a guard or a merge conflict forced a re-read. The durable record is more
  reliable than this session's recall; a successor should brief from the backlog
  rows, not from a summary.
