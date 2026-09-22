# Four CI guards retired 2026-09-22 (E45) — with the reason each one carries

> **Doc status:** `unknown` · category `unknown` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../../DOCUMENT-INDEX.md)
>
> ⚠️ `unknown` is what `scripts/ops/document_index.py` generates for anything
> under `docs/archive/` — it does not assess that directory. R6 fails a row the
> generator would not reproduce and R3 fails a header that disagrees with the
> row, so claiming `live` here would make two surfaces disagree. The content
> below is current as of 2026-09-22; the header describes the INDEX's opinion.

**DECIDED 2026-09-22 (operator).** Of the eight guards the E20 lane measured as
`dead`, three are named in [`docs/CLAUDE-RULES-CANONICAL.md`](../../../CLAUDE-RULES-CANONICAL.md)
as live enforcing mechanisms and were **re-pointed**; the rest were to be
**retired with a stated reason**. This file is that reason, one per guard.
**What would reverse it:** the archived register coming back, which the
operating plan says is not happening.

**MEASURED, by the E20 lane 2026-09-22**, over a stated population of **95**
guard scripts (`scripts/ci/check_*.py` **plus** `scripts/check_*.py`), every
module-level repo-path constant tested with `git cat-file -e HEAD:<path>`. Four
states, never collapsed: `live` 30 · `degraded` 11 · **`dead` 8** ·
`no_declared_subject` 46. Instrument: `scripts/ci/check_guard_liveness.py`.

**The one cause behind all eight.** The 2026-09-21 operating reset archived the
four review backlogs, `OPEN-ITEMS.json`, the lease, `SESSIONS.json`,
`MERGE-QUEUE.json`, `session-board.json`, `RECURRENCE-LEDGER.json` and
`operator-owed-register.json` — and nothing swept the guards that read them.

`killed` is a first-class outcome. Closing a dead check *with a stated reason*
is worth more than carrying one that grades nothing, and the code is in git
history: `git show <sha>^:scripts/ci/<name>.py`.

---

## `check_backlog_unresolve.py` — `backlog-unresolve-guard`

**Subject:** the four review backlogs, all archived.
**What it enforced:** a row that reached a terminal `status` may not be
re-opened without a stated reason.
**Why retired rather than re-pointed:** the post-reset registers have no
un-resolve to grade. `MANAGER-CHECKLIST.json` rows move between `state` values
by design — `landed_unproven` → `in_flight` on a failed observation is the
system working, not a row being quietly re-opened — and `PIPELINE.jsonl` is
append-only with `terminal_reason` REQUIRED to enter `done` or `killed`, which
is the same protection at the point of writing rather than after the fact.
Re-pointing it would have meant inventing a transition table nothing produces.
**⚠️ It was GREEN, not red.** Run on `main` 2026-09-22 it printed
*"backlog-unresolve: OK — no closed row was re-opened without a reason"* over a
population of zero files. That is the exact "green while grading nothing" this
whole row exists to end, and it is why a silent retirement would not have done.

## `check_open_items.py` — `open-items-guard`

**Subject:** `docs/claude/OPEN-ITEMS.json`, archived.
**What it enforced:** the register every session read at start is workable —
ids unique, rows dispositionable, observations not silently lost, a 21-day
affirmation window.
**Why retired rather than re-pointed:** its job was split at the reset and both
halves already have owners. Workability of a *build* row is
`MANAGER-CHECKLIST.json`'s `item_schema` plus the re-pointed
`claim-basis-guard`; workability of a *follow-up* row is
`scripts/ops/pipeline.py`, which REFUSES a row without `due_when` and
`origin.rerun` and refuses a terminal state without `terminal_reason` —
enforced by `pipeline-guard` on every PR. Re-pointing this guard would give
those rules a second owner, which is the drift the reset was for.
**Blast radius handled in the same change:** `scripts/ci/check_main_tree_health.py`
ran it as one of three cron probes, so `main-tree-watch.yml` was grading a red
it could do nothing about; that probe is removed with it. ⚠️ **MEASURED, not
inferred:** run against `main`'s tree 2026-09-22 the guard exits **1** with
*"docs/claude/OPEN-ITEMS.json is MISSING"*, and `main-tree-watch.yml` is
scheduled `37 * * * *`. Read that as *scheduled* hourly, not *fired* hourly —
that workflow's own header says a cron here is a ceiling on frequency and not a
promise, and this session did not read its run history.
`scripts/ci/check_wip_ceiling.py` loaded it to assert `MAX_ITEMS is None` — that
assertion is dropped, and `check_wip_ceiling` was itself unregistered at the
reset.

## `check_recurrence_ledger.py` — `recurrence-ledger-guard`

**Subject:** `docs/claude/RECURRENCE-LEDGER.json`, archived.
**What it enforced:** the ledger's internal shape — a recurrence class states
its cause, its occurrences and its prevention.
**Why retired rather than re-pointed:** there is no post-reset ledger and no
plan to build one. The 2026-09-09 full-system audit already recorded the ledger
as intake-less on its own terms — **85 backlog rows self-declared a recurrence
and ZERO cited a ledger class (85 of 1,630 rows)** — so what died at the reset
was a register that had never been wired to its own source. Re-pointing it would
mean creating a fourth intake to grade, which is the opposite of what the reset
was for. ⚠️ `CLAUDE-RULES-CANONICAL.md` § "The row you are reading outranks the
label in it" still cites `RC-STORED-FIELD-READ-AS-ITS-NAME` from that ledger;
that citation is **history about a recurrence**, not a claim that a mechanism
runs, so it is left as written.

## `check_register_field_loss.py` — `register-field-loss-guard`

**Subject:** `docs/claude/work/SESSIONS.json`, `OPEN-ITEMS.json` and the four
review backlogs — all six archived.
**What it enforced:** a merge may not silently drop a field from a shared
register; a genuine removal goes in `.github/register-removals/<slug>.json`.
**Why retired rather than re-pointed:** it existed because **six** shared JSON
registers were edited concurrently by many sessions and resolved by side. The
post-reset model has one JSON register written by one manager
(`MANAGER-CHECKLIST.json`) and one **append-only JSONL** store
(`PIPELINE.jsonl`) — and append-only is a structural answer to field loss, not
a graded one: `scripts/ops/pipeline.py` appends a whole record and never
rewrites a prior line, so the merge shape the guard policed cannot occur.
**⚠️ It was already refusing, correctly:** run on `main` 2026-09-22 it printed
*"NOTHING WAS CHECKED — none of the 6 declared registers exists … Refusing
rather than reporting a green that checked nothing"*. That refusal is the guard
working; retiring it is not overruling it, it is agreeing with it.
`.github/register-removals/README.md` is updated in the same change so it stops
naming a guard that no longer exists.

---

## The fifth one the operator listed, and why it is NOT retired

`check_stated_population.py` was on the dead list and **is not dead.** It is
kept, with four stale path constants pruned. See
[`scripts/ci/check_stated_population.py`](../../../../scripts/ci/check_stated_population.py)
for the correction and the probe that establishes it.

**Verified by running it**, 2026-09-22, against a planted diff adding
`docs/zz-probe.md` with the line *"Coverage is 42.9% of the fleet"*: it exits
**1** and names the line. Its real population is a GLOB — `WATCHED_PREFIXES =
("docs/",)` × `WATCHED_SUFFIXES = (".md",)` — over the PR diff, and it is
registered in `scripts/ci/run_guards.py` as `stated-population-guard`, running
on every PR. It read `dead` only because its *other* subject, a
`WATCHED_FILES` tuple naming the four archived backlogs, was the only
module-level repo-path constant the liveness instrument could see.

⚠️ **This is a FALSE POSITIVE of the liveness instrument, and it is the
instrument's own documented limit read in the other direction.**
`check_guard_liveness.py` states that *"`live` HERE MEANS ITS SUBJECT EXISTS,
NEVER THAT IT WORKS"*; the converse — a guard whose graded population is a glob
rather than a named constant can read `dead` while grading fine — is not stated
there and is now filed against it. Retiring a working guard on that reading
would have removed the only mechanical floor under § "Always state the
population", which is exactly the harm this row exists to prevent, one register
along.
