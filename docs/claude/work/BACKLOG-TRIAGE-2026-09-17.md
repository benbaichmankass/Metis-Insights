# Health-backlog triage — 2026-09-17

> **Doc status:** `live` · category `record` · produced by the OPS lane
> (`session_0178pRo8ZxrnrREzDzDzb9Zk`, MI-294) · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md)

**This is a TRIAGE, not a drain.** The deliverable is named chunks a later
session can pick up without re-deriving the classification. A count of rows
closed is not this document's measure and must not be substituted for it.

**File measured:** `docs/claude/health-review-backlog.json`, 9,300,064 bytes,
`updated_at: 2026-09-17`. ⚠️ Its `generated_at` reads `2026-08-18T09:35:00+00:00`
— a month stale, and a separate small finding.

---

## 0. THE BRIEF'S NUMBERS WERE WRONG IN ONE PLACE THAT MATTERS

The dispatch said *"932 unresolved health rows, 331 critical/high, 41 critical."*
Measured:

| | brief | measured | note |
|---|---|---|---|
| unresolved (`open` + `kept_open`) | 932 | **928** | immaterial |
| unresolved critical+high | 331 | **330** | immaterial |
| **critical** | 41 | **41 across all 1,624 rows — but only 18 among the unresolved** | ⚠️ **materially different** |

**41 is a Population-A number and must not be quoted as an open-work count.**
23 of the 41 criticals are already `resolved`/`superseded`/`wont_fix`/`invalid`.
A session told "41 criticals are open" would plan more than twice the work that
exists.

**Population A** = every row in the file = **1,624**
(`open` 732 · `kept_open` 196 · `resolved` 667 · `superseded` 14 · `wont_fix` 10 · `invalid` 5).
**Population B** = unresolved = **928**. Every number below states which it uses.

---

## 1. ⚠️ THE HEADLINE — A CANONICAL METRIC WENT 0 → 85

[`docs/CLAUDE-RULES-CANONICAL.md`](../../CLAUDE-RULES-CANONICAL.md) § "Backlog
governance", measured 2026-08-13 over 269 open rows across all three backlogs,
records:

> | **Tier-1 high/critical rows older than 14 days** | **ZERO** |

and calls that row **"the diagnosis"**, resting the whole governance regime on
it: *"when a row says it matters and says a session may act, it gets fixed,
reliably."*

**Measured 2026-09-17 over the health backlog ALONE: that count is 85.**

- Population: the 330 unresolved critical+high rows; 208 are Tier-1; **85 of
  those 208 were filed on or before 2026-09-03**. Severity mix **85 high, 0
  critical** (both unresolved Tier-1 criticals are 11d and 6d old).
- ⚠️ **The population is NARROWER than the baseline's, which makes the regression
  stronger, not weaker** — the 2026-08-13 zero spanned three backlogs; 85 is one
  of them.
- Oldest: `BL-20260730-2D-VOL-CELLS-UNAUDITABLE` at **49 days**.
- **66 of the 85 carry no date anywhere in the row later than 2026-09-03** — not
  touched, updated or re-verified since filing.

**The sharp reading: the 2026-08-13 rules fixed ADMISSION and did nothing about
DISPOSAL.** Every admission metric improved dramatically on this backlog —
no-`severity` 44% → **1.2%**, no-`tier` 24% → **3.3%**, no-`resolution_criteria`
38% → **3.1%** — while the one metric the rules called the diagnosis went 0 → 85.
**All 208 Tier-1 critical/high rows are workable by the rules' own definition,
and 85 have sat >14 days anyway.** Throughput, not workability, is now the
binding constraint, and the canonical doc still reads as though it is not.

⚠️ **This is a finding about a canonical document, so it is NOT fixed here.**
Correcting `CLAUDE-RULES-CANONICAL.md` with today's measurement is a Tier-1 doc
edit, but it changes the stated diagnosis of a governance regime — that belongs
in front of the operator, not in a triage's own diff.

**Wider age context (population = 928 unresolved):** median **16d** · p90 **38d**
· max **115d** · **536 of 928 (57.8%) older than 14d** · 207 >30d · 18 >60d.

---

## 2. THE CHUNKS

21 classes over the 330 unresolved critical+high rows, derived by reading all 330
`id`+`title` strings. **The partition is complete and verified: 330 indices, 330
unique, 0 missing, 0 duplicated.**

| # | class | n | crit/high | tier mix | oldest | median age | workable |
|---|---|---|---|---|---|---|---|
| 1 | MEASUREMENT | 47 | 1/46 | T1=34 T2=12 T3=1 | 42d | 15d | 34 |
| 2 | RESEARCH-FIDELITY | 40 | 1/39 | T1=27 T2=1 T3=10 T?=2 | 49d | **28d** | 27 |
| 3 | CI-LANDING | 26 | 0/26 | T1=26 | 31d | 13d | **26 (100%)** |
| 4 | MANAGER-SESSION | 25 | 0/25 | T1=25 | 16d | 12d | **25 (100%)** |
| 5 | ORDER-PATH | 23 | 3/20 | T1=1 T2=18 T3=4 | 34d | **25d** | 1 |
| 6 | DOC-CARRIAGE | 18 | 1/17 | T1=17 T?=1 | 48d | 14d | 17 |
| 7 | REGISTER-MERGE | 15 | 0/15 | T1=15 | 15d | 6d | **15 (100%)** |
| 8 | AUTOMATION-CADENCE | 15 | 1/14 | T1=15 | 21d | 6d | **15 (100%)** |
| 9 | GUARD-DEFECT | 14 | 0/14 | T1=14 | 17d | 8d | **14 (100%)** |
| 10 | JOURNAL-VENUE | 14 | 3/11 | T1=2 T2=12 | 47d | 9d | 2 |
| 11 | ALERTING | 13 | 1/12 | T1=7 T2=6 | 33d | 14d | 7 |
| 12 | ROUTING-ARBITRATION | 12 | 2/10 | T1=1 T2=4 T3=7 | 38d | 18d | 1 |
| 13 | PROP | 11 | 1/10 | T1=2 T2=6 T3=3 | 28d | 20d | 2 |
| 14 | COLLAPSED-STATE | 10 | 0/10 | T1=6 T2=4 | 34d | 13d | 6 |
| 15 | INFRA-TRAINER | 10 | 0/10 | T1=7 T2=3 | 34d | 23d | 7 |
| 16 | EXIT-CAPABILITY | 9 | 2/7 | T1=2 T3=7 | 38d | **27d** | 2 |
| 17 | ACCOUNT-VIABILITY | 8 | 0/8 | T2=2 T3=6 | 27d | **25d** | **0** |
| 18 | COVERAGE-DETECTION | 7 | 1/6 | T1=1 T2=6 | 49d | 11d | 1 |
| 19 | NO-CONSUMER | 5 | 0/5 | T1=1 T2=2 T3=2 | 32d | 25d | 1 |
| 20 | SECURITY | 5 | 0/5 | T1=2 T2=3 | 25d | 14d | 2 |
| 21 | STRATEGY-EDGE | 3 | 1/2 | T1=3 | 6d | 6d | 3 |

*"workable" = Tier-1 AND carries `resolution_criteria` AND not actively snoozed.*

### What a session picking up each chunk would actually do

1. **MEASUREMENT (47)** — *instrument defects: unprovenanced diagnostics, wrong denominators, tail-only reads.* → Fix the instruments the fleet is judged by, starting with those whose output is read as a verdict: `rCoverage` publishing 1.0 over 90% unverified rows, `totalPnlMeasured` summing estimated rows, `/api/bot/logs` returning a 13-millisecond window that reads as a clean negative.
2. **RESEARCH-FIDELITY (40)** — *the offline measurement does not describe the live system, or a gate can never fire.* → Re-establish what the M20/M7/promotion evidence says before anything is promoted on it. **The oldest and most stalled class** (median 28d; 20 of the 85-row regression).
3. **CI-LANDING (26)** — *a correctly-authored PR cannot land, or lands with no CI.* → Pick ONE landing path and make it work end to end; today the two binding rules for opening a PR contradict each other and a correctly-followed Tier-1 self-landing PR can never self-land.
4. **MANAGER-SESSION (25)** — *lease, registry, spawn, push, decision round-trip.* → Make the session registry say something true: it has a spawn writer and no close writer, so every row reads `working` forever.
5. **ORDER-PATH (23)** — *protective legs placed/amended/cancelled against the wrong scope.* → Make Alpaca and IB protective operations **trade-scoped rather than symbol-scoped**. Three criticals; one has already moved a sibling's resting stop to the wrong declared level on a live book.
6. **DOC-CARRIAGE (18)** — *a finding is filed correctly and reaches nobody.* → Give filed work a carrier. **This class is the meta-cause of the 85-row regression above.**
7. **REGISTER-MERGE (15)** — *a shared register silently loses rows on a merge, with valid output and green guards.* → Bind every register to the row-aware driver and make loss detectable server-side; a squash merge cannot run the local driver at all.
8. **AUTOMATION-CADENCE (15)** — *a scheduled workflow never fires, never succeeds, or produces its artifact and fails to LAND it.* → Fix the shared `commit-to-main` landing step first — one row names it *the largest single cause of discarded automation output*.
9. **GUARD-DEFECT (14)** — *a guard passes vacuously, grades the wrong object, or reddens a PR that did nothing wrong.* → Audit each guard against a planted positive control. **Now 15 with this session's own find** (`…PR-LANDING-R14-GRADES-AN-UNCOMMITTED-TREE-AS-AN-EMPTY-DIFF…`).
10. **JOURNAL-VENUE (14)** — *journal disagrees with the venue.* → Real money is here (3 criticals, 12 of 14 Tier-2); start with the settleCoin rows where a live real-money hedge book is never fetched and sits naked.
11. **ALERTING (13)** — *a page reaches nobody, or an alarm fires so constantly it is walked past.* → Cut the noise floor (91% of the operator ERROR channel is standing repeats) and then wire the pages that reach nobody.
12. **ROUTING-ARBITRATION (12)** — *a live leg never receives an order package.* → Establish per account which legs are electable and which are starved.
13. **PROP (11)** — *tickets, cushion, DD floor, report-back.* → Give `expiry_prompted` a timeout (one unanswered prompt wedged the sleeve for 12 days).
14. **COLLAPSED-STATE (10)** — *a field cannot express "we did not look".* → 14 read routes still return a bare `[]` from an `except` handler.
15. **INFRA-TRAINER (10)** — *VM/gateway/trainer/latency.* → The 60s exit-evaluation promise is breached on the durable record — settle whether the requirement or the system moves.
16. **EXIT-CAPABILITY (9)** — *no decision-driven exit exists, or the declared geometry is unreachable.* → Tier-3-heavy (7 of 9); prepare the operator decision on whether 22 of 34 open trades may only close at a level fixed at entry.
17. **ACCOUNT-VIABILITY (8)** — *an account is declared live and structurally cannot trade.* → Resolve `alpaca_live` as ONE question rather than six. **0 of 8 workable — this chunk is entirely an operator decision.**
18. **COVERAGE-DETECTION (7)** — *a naked or over-covered position reads as covered.* → Make coverage a per-ROW quantity on both sides on all three venues.
19. **NO-CONSUMER (5)** — *a signal is written and nothing reads it.* → Wire or delete.
20. **SECURITY (5)** — *unauthenticated exposure, data to third parties.* → 21 tables (2.3M signal rows) world-readable on a public host. **See § 5 — this session measured a NEW instance.**
21. **STRATEGY-EDGE (3)** — *the live edge itself.* → Attribute the 2026-08-30 regime break. **This is the named cycle priority's subject, and the operator found it, not a monitor.**

Full id lists per class (with `severity/tier/age`) are in this session's
measurement record; every id is FULL, never truncated.

---

## 3. THE SHAPE THAT MATTERS — TWO PILES, NOT ONE

**Five classes are 100% Tier-1 workable: CI-LANDING (26) · MANAGER-SESSION (25) ·
AUTOMATION-CADENCE (15) · REGISTER-MERGE (15) · GUARD-DEFECT (14) = 95 rows a
session may act on alone, today, with no operator in the loop.**
Every one of them is **the machinery the repo uses to do work** — not the trading
system.

Conversely the trading-side classes are almost entirely **not** session-workable:
ORDER-PATH 1 of 23 · JOURNAL-VENUE 2 of 14 · ROUTING-ARBITRATION 1 of 12 · PROP 2
of 11 · EXIT-CAPABILITY 2 of 9 · COVERAGE-DETECTION 1 of 7 · **ACCOUNT-VIABILITY
0 of 8**.

**122 of the 330 are Tier-2/Tier-3 and are gated on an OPERATOR DECISION, not on
session capacity — including 14 of the 18 unresolved criticals.** A drain session
cannot touch them; what they need is a decision packet.

⚠️ **Reporting these as one 330-row number hides the only thing about the backlog
that determines what to do with it.** The 95-row pile needs throughput. The
122-row pile needs the operator, and no amount of session throughput will move it.

---

## 4. TWO SCHEMA FINDINGS FOUND WHILE COUNTING

**(a) 6 of 6 active snoozes have silently expired.** `snoozed_until` appears as a
key on 295 rows but **287 carry `null`**; only **8** carry a real date, **6 of
them unresolved, and all 6 dates are in the past** (2026-08-22 → 2026-09-15).
Those rows came back due with nothing announcing it:
`BL-20260621-ACCOUNT-HISTORY-PULL` ·
`BL-20260820-DELEGATE-LINE-CITATIONS-UNMEASURED-AFTER-NUMBERING-FIX` ·
`BL-20260820-EXCHANGE-FILLS-IB-DELEGATE-FINDINGS-NOT-DATA-VERIFIED` ·
`BL-20260820-DIAG-FETCH-CANONICAL-BASE-VERIFIED-FROM-ONE-SESSION-ONLY` ·
`BL-20260820-OCI-INVENTORY-ISSUE-PATH-CANNOT-REQUEST-FAIL-ON-DRIFT` ·
`BL-20260820-DOC-FRESHNESS-HAND-GREPS-ARE-NOISIER-THAN-THE-CI-CHECKER`.

**(b) The snooze field is itself a collapsed state.** A row never snoozed and a
row whose snooze was explicitly cleared are byte-identical (`snoozed_until:
null`), and an expired snooze is indistinguishable from an active one without
arithmetic no consumer does. The defer path added in 2026-08 has quietly become
another way for a row to disappear.

**18.5% of the crit+high pile declares itself a recurrence** — 61 of 330 rows
carry `RECURRENCE` / `RECURRED` / `recurrence_of` / "third occurrence" in their
own text. Concentrated in CI-LANDING (10), MANAGER-SESSION (7), DOC-CARRIAGE (5),
COLLAPSED-STATE (5), MEASUREMENT (5), REGISTER-MERGE (5).

---

## 5. WHAT THIS SESSION ADDED TO THE PILE

Filing is the act of NOT taking the item, and these were found while measuring
rather than by going looking:

| id | class | severity |
|---|---|---|
| `BL-20260917-THE-WIP-CEILING-IS-FULL-AND-SIX-OF-SIX-GRADEABLE-IN-FLIGHT-OWNERS-ARE-DEAD-SESSIONS-SO-A-NEW-LANE-CANNOT-RECORD-ITSELF` | MANAGER-SESSION | high |
| `BL-20260917-THE-WORK-DECISION-SWEEP-REPORTS-ITSELF-HEALTHY-AND-ROUTED-WHILE-FAILING-TO-SEND-EVERY-CANDIDATE-ON-47-OF-47-RUNS` | ALERTING | **critical** |
| `BL-20260917-PR-LANDING-R14-GRADES-AN-UNCOMMITTED-TREE-AS-AN-EMPTY-DIFF-AND-ON-THAT-EMPTY-DIFF-TELLS-THE-AUTHOR-TO-SELF-LAND-WORK-IT-NEVER-EXAMINED` | GUARD-DEFECT | high |

---

## 6. THE THREE THINGS FOR THE OPERATOR

1. **The Tier-1 >14d count went 0 → 85** against a canonical doc that still calls
   that number "the diagnosis" and still says workable rows get fixed reliably.
   `CLAUDE-RULES-CANONICAL.md` § "Backlog governance" is now **stale in the
   reassuring direction**.
2. **The backlog has split into two piles needing different treatment** — 95 rows
   of self-inflicted process/CI machinery a session can fix alone right now, and
   122 Tier-2/3 rows (14 of 18 criticals) that need a decision.
3. **6 of 6 active snoozes have expired** with nothing announcing it, and the
   field cannot distinguish "never snoozed" from "snooze cleared".

---

## 7. HOW TO PICK UP A CHUNK

1. Take a class from § 2 and read its one-line description.
2. Filter the backlog to that class's ids, `status` in {`open`, `kept_open`}.
3. If it is one of the five 100%-workable classes, a session may act alone —
   start with the oldest.
4. If it is a Tier-2/3-heavy class, the deliverable is a **decision packet**, not
   a fix. Do not open it expecting to close rows.
5. ⚠️ **Age is why a row is in front of you, never why you close it**
   (`CLAUDE-RULES-CANONICAL.md` § Backlog governance, rule 6). Check before
   assuming still-broken *and* before assuming fixed.
