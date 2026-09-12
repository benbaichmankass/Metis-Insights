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

---

## 6. HAND-VERIFICATION RESULT (added 2026-09-12, same session): these are SYMPTOM classes, and a symptom class does not yield a shared fix

§4 told the next session to *"start with D and E"* and to *"hand-verify ≥2 members before
proposing any structural fix"* — the `backlog-drain` skill's own bar of *">=2 verified
members and a structural fix"*. **That was done. The bar is met on membership and FAILS on
the structural fix, and that is the finding.**

### What was verified

**Class D** — *artifact computed, then lost at the landing step*. Two members read in full:

| row | true positive? | root cause |
|---|---|---|
| `BL-20260911-SUNSET-PASS-PUSHES-STRAIGHT-AT-PROTECTED-MAIN-SO-ITS-ONLY-SCHEDULED-RUN-COULD-NOT-LAND-AND-IT-WILL-FAIL-IDENTICALLY-EVERY-MONDAY` | ✅ | a push at a branch-protected `main` (GH006) |
| `BL-20260909-COMMIT-TO-MAIN-CANNOT-OPEN-A-PR-WHEN-THE-SHARED-USER-API-QUOTA-IS-EXHAUSTED-SO-EVERY-RECEIPT-STRANDS-WITH-NO-PR` | ✅ | a shared per-USER GraphQL rate limit at `gh pr create` |

**Class E** — *a detector or guard exists but is inert / unexercised*. Two members read in full:

| row | true positive? | root cause |
|---|---|---|
| `BL-20260908-ALPACA-PAPER-OCO-GROUP-IS-INERT-A-STOP-13-POINTS-IN-THE-MONEY-HAS-NEVER-FIRED` | ✅ | the coverage predicate is a BOOLEAN over resting legs, so it cannot see that the VENUE has stopped evaluating the leg — nothing models order **liveness** |
| `BL-20260823-ALPACA-LIVE-IS-EXEMPT-FROM-BOTH-DETECTORS-THAT-WATCH-IT` | ✅ | the detectors work; the account is on **both** skip-lists, and its latch is frozen |

**4 of 4 are true positives for the SYMPTOM. 0 of 4 pairs share a ROOT CAUSE.**

### Why that matters more than the precision number

`BL-20260911-SUNSET-PASS-PUSHES-STRAIGHT-AT-PROTECTED-MAIN-SO-ITS-ONLY-SCHEDULED-RUN-COULD-NOT-LAND-AND-IT-WILL-FAIL-IDENTICALLY-EVERY-MONDAY` says it in terms about its own sibling: *"filed separately
rather than folded in, **because fixing one does not fix the other** and a single row would
hide the second."* The row author had already reached this conclusion; the clustering in §2
re-merged what they had deliberately separated.

**So the classes above are groupings by what the failure LOOKS LIKE, not by what causes it.**
A drain that picks a symptom class and looks for one fix will not find one — and the risk is
worse than wasted effort: it invites a fix that *appears* to retire the class while leaving
most members untouched.

### What DID work, and it is the pattern to repeat

The one structural fix this exercise produced came from going **one level below** class D, to
a root-cause sub-class: *a workflow that pushes at a protected branch*. That sub-class has
**eight** historical members (`session-reaper`, `research-queue-dispatch`, `gpu-burst-train`,
`reconcile-open-prs`, `m20-exit-lever-sweep`, `trainer-offload-train`,
`replay-pregate-nightly`, `sunset-pass`), **seven already fixed one at a time**, and it is
mechanically checkable — so it earned a guard (`scripts/ci/check_workflow_push_target.py`)
rather than a ninth careful fix.

⚠️ **And the fix it earned is a GUARD, not a mass edit.** The class was already nearly
drained; what was missing was anything stopping the ninth instance. **That is the realistic
shape of a class-first win here** — not *"one fix closes nine rows"*, but *"one guard stops
the class recurring after the rows are closed one at a time anyway."*

### Instruction to the next session, replacing §4 step 2

1. **Do NOT pick a symptom class and hunt for its single fix.** Verified 4/4 above that they
   do not have one.
2. **Sub-classify by ROOT CAUSE first**, then ask whether the sub-class is *mechanically
   checkable*. If it is, the deliverable is a guard. If it is not, the rows are individual
   work and should be drained oldest-first.
3. **A root-cause sub-class with a long fixed-one-at-a-time history is the strongest
   candidate**, because the recurrence is already evidenced and the fix is already known —
   only the enforcement is missing. That is `CLAUDE.md`'s own *"a recurrence is machinery,
   not another reminder."*

⚠️ **Also do not re-quote §2's counts.** Re-deriving them the same day from this document's
own prose, with independently written patterns, gave **D 16 (not 26)** and **E 43 (not 24)**.
The counts are not reproducible from the class descriptions, which is a stronger reason to
distrust them than the ~67% precision §3 already records.
