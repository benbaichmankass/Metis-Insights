# PM-side VM diag relay

The PM-side / web-sandbox session can't reach the Oracle VM directly
(see `vm-operator-mode.md` § 9 for why — the platform allowlist
accepts `*.github.com`, `*.vercel.app`, `*.anthropic.com`, etc., not
`158.178.210.252:*`). This doc is the contract for how a session
fetches `/api/diag/*` data anyway.

If you skim nothing else: open a labelled issue, wait, read the
result comment.

## TL;DR — fetching diag data from a sandbox session

```
1. Use `mcp__github__issue_write` (operation: open) with:
     title  = "[diag-request] snapshot?limit=200"
     labels = ["vm-diag-request"]
     body   = "" (anything, ignored by the workflow)

2. Wait ~30–60 s. The `vm-diag-snapshot` GitHub Actions workflow
   triggers on `issues.opened` filtered to that label, runs the
   diag fetch over SSH + curl, posts the JSON back as a comment,
   and closes the issue.

3. Poll `mcp__github__issue_read` (operation: get_comments) on the
   issue number. The newest comment whose author is
   `github-actions[bot]` carries:
     **vm-diag-snapshot** result for `<path>`
     Run: <url>
     Bytes: <size>

     ```json
     <pretty-printed snapshot or audit/journal/services tail>
     ```

4. Parse and proceed. Closed issues stay around as a permanent
   audit log of every diag query in the repo's issue history.
```

`<path>` can be any of the read-only diag endpoints documented in
`vm-operator-mode.md` § 9 — `snapshot?limit=N`, `audit?limit=N`,
`journal?table={order_packages|trades}&limit=N`, `status`,
`services`, `journalctl?unit=<allowlisted>&lines=N`,
`log_file?name={audit|status|heartbeat|bot_log}&lines=N`.

## TL;DR — fetching from outside a session (operator)

```
Actions → vm-diag-snapshot → Run workflow → main → defaults → run.
```

The `workflow_dispatch` path keeps the artifact on the run page (the
issue path doesn't, to avoid duplicating data). Use this when you
want the full JSON downloadable rather than embedded in an issue
comment.

## Why this shape

I (Claude on the web sandbox) have GitHub MCP tools that are good at:

- creating issues (`issue_write`)
- reading issues + comments (`issue_read`, `pull_request_read` for
  the comments-on-PR variant)
- creating PRs and committing files

I have **no** MCP tool for:

- `workflow_dispatch` (firing a workflow programmatically)
- listing or downloading workflow run artifacts
- streaming run logs

So the relay can't be driven by `workflow_dispatch`. The cleanest
trigger I can drive is `issues.opened` filtered by label, and the
cleanest result channel is an issue comment from
`github-actions[bot]`. Both are first-class objects in the GitHub
MCP I already have.

If/when a richer GitHub MCP becomes available (the official
`github/github-mcp-server` has an `actions` toolset that exposes
`run_workflow` + `download_workflow_run_artifact`), this relay can
collapse back to a single `workflow_dispatch` call from the session.
Until then, the issue-driven loop is the contract.

## Trust boundary

Tier 1 read-only — same class as everything else in
`/api/diag/*`. The workflow:

- only runs `curl -sS --fail -H 'Authorization: Bearer …' …
  /api/diag/<path>` over the SSH tunnel — fixed-form, no shell
  expansion of the issue title beyond a regex-validated path
  fragment (`^[A-Za-z0-9/?&=_.-]+$`)
- never SSHes a non-curl command
- doesn't call any of the routes that `vm-operator-mode.md` § 9
  marks Tier 3 (mutating routes don't exist on the diag surface
  anyway; the workflow can't reach them by construction)

The trust boundary is entirely on the FastAPI router
`src/web/api/routers/diag.py` (which is itself protected by
`DIAG_READ_TOKEN`). The workflow is just a transport.

`secrets.VM_SSH_KEY` and `secrets.DIAG_READ_TOKEN` never appear in
the run log — GitHub auto-masks any value matching a registered
secret.

## Prerequisites (one-time setup, already done)

- repo secret `VM_SSH_KEY` — contents of `ict-bot-ovm-private.key`
  (the same key the operator's Colab notebook uses).
- repo secret `DIAG_READ_TOKEN` — bearer from
  `/etc/ict-trader/web-api.env` on the VM.
- repo label `vm-diag-request` — auto-created by
  `.github/workflows/bootstrap-labels.yml`, which runs on every
  merge that touches its own file. To recreate manually if it ever
  gets deleted: Actions → bootstrap-labels → Run workflow.
- workflows `.github/workflows/vm-diag-snapshot.yml` and
  `.github/workflows/bootstrap-labels.yml` — committed in
  PR #486 + #487.

To rotate `DIAG_READ_TOKEN`: edit `/etc/ict-trader/web-api.env` on
the VM, restart `ict-web-api.service`, then update the GitHub repo
secret. The workflow picks up the new value on its next run.

## Failure modes

The workflow posts a structured failure comment back to the issue
when any step errors. Common causes:

| symptom | likely cause | fix |
|---|---|---|
| `VM_SSH_KEY secret is unset` | secret missing or misnamed | re-add under Settings → Secrets → Actions |
| `Permission denied (publickey)` | key contents corrupted on paste | paste again preserving newlines, including BEGIN/END markers |
| `curl: (7) Failed to connect to 127.0.0.1` | VM-side `ict-web-api.service` is down | `systemctl restart ict-web-api` on the VM |
| `HTTP 503 diag_disabled` | VM env doesn't have `DIAG_READ_TOKEN` set | check `/etc/ict-trader/web-api.env` |
| `HTTP 401` | GitHub secret ≠ VM env | re-sync token between the two |
| run never starts | label name typo on issue | label must be exactly `vm-diag-request` |
| run starts but never replies | github-actions bot lacks `issues: write` | workflow already declares it; check repo Actions permissions |

### When the relay itself is down — self-heal

If every diag request comes back with `❌ vm-diag-snapshot run failed`
and the underlying run shows `Process completed with exit code 7`,
that's `curl: (7) Failed to connect to 127.0.0.1` — the FastAPI
process serving `/api/diag/*` (`ict-web-api.service`) is down on the
VM. The diag relay can't fix itself; the operator-actions allowlist
doesn't include a web-api restart; and the sandbox session has no
`workflow_dispatch` MCP to fire it anyway.

The companion workflow `vm-web-api-recover.yml` (PR added it under
`/.github/workflows/`) closes that loop. Same trigger pattern as
this relay — `issues.opened` filtered to label `vm-web-api-recover`:

```
mcp__github__issue_write(method='create',
    title='[vm-recover] restart ict-web-api',
    labels=['vm-web-api-recover'],
    body='<one-sentence reason — e.g. relay #N exited 7 twice in a row>')
```

The workflow SSHes to the VM, runs `scripts/ops/restart_web_api.sh`
(fixed-form: `systemctl restart ict-web-api.service` + 30 s wait
for `is-active=active` + `/api/health` probe), then comments the
output back to the issue and closes it. Total round-trip ~30 s.

After the comment lands, retry the original diag request — the
relay should now succeed. The web-api restart has zero effect on
the trader process; only the dashboard / diag surface bounces.

## When NOT to use this

- **Anything mutating.** The diag surface is read-only by design;
  if you need to restart a service, edit a config, or push a new
  commit to the VM, that's the Telegram `/vm_write` path. See
  `vm-operator-mode.md` § 6.
- **Sub-second latency.** The relay adds 30–60 s of GitHub-Actions
  cold start + SSH handshake. For a one-off probe that's fine; for
  a tight diagnostic loop the operator should SSH directly from a
  laptop / Colab.
- **High volume.** GitHub-hosted runner minutes are free for public
  repos but metered for private. Don't loop this workflow at
  per-second rates; the VM is one Oracle free-tier shape and won't
  thank you.
