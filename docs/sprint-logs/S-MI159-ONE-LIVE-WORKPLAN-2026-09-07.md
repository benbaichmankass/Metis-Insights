# S-MI159-ONE-LIVE-WORKPLAN-2026-09-07

> **Doc status:** `historical` · category `history` · last verified `2026-09-09` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md)

**Sprint ID:** `S-MI159-ONE-LIVE-WORKPLAN-2026-09-07`
**Session:** `session_016szw9vJrzPUCH1FVMuXycM` (work session, manager `session_01HrmZ1RRNM4UnEUaFdrPEjj`)
**Work object:** `WO-20260907-ONE-LIVE-WORK-PLAN-A-ROADMAP-THAT`
**Branch:** `claude/mi159-one-live-workplan` · **PR:** #11241
**Tier:** 1 throughout. No `config/`, no `src/runtime/`, no order path, no strategy parameter.

---

## 0. Why this session existed

A manager session gave the operator a roadmap status read that was **wrong**, because it
reasoned from `docs/research/WORKPLAN-2026-08-14.md` — a document still declaring
`Status: ACTIVE` while three later generations of plan had superseded it in practice. The
operator caught it, and asked for three things.

⚠️ **The work object was NOT on `origin/main`** (`git cat-file -e` fails). Already filed;
this session worked from the dispatch brief and did not re-diagnose it.

---

## 1. What was verified, and where the brief was wrong

Every claim in the dispatch brief was re-checked, per this repo's rule that a previous
session's check is not verification. **Three did not survive.**

| brief claim | verdict | what the field says |
|---|---|---|
| TWELVE work-plan documents | ❌ **wrong** | **THIRTEEN.** Population: `git ls-files \| grep -iE "workplan\|work-plan"` = 24 paths, −10 `docs/sprint-logs/`, −1 `.claude/skills/` = 13. Both `docs/workplan.md` and `docs/claude/workplan.md` exist (211 and 981 lines); the count saw one. |
| the chain is `08-14 → -08-21 → -08-26 → -08-29` | ⚠️ **partly unfounded** | The `08-21 → 08-26 → 08-29` half is documented in each successor's own header. The **`08-14 → 08-21` link is written nowhere**: `WORKPLAN-2026-08-21.md` contains **zero** occurrences of `08-14`. **That absence IS the incident** — the successor took over without naming what it replaced. |
| `ROADMAP.md` § "Next" points at the stale 08-14 | ✅ **confirmed** | Line 227, verbatim: *"read `WORKPLAN-2026-08-14.md` first"*. |
| `CYCLE-PRIORITY.json` is newer than every work plan | ✅ **confirmed** | `updated_at 2026-09-06T10:05:00Z` vs the newest plan's declared 2026-08-29. ⚠️ Established from **content**, not git dates — this is a `--depth 1` clone, so every file's commit date is the clone date and git history proves nothing here. |
| MI-155: all 44 legs `insufficient_n`, max live n 8 vs floor 30 | ✅ **confirmed** | `docs/research/exit-location-fidelity-2026-09-07.md`. ⚠️ Minor: the artifact says `n_bt = 0` on **every row of its table**, not "41 of 44". |
| the `/api/bot/performance` contradiction | ✅ **reproduces exactly** — ❌ **but its named cause does not** | See § 4. |
| `journalTrust` flags on both real-money accounts | ✅ **confirmed live** | `accountsKnownDivergent: ["bybit_2"]`, `accountsUnrecorded: ["alpaca_live"]`. |

**A fourth finding the brief did not contain:** **TWO** documents declared themselves live,
not one. Besides 08-14, `docs/research/POST-VALUE-PIVOT-WORKPLAN-2026-07-27.md` also read
`Status: ACTIVE`. So the guard below had a **real** positive on the day it was written.

---

## 2. Deliverable 1 — exactly one live work plan

**Live plan: `docs/claude/WORKPLAN-2026-08-29.md`.** Established from the documents:
08-29 supersedes 08-26; 08-26 replaces 08-21; nothing claims to supersede 08-29; and
`WORKPLAN-NIGHT-2026-08-29` states in its own header that it is subordinate and *"does not
supersede it"*.

**Census, with the terminal status each document now carries in its own header:**

| document | status | basis |
|---|---|---|
| `docs/claude/WORKPLAN-2026-08-29.md` | **`live`** | plan of record |
| `docs/claude/WORKPLAN-2026-08-26.md` | `superseded` | named by 08-29 |
| `docs/claude/WORKPLAN-2026-08-21.md` | `superseded` | named by 08-26 |
| `docs/claude/WORKPLAN-NIGHT-2026-08-29.md` | `closed_unfinished` | an addendum whose one-night window closed 9 days ago |
| `docs/claude/workplan.md` | `historical` | self-declared superseded 2026-05-10 (S-CANON-1) |
| `docs/workplan.md` | `historical` | same, different file |
| `docs/research/AUTONOMOUS-WORKPLAN-2026-07-30.md` | `superseded` | self-declared, by `RESEARCH-PROGRAM-2026-07-30.md` |
| `docs/research/POST-VALUE-PIVOT-WORKPLAN-2026-07-27.md` | `closed_unfinished` | was declaring ACTIVE |
| `docs/research/ROADMAP-REVIEW-WORKPLAN-2026-08-04.md` | `closed_unfinished` | its successor explicitly disclaims superseding it |
| `docs/research/WORK-PLAN-2026-08-02.md` | `closed_unfinished` | no successor names it |
| `docs/research/WORKPLAN-2026-08-05.md` | `closed_unfinished` | no successor names it |
| `docs/research/WORKPLAN-2026-08-14.md` | `closed_unfinished` | **the incident document** |
| `docs/research/WORKPLAN-desoak-…-2026-07-26.md` | `closed_unfinished` | no successor names it |

**`closed_unfinished` is kept distinct from `superseded` and the distinction is enforced.**
The operator asked for this specifically: a plan whose work was *overtaken* is not the same
as one whose work was *abandoned*, and collapsing them loses what was left undone. Guard
rule **R4** fails a `closed_unfinished` plan that does not say what was left.

**Residuals, where verified:**
- **`WORKPLAN-2026-08-14.md` → Lane 0 only.** Lanes 1–3 delivered; **Lanes 4 and 5 were
  planning errors** — both already shipped when the plan was written, which is exactly
  `BL-20260814-ROADMAP-QUEUED-LISTS-CONTRADICT-SHIPPED-LINES`.
- **`POST-VALUE-PIVOT-2026-07-27` → both tracks**, Track 2 blocked on its one stated
  operator hand-off (Schwab keys).

Where a residual was **not** audited, the header says so. *"We did not look"* is an
accepted answer under R4; **silence is not**.

### The detector — `scripts/ci/check_one_live_workplan.py`

Six rules, **12 planted controls**, registered in `run_guards.GUARDS` (**ungated** — the
failure is a document going stale while nothing touches it, which a diff-scoped guard
cannot see) and in `guard_selftests.COVERED_BY_CHECKER`, which `check_selftest_wiring.py`
**verifies rather than trusts**.

R1 exactly one `live` (**zero is a separate failure**, not folded in) · R2 every discovered
plan declares a status — so a *new* plan document fails until it does · R3 `superseded`
names a successor that exists · **R4** the residual rule · R5 closed vocabulary, with the
token charset admitting `-` so a plausible typo gets the R5 message rather than a confusing
"no status" · R6 a terminal plan must not still carry a legacy `**Status:** ACTIVE` line,
with the struck-through form deliberately accepted so the record survives.

⚠️ **Proven on a real positive, not only on plants:** the guard **failed on the real tree**
before the headers were applied (13 × R2). Its **negative control runs first** and caught a
staging bug in its own fixtures the day it was written. Two further controls assert the
*honest* answers pass — an unaudited residual, and a struck-through `ACTIVE`.

---

## 3. Deliverable 2 — `ROADMAP.md` matches reality, or says it was not checked

- **§ "Next" repointed** at the live plan and the task ranking, and now records that
  `CYCLE-PRIORITY.json` is newer than every work plan and sits above them.
- **M15 corrected — 71 days stale, in the dangerous direction.** Newest dated claim was
  2026-06-28, so it read as though Alpaca real money was not routed. `config/accounts.yaml`
  (read from the field): `alpaca_live` is `mode: live` / `real_money` /
  `strategies: ['tlt_pullback_1h']`. And `MI-140` records the leg **cannot place**.
- **M20 corrected — 13 days stale**, missing the whole 2026-09-06→07 exit-mechanics thread
  (MI-146→158): **25 of 44** legs with no reachable take-profit; **all 44** grade
  `insufficient_n`; **0 of 44** had a backtest read before going live.
- **The other 31 rows are marked `UNVERIFIED`** — not guessed at, not left blank.
- **Measured for all 33 rows:** 18 carry a newest internal date ≥30 days old (median 42);
  **4 carry no date at all**. Staleness is explicitly stated as a *triage signal*, not as
  inaccuracy — it produced both corrections and some false leads.
- **Partly corrects the live plan's Lane S3** (*"M6/M9/M10 carry no date at all"*): M9 does
  carry one; the undated set is M0–M5, M6, M10, M17.

⚠️ **`BL-20260814-ROADMAP-QUEUED-LISTS-CONTRADICT-SHIPPED-LINES` CANNOT be closed.** Both
instances it names are **still present** — M30's *"Queued loose-ends"* and M29's *"P1b
remaining"*. Not fixed here, so it stays open.

---

## 4. Deliverable 3 — TASK-level priority, and a corrected diagnosis

[`docs/claude/TASK-PRIORITY-2026-09-07.md`](../claude/TASK-PRIORITY-2026-09-07.md) ranks
**16 tasks** anchored to `CY-20260906-TRADING-TRUTH`. Four reach into tracks we are not
funding (M31, M24, M40, M15). **M21 is recorded as a null result** — looked at, nothing
this cycle needs, which is a different statement from *we did not look*. **No milestone is
recommended for closure, pruning or consolidation**, and the document says so explicitly.

### The measurement contradiction — the cause was wrong

**MEASURED live**, `GET /api/bot/performance`, HTTP 200. **The population is the `paper`
sub-block, n = 872** — the top-level real-money block (n=424) reads `totalPnl −69.53` and
`expectancyR −0.3153`, **consistent in sign, no contradiction there at all.**

| paper (n=872) | value |
|---|---|
| `totalPnl` | **+$120,790.89** |
| `expectancyR` | **−0.1341** |
| `rCoverage` | **1.0** |
| `pnlCoverage` | **0.2041** |
| `rBasis.refusedWrongSide` | **0** |
| R contaminated | 91/872 = **10.4%** |

Both reproduce arithmetically, so neither is a computation bug. **The concentration is the
finding:** `ict_scalp_mgc_15m`, **n = 6**, carries **+$231,532.00** — **192% of the entire
headline** — on an utterly ordinary **+2.16R**. Top 10 legs = 20.0% of rows, 127% of the
headline.

⚠️ **The standing diagnosis (R contamination, `MI-30`/`MI-144`) is wrong, and wrong in the
direction that matters.** `refusedWrongSide` is **0**; R contamination is 10.4% at
`rCoverage` **1.0**. **`expectancyR` is the SOUNDER instrument, and the gates read it.**
The **dollar** figure is the broken one — 20.4% measured, dominated by a futures multiplier
on six rows. Same class `CLAUDE.md` documents for the +$284k orphaned `ib_paper` rows.
Ranking it as an R defect would have pointed a session at the instrument that works.

⚠️ **This does NOT refute `MI-30`**, whose 17.2% is a claim over *all closed rows* — a
different population, not reproduced here. It stays open on its own terms.

---

## 5. Infrastructure finding — the coordination board is dead

Filed **`BL-20260907-COORDINATION-BOARD-6927-IS-FULL-SO-THE-MANDATORY-START-POST-IS-IMPOSSIBLE-BY-EVERY-PATH`** (high).

The mandatory `▶️ START` post could not be made **by any documented path**:
1. **MCP** `add_issue_comment` → `403 Resource not accessible by integration` (the
   documented write-scope boundary, not the transient drop).
2. **`board-post.yml` relay** → ran, and wrote:
   `FAILED: GraphQL: Commenting is disabled on issues with more than 2500 comments`.

**The relay is not the defect — it failed loudly and failed the run, exactly as designed.**
Issue #6927 has crossed GitHub's hard 2500-comment ceiling. Every concurrent session is now
structurally unable to comply with a binding rule, and the collision-avoidance mechanism the
2026-07-22 incident produced has no detector left. Strictly worse than
`BL-20260901-COORDINATION-BOARD-WRITES-403-FROM-THIS-SESSION-WHILE-READS-SUCCEED`, which at least had a working relay as its
remedy. Remedies are proposed in the row; none applied here (out of Tier-1 docs scope, and
one is an operator call).

---

## 6. Verification performed

- `check_one_live_workplan.py --self-test` → **12/12 controls pass**.
- `check_one_live_workplan.py` on the real tree → **fails before** the headers, **passes
  after**.
- `check_selftest_wiring.py` → *"All registered self-tests resolve to a covering path that
  was verified, not assumed."*
- `run_guards.py --only one-live-workplan --all` → **PASS**.
- `check_pr_landing.py --base origin/main` → **`OK — state=declared_self_land`** (both
  files present: `.github/pr-landing/…json` and `.github/pr-automerge-requests/…txt`).

## 7. Explicitly NOT done

- The 31 `UNVERIFIED` roadmap rows — ranked as **T-11**, not silently graded.
- `BL-20260814-ROADMAP-QUEUED-LISTS-CONTRADICT-SHIPPED-LINES` — **not closed**; both its instances still stand.
- The coordination-board remedy — **filed, not applied**.
- `MI-30`'s all-closed-rows population — **not reproduced**.
- Residual audits for 5 of the 7 `closed_unfinished` plans — marked *unaudited* in their
  own headers rather than guessed at.
