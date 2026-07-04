# PM-side VM diag relay

There are two transports for the read-only `/api/diag/*` surface, and
they return identical JSON. **Prefer direct; fall back to the relay.**

## Transport A — direct HTTP (preferred, when the session is configured)

A session whose cloud environment sets `DIAG_BASE_URL` +
`DIAG_READ_TOKEN` (and whose Network access permits egress to the host)
can hit the diag surface directly, in one shot:

```
scripts/ops/diag_fetch.sh 'audit?limit=600'
scripts/ops/diag_fetch.sh 'journal?table=trades&limit=100'
scripts/ops/diag_fetch.sh 'status'
```

`diag_fetch.sh` resolves `$DIAG_BASE_URL/api/diag/<path>` with the
bearer in a 0600 curl config (token never hits argv/logs). Exit `0` →
JSON on stdout. Exit `3` → direct path unavailable (env unset, egress
blocked, or web-api down) → use Transport B. The bearer value is
delivered by the `get-diag-token` workflow; it is installed onto the VM
by `set-diag-token`. Both are documented under "Token management" below.

> ⚠️ Direct egress to a raw `http://IP:8001` may still be refused by
> the platform's HTTP/HTTPS security proxy even at Network access =
> Full (it filters by hostname; a non-standard port on a bare IP can be
> dropped). If `diag_fetch.sh` keeps returning `3` despite the env vars
> being set, point `DIAG_BASE_URL` at an HTTPS **hostname** for the diag
> API. Until that's in place, Transport B keeps everything working.
>
> Also note: SSH from a web session is impossible regardless of Network
> access — the proxy is HTTP/HTTPS only. So direct access covers the
> diag *read* API only; anything needing arbitrary VM bash stays on the
> relays (Transport B / trainer-vm-diag).

## Transport B — GitHub-issue relay (fallback, always available)

When direct access isn't configured (or `diag_fetch.sh` returns `3`),
the session fetches `/api/diag/*` through a GitHub Actions relay. This
is the original mechanism and needs no per-session setup.

If you skim nothing else: open a labelled issue **with the exact title
format below**, wait, read the result comment.

## ⚠️ Common mistakes (read before first use)

**1. The issue TITLE is the diag path. The body is ignored.**
The workflow reads the title, strips the `[diag-request]` prefix, and
passes the remainder directly to `curl .../api/diag/<path>`. It
validates against `^[A-Za-z0-9/?&=_.:%-]+$` — spaces, commas, and any
character *outside* that set in the title cause an immediate validation
error. Note the set **permits** `:` (and `.`, `%`, `=`, `&`), so an ISO
timestamp in a query value (e.g. `journalctl?...&since=2026-05-10T21:13:00Z`,
or `shadow_stats?since=2026-05-28T00:00:00Z`) is a valid path. The body
content is never read.

**2. `cmd:` in the body is for `trainer-vm-diag`, NOT this workflow.**
`trainer-vm-diag` runs arbitrary bash on the trainer VM and reads the
`cmd:` field from the issue body. `vm-diag-snapshot` only runs a
fixed-form curl — no shell, body ignored. These are two completely
different workflows.

**3. Use `limit=5` to see packages/trades; `limit=200` only shows audit_tail.**
GitHub truncates issue comments at ~55 kB. `snapshot?limit=200` produces
~665 kB; only the `audit_tail` array (200 entries × ~1 kB each) fits.
The `order_packages`, `trades`, and `vm_health` sections are always
truncated out. Use `snapshot?limit=5` when you need to inspect positions,
packages, or trade SL/TP. Use `audit?limit=200` only for audit history.

**4. Back-to-back requests run concurrently — no spacing needed.**
Since 2026-07-04 (BL-20260611-002) the concurrency group is keyed on
the issue number, so each request gets its own lane: bursts execute in
parallel and cannot cancel one another. (The earlier shared-group setup
dropped queued bursts even with `cancel-in-progress: false` — GitHub
keeps at most one PENDING run per group; verified 2026-06-11 and
2026-07-03.) Each job stays bounded by `timeout-minutes: 5` plus the
SSH/curl timeouts. Fire as many as you need.

## TL;DR — fetching diag data from a sandbox session

```
1. Use `mcp__github__issue_write` (method: create) with:
     title  = "[diag-request] snapshot?limit=5"
     labels = ["vm-diag-request"]
     body   = ""  ← ignored; anything or empty works

   Use snapshot?limit=5 for packages/trades/health.
   Use audit?limit=200 for audit trail only.
   Use journal?table=trades&limit=20 for trade rows.
   Use journalctl?unit=ict-trader-live.service&lines=100 for logs.

2. Wait ~30–60 s. The `vm-diag-snapshot` GitHub Actions workflow
   triggers on `issues.opened` filtered to that label, runs the
   diag fetch over SSH + curl, posts the JSON back as a comment,
   and closes the issue.

3. Poll `mcp__github__issue_read` (method: get_comments) on the
   issue number. The newest comment from `github-actions[bot]` carries:
     **vm-diag-snapshot** result for `<path>`
     Run: <url>
     Bytes: <size>

     ```json
     <pretty-printed snapshot>
     ```

4. Parse and proceed. Closed issues stay as a permanent audit log.
```

`<path>` can be any of the read-only diag endpoints documented in
`vm-operator-mode.md` § 9 — `snapshot?limit=N`, `audit?limit=N`,
`journal?table={order_packages|trades}&limit=N`, `status`,
`services`, `journalctl?unit=<allowlisted>&lines=N[&since=<iso>][&until=<iso>]`,
`log_file?name={audit|status|heartbeat|bot_log}&lines=N`.

`journalctl` `since` / `until` accept strict ISO-8601 timestamps
(`2026-05-10T21:13:00Z`, `2026-05-10T21:13:00+00:00`, or
`2026-05-10 21:13:00`) and forward to `journalctl --since` / `--until`
on the VM. Without them the endpoint is tail-only and reaches back
~20-30 minutes at the live-trader's log rate; with them, any
historical window the systemd journal still retains is reachable. The
55KB GitHub issue-comment cap still applies, so very large windows
should pair `since=` with a tight `until=` to keep the response
under the cap. Added in PR #821 (FU-20260511-001).

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
  fragment (`^[A-Za-z0-9/?&=_.:%-]+$`)
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

## Token management (get-diag-token / set-diag-token)

Two issue-/dispatch-driven workflows manage the bearer without anyone
SSHing the VM by hand:

- **`get-diag-token`** (label `get-diag-token`) — resolves the current
  `DIAG_READ_TOKEN` value (from the repo secret if set, else read off
  the VM) and delivers it to the repo owner as a short-retention
  artifact (dispatch) or an issue comment (issue path). Use it to fill
  a cloud environment's `DIAG_READ_TOKEN` env var for Transport A.
  Delete the run/issue afterward to clear the at-rest copy.
- **`set-diag-token`** (label `set-diag-token`) — pushes the
  `DIAG_READ_TOKEN` repo secret onto the VM
  (`/etc/ict-trader/web-api.env`, atomic write + backup) and restarts
  `ict-web-api`, validating by `/api/diag/status` HTTP code only. The
  token flows one way (GitHub secret → VM) and is never printed.

To **rotate**: `openssl rand -hex 32` → set the `DIAG_READ_TOKEN` repo
secret to it (Settings → Secrets → Actions) → run `set-diag-token` to
push it to the VM → set the same value as the `DIAG_BASE_URL` consumer's
`DIAG_READ_TOKEN` env var. The relay (Transport B) reads the repo secret
directly, so it picks up the new value on its next run automatically.

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
| `Rejected diag_path (illegal characters)` | issue title has spaces, commas, or other non-path chars | use exact format `[diag-request] snapshot?limit=5` |
| run never replies, issue stays open | runner hung past `timeout-minutes: 5` (extremely rare with current SSH/curl timeouts) | re-open the issue; if recurring, check vm-web-api self-heal |

### When the relay itself is down — self-heal

If every diag request comes back with `❌ vm-diag-snapshot run failed`
and the underlying run shows `Process completed with exit code 7`,
that's `curl: (7) Failed to connect to 127.0.0.1` — the FastAPI
process serving `/api/diag/*` (`ict-web-api.service`) is down on the
VM. The diag relay can't fix itself; the system-actions allowlist
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

### Posting a prop report-back — the write counterpart

This relay (and the read-only `/api/bot/*` allowlist on
`vm-diag-snapshot`) is **GET-only**. The one inbound write a PM-side
session needs is the **Breakout manual-bridge report-back** — telling
the bot about a prop fill/close or an account-status snapshot it has no
broker feed for. That goes through a separate issue-driven workflow,
`prop-report.yml` (label `prop-report`), which POSTs the report to
`POST /api/bot/prop/report` over SSH + curl:

```
mcp__github__issue_write(method='create',
    title='[prop-report] breakout fill',
    labels=['prop-report'],
    body='```json\n{"account_id":"breakout_1","symbol":"MES","direction":"long","status":"closed","entry_price":5000,"exit_price":5010,"qty":1,"pnl":50,"reason":"tp"}\n```')
```

The issue **body** carries a single JSON object (the ```json fence is
optional — it's stripped); the workflow validates it is one object
(`jq -e 'type=="object"'`), POSTs it to the VM, and comments the
endpoint's JSON response + HTTP status back before closing the issue.
The body shapes are the two in `src/prop/prop_report.py::ingest_report`
(fill/close, or `kind:"account_status"`). The untrusted body never gets
inline-interpolated into the remote shell (base64 hop). The endpoint is
**Tier 2** (DB write + notification) and **token-gated by
`DASHBOARD_API_TOKEN` when set** — the workflow sources that token from
`/etc/ict-trader/web-api.env` **on the VM** and adds the bearer header
only when present (it never reaches the runner / run log); when the VM
hasn't set the token the endpoint accepts the call without it. Carry the
operator's Tier-2 OK into the issue `body` as the audit record. This is
the only write the relay family exposes; everything else mutating stays
on `system-actions` / Telegram `/vm_write`.

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
