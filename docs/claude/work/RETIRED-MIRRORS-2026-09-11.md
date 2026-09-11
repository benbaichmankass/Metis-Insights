# Retiring the work-store backlog mirrors — `DEC-20260911-WORK-STORE-BACKLOG-MIRRORS`

> **Doc status:** `unknown` · category `unknown` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**Operator decision, answered 2026-09-11T05:55Z: `retire_mirrors`.** Recorded as the `answer:`
block on
[`WO-20260911-SWEEP-THE-623-DORMANT-WORK-OBJECTS-AND.yaml`](objects/WO-20260911-SWEEP-THE-623-DORMANT-WORK-OBJECTS-AND.yaml),
which is the authority for the scope below. This file is the **audit trail** for enacting it,
so a later session can **check the rule rather than inherit an unexplained state change**.

Companion to [`DORMANT-SWEEP-2026-09-11.md`](DORMANT-SWEEP-2026-09-11.md), which is the sweep
that produced the question.

---

## 0. ⚠️ What this is NOT — four things, each stated in the operator's own answer

1. **IT CLOSES ZERO FINDINGS, and must never be reported as "closing 553 items."** The four
   review backlogs are **untouched** and remain the state of record for every finding. Their
   row counts, read at the same sha as everything below: `health` **1450** · `performance`
   **117** · `ml` **109** · `research` **19**. Not one row was added, removed, or re-statused.
   What changed is only whether the work store carries a **second copy**.
2. **The licence is NARROW.** It covers rows **verified to join a live backlog row**. That
   verification was **re-established here on the population actually acted on** — it was not
   inherited from the sweep. See § 2.
3. **It is not a licence to bulk-close anything else.** Retiring a *verified mirror* is a
   different act from closing a row nobody read; the store's own
   [`README.md`](README.md) records the 2026-09-01 bulk pass doing real harm.
4. **It is not the renderer half.** Part (3) of the object's `done_condition` — the SPA showing
   which of the remainder are parked versus accruing — is a renderer change and is out of scope.

⚠️ **IT DOES REVERSE THE PHASE-C MIGRATION OF 2026-09-01, deliberately and on the operator's
instruction.** Anything that later wants *"which findings have work objects"* must read the
**BACKLOG**, not this store. **Do not read the absence of these files as data loss** — every one
of them was a mirror whose original survives.

---

## 1. Population — stated once, used throughout

**All 712 `docs/claude/work/objects/*.yaml` at `main` `825964034`, read 2026-09-11T06:1xZ.**
All 712 parsed; **0 parse failures**.

⚠️ **Re-measured at the sha acted on, NOT quoted from the sweep.** The sweep measured 710
objects at `4e9255acb`; this is 712 at `825964034`, and the figures below are this store, not
that one. Quoting the older numbers as current is the failure this line exists to prevent.

| `lifecycle` | before | after |
|---|---|---|
| `dormant`   | **556** | **3** |
| `ready`     | 60 | 60 |
| `done`      | 63 | 63 |
| `waiting`   | 25 | 25 |
| `in_flight` | 8 | 8 |
| `accepted`  | 0 | 0 |
| **total**   | **712** | **159** |

**No lifecycle was changed by this pass. The only operation was file removal.**

---

## 2. The rule, and the positive control re-established on the acted-on population

**THE TEST, applied per row — all three must hold:**

1. the object is a **Phase-C migrant** — its `blocked_on_basis` begins
   `NOT_ASSESSED — migrated in bulk 2026-09-01` (an exact prefix test, not a guess); **and**
2. its `lifecycle` is **`dormant`**; **and**
3. `source.row_id` **joins a row that is present and NON-terminal** in the backlog named by
   `source.backlog` — status `open` or `kept_open`. Terminal is
   `resolved` · `wont_fix` · `superseded` · `invalid`.

| cohort | n |
|---|---|
| objects at `825964034` | 712 |
| Phase-C migrants | **576** |
| …joined a live backlog row | **555** |
| …joined a **terminal** backlog row (already closed by the sweep's Rule 1 — untouched here) | 21 |
| …**UNMATCHED** | **0** |
| migrants meeting all three tests (the cohort the decision names) | **554** |
| **excluded by the referential-integrity check (§ 3)** | **1** |
| **RETIRED** | **553** |

**`0 unmatched` is the POSITIVE CONTROL and it was re-derived here, not inherited.** A partial
match would have meant the probe was broken rather than that rows were missing. The join was
also checked against the *declared* backlog file rather than any backlog: **0** rows joined only
via a different file, so no row was matched by luck.

**Where the 553 came from, and what their originals still say:**

| backlog (untouched) | `open` | `kept_open` | retired mirrors |
|---|---|---|---|
| `health-review-backlog.json` | 303 | 181 | **484** |
| `performance-review-backlog.json` | 12 | 29 | **41** |
| `ml-review-backlog.json` | 3 | 19 | **22** |
| `research-review-backlog.json` | 6 | 0 | **6** |
| **total** | **324** | **229** | **553** |

Severity at migration, for the retired set: `medium` 244 · `high` 154 · `low` 106 · unset 37 ·
`critical` 7 · other free-text 5. **Recorded, not acted on** — severity is a property of the
FINDING, which the backlog already holds.

---

## 3. The referential-integrity check — and the one row it excluded

**A dangling edge is read by `constraint_readout.py` as a real blocker, and a false blocker is
worse than a missing one.** So before any removal, everything that could *point at* a cohort row
was checked: typed `blocked_on` edges and `parent_intent` on every surviving object, every other
file under `docs/claude/work/`, and repo-wide references to the object **file path**.

**ONE typed structural edge pointed into the cohort, and that row was therefore NOT retired:**

> [`WO-20260901-PHASE-H.yaml`](objects/WO-20260901-PHASE-H.yaml) declares
> `blocked_on: {kind: object, ref: BL-20260901-RETIRE-ANDROID-AND-STREAMLIT-FROM-THE-LIVE-FEED}`,
> which the readout grades **`hold: holding`** — a live, load-bearing blocker, not a stale one.

**`BL-20260901-RETIRE-ANDROID-AND-STREAMLIT-FROM-THE-LIVE-FEED.yaml` stays in the store.** It
meets the join test; it is excluded because deleting it would leave Phase H pointing at a ref
that no longer resolves. That is outside the licence until the edge is dealt with, and dealing
with it is not this pass's to decide — rewriting another object's typed edge is a claim about
its dependencies, which is exactly what the store forbids doing on inference.

**Four other classes were checked and cleared, each for a stated reason:**

| what was found | n | why it is not a dangling reference |
|---|---|---|
| `decision_requests` prose in `WO-20260901-PHASE-H`, `WO-20260903-SUNSET-DISPOSITIONS-OWED` naming a cohort id | 3 | prose naming the **backlog finding**, which survives. Migrated object ids ARE the backlog row ids, so the reference resolves to the state of record either way. |
| `MANAGER-CHECKLIST.json` notes naming a cohort id | 5 | same — read in context they say *"the gap is filed as BL-…"*, *"two rows already filed"*. References to findings, not to work objects. |
| `SESSIONS.json` + four evidence docs + one spawn prompt | 6 | same. |
| `docs/claude/UNCARRIED-SPEC-BASELINE.json` naming 6 cohort **file paths** | 6 | a **dated snapshot** measured at `817a5a5f`, whose own `_doc` says *"IT IS A SNAPSHOT, NOT A LIVE READ … re-run rather than trusting this file's age."* Not a live consumer, and not a CI guard — `uncarried_specs.py` appears in no guard list and no workflow. |

**There is no `docs/claude/work/steps/` directory**, so no step was orphaned. Nothing re-creates
these files: `migrate_backlog_to_work_objects.py` is invoked in CI only as `--self-test`.

---

## 4. The one measured consequence, quantified rather than asserted

`scripts/ops/uncarried_specs.py --json` was run on a clean `origin/main` worktree **and** on this
tree, so the diff is attributable to these deletions alone rather than to five days of drift.

**Population 383 specs, unchanged. Exactly 3 specs change state, all in the same direction:**

| before | after | spec |
|---|---|---|
| `dormant_only` | `mentioned` | `docs/research/declare-the-9-cannot-be-valued-yet-2026-08-24.md` |
| `dormant_only` | `mentioned` | `docs/research/exit-capture-deepdive-2026-07-30.md` |
| `dormant_only` | `mentioned` | `docs/research/exit-reason-frozen-at-close-2026-08-22.md` |

`active` **51 → 51** · `queued` **3 → 3** · `uncarried` **29 → 29**.

⚠️ **NO SPEC LOSES CARRIAGE, and that is a claim from the instrument's own ladder rather than a
hope:** `dormant_only` is explicitly **NOT carried** — its baseline `_doc` calls it *"the state
the motivating incident sat in for 14 days"* — so a dormant carrier could never have been what
made a spec `active`. The three above move to a state that is equally not-carried and is more
honest about it. Re-run the instrument rather than reading the committed baseline, which
predates this.

---

## 5. Instrument readings, before and after

| | before | after |
|---|---|---|
| `check_wip_ceiling.py` | `verdict=ok` — 8 in flight, ceiling 8 | `verdict=ok` — 8 in flight, ceiling 8 |
| `constraint_readout.py` verdict | `insufficient_basis` | **`insufficient_basis`** |
| assessed `blocked_on` coverage | 73 of 712 — **10.3%** | 73 of 159 — **45.9%** |
| edge basis `unstated` | 639 | 86 |
| stages assigned `bulk_by_source_file` | **576** | **23** |
| stages chosen `per_object` | 133 | **133** |

⚠️ **THE VERDICT DID NOT CHANGE, AND THIS PASS MUST NOT BE REPORTED AS HAVING MOVED THE
DIAGNOSIS.** Coverage rose only because the **denominator** shrank — the numerator is **73 both
times**. **Not one `blocked_on` edge was written, corrected, or invented.** 45.9% is still below
the declared 50% floor, so the readout still names no stage, which is the correct output.

⚠️ **`bulk_by_source_file` 576 → 23 is the genuinely informative line**, and it is the same
arithmetic being honest rather than a new fact: the readout's standing warning that its stage
histogram *"is a census of ONE filename"* now applies to 23 rows instead of 576. `per_object` is
**unchanged at 133** — no stage was re-judged.

**The WIP ceiling is untouched and was never at issue.** It counts the `in_flight` set (8, at
its ceiling of 8); this pass touched only `dormant` rows, created no in-flight object, and filed
**no** `wip-ceiling-exception`.

---

## 6. How to reverse this

Every retired file is recoverable in full from git at `825964034`. The complete list of the 553
retired ids is the deletion set of the commit that carries this file; no id is lost. Reversing
the act is `git checkout 825964034 -- docs/claude/work/objects/` — but note that doing so
restores the pile the operator asked about, and the decision that removed it is recorded on
`WO-20260911-SWEEP-THE-623-DORMANT-WORK-OBJECTS-AND`.
