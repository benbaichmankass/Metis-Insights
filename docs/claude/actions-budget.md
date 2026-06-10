# GitHub Actions budget — scope + management (the automation is Claude's to run)

The operator does **not** touch GitHub Actions — Claude is the only driver, so
keeping Actions **storage** and **minutes** within budget is Claude's
responsibility. This doc is the management contract. (Adopted 2026-06-10 after
the account-wide artifact-storage quota filled and red-X'd the Android build.)

## The scope you're managing

GitHub Actions resources are billed **per account**, shared across **all three
repos** (`ict-trading-bot`, `ict-trader-dashboard`, `ict-trader-android`):

- **Storage** (~0.5 GB on the free plan) — build **artifacts** + run logs.
  When it fills, uploads fail (`Failed to CreateArtifact: Artifact storage
  quota has been hit`). A per-repo purge only frees **that** repo's artifacts,
  so **every repo that uploads artifacts needs its own purge.**
- **Minutes** — every workflow run burns minutes. Runaway schedules (a cron
  that fires forever for a job that's already done) are pure waste.

## The two failure modes that bit us (2026-06-10) + the fixes

1. **Account-wide storage full** — the Android repo had **no purge** and kept
   signed AABs at the **90-day default**, while `release.yml` double-built every
   PR'd branch (`push: claude/**` + `pull_request` triggers overlapped).
   - Fix: added `ict-trader-android/.github/workflows/purge-artifacts.yml`
     (daily 7-day trim + on-demand total purge); capped AAB + APK + build-log
     retention to 7 days; dropped the `push: claude/**` trigger.
   - Build-validation jobs were also made resilient: the Android
     `upload-artifact` steps are `continue-on-error: true` so a *future* quota
     hit reports the real (green) build instead of a false red X.
2. **Runaway cron** — `provision-training-vm-auto-retry.yml` fired **every 10
   min forever** (144 runs/day) for a trainer VM that has been provisioned for
   weeks; each tick SSH'd the live VM + pip-installed the OCI SDK for a no-op.
   - Fix: removed the `schedule:` cron; the workflow is `workflow_dispatch`-only
     now (re-provision on demand if the trainer VM is ever terminated).

## Standing rules (apply to every new workflow)

- **Cap `retention-days` on every `upload-artifact`** — 7 days unless there's a
  specific reason. The default is 90.
- **Never add a forever-cron for a one-time job.** If a schedule exists to wait
  for a condition (capacity, provisioning), it must stop once the condition is
  met — prefer `workflow_dispatch` + a manual/Claude re-trigger over an
  unbounded cron.
- **Dedupe triggers.** `push: <branch-glob>` + `pull_request` double-builds
  every PR'd branch. Pick one (usually `pull_request` for validation +
  `push: [main]` for post-merge).
- **Every repo that uploads artifacts has a `purge-artifacts.yml`** (daily 7-day
  schedule + an on-demand total purge). The bot + Android both have one;
  `ict-trader-dashboard` uploads no artifacts so it doesn't need one.

## How to free a full quota on demand (Claude-driven, no operator)

The hosted GitHub MCP **cannot dispatch workflows** (`run_workflow` → 403), so
the purges are fired by **opening an issue** (the MCP can do that):

- **ict-trading-bot** — open an issue with the label **`purge-artifacts-now`**
  (body may set `older_than_days: 0` for a total purge / `dry_run: true` to
  preview). `purge-artifacts.yml` runs, comments the result, closes the issue.
- **ict-trader-android** — open an issue whose **title starts with
  `[purge-artifacts]`** (title-based, no label dependency). Same behaviour.

Both also run a **daily 03:00/03:30 UTC** schedule (delete > 7 days old) as the
self-healing safety net — so even if a workflow re-introduces a long retention,
storage drifts back down within a day without intervention.

## Periodic check (fold into /health-review)

Once in a while, confirm nothing has regressed: no unbounded cron reappeared,
no `upload-artifact` without `retention-days`, and the per-repo purges are still
present + scheduled. If the quota ever fills again, fire the on-demand purges
above first, then find the workflow that re-introduced the bloat.
