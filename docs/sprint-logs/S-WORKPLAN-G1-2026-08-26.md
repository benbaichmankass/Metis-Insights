# Sprint Log: S-WORKPLAN-G1-2026-08-26

## Date Range
- Start: 2026-08-26T10:20Z (picks up where `S-WORKPLAN-GATE0-2026-08-26` ends)
- End: 2026-08-26T22:40Z

> Session `b9kyip`, continuation segment. The session's FIRST segment is already
> recorded in the `WORKPLAN-2026-08-26.md` session-log row for `b9kyip` (G1 code
> half + G3 first slice). This log covers the six PRs merged after it.

## Objective
- **Primary:** drive `docs/claude/WORKPLAN-2026-08-26.md` autonomously past
  GATE 0 into the lanes, holding Tier-3 calls for the operator.
- **Secondary:** whatever the work itself surfaced. Two of the six PRs exist
  only because something broke while doing something else.

## Tier
- **Tier 1** for five of the six PRs (docs, tests, research tooling, an
  observe-only default).
- **Tier 2** for the venue repair on `ib_paper` (a live IB order cancelled) and
  for the prop-journal write — both carried an explicit operator OK in chat.
- **Tier 3:** none taken. The env flip that would ARM the new sweep was offered
  and the operator chose `annotate` first; nothing is armed.

## Starting Context
- Active roadmap items: **M20** Active Trade Management (operator's priority),
  GATE 0 closed at the start of this segment.
- Prior sprint: `S-WORKPLAN-GATE0-2026-08-26`.
- Known risk at start: `ib_paper` was carrying a live over-cover that a prior
  session had cleared **by hand**, with the mechanism unproven
  (`OI-20260826-MHG-OVER-COVER-MECHANISM-UNVERIFIED`).

## Work Completed

### 1. `#10345` + `#10346` — the e35 corpus nobody collected, and a gate that counted inert folds as wins
1,629 measured bracket-geometry cells had sat on `claude/e35-bracket-corpus`
since 2026-08-24, invisible to every consumer of the committed corpus. The job
had emitted a loud, correct notice that a human must open the PR — **a notice
addressed to nobody is not a mechanism**. Recovered by UNION keyed on
`measurement_key`, never a reset: neither side was a superset (main held 15 keys
the branch lacked). `m20_corpus_union.py` was **parameterised** on which
extractor owns the key rather than copied, with a lazy import pinned by a test
that re-imports in a clean interpreter — the m20 sweep's conflict re-derive
copies only the m20 extractor onto the runner, so an eager import would break
that recovery path at the moment it is needed.

Verified against the produced FILE, not the log: 8,211 rows / 8,211 unique keys
/ 0 duplicates.

Separately, `e35_bracket_geometry_sweep` graded on the RAW win tally while the
comment directly above it asserted the effective tally was used — *field beats
comment*, and here the comment claimed a fix the code did not perform. Now
grades on `wins_effective` and records `verdict_basis` beside the verdict; a
missing `wins_effective` falls back and **says so**, never to a silent zero.
Population restated after recovery: 51 gate-passing cells, all 51 gradeable, 9
flip across 8 legs. Zero coverage-matrix statuses change — all 12 matrix-cited
cells re-checked individually.

### 2. `#10347` — `alpaca_live` at $200
Two corrections found by reading `config/accounts.yaml` instead of the register
that is supposed to describe it: the register's risk block was stale on **every
value**, and the account is `mode: dry_run` — which is the actual explanation
for the zero gross notional across 91 `exposure_soak` rows that the row's own
severity basis cited as evidence of a sizing failure.

Filed the sizing floor the funding decision should be made against. Population:
the 10 of 11 declared symbols for which `/api/bot/candles` returned a close on
2026-08-26 (SPLG returned none and is excluded, not assumed). At a 5% stop,
4 of 10 round to ZERO shares; one share of SPY costs 3.8x the entire account.
Funding at $200 does not fund a scaled-down roster — it funds a SUBSET selected
by share price rather than by any evaluation of the legs. Inert while dry_run;
binds the moment it goes live at that funding.
`BL-20260826-ALPACA-LIVE-AT-200-USD-CANNOT-SIZE-ITS-LARGEST-SYMBOLS`.

### 3. `#10352` — the over-cover generator, caught in the act
The already-shipped survivor-join fix (`b81458a4`) was verified **deployed**
before anything was concluded from its absence. Then a *different* mechanism was
caught live at 2026-08-26T02:08:35Z on `ib_paper`/MHG: a routine trailing amend
armed `oca-protect-t4796` **beside** two legacy-named groups, reaching 300%
across 3 groups — with no `no oca_key` warning, no Error 10147 and no
survivor-join log, i.e. none of the three signatures of the known defect.

Cause: `place_protective`'s keyed pre-cancel is scoped **BY NAME** to
`oca-protect-t<trade_id>`, so a group resting under a legacy or bare-numeric
name is never a cancellation candidate and the re-arm mints a second,
non-mutually-cancelling group beside it. It mints **once per trade**, at the
legacy→keyed transition — finite, not an unbounded generator (an earlier reading
of mine said unbounded; corrected).

Shipped `src/runtime/stray_oca_groups.py` — a **pure decision function**, so the
policy is arguable in tests rather than against a live position (the lesson of
`BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG`). Five
states, never collapsed; a **keyed sibling group is preserved by construction**
because it carries its owning trade id in its own name, and an **ungrouped** leg
is reported and never cancelled. Default `annotate`: the decision runs in full
and cancels nothing.

### 4. `#10353` — making it stageable
`#10352` shipped a bare global while both its PR body and its `CLAUDE.md` row
promised "stage it on `ib_paper` first". The two IB accounts are `ib_paper`
(`mode: live`, class **paper**) and `ib_live` (`mode: dry_run`, class
**real_money**) — so a global flip is safe only by accident of current config.
Added `PROTECTION_STRAY_GROUP_ACCOUNTS`, **empty = NONE**, deliberately the
opposite polarity to `CONVICTION_SIZING_ACCOUNTS` / `NETTING_ATTRIBUTION_ACCOUNTS`
(those widen a size and a DB write; this one cancels a live position's resting
exits). The allowlist scopes the CANCEL, never the MEASUREMENT — the correction
`NETTING_ATTRIBUTION_ACCOUNTS` needed on 2026-08-09.

### 5. Venue repair on `ib_paper`/MGC (Tier-2, operator-approved)
Reconciled against the journal **before** acting: trade 5007 short 51 @ 4660.9
matches `oca-protect-t5007` on price *and* qty; the bare-numeric group
`834864174` (STP BUY 50 @ 4699.9, submitted under a rotated clientId) is the
stray. Cancelled order 422 only. IBKR returned error 202 while the verification
re-read contradicted it; two fresh-process dry runs settled it (`not_found`,
8→6 orders).

### 6. `#10354` — recording that it did NOT self-heal
~1h25m later: exchange MGC short **50** vs journal `position_size` **51**,
unconverged. Control: MHG 29/29 and MES 15/15 agree exactly, so this is not a
reader artefact. Protection 51/51 = 102%. **No IB detector covers it** — the
over-cover page needs *disjoint* groups (there is one), and
`journal_qty_divergent` exists only in the Bybit sweep.

### 7. Prop trade logged (Tier-2, operator-instructed)
Operator terminal History screenshot → `POST /api/bot/prop/report` via the
`prop-report` relay (issue #10355). Fill id **36**: `breakout_1` SOLUSD long
25.00 @ 99.94, status `open`, linked to ticket `prop-manual-784e35819c9f`, which
advanced `awaiting_report` → `filled`. Verified by reading the journal back, not
from the workflow's own 200.

## Validation Performed
- Full guard runner on committed work (not on the working tree — see Drift).
- Targeted test suites, plus **a control run of the identical selection against
  `origin/main`**. That control is what caught the worst defect of the session.
- Two fresh-process dry runs to settle a contradicted broker read.
- Live read-back of every VM-side write.
- **Gap not verified:** nothing has yet been observed from the new sweep in
  `annotate`. It writes no durable soak file — only a trader-journal WARNING —
  so reviewing it means a `journalctl` pull, not a log endpoint.

## Contradictions or Drift Found
- **`sqlite3.Row` has no `.get()`.** A `row.get("account_id")` I added raised
  into `_attempt_naked_autoprotect`'s broad `except` and **silently disabled
  every naked re-arm** — the safety path that re-arms an unprotected live
  position. Five tests failed, a count close enough to the known-noisy sandbox
  set to have been waved off. Caught **only** by the `origin/main` control run
  (main: 258 passed / 0 failed; branch: 5 failed). Fixed, and pinned by a test.
- **`IBClient._open_trades` collapses a read failure into an empty book**
  (`except Exception: return []`), so *we could not look* is indistinguishable
  from *nothing rests*. The new sweep reads `ib.openTrades()` directly; the
  shared helper was **filed, not fixed** — it sits on the IB order path and
  wants its own review of all callers.
  `BL-20260826-OPEN-TRADES-COLLAPSES-A-READ-FAILURE-INTO-AN-EMPTY-BOOK`.
- **A stray OCA leg filled and the journal never learned** —
  `BL-20260826-A-STRAY-OCA-LEG-FILLED-AND-THE-JOURNAL-NEVER-LEARNED`.
- **My own process lapse, twice in one session:** guards run, *then* a backlog
  row filed, then pushed without re-running — which is exactly what the runner
  warns about, and it turned `claim-basis-guard` red in CI on `#10352`. Recorded
  rather than quietly fixed.
- The board `▶️ START` I posted said "READS ONLY" after I had already merged
  order-path code; corrected in place with a `⚠️ SCOPE CORRECTION`.

## Risks and Follow-Ups
- ⚠️ **Nothing is armed.** `PROTECTION_STRAY_GROUP_MODE=annotate`,
  `PROTECTION_STRAY_GROUP_ACCOUNTS` unset (= NONE). **Before arming, read two
  things:** (a) `ib_paper`/MES rests a single **legacy-named** group
  `oca-protect-408` which the sweep classifies `stray_unkeyed` and would cancel
  — and it is MES's **only** protection; (b) `annotate` produces no durable soak
  file, so "observe first" means a journal pull.
- The MGC journal/exchange qty divergence (50 vs 51) is unowned by any detector.
- 🔴 `OO-20260825-DIAG-READ-TOKEN-ROTATION` — still unrotated, operator-only.
  `get-diag-token` REFUSES on a public repo and is not a delivery path.
- `OO-20260825-BREAKOUT-1-BALANCE-REPORT` — the prop balance snapshot is 78.2h
  old and this session opened a position against it (see below).

## Deferred Items
- Lane C1 (partial leg-id capture, Tier-2), Lane D1 (record M16 honestly).
- An IB-side `journal_qty_divergent` detector.
- Observing `annotate` rows before arming.

## Next Recommended Sprint
- **Suggested:** observe the stray-group sweep's `annotate` output, then arm on
  `ib_paper` — with the MES legacy-group finding resolved FIRST, because the
  sweep as written would cancel MES's only protection.
- **Required verification before starting:** pull the trader journal for the
  sweep's WARNING lines; re-read `ib_paper`'s resting orders against a FRESH
  client, never `/api/diag/ib_open_orders` alone
  (`OI-20260826-DIAG-IB-ORDER-READ-IS-STALE`).

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] Roadmap status was checked.
- [x] Contradictions were recorded — including my own.
- [x] Remaining unknowns were stated clearly.
