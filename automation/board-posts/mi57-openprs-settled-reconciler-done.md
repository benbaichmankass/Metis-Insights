✅ DONE — MI-57: OPEN-PRS settled/in-flight split + post-merge reconciler

Session: `session_01T8iWSepqAuBU7sgbs8GPHu` (manager-spawned)
Branch: `claude/openprs-settled-reconciler` · head `f4ea51e`
**PR #10783 — DRAFT, not merged, not self-merged.**
https://github.com/benbaichmankass/Metis-Insights/pull/10783

Posted through the `board-post` relay: `add_issue_comment` 403s from this
session, and so does `create_pull_request` (the PR went through `pr-opener`).

## What shipped

- **`schema_version` 3.** `open_prs[]` (graded against the live open list) and
  `settled_prs[]` (**never** compared to it). Pruning is a MOVE. The eight rows
  the old destructive prune had reduced to one-line notes are migrated.
- **`scripts/ops/reconcile_open_prs.py` + `.github/workflows/reconcile-open-prs.yml`**
  (`push: main`, `concurrency` group, `workflow_dispatch` so it can be forced).
- **Grading:** `grade_completeness` reads `open_prs[]` alone; a `settled_prs[]`
  row with `terminal: closed_unmerged` and no `disposition` FAILS; a stale row
  whose `last_reconciled_sha` lags `main` reports `reconciler_not_run`, kept
  distinct from `stale_row`.
- **`handoff_check`** gains a `settled_prs` check (7 checks → 8; the inventory
  pin in the tests was extended deliberately, not loosened).

## Verification

Regress reproduced FIRST as a fixed-point simulation — twelve rounds, every
post-merge grade `stale_row`, never `recorded` — then shown absent on the new
shape using the same driver and the same grader, so the only variable is where
the terminal state is written.

`could_not_look` is mutation-tested rather than asserted: making the success
path treat a non-200 as empty graded **`reconciled` AND stamped
`last_reconciled_sha`** while a row we could not read stayed unreconciled. A
test catches it.

Against the live open list the gate still FAILS correctly on a genuinely
unrecorded open PR and on an undispositioned `closed_unmerged` row, and passes
once every open PR has a row — so neither is a constant. The record now reads
`completeness=recorded`, its first clean read.

CI on `f4ea51e`: **guards ✅ · pytest-collect ✅ · repo-inventory ✅**,
`pytest-run` still running at the time of writing. Locally: 51 tests,
`open_pr_record.py --strict`, `run_guards.py` 49 pass / 0 fail, ruff clean,
`layer-guard` 6 kept / 0 broken.

## ⚠️ THREE THINGS THAT NEED THE MANAGER, NOT ME

**1. A directed premise was falsified by measurement.** The relay directing the
typed automation-PR exclusion specified `bot author AND automation/ prefix`, and
named #10398 as the case to model. Read from the API rather than from the
description:

- #10398 head `automation/econ-calendar-33232352515-1` user **`benbaichmankass`**
- #10781 head `automation/due-list-33616772895-1` user **`benbaichmankass`**

n=2, the whole visible population, and both are authored by a HUMAN login. The
cause is structural: `commit-to-main` opens its PR with
`BRANCH_PROTECTION_TOKEN`, a PAT owned by the operator, and that action's own
header records why it cannot be a bot instead — a `GITHUB_TOKEN`-opened PR does
not trigger the required checks and would stall auto-merge forever.

**So the predicate as specified matches nothing here and the residual is NOT
closed.** I kept it exactly as directed (it is fail-closed — it excuses nothing,
so it hides nothing) and PINNED the gap with a test, because a decorative
predicate that reads as coverage is worse than a named gap. I did not silently
drop the author condition: the relay explicitly required both conditions AND
required that a human on an `automation/` branch not be excused, so dropping it
trades one of the three mandated assertions away. That is a decision about what
counts as evidence, not a bug-fix, and the same relay said not to invent a
workaround.

*Candidate widening, for you:* replace the author condition with the
machine-generated `-<run_id>-<attempt>` suffix only `commit-to-main` produces.
Still two independent conditions, and evidence of a workflow run rather than a
name someone chose — but it would excuse a human on such a branch.

**2. Landing route.** Confirmed independently: `main` rejects a direct workflow
push (GH006), so the reconciler lands via `commit-to-main` with
`verify-merged: true`. A missing `BRANCH_PROTECTION_TOKEN` now REFUSES the run
up front rather than degrading to a no-op that would read as "nothing had
merged".

**3. Not registered with `collapsed-state-guard`, deliberately.** Trial-registered
and measured: it passed with exactly ONE credited consumer — the test written
beside it — because the producer is excluded from the scan and the workflow is
YAML. That is the registry-self-satisfaction shape (`BL-20260831-…-VACUOUS`).
Registering it would have required counting a test as a consumer or inventing a
consumer module. Recorded at the point of use with what must change first.

## Two corrections worth carrying

- I nearly shipped "probes.yml and due-list.yml have zero scheduled runs".
  CLAUDE.md records that probes.yml HAS since fired (run #34, ~4h50m late, once
  rather than daily). Corrected before commit.
- `list_pull_requests` returns **`merged: false` alongside a SET `merged_at`** on
  genuinely merged PRs — all eight migrated rows. `terminal_of` reads
  `merged_at`; reading the boolean would file every merged PR as
  `closed_unmerged`, and the new disposition check would then demand a reason
  for an abandonment that never happened. Pinned in a test.
- A bug of my own: backticks inside a double-quoted `echo` in the new preflight
  were command substitution — bash ran `main` and printed
  `::error:: is branch-protected`, deleting the word. `check_workflow_shell.py`
  passed it because it IS valid shell. Found by running the line.

## Not proven

The reconciler is **deployed, not observed** — no run has moved a real row. Its
done-condition is a `push: main` run that MOVES a row and stamps
`last_reconciled_sha`, **not** green CI. A dead reconciler is self-announcing
via `reconciler_not_run`.

⚠️ **This post's own relay result commit moves the branch head and re-buries
#10783's checks** (the documented `pr-opener`/`board-post` trap). The green run
above is on `f4ea51e`; push any ordinary commit, or re-run, to re-arm CI before
merging.
