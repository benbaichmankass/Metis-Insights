# Merge queue — enablement + operation (BL-20260726-MERGE-QUEUE-ENABLEMENT)

**Why:** the honour-system merge-claim protocol (post a `🔒 MERGE SLOT CLAIM`
on #6927 → sync to `main` → merge → `🔓 RELEASE`) keeps getting skipped under
load — sessions merge stale branches and hit the `behind`/`dirty` retest churn
(BL-20260720-MERGE-PROTOCOL-LAPSE, and again 2026-07-26). GitHub's **native
merge queue** removes the human-discipline dependency: it auto-syncs each PR to
the queue head, runs the required checks on that merged result, and merges in
order — no manual "sync-immediately-before", no two sessions racing.

## Prerequisite (SHIPPED in this PR) — `merge_group` triggers

A merge queue forms a temporary `merge_group` ref and waits for the branch's
**required status checks** to report on it. **If a required check's workflow
does not trigger on `merge_group`, it never reports and the queue stalls —
blocking every merge.** Before this PR, **zero** workflows triggered on
`merge_group`.

This PR adds a **dormant** `merge_group:` trigger to all 23 PR-check workflows
(pytest-run, pytest-collect, ruff-lint, secret-scan, repo-inventory + every
guard). Dormant = no `merge_group` events fire until you enable the queue, so
current PR merging is byte-for-byte unaffected. Each addition is a plain
`merge_group:` key alongside the existing `pull_request:`/`push:` triggers.

## Operator enable steps (Settings → Branches)

Do this **after** this PR merges to `main`:

1. **Settings → Branches → Branch protection rule for `main` → edit.**
2. Tick **"Require merge queue"**.
3. Set the merge method to **Squash** (matches the repo convention).
4. Under **"Build concurrency"** leave the defaults (1–5) unless you want batching.
5. Confirm **"Require status checks to pass"** lists the same required checks as
   today — the queue reuses that list. (Do **not** add a required check that
   lacks a `merge_group` trigger — it would deadlock the queue.)
6. Save.

## Validate BEFORE relying on it (fail-safe first merge)

The first queued merge is a **validation** — if a guard misbehaves on the
`merge_group` ref, the queue simply doesn't merge that PR (fail-safe), it does
not corrupt `main`:

1. Open a trivial no-op PR (e.g. a one-line docs/comment change).
2. Click **Merge when ready** (adds it to the queue).
3. Watch the queue run the required checks on the `merge_group` ref. All green
   → it merges automatically. If a specific guard errors on `merge_group`
   (e.g. a diff-based guard that assumed PR context), note which one and fix
   that workflow (usually: make it tolerate the `merge_group` ref, or drop it
   from the required-checks set if it's not truly required). Repeat until green.
4. Once a validation PR merges cleanly, the queue is trustworthy for real PRs.

## How sessions merge once the queue is on

- **Add the PR to the queue** (GitHub "Merge when ready", or
  `enable_pr_auto_merge`). GitHub auto-syncs it to the queue head, runs checks,
  merges in order. **No manual `git fetch && merge origin/main` immediately
  before merging, no `behind`/`dirty` churn.**
- The `docs/claude/session-board.json` `merge_slot` + the `🔒 CLAIM`/`🔓 RELEASE`
  comments become **belt-and-suspenders** for the rare non-queued path (an admin
  bypass merge, a hotfix). The board (#6927) stays **primary for work
  coordination** — `▶️ START` / `✅ DONE` / `❓ QUESTION` / `active_sessions`
  registration — which the queue does not replace.

## Rollback

Untick "Require merge queue" in branch protection. The dormant `merge_group:`
triggers are then simply never fired again (harmless to leave in place). The
honour-system claim protocol resumes as the sole serializer.
