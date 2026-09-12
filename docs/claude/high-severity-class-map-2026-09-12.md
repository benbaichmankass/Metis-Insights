# A candidate class map for the 274 open `high` backlog rows

> **Doc status:** `live` · category `evidence` · last verified `2026-09-12` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)

**MI-279 (OPS lane, `/backlog-drain`), `session_013iSqp4LsU1eq8K326eUtgj`, 2026-09-12.**
Produced as the opening move of U3 (class-first on the `high` rows) and handed
forward **unfinished, on purpose** — see § 4.

---

## 1. Why a class map at all

The `backlog-drain` skill is explicit that one-at-a-time closing has never once
caught up: measured opened/closed at 43/8, 231/94, 249/175, 536/326, and
**September 2026 stands at 186/34** across the three backlogs against 965 open.
**Fixing CLASSES is the only move whose arithmetic beats the filing rate.** This
is the first attempt to name the classes over the `high` population.

## 2. The candidate clustering

**POPULATION:** the **274** rows with `status: open` and `severity: high` in
[`docs/claude/health-review-backlog.json`](health-review-backlog.json),
2026-09-12. Rows may match more than one class.

| n | candidate class |
|--:|---|
| 71 | **F.** stale prose vs field — a doc/comment disagrees with the code or config |
| 67 | **G.** provenance — a number is fabricated, estimated, or unmeasured |
| 66 | **C.** collapsed state — the field cannot say *"we did not look"* |
| 26 | **D.** artifact computed, then lost at the landing step |
| 24 | **E.** a detector or guard exists but is inert / unexercised |
| 21 | **A.** deployed but never observed on the fleet |
| 12 | **B.** written and never read — a signal with no consumer |

**179 of 274 (65%) match at least one class; 95 match none.**

## 3. ⚠️ THE PRECISION IS ~67% AT n=6, AND THIS IS A TOKEN PROBE, NOT A VERDICT

**This clustering is keyword matching over title + summary + detail.** The skill
records that exactly this kind of probe was *"almost entirely wrong"* the first
time it was built — it claimed 108 of 542 rows and a six-row hand check found
~none closable. **So this map is NOT reported as a set of verified classes.**

I hand-checked **6 rows sampled from class C** (seed fixed, so it is
reproducible):

| row | verdict |
|---|---|
| `BL-20260830-BROKER-TRUTH-LEDGER-STALE-59-REAL-MONEY-CLOSES-UNRECONCILED` — *"a consumer cannot tell that window from the measured one"* | **true positive** |
| `BL-20260909-UNDER-NETTING-A-JOURNAL-ROW-CAN-BE-MASKED-BY-A-SIBLINGS-PROTECTIVE-LEG-AND-NOTHING-GRADES-PER-ROW-COVERAGE` — coverage graded per SYMBOL, never per ROW | **true positive** |
| `BL-20260908-DOCUMENT-INDEX-WRITE-STAMPS-A-FALSE-LAST-VERIFIED-ON-436-DOCS-AND-THE-GUARD-TELLS-YOU-TO-RUN-IT` — a false stamp collapses *verified today* with *never verified* | **true positive** |
| `BL-20260906-PROP-TICKET-RISK-SOAK-READS-ANNOTATE-AND-IS-SEVEN-DAYS-STALE-WHILE-THE-REGISTER-SAYS-ENFORCE-IS-ARMED` — collapses *not armed* with *not written* | true positive (weaker) |
| `BL-20260816-CORPUS-CONFLICT-REDERIVE-RUNS-THE-STALE-BRANCH-EXTRACTOR` — green-while-dropping-fields; belongs in **D/E** | **FALSE POSITIVE** |
| `BL-20260818-XRP-PULLBACK-LEG-REJECTS-EVERY-DECISION-EXIT-LEVER` — a strategy-quality finding; matched an incidental token | **FALSE POSITIVE** |

**≈4 of 6 → precision ~67%, n=6.** That is a *tiny* sample on *one* class and
says nothing about F or G, whose patterns (`stale`, `provenance`) are looser and
are likely **worse**. **Recall is entirely unmeasured** — the 95 unmatched rows
were not read, and some certainly belong to a class the patterns miss.

**Use this to choose where to look. Do not quote the counts as findings.**

## 4. What the next session should do with it, in order

1. **Do not widen the patterns.** Precision is already the weak axis; a broader
   regex buys recall it cannot verify. Hand-check instead.
2. **Start with D (26) and E (24), not with F or G.** They are the smallest, the
   most structural, and the ones where a *single* fix plausibly retires many
   members — D in particular, since this session already found and fixed **two**
   distinct instances of it (a raw push at protected `main`, and the shared
   `commit-to-main` action refusing gitignored paths) inside one morning, and
   those were closed as `BL-20260911-REPLAY-PREGATE-NIGHTLY-HAS-ZERO-SUCCESSFUL-RUNS-IN-ITS-ENTIRE-LIFE-AND-25-MONTHS-OF-ML-PREGATE-EVIDENCE-HAS-ACCRUED-TO-NOTHING` part (2) and a **third
   sub-cause** of `BL-20260911-THE-SHARED-COMMIT-TO-MAIN-LANDING-STEP-IS-THE-LARGEST-SINGLE-CAUSE-OF-DISCARDED-AUTOMATION-OUTPUT`.
3. **For each candidate class, hand-verify ≥2 members before proposing a
   structural fix** — that is the skill's own bar (`>=2 verified members and a
   structural fix`), and the false positives above show why the probe cannot
   supply it.
4. **F and G are probably not single classes at all.** "Stale prose" and
   "provenance" are *properties* a row can have, not shared root causes, so a
   single fix is unlikely to retire either. Expect to split them before acting.

## 5. What this document is NOT

- ⚠️ **Not a verified class list.** §3 is the whole caveat and it is load-bearing.
- ⚠️ **Not a to-do list.** No row here was dispositioned by this document; the
  rows MI-279 actually dispositioned are the 20 `critical` ones, recorded in
  their own `notes`.
- ⚠️ **Not a claim that 65% of the `high` backlog is classifiable.** the 179-of-274 figure is the
  share the *patterns matched*; at the ~67% precision measured on 6 hand-checked
  rows it is an overestimate of the share genuinely in a class.
