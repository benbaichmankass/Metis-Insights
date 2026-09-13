# The sweep corpus's schema downgrade: what was fixed, what is gone, and what is unknowable

> **Doc status:** `live` · category `evidence` · last verified `2026-09-12` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)

Closes `BL-20260816-CORPUS-CONFLICT-REDERIVE-RUNS-THE-STALE-BRANCH-EXTRACTOR`
and records the finding that replaces its data half.

---

## 1. The code half is fixed AND verified on a real conflicting run

The row's criterion (1) is explicit about what would not count: *"A run that
does NOT conflict proves nothing, since the bug only exists on the conflict
path."*

**How a conflict is detectable from a job log at all.** `corpus union:` is
printed by `m20_corpus_union.py` and the workflow calls that script from
**exactly one site** — inside `rebase_onto_target`'s conflict branch, after the
`git reset --hard` (`.github/workflows/m20-exit-lever-sweep.yml:711`, the only
`union_dispatched.py` invocation in the file). So that line in a log is a
positive marker that the conflict path fired, not an inference from the notice
text.

**The negative control — run 49, PRE-fix** (`31976325152`, corpus job
`95236789561`, dispatched on `main` at `cb8bc5a2`), read from the job log:

```
##[notice]rebase conflicted on the corpus — re-deriving the
HEAD is now at 9a085926 corpus: merge exit-lever sweep run 2026-08-14T10:57Z
runs merged: 13  new rows: 52  superseded: 2  malformed-dropped: 0
corpus now: 998 rows
[main e9fb0d6d] corpus: merge exit-lever sweep run 2026-08-16T22:30Z
corpus pushed to claude/m20-sweep-corpus
```

The reset landed on `9a085926`, and the 52 rows it wrote carried none of the
eight `live_tp_reach_r_*` keys.

**The positive control — run 53, POST-fix** (`31989495640`, corpus job
`95270736625`, dispatched on `main` at `e90108644`):

- `e90108644` **contains** both `3eeb4f17d` (#9812, the extractor preservation)
  and `5eb2aa11a` (#9827, the union) — checked with `git merge-base
  --is-ancestor`, not assumed from the dates.
- Its log carries `corpus union: into 1374 + from 1364 -> 1374 rows`, so the
  conflict path fired.
- It wrote `runs merged: 1  new rows: 2` at `sweep_generated_at`
  `2026-08-17T02:58:23.971915+00:00`.
- In the corpus at `claude/m20-sweep-corpus` HEAD that `run_id` has **2 rows,
  both carrying `live_tp_reach_r_n_IS`**.

Conflict path fired · rows written · rows carry the field. That is criterion (1).

## 2. The data half's subject no longer exists

Criterion (2) asks for *"the 52 rows already written on
`claude/m20-sweep-corpus`"* to be re-derived or marked. **They are not there,
and have never been there in any commit now reachable.**

Measured over every commit on that branch that touches the corpus file:

| commit | date | rows | rows dated `2026-08-16` |
|---|---|---:|---:|
| `7aa293dac` | 08-16T09:40 | 1264 | 0 |
| `813c86d50` | 08-17T02:39 | 1264 | 0 |
| `10f5e2ee1` | 08-17T05:01 | 1364 | 0 |
| `1b8e2fd78` | 08-17T06:49 | 1376 | 0 |
| `538b9f24b` | 08-17T11:31 | 1379 | 0 |
| `e85bc6350` | 08-29T10:50 | 1670 | 0 |

⚠️ **The probe is not blind** — the same substring test over the same file
returns `2026-08-14T` → 39, `2026-08-15T` → 348, `2026-08-17T` → 65,
`2026-08-29T` → 312. Only `2026-08-16` is zero.

**The mechanism.** Run 49's log says `corpus pushed to
claude/m20-sweep-corpus`, so `e9fb0d6d` did land. Four of the six commits above
(`7aa293dac`, `813c86d50`, `10f5e2ee1`, `1b8e2fd78`) are **ancestors of
`origin/main`** — so the branch was subsequently reset onto the default branch,
and `e9fb0d6d` became unreachable (`git cat-file -e e9fb0d6d` fails in a clone
that holds the whole branch). The 52 rows were not repaired; they were
discarded along with the commit that carried them.

## 3. What replaces it: the distinction is not clerical, it is unavailable

The criterion's purpose was consumer distinguishability — *"'the field was not
measured' vs 'the field was measured and dropped in transit'"*. Whether a run's
extractor **could** emit a field is a property of the commit it was dispatched
with, and **the corpus records no dispatched sha.** `run_id` is deliberately
the sweep's own `generated_at` timestamp (`m20_corpus_extract.run_id_for` — the
right choice for its own purpose, keeping one matrix run from splitting into N),
so a row cannot be joined back to a workflow run whose `head_sha` would answer
it.

`scripts/research/m20_corpus_schema_census.py` grades what the committed data
can support, against **two** candidate boundaries, and refuses to choose
between them:

```
POPULATION: 1379 row(s) from m20-sweep-corpus.jsonl   (origin/main, 2026-09-12)
  field graded            : live_tp_reach_r_n_IS
  corpus boundary         : 2026-08-14T10:49:15.493094+00:00
  default-branch boundary : 2026-08-13T15:55:20+00:00  [stated]

  present                         452
  absent_predates_both            906
  absent_after_both                 1
  absent_between_boundaries        20
  absent_undateable                 0
```

⚠️ **THIS CORRECTS MY OWN FIRST READING IN THIS SESSION, AND BY 20×.** A hand
probe against the default-branch boundary alone reported **21** rows "missing
the field after it landed". Twenty of those twenty-one sit **between** the two
boundaries: a feature-branch dispatch can carry a field before the default
branch does, so on those rows the corpus boundary and the default-branch
boundary disagree and the corpus cannot settle it. Silently taking the
default-branch boundary is the unprovenanced-diagnostic **sub-class B** shape
(implicit input selection) `CLAUDE.md` names — committed here by the session
that went looking for it. Exactly **one** row is after both boundaries.

The census writes nothing and re-grades nothing. Stamping "schema-degraded"
onto twenty rows whose cause is not established would record a claim nobody
measured, which is worse than the absence it replaces.

**This document specifies no work of its own, deliberately** — it is the
EVIDENCE for a closed row, and a session reading it is not being asked to do
anything by it. The remedy it points at (stamp the dispatched sha and the
Actions run id onto every corpus row, so field-absence becomes gradeable
rather than arguable) is carried by the two registers whose job that is, and
its done-condition lives there rather than here:

- `docs/claude/health-review-backlog.json#BL-20260912-THE-SWEEP-CORPUS-RECORDS-NO-DISPATCHED-SHA-SO-A-MISSING-FIELD-CANNOT-BE-TOLD-FROM-ONE-THE-EXTRACTOR-COULD-NOT-EMIT`
  — the finding and its updates.
- `docs/claude/work/objects/BL-20260912-THE-SWEEP-CORPUS-RECORDS-NO-DISPATCHED-SHA-SO-A-MISSING-FIELD-CANNOT-BE-TOLD-FROM-ONE-THE-EXTRACTOR-COULD-NOT-EMIT.yaml`
  — the work, `lifecycle: dormant`. ⚠️ Dormant means **carried, not queued**:
  nothing is scheduled to pick it up, and that is the honest state rather than
  a promise nobody is keeping.
