# Backlog drain, 2026-09-09 — burn-down and the mechanism question

> **Doc status:** `live` · category `evidence` · last verified `2026-09-09` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md)

> Session `session_01Hw9mp8cEeZqF1KSGZHyHui` · object `WO-20260909-THE-HEALTH-BACKLOG-GREW-BY-49-WHILE`
> under `IN-20260903-TRADING-SYSTEM-HEALTH` · registry key `pending-20260909T071304Z`.
> A **closing-only** session per `.claude/skills/backlog-drain/SKILL.md`.

## 1. Burn-down

Population stated at both ends, per the object's done-condition.

| | rows | `open` | `kept_open` | **unresolved** |
|---|---|---|---|---|
| BEFORE (07:12Z) | 1359 | 584 | 181 | **765** |
| AFTER (08:4xZ) | 1360 | 582 | 181 | **763** |

- rows **CLOSED**: 3 · rows **FILED**: 1 · **net unresolved −2**
- CLASS retired: **none** — and §3 explains why there was none to retire.

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

1. **`BL-20260908-THE-ALPACA-OVER-CLOSE-IS-UNPINNED-ON-MAIN…`** — criterion (a), both halves, read
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

`BL-20260909-NO-WRITER-SIDE-GUARD-STOPS-A-PAGING-INPUT-SHIPPING-WITHOUT-A-READ-SURFACE-…`

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
| `…ALPACA-COVERAGE-IS-SIDES-NOT-QUANTITY` | **(1) CLEARED** — detector observed on the named surface (`position 72.0 … stop for only 16.0 — 56 unprotected`), and **discriminating** (also fired on two new USO positions). **(2) NOT cleared — the exhibit WORSENED to 75.0 vs 0.0**; see §6 |
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

1. **The TLT exhibit worsened.** The standing decision *"the 56 naked TLT shares on trade 5414 stay
   naked"* was made against a **72-share position carrying a 16-share stop**. As of
   2026-09-08T13:37:36Z the feed reads **`position 75.0 carries a resting stop for only 0.0 — 75
   unprotected`**: the 16-share stop is gone, so trade 5266's bracket has vanished too and the whole
   short is naked. Recorded, not acted on. (`alpaca_portfolio` is a paper margin book.)
2. **`/api/bot/db/tables` serves the money DB's tables and rows to anyone on the internet, today.**
   ⚠️ **This is NOT the closed diag-token question and must not be filed under it.** That decision
   concerns `/api/diag/*`, which is read-only and at least **bearer-gated**. This is a *different*
   surface with **no credential at all**. Phase H has not attached `require_session` to it.

## 7. Bounds honoured, and one process note against myself

`config/`, `src/`, and every order path untouched — the diff is one JSON file plus this document.
No Tier-3 item enacted. No exit-matrix cell re-graded. No standing decision re-opened. The
diag-token rotation question was not raised and no successor row was filed.

⚠️ **Process note, recorded because the object's own bounds warned about exactly this shape.**
`document-index-guard` failed on the new document, and the documented remedy
(`python3 scripts/ops/document_index.py --write`) rewrote **446 files** — bumping the `last verified`
date in every registered document, including `CLAUDE.md` and `docs/CLAUDE-RULES-CANONICAL.md`. That
is precisely the "large diff on a small edit" signal, one register over from the backlog-serialisation
trap. It was reverted and the single index row added by hand. **Not filed as a new row** (this is a
closing session and the tool is behaving as designed for a full re-index), but a session adding one
document should add one row, not re-date the corpus — worth a look by whoever owns that tool.
