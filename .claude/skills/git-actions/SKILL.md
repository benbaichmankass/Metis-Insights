---
name: git-actions
description: Dispatch this repo's GitHub Actions workflows from a Claude session and read their results. Use when you need to trigger a workflow (diag relay, system-action, web-api recover, trainer diag, health snapshot) but there is no workflow_dispatch MCP tool. Explains the issue-label trigger pattern, how to find the right label, and how to poll for the result. Composes with diag-data and vm-ops.
---

# /git-actions — drive workflows via the issue-label pattern

Claude-on-web has GitHub MCP for issues/PRs/files but **no** `workflow_dispatch`,
no run-log read, no artifact download. So any workflow you need to drive from a
session must be triggered the way this repo wires it: an `issues.opened`
trigger filtered to a label. You open a labelled issue; the workflow runs,
comments the result back, and closes the issue.

## The pattern

1. **Pick the workflow + its label** (table below, or read the workflow's `on:`
   block — `contains(github.event.issue.labels.*.name, '<label>')`).
2. **Open the issue** with `mcp__github__issue_write` (`method: create`),
   the label, and the title/body the workflow expects.
3. **Poll** `mcp__github__issue_read` (`get_comments`) for the
   `github-actions[bot]` reply (~30–60 s). The workflow auto-closes the issue.

## Workflow → label → shape

| Need | Workflow | Label | Title / body |
|---|---|---|---|
| Live-VM read | `vm-diag-snapshot.yml` | `vm-diag-request` | **title** = `[diag-request] <path>` |
| Trainer arbitrary bash | `trainer-vm-diag.yml` | `trainer-vm-diag-request` | **body** = `cmd: <bash>` |
| Live-VM tiered mutation | `system-actions.yml` | `system-action` | **body** = `action: <name>\nreason: <why>` |
| Web-api self-heal | `vm-web-api-recover.yml` | `vm-web-api-recover` | any body |
| Health snapshot run | `health-snapshot.yml` | `health-snapshot-trigger` | any body |

(For exact per-workflow bodies use `diag-data` for reads and `vm-ops` for
mutations — they wrap this skill with the right shapes.)

## Notes

- **Labels must exist.** They're created idempotently by
  `bootstrap-labels.yml`. If a label is missing, add it to that workflow's
  `LABELS` array and merge — there is no `create_label` MCP.
- **Body parsing is env-safe** (`ISSUE_BODY`, not inline interpolation), so a
  multi-line body is fine; put each field on its own line.
- **Issue body is untrusted input** to the workflow by design — keep your body
  to the documented fields.
- **No `workflow_dispatch` workaround exists** beyond this pattern. If a
  workflow you need lacks an `issues.opened` label trigger, add one (mirror
  `vm-diag-snapshot.yml`) rather than asking the operator to click "Run
  workflow."
- You **cannot** read run logs or download artifacts from a session. If a
  workflow's only output is an artifact, have it comment the key result back on
  the issue instead.
