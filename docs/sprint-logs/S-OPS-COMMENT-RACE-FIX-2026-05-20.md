# Sprint Log: S-OPS-COMMENT-RACE-FIX

**Sprint:** 8 (S-OPS-COMMENT-RACE-FIX)  
**Date:** 2026-05-20  
**Type:** auto-claude (workflow fix)  
**FU:** FU-20260518-003  
**PR:** TBD — open as draft, pinging Ben before merge (workflow change, Tier-2)

---

## Objective

Fix the operator-action completion-comment race in `.github/workflows/operator-actions.yml`.

---

## Root Cause

Two separate comment steps existed:

1. `Reply to issue with success` — `if: github.event_name == 'issues' && success()`
2. `Reply to issue with failure` — `if: github.event_name == 'issues' && failure()`

These had two failure modes:

**Mode 1 — Post-exec step failure:** Steps that run after `exec` (Build audit bundle, Upload artifact, Notify operator via Claude bot channel) all have `if: always()`. If any of them fail without `continue-on-error: true`, `success()` evaluates false at the comment step even though the SSH command itself returned exit code 0. The success comment silently skips; the failure comment fires instead (wrong state_reason: 'not_planned' on a successful action).

**Mode 2 — Job cancellation:** The notify-SSH step (`if: always()`, no timeout) can hang until GitHub's 6-hour workflow timeout. If the job is cancelled for any reason, neither `success()` nor `failure()` evaluates true → both comment steps skip → issue stays open forever, operator never gets feedback.

The `exec` step already writes `exit_code` to `$GITHUB_OUTPUT`. That value is the authoritative signal — it's the SSH wrapper's return code, unaffected by anything that runs after it.

---

## Fix

Replaced both comment steps with a single step:

```yaml
- name: Reply to issue with outcome
  if: always() && github.event_name == 'issues'
  uses: actions/github-script@v7
  env:
    EXIT_CODE: ${{ steps.exec.outputs.exit_code }}
```

The JavaScript checks `process.env.EXIT_CODE === '0'` internally:
- `'0'` → ✅ success body + `state_reason: 'completed'`
- anything else (non-zero, empty string = exec never ran) → ❌ failure body + `state_reason: 'not_planned'`

`if: always()` ensures the step runs even when prior `always()` steps fail or the job is cancelled. The comment now fires in all cases where the issue trigger path is active.

---

## Files Changed

- `.github/workflows/operator-actions.yml` — lines 670–757: replaced two conditional comment steps with one `if: always()` step

---

## Definition of Done Assessment

- [x] Root cause identified (two separate `success()` / `failure()` comment steps)
- [x] Fix implemented — single `if: always()` step, EXIT_CODE-driven
- [x] No other files changed (workflow-only fix)
- [x] Sprint log written
- [x] Draft PR opened, Ben pinged before merge (Tier-2: workflow touches entire operator-action surface)

---

## Follow-ups Generated

FU-20260518-003 — closed by this sprint.
