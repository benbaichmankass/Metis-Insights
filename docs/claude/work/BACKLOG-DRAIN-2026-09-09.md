# Backlog drain, 2026-09-09 — burn-down and the mechanism question

> **Doc status:** `unknown` · category `unknown` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

> Session `session_01Hw9mp8cEeZqF1KSGZHyHui` · object `WO-20260909-THE-HEALTH-BACKLOG-GREW-BY-49-WHILE`
> under `IN-20260903-TRADING-SYSTEM-HEALTH` · registry key `pending-20260909T071304Z`.
> A **closing-only** session per `.claude/skills/backlog-drain/SKILL.md`.

## 1. Burn-down

Population stated at both ends, per the object's done-condition.

| | rows | `open` | `kept_open` | **unresolved** |
|---|---|---|---|---|
| BEFORE — session start, 07:12Z | 1359 | 584 | 181 | **765** |
| `origin/main` at landing time | 1362 | 587 | 181 | **768** |
| AFTER — this branch | 1363 | 585 | 181 | **766** |

- rows **CLOSED**: 3 · rows **FILED**: 1 · **this session's net: −2**
- CLASS retired: **none** — and §3 explains why there was none to retire.

⚠️ **Three populations, not two, and they must not be collapsed.** The register moved
*underneath* this session TWICE, through two separate merge conflicts on this one file. Other
lanes added three rows while the work was in flight (`open` 584 → 587), so the branch lands at
**766**, not the 763 a naive before/after subtraction of this session's own −2 would predict.
The **manager's 765 premise was correct when measured** and none of this corrects it — the
session's own contribution is −2 at both ends (768 → 766 against main at landing time).

Both conflicts were resolved the same way and **never by merging JSON by hand**: take main's file
wholesale, re-apply this session's changed rows onto it as a script, re-assert the byte-exact
round-trip. The second resolution added a **clobber check** that compares each side's rows against
the merge base and REFUSES if any row was edited on both — it reported `BOTH changed: none`, 13
rows mine, 2 rows theirs, so the other lanes' work is provably preserved rather than assumed to be.

That three rows arrived from other lanes during a ~1-hour session, against this session's 3 closes,
is itself a small live data point for §3.

**The manager's premise was CONFIRMED, not falsified.** 584 + 181 = 765 is exactly what the file
read at session start.

**Say the weak part plainly: −2 is a thin drain.** It clears the done-condition as written
(measurably lower, populations stated, every close carrying evidence) and it is nowhere near the
filing rate. §3 is the more useful half of this document, because it explains why that is
structural rather than a matter of effort.

**File integrity.** `backlog_append.detect_format` was asserted BEFORE the first edit and again
after the last: the serialisation is `{"indent": 2, "ensure_ascii": False}` + `"\n"`, and a no-op
rewrite reproduces all 7,172,871 bytes exactly. Final diffstat on the backlog is **99 insertions /
12 deletions** across 13 touched rows. A naive read-append-write would have been ~21,000 lines and
would have re-attributed every pre-existing row to this PR.

## 2. What was closed, and on what evidence

Each close names which clauses were **verified** and which were **inferred**.

1. **`BL-20260908-THE-ALPACA-OVER-CLOSE-IS-UNPINNED-ON-MAIN-ITS-TESTS-ARE-ON-AN-UNMERGED-BRANCH`** — criterion (a), both halves, read
   from `origin/main` rather than the working tree. `tests/test_close_confirm_trade_scope.py` is on
   main (503 lines) and `git grep` for the test name returns five paths **including a `tests/`
   path**, where the row's own 2026-09-08 measurement found exactly one — the backlog JSON itself.
   Not closed on the expectation of #11279 merging, which the row explicitly forbids.

2. **`BL-20260901-OPERATOR-ALERTS-HAS-NO-READ-SURFACE`** — criterion (A), including the measurement
   the row insisted a bare HTTP 200 does not buy. Population: all 302 rows of the ring, span
   2026-09-04T12:31Z → 2026-09-09T06:44Z. **#10666's exponential backoff is CONFIRMED working**
   against live data — median inter-page interval by streak: 13–14 → 5.0 min · 33–35 → 10.1 ·
   74–75 → 20.5 · 154–156 → 40.0 · 274–276 → 60.0, **holding at the 60-minute cap out to streak
   1117**. A clean doubling ladder into a ceiling. Clause (B) CLASS was *not* met — see §4.

3. **`BL-20260903-DECISION-SWEEP-MARKS-PROMPTED-WITHOUT-RECORDING-WHERE-IT-SENT`** — both halves,
   and both **exercised**, not merely deployed. 10 of 16 `prompted` entries carry
   `{destination, poll_state, token_from, chat_from}`; **2 carry a completed re-delivery block**, so
   the re-send the criterion asked for has actually fired. `token_from` holds the token *variable
   name* (`TELEGRAM_CLAUDE_BOT_SECRET`), never a value — which matters, as the file is on a read
   surface. `work_decision_sweep_receipt` is allowlisted and present at 123,480 bytes.

## 3. THE MECHANISM QUESTION — answered, with the arithmetic

The object asked what makes the pile grow faster than it drains. **Three candidate answers were
tested and all three were REFUTED**, so nobody re-runs them:

- **REFUTED — duplication.** A near-duplicate scan over all 765 unresolved titles (Jaccard ≥ 0.5,
  ≥ 4 shared content tokens) finds **exactly 1 pair**. `backlog_append.py`'s dedupe refusal works.
  The pile is 765 genuinely distinct findings.
- **REFUTED — retirable classes.** **0 of 765** rows carry a `class` or `class_members` field.
  There was no class to retire, which is why this session retired none.
- **REFUTED — staleness.** This is the important one. **Every row I could check mechanically was
  still failing when checked.** `bot_log` still absent one month after filing and still in the
  allowlist (neither branch of its criterion). The error-feed digest still frozen at a
  **byte-identical** `covers_since` watermark. `/api/bot/db/tables` still **HTTP 200 unauthenticated**
  eight days on. The builder-exception latch still unwritten fifteen days on.

**What it actually is.** Hand-adjudicated random sample, n = 18, seed 20260909, drawn from the 765:

| what the criterion needs | n | share |
|---|---|---|
| an operator decision, a Tier-2/3 code change, or a research run | 14 | **78%** |
| checkable by a read alone | 4 | 22% |

Of those 4, **2 were checked and still failing**, 1 was ambiguous (its constraint had been removed a
third way its criterion did not anticipate), and 1 was not reached. **Zero passed.**

> **The pile grows because the rows are ACCURATE and the work they name is not being done.**
> It is not a bookkeeping backlog. A closing-only session is *by construction* barred from the
> operator decisions, Tier-2/3 changes and research runs that ~78% of rows require — so it can only
> ever reach the ~22% verification-shaped remainder, and today most of even that remainder failed
> its check rather than passing it.

**Therefore the corrective is not more drain sessions.** The binding constraint sits *behind* the
backlog — decision and build capacity — not *in front of* it. A dedicated closing session is a real
and useful instrument (three evidence-backed closes and nine re-verifications today), but it cannot
be the counterweight to the filing rate, and planning as if it were will keep producing net −2.

⚠️ **Scope of that claim, stated rather than left implied:** n = 18 of 765 is a small sample, and the
78/22 split carries real sampling error. What it will not do is reverse: the checkable fraction would
have to be several times larger than measured for a closing-only session to keep pace, and the
independent 98.0% `not_checkable` figure below points the same way.

**The corroborating arithmetic** (filed vs closed-stamped, by month):

| month | filed | closed |
|---|---|---|
| 2026-05 | 30 | 6 |
| 2026-06 | 133 | 70 |
| 2026-07 | 209 | 129 |
| 2026-08 | 681 | 251 |
| **2026-09** | **304** | **10** |

Filing roughly tripled in August; **closing collapsed to 10 in September**. Note the closing rate
fell *while* the reviews finding these defects got sharper — consistent with the reading above.

**The compounding fix the skill already names is confirmed and quantified.**
`scripts/ops/backlog_drain_candidates.py` scanned 834 open rows across the three backlogs and
returned **817 `not_checkable` (98.0%)**, shortlisting 3 — **of which 1 was a false positive of a
defect already filed** (`BL-20260901-DRAIN-CANDIDATES-DIAG-SIGNAL-CHECKS-THE-ALLOWLIST-NOT-THE-FILE`;
it shortlisted, as `likely_met`, a row whose entire finding is that the file does not exist). That
is the **second** drain session misled by the same signal. Every row filed with a criterion naming a
concrete surface — a diag route, a test id, a guard, an exact field — is a row closable cheaply
later; every row filed as prose needs a full session forever.

## 4. One row filed, and why a closing-only session filed it

`BL-20260909-NO-WRITER-SIDE-GUARD-STOPS-A-PAGING-INPUT-SHIPPING-WITHOUT-A-READ-SURFACE-AND-ITS-ONLY-CARRIER-IS-A-RESOLVED-ROW`

Closing `BL-20260901-OPERATOR-ALERTS-HAS-NO-READ-SURFACE` on its clause (A) would have **deleted**
its clause (B): the class fix's only other carrier,
`BL-20260825-ALERT-AND-CADENCE-STATE-FILES-SHIP-WITHOUT-A-READ-SURFACE`, is already `resolved` while
its own criterion 3 reads *"NOT DONE, and the honest gap: nothing PREVENTS the sixth occurrence."*
Leaving the class recorded only inside a resolved row is the written-and-never-read failure this
repo keeps paying for — and it would have been **caused by a drain session tidying up**. Filed
explicitly, as the object's bounds require.

## 5. Re-verifications recorded (9 rows) — no status change, evidence added

These make the next session's job cheaper and are the honest alternative to a false close.

| row | what today established |
|---|---|
| `…ALPACA-COVERAGE-IS-SIDES-NOT-QUANTITY` | **(1) CLEARED** — detector observed on the named surface (`position 72.0 … stop for only 16.0 — 56 unprotected`, 2026-09-08T11:08:10Z), and **discriminating** (also fired on two new USO positions). **(2) NOT cleared** — but see §6: the exhibit went 72/16 → 75/0 on 09-08 and is **75/75 covered on the live 09-09 read**, so the naked condition closed rather than worsening. Part (2) still wants an *attributed* disposition, which nobody has established |
| `…ALPACA-ACCEPTS-THE-CANCEL-WITH-A-2XX…` | INSTANCE clause cleared — `cancel_accepted_ineffective` observed in the live ledger and in 6 real `close_failure` bodies. 3 clauses remain |
| `…WEDGE-LEDGERS-VANISH-CLOCK…` | Mechanism re-confirmed in code; `last_seen` **7.5 h stale while the sweep runs every pass**. `observation_gap_at` is NOT the fix. Dated prediction due 2026-09-11 |
| `…BUILDER-EXCEPTION-LATCH-HAS-NEVER-BEEN-WRITTEN` | **Third** absent read; criterion (c) re-classification now DUE. Positive control: 44 of 46 siblings present |
| `…E35-LANDING-ASSERTION-IS-VACUOUS…` | Sibling READ: `m20-exit-lever-sweep.yml` invokes `assert_rows_landed` **not at all** — a third state its criterion did not offer |
| `…DIAG-BOTLOG-TARGET-ABSENT` | Still absent, still allowlisted — neither branch, one month on |
| `…FROZEN-DIGEST-IS-SELF-WORSENING…` | `covers_since` **byte-identical** to the row's watermark; `truncated_feeds` non-empty; page-cap banner still present |
| `…DB-EXPLORER-SERVES-21-MORE-TABLES-UNAUTHENTICATED…` | **HTTP 200 unauthenticated**, live, on both `/db/tables` and `/db/table/trades` |
| `…DRAIN-CANDIDATES-DIAG-SIGNAL…` | Recurrence confirmed — second drain session misled |

## 6. Two things the operator should see

Neither re-opens a standing decision; both report that facts moved under one.

1. **The TLT exhibit — CORRECTED 2026-09-09T08:22Z, and the correction is the point.**
   ⚠️ **An earlier version of this section said "the whole short is naked" in the PRESENT tense. That was
   WRONG, and wrong in the way this document is otherwise about.** The evidence behind it was **dated log
   rows from 2026-09-08** (`alpaca_partial_stop_coverage detected: alpaca_portfolio/TLT: position 75.0
   carries a resting stop for only 0.0 — 75 unprotected`, at 2026-09-08T13:37:36.743503Z) reported as
   though it described the fleet now. That is a **DATED SNAPSHOT read as a live read** — the exact
   collapse `check_trainer_capture_watch.py` prints a standing warning about, made in a session whose
   central finding is provenance discipline. State the population; I did not, on this one.
   **THE LIVE READ** (`/api/diag/alpaca_open_orders`, `read_state: orders_read`, 2026-09-09T08:22Z):
   `alpaca_portfolio` holds TLT **short 75** against a resting **buy-75 stop @ 82.40** (`held`,
   submitted **2026-09-08T18:49:44Z**) — **fully covered, 75 of 75.** The naked window opened at
   13:37:36Z and closed roughly five hours later, on 09-08; my report of it was ~19h stale.
   So the standing decision's premise DID move, but the other way: it was made against 72 shares with a
   16-share stop (22% covered); the position is now 75 and the 16-share stop is gone, **replaced by a
   75-share stop covering the whole position** (100%). The `partial_stop_coverage_alert_state` latch
   corroborates — `sev=56` last fired 2026-09-08T11:08:10Z, `sev=75` last fired 2026-09-08T13:37:36Z,
   and **nothing has fired since**.
   ⚠️ **NOT ESTABLISHED: what placed that stop.** The Alpaca broker-naked sweep re-arming is the obvious
   candidate and was NOT observed. An unattributed repair is not evidence a mechanism works — this repo
   has already been bitten by crediting one (the `PROTECTION_REASSERT_MODE` exhibit that vanished).
   Nothing was acted on either way. (`alpaca_portfolio` is a paper margin book.)
   ⚠️ **A route discrepancy, reported and NOT filed as a defect:** `/api/bot/positions` at
   2026-09-09T07:44:55Z returned only `bybit_2` XRPUSDT and no TLT, while `/api/diag/alpaca_open_orders`
   shows TLT open on TWO Alpaca accounts (`alpaca_paper` short 707, `alpaca_portfolio` short 75) at
   08:22Z. Both reads are honest; the `/api/bot/positions` route does not surface these accounts.
   Whether that scoping is deliberate is NOT established, which is why this is an observation.
2. **`/api/bot/db/tables` serves the money DB's tables and rows to anyone on the internet, today.**
   ⚠️ **This is NOT the closed diag-token question and must not be filed under it.** That decision
   concerns `/api/diag/*`, which is read-only and at least **bearer-gated**. This is a *different*
   surface with **no credential at all**. Phase H has not attached `require_session` to it.

## 7. Bounds honoured, and one process note against myself

`config/`, `src/`, and every order path untouched — the diff is one JSON file plus this document.
No Tier-3 item enacted. No exit-matrix cell re-graded. No standing decision re-opened. The
diag-token rotation question was not raised and no successor row was filed.

⚠️ **Three further process failures of mine, recorded because a drain session's whole product is
whether its claims can be trusted.** All happened after the burn-down above and none change it.

1. **A DATED SNAPSHOT REPORTED AS A LIVE READ** — §6's original text. Corrected in place there; the
   full account is in that section. It is the same collapse `check_trainer_capture_watch.py` prints a
   standing warning about, committed in a session whose central finding is provenance discipline.
2. **THE CORRECTION LOST THE AUTO-MERGE RACE BY ~3 MINUTES.** #11510 merged at 08:21:37Z; the fix was
   pushed at 08:24:48Z and is not in the squash. I had declared the branch closed to new content on
   arming and broke that deliberately, because a false claim about position protection landing in a
   permanent artifact is worse than the race — the judgement holds, the timing did not, and the fix
   landed as a separate PR off `main` instead (a merged PR cannot be reused).
3. **A `grep` OF `main` RETURNED A FALSE NEGATIVE AND I NEARLY BELIEVED IT.** Checking whether the
   stale claim had landed, `grep -c "the whole short is naked"` returned `0` — because the phrase
   wraps across two lines in the rendered file. The claim *was* there. RULE ONE's second clause is
   exactly this: a negative result needs a denominator, and the probe must be shown able to find a
   positive before its silence means anything. I caught it only by reading the section directly.
4. **CONTENT AND THE ARMING PAIR WENT IN ONE PUSH**, so `claude-pr-automerge` *created* the correction
   PR rather than adopting a hand-opened one — giving it the zero-CI window this file's own §7 note
   warns about (`total_count: 1`, only the workflow's own check). The two-push order is: open the PR
   by hand FIRST, push the arming pair SECOND. I got that right on #11510 and wrong on its successor.

⚠️ **Process note, recorded because the object's own bounds warned about exactly this shape.**
`document-index-guard` failed on the new document, and the documented remedy
(`python3 scripts/ops/document_index.py --write`) rewrote **446 files** — bumping the `last verified`
date in every registered document, including `CLAUDE.md` and `docs/CLAUDE-RULES-CANONICAL.md`. That
is precisely the "large diff on a small edit" signal, one register over from the backlog-serialisation
trap. It was reverted and the single index row added by hand. **Not filed as a new row** (this is a
closing session and the tool is behaving as designed for a full re-index), but a session adding one
document should add one row, not re-date the corpus — worth a look by whoever owns that tool.
