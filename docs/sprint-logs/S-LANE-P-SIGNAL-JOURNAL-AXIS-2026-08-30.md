# Sprint Log: S-LANE-P-SIGNAL-JOURNAL-AXIS-2026-08-30

## Date Range
2026-08-30 (single session, `01HYXKHpDQeWv3u4rjWWoL2J`).

## Objective
Work Lane P (P1/P2) per `WORKPLAN-2026-08-29.md` § "Recommended order" item 3,
and carry the loud `OI-20260830-E35-GEOMETRY-SHIPPED-TO-9-LEGS-NOT-YET-LIVE-VERIFIED`.

## Tier
**Tier 1 throughout.** Read-only observability + docs. No order path, no
`config/`, no VM mutation, no service restart. One free-runner workflow
dispatch (`gld-compat-matrix`, read-only by its own header).

## Starting Context
`origin/main` at `b0c3f468`. A concurrent `/system-review` session
(`claude/full-system-review-v68vcm`, PR #10482) was live and **unannounced on
the board**, holding `CLAUDE.md`, `docs/claude/OPEN-ITEMS.json` and
`docs/claude/health-review-backlog.json`. The session brief's one DUE item
(`OI-20260830-BYBIT-HEDGE-MODE-ARMED-BUT-UNEXERCISED`) was that session's own
subject, so it was **left to them rather than duplicated**.

## Repo State Checked
- `origin/main` `b0c3f468` (the handoff named `7c47ecb9`; #10461 had landed since).
- Board issue #6927 read before the first substantive call; `▶️ START` posted
  naming the claimed files and the deconfliction.
- Merge slot free (last `🔓 RELEASE` 09:15:10Z).

## Files and Systems Inspected
- `src/prop/account_rulesets.py::_standard_ruleset`, `src/prop/standard_account_size.py`
- `scripts/prop/account_compat_matrix.py`, `.github/workflows/gld-compat-matrix.yml`
- `src/runtime/dead_leg.py`, `scripts/ops/dead_leg_audit.py`, `src/runtime/silent_refusal_alert.py`
- Live: `/api/bot/config`, `/api/bot/positions`, `/api/bot/trades/closed`,
  `/api/bot/db/{tables,table/*}`, `/api/diag/{version,audit_query}`
- GH Actions run `33306014754` (job `99242607803`)

## Work Completed

### 1. P1 — already done, and the workplan said otherwise for three days
**No code was written for P1.** Reading `_standard_ruleset` before claiming the
work showed it already builds `drawdown_type="intraday_high"` +
`drawdown_breach="refusal"` with the synthetic `$10,000` gone (#10364, 08-27),
and its residual `BL-20260829-GLD-COMPAT-SUMMARY-CALLS-UNGRADED-A-REJECTION-AND-ITS-STANDARD-ARM-IS-INERT-ON-A-RUNNER` already fixed in #10393
(`c1f50fc`, 08-29) — a dedicated `UNGRADED` branch plus a real balance source.

The workplan row read *"Unchanged from 08-26/08-27 and not re-verified this
session"* and sat at **position 3 of Recommended order**, ahead of B6/B5. A
session following the plan would have spent a unit rediscovering a merged fix.
Row corrected in place; *field beats comment*.

### 2. P1 — verified by EXERCISING it, which had never been done
Both defects were fixed on 08-29 and **never observed working**; the row was
still `open`/`high`. Dispatched `gld-compat-matrix` on `main` (free runner).

| | 2026-08-29 (filed) | run `33306014754`, 08-30 |
|---|---|---|
| standard accounts graded | **0 of 10** | **9 of 10** |
| size basis | none → `unreadable` | `size=measured`, `source=db`, 11 accounts |
| the one refusal | — | `ib_live`, balance genuinely `None`/`api_ok=false` |

The refusal is **discriminating, not blanket**, and no synthetic size returned —
which is what makes this fixed rather than merely quiet. Row marked `resolved`
with the measurement; **one residual filed separately rather than swept in**
(`ib_live` reads `UNGRADED` with no stated reason — the workflow renders its own
table with no size column, though the *script* already renders `— (size_state)`).

### 3. P2 — the "signals, journals nothing" detector (the session's code)
Motivating claim **verified first, and it is worse than recorded**:
`trend_donchian_sol` is enabled/live and routed to `bybit_1`; **144** actionable
buy signals since 08-01 (workplan said 120); newest journal row of any kind
**2026-06-29**; all 7 trade rows on `breakout_1`; **zero rows on `bybit_1`,
ever**. Nothing alerted for two months.

Shipped a **third axis** in `src/runtime/dead_leg.py` — the module that exists so
the offline report and the live alert cannot disagree about a row, so not a new
module and not a second copy of the rule:
`signal_journal_state_for(actionable_signals, journal_rows, *, table_present)` →
`journaling · signals_never_journaled · no_actionable_signals · unknown`, plus
the audit consumer + render, sourced from `signals` rather than `legs`.

## Validation Performed
- `tests/test_dead_leg_audit.py`: **29 passed** (18 pre-existing + 11 new).
  Existing 18 still green after the fixture-helper change, which was written so
  their meaning is unchanged (`side` defaults to `None`).
- `run_guards.py --base-ref main`: **PASS 38 · FAIL 1**, the failure being
  `layer-guard` exit **127** (`lint-imports` absent locally); after installing
  import-linter it reports *"Contracts: 6 kept, 0 broken"*. **39/39 relevant pass.**
- `ruff` **clean** at the CI-pinned version. ⚠️ My first measurement said "45
  errors vs 30 baseline" and was **wrong** — it used unpinned ruff 0.16, whose
  default-ruleset expansion `requirements-dev.txt` explicitly pins against. At
  `0.15.22` both baseline and branch are clean.
- Negative controls hold for the two likeliest false-positive classes: a
  refusing leg (has rows, has an owner) and a packages-only leg.

## Documentation Updated
- `docs/claude/WORKPLAN-2026-08-29.md` — Lane P table + Recommended order.
- `docs/claude/health-review-backlog.json` — 1 resolved, 3 filed (all via
  `backlog_append`; the resolve was a `detect_format` round-trip, 7/1 numstat in
  a 1013-row file, no reformat churn).
- This log.

## Contradictions or Drift Found
1. **The workplan listed a completed item as open for three days** (P1), at
   position 3 of the recommended order. Corrected.
2. **A `high`-severity backlog row stayed `open` after both its defects were
   fixed in the same overnight PR that followed its filing.** Resolved with
   evidence. The pattern — file in the evening, fix in the same PR, never close —
   is worth noticing beyond this instance.
3. **Two guards caught real defects in this session's own work**, which is the
   system working: `timestamp-comparison-guard` (below), and the guard runner
   refusing to scan a stale `/tmp/pr.diff` rather than report a green having
   checked nothing.

## Risks and Follow-Ups
- `BL-20260830-TREND-DONCHIAN-SOL-SIGNALS-144-TIMES-AND-JOURNALS-NOTHING-ON-BYBIT-1` (**high**) —
  the detector makes it visible; **the cause is undiagnosed and the leg is still
  signalling into a void**. Leading hypothesis is a per-SYMBOL arbitration loss
  (`aggregate_intents`, Lane P/P3 — `bybit_1` also runs `trend_donchian_sol_4h`
  and `sol_pullback_2h` on SOLUSDT), which would make the silence *expected but
  unlogged*; the alternative is a wiring bug. **Opposite remedies — establish
  which before changing anything.**
- `BL-20260830-SIGNAL-JOURNAL-AXIS-HAS-NO-LIVE-ALERT-ONLY-AN-OFFLINE-AUDIT`
  (medium) — the blind spot is narrowed, not closed.
- `BL-20260830-COMPAT-TABLE-OMITS-THE-SIZE-BASIS-SO-UNGRADED-DOES-NOT-SAY-WHY` (low).
- ⚠️ `alpaca_live` graded **ROUTE** on the compat run. That is a 0-bps harness
  verdict and **must not** be read as unblocking the go-live —
  `OI-20260829-ALPACA-GOLIVE-BLOCKED-ON-T1-SETTLEMENT-MODEL` is loud and open,
  and a compat verdict is not a settlement model.

### A bug this session wrote, and what caught it
The first draft compared `order_packages.created_at >= datetime('now', ?)` raw.
That column is ISO-8601 with a `T`; `datetime('now')` is space-separated;
compared as strings they agree on the date and disagree at character 11
(`T` 0x54 vs space 0x20), so every package on the boundary **date** sorted as
in-window. The direction is the dangerous one — an over-counted package makes a
leg that journalled nothing look like it journalled something, **suppressing the
very finding the axis exists to raise**. `timestamp-comparison-guard` caught it.

⚠️ **The first regression test for it was VACUOUS and I only found that by
mutation-testing it.** It used a 90-day-old package, which the buggy string
compare also excludes, so it passed with the bug reintroduced. It now places the
row one second before the boundary instant, read back out of SQLite, and fails
when the bug returns. **Noted because the sibling test
`test_window_boundary_does_not_swallow_a_day_on_the_iso_separator` uses a
30-day-old fixture for the same claim and looks like it has the same problem —
unverified, not fixed here.**

## Deferred Items
- **P3** (Tier-3, arbitration scope) — untouched.
- The live alert for the new axis — filed, deliberately not half-built.
- Registering `signal_journal.state` with `collapsed-state-guard` — **not done
  on purpose.** The guard requires every state to be branched on by a real
  consumer; registering today would either fail or invite the decorative branch
  the guard exists to prevent. The four states *are* reported distinctly
  (`signal_journal_state_counts`), which is the honest precondition, but
  `unknown` still has no per-state consumer branch.
- `OI-20260830-E35-…` observation — see Wrap-Up; the register file is held by a
  concurrent session.

## Next Recommended Sprint
Diagnose `trend_donchian_sol` (read the 144 audit rows' `reason` + the
`regime_hard_gate` rows for the same ticks) — it is the one open item with a
live leg behind it. Then Lane A on Monday's US open, which is calendar-blocked
and cannot move.

## Wrap-Up Check
- [x] Board `▶️ START` posted before the first substantive tool call; `✅ DONE` at wrap.
- [x] Tests + guards green; lint clean at the pinned version.
- [x] Backlog rows filed through `backlog_append`, no reformat churn.
- [ ] `OPEN-ITEMS.json` **NOT updated — deliberately.** PR #10482 (concurrent,
      live) holds that file. The e35 observation below is recorded here and on
      the board instead of racing it.

### `OI-20260830-E35-GEOMETRY-SHIPPED-TO-9-LEGS-NOT-YET-LIVE-VERIFIED` (loud) — carried, half-cleared
- **(a) config read-back — SATISFIED.** All **11 field values across 10 legs**
  read back from the live `/api/bot/config`, held leg included
  (`trend_donchian_eth_prop` still 2.5). `/api/diag/version` shows the web-api
  process running `git_sha 892c9a2c` — the e35 shipping commit itself.
- **(b) a closed real-money trade under the new geometry — NOT satisfied.**
  There IS a closed real-money row on an e35 leg since the deploy
  (`trend_donchian_eth_4h`, trade 4904, `bybit_2`, closed 09:48:15Z) and **it
  does not count**: `openedAt 2026-08-21T21:54:54Z`, nine days *before* the
  deploy, so it carries the OLD geometry. It also fails all three axes
  `PB-20260830-XRP-4H-FIRST-CLOSE-…` warns about — `closeReason: reconciler`,
  `pnlProvenance: estimated`, `journalTrust: known_divergent`.
- **Why nothing has opened yet:** the donchian legs are inside their channels
  (SOL close 104.64 within [100.53, 110.58]); every recent audit row is `_eval`
  with `side: none`.
- ⚠️ **The `clears_when` wording is a trap and should be sharpened.** *"at least
  one CLOSED trade exists on a real-money leg under the new geometry"* reads as
  satisfiable by trade 4904 — it is a closed trade, on a real-money e35 leg,
  after the deploy. What is required is a trade **OPENED** after the deploy.
  Recommend the owning session amend it to say so.
