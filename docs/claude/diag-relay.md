# PM-side VM diag relay

There are two transports for the read-only `/api/diag/*` surface, and
they return identical JSON. **Prefer direct; fall back to the relay.**

## Transport A — direct HTTP (preferred, when the session is configured)

A session holding **`DIAG_READ_TOKEN`** can hit the diag surface directly, in
one shot. **`DIAG_BASE_URL` is OPTIONAL** (corrected 2026-08-20) — see the
candidate-order note below:

```
scripts/ops/diag_fetch.sh 'audit?limit=600'
scripts/ops/diag_fetch.sh 'journal?table=trades&limit=100'
scripts/ops/diag_fetch.sh 'status'
```

`diag_fetch.sh` tries an **ORDERED LIST of candidate bases**, with the bearer
in a 0600 curl config (token never hits argv/logs), and prints `served by
<base>` on stderr so a reader can tell WHICH host answered. Exit `0` → JSON
on stdout. Exit `3` → no candidate answered → use Transport B. The bearer
value is delivered by the `get-diag-token` workflow; it is installed onto the
VM by `set-diag-token`. Both are documented under "Token management" below.

| configured `DIAG_BASE_URL` | order tried |
|---|---|
| unset | canonical HTTPS |
| plain-http, or names a known VM IP | **canonical HTTPS first**, configured second |
| a deliberately-set https base | configured first, canonical second |

Canonical is `https://ict-bot.duckdns.org` — the Caddy route the Svelte SPA
already uses, which works at the **default `Trusted`** network level with no
cloud-environment change.

> ⚠️ A raw `http://IP:8001` is not "may be refused" — it **IS** dropped
> (measured 2026-08-20: rc/http `000` against `141.145.193.91:8001` at the
> default `Trusted` level, while `https://ict-bot.duckdns.org` returned `200`
> on `/api/health` AND on a bearer'd `/api/diag/version` in the same session).
> The proxy allowlists by **scheme + hostname**, not by destination identity.
> **You no longer have to do anything about that** — the candidate list above
> handles it, which is why this note is a caveat rather than an instruction.
>
> The history is worth one line, because the previous remedy LOOKED like it
> worked: `diag_fetch.sh` used to "self-heal" a stale base by rewriting the
> retired micro to the **raw live IP** — a host the proxy drops. It logged
> success, timed out, and exited `3`, so every session paid the relay hop
> while a fix sat visibly in the file. A heal that produces an unreachable
> host is worse than no heal (`BL-20260818-DIAG-BASE-URL-POINTS-AT-TERMINATED-VM`).
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

**1. A NON-EMPTY BODY IS THE PATH LIST. The title is only the fallback.**
*(Corrected 2026-08-10 — this section previously read "The issue TITLE is
the diag path. The body is ignored," which has been false since the
multi-path batching change and cost a live session a rejected request.
`resolve_multi` in `vm-diag-snapshot.yml` is authoritative: it parses the
body FIRST — JSON array, fenced JSON array, or one path per line — and
falls back to the title **only when the body is empty or yields no
candidates**. Field beats comment.)*

So:

- **Put one `/api/diag/...` (or allowlisted `/api/bot/...`) path per line in
  the BODY, and no prose.** A sentence like *"path is in the title"* is
  itself parsed as a candidate path and fails validation, aborting the whole
  request before any VM contact — the failure comment says
  `MALFORMED REQUEST, not a VM outage`, and it means exactly that: the run
  tells you **nothing** about the VM's health.
- The title is display-only in that case. Give it a human label; it is still
  read (with the `[diag-request]` prefix stripped) when the body is empty.
- Up to `MAX_PATHS` = **15** paths per issue, fetched over ONE ssh session —
  batch your reads rather than opening an issue each (every issue is a
  separately-billed Actions job).
- Each path validates against `^[A-Za-z0-9/?&=_.:%-]+$`. The set **permits**
  `:` (and `.`, `%`, `=`, `&`), so an ISO timestamp in a query value (e.g.
  `journalctl?...&since=2026-05-10T21:13:00Z`) is valid. **One bad path fails
  the whole batch** (`sys.exit(1)`), so keep prose out entirely.
- `/api/bot/...` paths are allowed only from the relay's **read-only
  allowlist** in the same step (it mirrors the `workflow_dispatch` allowlist —
  keep the two in sync). It covers the soak surfaces (`pairs/soak`,
  `allocator/soak`, `exit-ladder/soak`, `fc-geometry/soak`), `performance`,
  `positions`, `trades/closed`, the `ml/*` family, and more; anything else is
  rejected by name rather than silently proxied.

A working body:

```
/api/diag/version
/api/bot/pairs/soak?limit=3
```

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

**5. Batch your reads — every issue is a separately-billed Actions job.**
This repo hit its GitHub Actions free-tier minutes cap (2,000/month) on
2026-07-06; in the first 5.5 days of that billing cycle alone this repo
opened 427 issues, 90% of them single-path relay/action calls (one
`/system-review` session alone opened 33 separate diag-request issues —
MB-20260706-CI-MINUTES). Every diag-relay issue, like every PR push, is
its own billed runner-minute, and point 4 above (bursts don't collide)
does NOT mean bursts are free.

Two ways to cut this, both live now:

- **Prefer the bundled endpoint.** `snapshot?limit=N` already carries
  heartbeat, status, audit tail, order_packages, trades, vm_health, and
  service states in one path. If that covers what you need, request it
  instead of separate `status`/`services`/`journal?table=trades` calls.
- **Batch multiple paths into ONE `vm-diag-snapshot` issue** (added
  2026-07-06, MB-20260706-CI-MINUTES). The issue **title** still carries a
  single path exactly as before (kept as the documented fallback — no
  existing muscle-memory/doc breaks). To request several paths in one
  issue instead, put a list in the issue **body**: either a JSON array
  (`["snapshot?limit=5", "audit?limit=200"]`, optionally inside a ```json
  fence) or one path per line (plain text or a `-`/`*` bulleted list).
  When the body parses to a non-empty list it wins over the title; an
  empty/unparsable body falls back to the single title path exactly as
  before — so old single-path issues keep working unmodified. All
  requested paths run over **one ssh session** server-side (the reconnect
  is the expensive/billed part, not the curl) and come back as **one**
  combined issue comment, one `## <path>` section per result. Capped at 15
  paths per issue (GitHub's ~65 KB comment limit); if the combined output
  would exceed the safe size, each path gets a `(truncated, N more bytes)`
  marker rather than being silently dropped. Every path — title or body —
  still passes through the exact same validation regex/allowlist
  individually; nothing about the trust contract is relaxed.

> **Diag paths are bare; `/api/bot` paths MUST be prefixed `api/bot/`.**
> The relay's default upstream is `/api/diag/`, so a diag path is written
> bare (`snapshot?limit=200`, `journal?table=trades&limit=200`,
> `db_info`). To reach an allowlisted read-only `/api/bot/*` Tier-1 GET you
> must namespace it explicitly — `api/bot/stats`,
> `api/bot/order-packages?limit=40`,
> `api/bot/db/table/order_packages?filter_col=status&filter_op=eq&filter_val=orphaned&limit=1`
> (the `db/table/<t>?filter_col=…&limit=1` form returns the filtered
> `total`, i.e. a status count). A bare `stats` / `db/table/...` resolves
> under `/api/diag/` → **404**, which a batched request now reports as
> `{"error":"fetch_failed","stage":"http_error","http_code":"404",…}`.
>
> **This warning did not prevent the mistake, which is why the MESSAGE was
> fixed (2026-08-13).** `BL-20260726-RELAY-APIBOT-FETCHFAILED` "corrected"
> the footgun by writing this very paragraph, and a session made the same
> error on 2026-08-13 anyway — because the old bare `{"error":"fetch_failed"}`
> named no stage, so at the moment of failure there was nothing to connect
> the result back to this note. It read as a VM-side outage and cost a full
> relay round-trip. A doc a reader must already suspect is not a remedy for a
> diagnostic that says nothing; see the `stage` field in the table under
> **Failure modes**.

**`trainer-vm-diag`'s `cmd:` block already supports chaining multiple
bash commands in one issue** — no workflow change was needed there. The
fix for that relay is purely behavioral: combine several commands into
one `cmd:` block instead of opening N issues for N commands.

Rule of thumb: before opening a diag-relay issue, ask "could this be one
`snapshot` call, or one multi-path body / multi-command `cmd:` block,
instead of N single-path issues?" If yes, batch it. See the `diag-data`
skill for the same guidance framed as the default recommended pattern.

### Any non-trivial `cmd:` script MUST be base64'd (2026-08-13)

**`cmd: |` is a YAML block scalar: it strips the block's common indentation
but PRESERVES every line's relative indentation.** So a Python snippet written
across multiple lines arrives at the interpreter indented, and dies on line 1:

```
File "<string>", line 2
  import json, glob
IndentationError: unexpected indent
```

A heredoc fails the same way for the same reason — the terminator (`PY`,
`EOF`) is indented too, so it never terminates.

One 2026-08-13 session hit this **four times** (#9063 heredoc, #9064, #9069,
#9076) and "fixed" it three times by collapsing the script to a single
physical line. That works and does not scale: a one-liner with nested
comprehensions is unreviewable, and the fourth failure happened precisely
because the analysis had outgrown one line.

**The durable form — indentation-immune, arbitrarily long, and reviewable
because the plaintext is in the issue body above it:**

```
cmd: |
  cd /home/ubuntu/ict-trading-bot 2>/dev/null || cd ~; echo '<BASE64>' | base64 -d | python3 -
```

Build it with `base64 -w0 script.py` (the `-w0` matters — wrapped base64
reintroduces newlines and the problem), and **`ast.parse` the script locally
first**: a syntax error costs a full relay round-trip, and the base64 hides it
from review. Paste the readable source into the issue body so the command
stays auditable — the encoding is transport, not obfuscation.

### Relay output is CAPPED, and a truncated result parses cleanly

A GitHub comment maxes out around 65 KB, and the relay appends a
`... (truncated)` marker rather than failing. **A truncated JSONL/line-oriented
payload still parses** — #9071 returned 241 well-formed rows of a longer set,
and nothing about those rows says they are partial. Computing a statistic over
them would have been a silent unasserted-denominator error (sub-class **C** in
CLAUDE.md § "Diagnostic provenance").

Two habits: **grep the payload for `truncated` before using it**, and prefer
**aggregating on the trainer** — send the arithmetic to the data and return
summary rows, rather than returning raw rows and summarising locally. The
second is what makes the cap a non-issue instead of a trap.

## TL;DR — fetching diag data from a sandbox session

**Default pattern — batch every path you'll need into ONE issue** (added
2026-07-06; see point 5 above for why this matters):

```
1. Use `mcp__github__issue_write` (method: create) with:
     title  = "[diag-request] snapshot?limit=5"   ← still required (fallback path)
     labels = ["vm-diag-request"]
     body   = ["snapshot?limit=5", "audit?limit=200",
                "journal?table=trades&limit=20"]
              ← a JSON array (or one path per line) of every path you
                need this round. Wins over the title when non-empty.

2. Wait ~30–60 s. The `vm-diag-snapshot` workflow fetches ALL listed
   paths over one ssh session and posts ONE combined comment, closes
   the issue.

3. Poll `mcp__github__issue_read` (method: get_comments). The newest
   `github-actions[bot]` comment carries one `## <path>` section per
   requested path, each with its own fenced JSON block.

4. Parse and proceed. Closed issues stay as a permanent audit log.
```

**Single-path fallback (still fully supported, unchanged since before
2026-07-06)** — use when you only need one path:

```
1. Use `mcp__github__issue_write` (method: create) with:
     title  = "[diag-request] snapshot?limit=5"
     labels = ["vm-diag-request"]
     body   = ""  ← empty/unparsable body falls back to the title path

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
     **vm-diag-snapshot** result — 1 path(s) fetched over one ssh session
     Run: <url>

     ## `<path>`
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
  artifact (dispatch) or an issue comment (issue path).
  ⚠️ **GATED ON `repository.private`, FAIL-CLOSED — and this repo is
  PUBLIC (measured 2026-08-25), so both delivery paths REFUSE today.**
  Do not reach for it here; the refusal is the feature, not an outage.
  Three states, never collapsed: `private` delivers · `public` refuses ·
  `unknown` (**we could not read the visibility**) refuses too, because
  a delivery justified by an unverified visibility claim is exactly the
  defect. Its header used to justify itself with *"this repo has exactly
  two principals … the audience is the owner only"* — a claim about
  visibility written into a comment, which went stale silently when the
  repo flipped public → private (2026-07-06) → public (2026-07-07). The
  cost was measured, not theoretical: a live bearer sat readable in
  issue #1615 comment 4507810670 from 2026-05-21 and still returned 200
  against the live VM on 2026-08-18
  (`BL-20260818-GET-DIAG-TOKEN-EMITS-SECRET-TO-PUBLIC-SURFACE`,
  `BL-20260818-DIAG-READ-TOKEN-PUBLIC-EXPOSURE-UNREMEDIATED`).
  **On a public repo the operator originates the value and sets it in
  both places by hand** — the repo Actions secret AND the consuming
  environment's `DIAG_READ_TOKEN` — then runs `set-diag-token` to push
  it to the VM. If the repo is ever made private again the workflow
  starts delivering on its own; nothing else needs changing.
  Delete the run/issue afterward to clear the at-rest copy.
- **`set-diag-token`** (label `set-diag-token`) — pushes the
  `DIAG_READ_TOKEN` repo secret onto the VM
  (`/etc/ict-trader/web-api.env`, atomic write + backup) and restarts
  `ict-web-api`, validating by `/api/diag/status` HTTP code only. The
  token flows one way (GitHub secret → VM) and is never printed.
  ⚠️ **READ THE `rotation_state`, NOT THE FACT THAT THE RUN WAS GREEN.**
  It fingerprints (sha256, 12-hex prefix — never the value) what the VM
  **serves** before the install and after it, and reports four states:
  `rotated` (the served fingerprint CHANGED and the new one authorizes) ·
  `unchanged` (identical — a **no-op**, reported loudly as one, never as
  a rotation) · `unknown_before` (**we could not read the pre-state**, so
  the run cannot say which happened — do not record it as a rotation) ·
  `failed`. It used to print *"authorized with the new token"* after
  testing only that the token authorizes, which is equally true of a
  value that never changed: run 32117038449 (2026-08-18) was green over
  an **unchanged** secret and a live token exposure stayed open behind
  the green (`BL-20260818-SET-DIAG-TOKEN-REPORTS-NEW-ON-UNCHANGED-VALUE`).

To **rotate**: `openssl rand -hex 32` → set the `DIAG_READ_TOKEN` repo
secret to it (Settings → Secrets → Actions — confirm it is the
**repository** Actions secret, not an environment-scoped one, and that
the edit saved) → run `set-diag-token` to push it to the VM → **confirm
it reported `rotation_state: rotated`, not `unchanged`** → set the same
value as the `DIAG_BASE_URL` consumer's `DIAG_READ_TOKEN` env var.
The pass condition is the **three-way probe**, not a green run: the OLD
token → 401, garbage → 401, and the NEW token → 200. A green run over an
unchanged secret is a state that has actually happened here. The relay (Transport B) reads the repo secret
directly, so it picks up the new value on its next run automatically.

## Failure modes

The workflow posts a structured failure comment back to the issue
when any step errors. Common causes:

**Read `stage` first on a batched request** (2026-08-13). A per-path failure
comes back as `{"error":"fetch_failed","stage":…,"http_code":…,"curl_exit":…}`
instead of the old bare `{"error":"fetch_failed"}`, which collapsed *every*
cause — service down, bad bearer, unknown table, malformed path, timeout —
into one string that named none of them. The two integers separate the stages
that matter, so **"the VM is broken" and "your request was wrong" are no
longer the same message**:

| `stage` | `http_code` / `curl_exit` | what actually happened |
|---|---|---|
| `no_http_response` | `000` / `7` | never reached the service — connection refused (`ict-web-api` down → see self-heal below) |
| `no_http_response` | `000` / `28` | no response before the per-path `--max-time` bound |
| `http_error` | `404` / `0` | **reached the VM and it answered** — the path is wrong. Nearly always a `/api/bot/*` path missing its `api/bot/` leader (see ⚠️ Common mistakes) |
| `http_error` | `401` / `0` | reached it; bearer rejected — GitHub secret ≠ VM env |
| `http_error` | `503` / `0` | reached it; `DIAG_READ_TOKEN` unset on the VM |
| `http_error` | `5xx` / `0` | reached it; the endpoint itself raised |

A non-zero `curl_exit` with a `2xx`/`4xx` code is not a thing — `--fail` was
removed from this branch, so curl exits 0 whenever the transport succeeded and
`http_code` alone carries the refusal. The single-path `workflow_dispatch`
branch deliberately still uses `--fail`: it has no fallback, so a failure turns
the run red and curl's stderr names the code in the run log.

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
