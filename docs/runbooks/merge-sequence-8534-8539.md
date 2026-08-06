# Merge runbook — #8534 → #8539 (2026-08-06 Actions-outage backlog)

**Why this file exists.** Both PRs were blocked by the 2026-08-06 GitHub Actions
incident (major outage from 15:22Z; PR/push run-creation dead repo-wide from
~17:35Z; jobs queued 2h+ without a runner). The operator went offline expecting
the merges to be driven autonomously on recovery. The sequence below is
**order-dependent** — getting it wrong strands every open PR on a required check
that can never report. Written down because a long session gets
context-compacted and the ordering must not be re-derived from memory.

Delete this file once both PRs are merged.

---

## State at time of writing (19:45Z, 2026-08-06)

| PR | Branch | State |
|---|---|---|
| **#8534** | `claude/roadmap-workplan-update-lw6qf0` | Tier-2, **operator-approved**. 29 checks: 2 success, 27 queued since 17:34Z. |
| **#8539** | `claude/metis-insights-workplan-cont-fczb1e` | Draft. **0 check runs** — its pushes landed inside the dead window, so nothing was ever created. `mergeable_state: blocked`, NOT `dirty` (no conflict). |

`run_workflow` via `mcp__github__actions_run_trigger` **works** (verified 19:45Z,
204 + run queued). `CLAUDE.md` § "PM-side session capabilities" still claims it
403s — that line is stale as of the 2026-08 MCP.

---

## Live status log (newest first — append, don't rewrite)

- **22:40Z — no workaround exists; #8539 must wait for webhooks. Stop looking.**
  GitHub 22:18Z: *"success rates for new workflow runs have increased to 97% …
  However, webhook triggers remain throttled."* Jobs execute fine once created;
  **creation** is the throttled half. Three pushes (229c6b0, 5697e62, and an
  explicit empty commit 30b5556) each produced **zero** runs, and the repo-wide
  queue is nearly empty (24 items, most stale from May) — so this is not backlog.
  **The `workflow_dispatch` escape hatch does NOT work here, for two independent
  reasons — do not burn time re-deriving them:**
  1. `guards.yml` is **new in this PR**, so it is not on the default branch, and
     GitHub returns **404** on `POST /actions/workflows/guards.yml/dispatches`
     for any ref. A workflow must exist on the default branch to be dispatchable.
     This makes a CI-consolidation PR *uniquely* webhook-dependent: the very job
     it introduces cannot be summoned any other way.
  2. `pytest-collect` (a required context) has **no `workflow_dispatch` trigger
     at all**, so even for workflows that do exist on `main`, the required set
     cannot be fully satisfied by dispatch.
  Consequence: nothing is lost by waiting — the branch is pushed and locally
  verified (30 PASS / 0 FAIL, 367 tests). Do **not** contort the repo (e.g.
  adding `workflow_dispatch` to `pytest-collect` just to force a merge tonight);
  a required gate satisfied by an unusual path is exactly what this PR exists to
  stop being possible.
  *Worth considering later, on its own merits and not under time pressure:*
  giving every required-check workflow a `workflow_dispatch` trigger so a future
  webhook outage is recoverable. That is a real hardening item, not a hack —
  file it rather than doing it mid-incident.

- **22:25Z — ✅ STEP 1 DONE: #8534 MERGED** (squash → `cac7037`). 28 checks passed
  in CI; `claim-basis-guard` had been reaped by the outage (run `failure`, job
  `cancelled`, **logs 404** — it never executed), so it was verified **locally**
  against the exact head 9c6fde0 with the same `--base origin/main`:
  `3 backlog file(s) scanned, 0 basis-less new claim row(s)`, exit 0. The two
  guards that had never run against the new marker script were given real log
  reads: `dry-run-guard` → `clean` on the 923-line diff;
  `diagnostic-provenance-guard` → self-test fired **and** scan `OK`.
  **Steps 2–3 done too:** `origin/main` merged into the branch (one conflict in
  `health-review-backlog.json`, resolved by taking main's copy + re-appending
  `BL-20260806-ISSUE-TRIGGER-FANOUT-77PCT-OF-ALL-RUNS`; 383 items, valid JSON),
  local verification now **30 PASS / 0 FAIL** (the pre-merge failure was exactly
  the dangling backlog ref #8534 resolved) and **367 tests pass**. Pushed 229c6b0.
  **STILL BLOCKED at step 3:** push webhooks remain throttled — the branch shows
  `total_count: 0` after the push, and the #8534 **merge push to `main` also
  created no run** (no `branch-protection-sync` run at 22:17Z). #8539 cannot get
  check runs until webhook delivery recovers; another push will be needed then.
  **DO NOT run step 4 yet** — dispatching the protection swap opens a window that
  blocks every other open PR, and it must not be opened while #8539 is unable to
  go green.
  **De-risked:** the 19:37Z `workflow_dispatch` of `branch-protection-sync` on
  `main` **completed successfully**, so step 4's mechanism is proven working
  (PAT present, protection applied), not merely API-accepted.

- **20:48Z** — #8534 still **4 of 29** (unchanged since 20:01Z; ~47 min with zero
  progress, so the earlier "accelerating" read was wrong — treat the drain as
  erratic, not trending). Branch still `total_count: 0`.
  **GitHub's 20:34Z update names the mechanism:** *"Webhook triggers are currently
  throttled to help with recovery and we are processing approximately 15% of
  webhooks."* So the missing runs for the #8539 pushes were **dropped, not
  delayed** — which is exactly why step 2's push is mandatory and why waiting
  will never produce them. Also: runners are described as "stuck retrying
  unavailable jobs", which fits the queued-but-never-started shape.
- **20:05Z** — 4 of 29 green (timestamp-comparison 17:40Z, qty-legalization
  19:18Z, soak-doctrine 19:56Z, harness-lever-coupling 20:01Z).
- **17:35Z** — `pull_request` run-creation stops repo-wide (last such run
  17:34:55Z).
- **15:22Z** — GitHub Actions incident opens.

**Cheap recovery test, no push needed:** `list_workflow_runs` filtered to branch
`claude/metis-insights-workplan-cont-fczb1e`. `total_count > 0` ⇒ webhook
delivery is back. Do **not** infer recovery from #8534's queue draining — those
are different subsystems and they diverged all evening.

---

## Step 1 — merge #8534 (must be first)

It carries `scripts/ops/mark_netted_duplicate_pnl.py` (which #8539's system-action
wraps) **and** the `BL-20260806-CI-FANOUT-AMPLIFIES-ACTIONS-OUTAGES` backlog row
that #8539's docs cite. Until it is on `main`, #8539's `artifact-validity-guard`
correctly fails on a dangling reference.

1. `get_check_runs` on 8534. **ASSERT `total_count > 0`** — a 0 means checks
   unregistered, not clean CI.
2. All 29 `completed`? If any failed, **read one failing log before re-running**:
   - still the action-download / runner class → re-run failed jobs;
   - a *different* cause → it is REAL. Read and fix it; do not re-run past it.
   - `dry-run-guard` and `diagnostic-provenance-guard` are diff-scoped against the
     new `scripts/ops/mark_netted_duplicate_pnl.py` and had **never executed
     against it** as of the outage — give both a real log read, not an assumption.
3. Post `🔒 MERGE SLOT CLAIM` on board **#6927**, read the tail first.
4. **Merge squash.**
5. Post `🔓 MERGE SLOT RELEASE`.

## Step 2 — bring #8539 up to date and force its checks to exist

`main` has moved (step 1), and #8539 has **zero** check runs, so both problems are
fixed by the same push.

```bash
git fetch origin main
git checkout claude/metis-insights-workplan-cont-fczb1e
git merge origin/main          # or rebase; either is fine, no conflict expected
git push origin claude/metis-insights-workplan-cont-fczb1e
```

If the merge is genuinely empty and produces no commit, push an empty commit
(`git commit --allow-empty`) — **a push is required**, because run creation is
what is missing, not a green.

Then confirm locally before spending CI on it:

```bash
git diff origin/main...HEAD > /tmp/pr.diff
python3 scripts/ci/run_guards.py --base-ref main --event-name pull_request
```

Expect **30 PASS / 0 FAIL** once #8534 is on `main` (the one pre-merge failure was
the dangling backlog ref, which step 1 resolves).

## Step 3 — wait for #8539's own checks to go green

Expected contexts on this branch: `guards`, `pytest-collect`, `pytest-run`,
`repo-inventory`. Drive to green **before** step 4 — step 4 opens a window that
blocks every other PR in the repo, so keep it as short as possible.

## Step 4 — update branch protection BEFORE merging (the load-bearing bit)

`branch-protection-sync.yml` only runs on **push to `main`** — i.e. *after* a
merge. But #8539 deletes the 13 workflows whose job ids are currently **required
contexts**, and a required context that can never be reported leaves a PR pinned
on "Expected — Waiting for status to be reported" forever. So #8539 cannot merge
under the current spec, and fixing it after the merge is too late.

The workflow reads `REQUIRED_CONTEXTS` **from the ref it runs on**, so dispatch it
on the PR branch, where that list is already
`["pytest-collect","pytest-run","guards"]`:

```
mcp__github__actions_run_trigger
  method: run_workflow
  workflow_id: branch-protection-sync.yml
  ref: claude/metis-insights-workplan-cont-fczb1e
```

Confirm it succeeded (open a `[bp-report]` issue → `branch-protection-report.yml`
posts the live protection back) before proceeding.

> ⚠️ **From this moment until step 5 completes, every OTHER open PR is blocked** —
> it will be asked for a `guards` context its branch cannot produce. If a
> concurrent PR needs to land, land it *before* step 4. Heads-up is posted on
> board #6927.

## Step 5 — merge #8539

Claim the merge slot on #6927, mark the PR ready for review (it is a draft), merge
**squash**, release the slot. The push to `main` re-runs `branch-protection-sync`
from `main`'s now-identical copy — a no-op confirming the spec.

## Step 6 — only then, the work #8534 unblocks

1. `mark-netted-duplicate-pnl` **dry run** via `system-actions` (issue body:
   `action: mark-netted-duplicate-pnl`, `account_id: bybit_1`, `reason: …`).
   **Review the printed row list before applying.** Then re-dispatch with
   `apply: true`.
2. Re-run the P1.x trust map on the cleaned rows. **Do not quote the
   `htf_pullback_trend_2h`/BTCUSDT live mean-R until this has run** — it is
   unusable in either direction while 31 `bybit_1` rows still read MEASURED.

---

## Rollback

Nothing here is destructive. If step 4 lands but step 5 cannot complete, restore
the previous gate by dispatching `branch-protection-sync.yml` on **`main`**
(whose copy still holds the pre-consolidation 15-context list) — that immediately
unblocks every other PR, and #8539 can wait.
