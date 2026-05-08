# VM operator mode — the contract

This doc binds any Claude Code session that runs **on the Oracle VM**
(`158.178.210.252`, `eu-paris-1`, `VM.Standard.E2.1.Micro`) and any
Telegram-dispatched invocation that reaches that VM.

It is the operator manual for "Claude on the VM." It is **not** a feature
spec — features live in sprint prompts. The rules below override any
session prompt that conflicts.

> If you are reading this from the web sandbox or a developer laptop,
> these tiers do **not** apply to you. They apply only when the working
> tree is on the VM and the runner unit is the parent process.

---

## 1. Detection — how Claude knows it's on the VM

A VM-resident session has `/etc/claude/vm-marker` present. The marker
contains the VM hostname, the Oracle OCID prefix, and the bootstrap
date. The runner unit refuses to start if the marker is missing.

If you are not sure whether you are on the VM, run
`cat /etc/claude/vm-marker`. Absent file → web sandbox / laptop → the
tier rules below do not bind.

## 2. Authority tiers

Three tiers, hard-coded in `deploy/claude-permissions.{read,write}.json`
and referenced by the runner systemd unit. The bot decides which file to
load based on the command (`/vm` vs `/vm_write`) before invoking
`claude -p`.

### Tier 1 — autonomous (read-only ops)

No prompt. `/vm <prompt>` invocations operate at this tier.

Allowed without confirmation:
- `Bash(journalctl:*)`, `Bash(systemctl status:*)`, `Bash(systemctl is-active:*)`,
  `Bash(systemctl cat:*)`, `Bash(systemctl list-units:*)`.
- `Bash(git status:*)`, `Bash(git log:*)`, `Bash(git diff:*)`,
  `Bash(git branch:*)`, `Bash(git remote:*)`, `Bash(git fetch:*)`.
- `Bash(ls:*)`, `Bash(cat:*)`, `Bash(grep:*)`, `Bash(find:*)`,
  `Bash(head:*)`, `Bash(tail:*)`, `Bash(wc:*)`, `Bash(file:*)`,
  `Bash(stat:*)`, `Bash(du:*)`, `Bash(df:*)`, `Bash(free:*)`,
  `Bash(ps:*)`, `Bash(uptime:*)`, `Bash(hostnamectl:*)`.
- `Bash(python3 scripts/repo_inventory.py)`, `Bash(python3 scripts/secret_scan.py)`,
  `Bash(PYTHONPATH=. pytest --collect-only*)`,
  `Bash(PYTHONPATH=. pytest tests/*)` (read-only — pytest does not
  mutate the trader's runtime state, the journal db is path-scoped).
- `Read(*)` for any file under `/home/ubuntu/ict-trading-bot/`,
  `/opt/ict-trading-bot/`, `/etc/ict-trader/`, `/etc/claude/`,
  `/var/log/`, `/run/log/`.
- All MCP read tools (GitHub `pull_request_read`, `list_*`, `get_*`).

### Tier 2 — Telegram-confirmed mutations

`/vm_write <prompt>` invocations operate at this tier. The bot **must**
post a confirmation prompt to the operator chat and wait for a `YES`
reply (or an inline-button tap) before spawning the runner. The
confirmation message includes the prompt verbatim and a 60-second
timeout.

Allowed under Tier 2 (in addition to all Tier 1):
- `Bash(systemctl start:*)`, `Bash(systemctl stop:*)`,
  `Bash(systemctl restart:*)`, `Bash(systemctl reload:*)`,
  `Bash(systemctl daemon-reload)`.
- `Bash(git add:*)`, `Bash(git commit:*)`, `Bash(git push:*)` (but not
  `--force`, see Tier 3).
- `Edit(*)` and `Write(*)` for files **not** in the Tier 3 deny list.
- MCP write tools (GitHub `create_pull_request`, `create_or_update_file`,
  `add_issue_comment`, `merge_pull_request` (non-main), etc.).
- `Bash(pip install --user:*)`, `Bash(npm install:*)` only inside
  project-local venvs / `node_modules`. Never global, never as root.

### Tier 3 — hard-blocked

Always denied. **No Telegram approval unlocks these.** A Tier 3 attempt
must abort the runner and post an alert to the operator chat with the
attempted action.

- `Edit(src/runtime/orders.py)`, `Edit(src/runtime/risk_counters.py)`,
  `Edit(src/runtime/notify.py)`, `Edit(src/runtime/signal_writer.py)`,
  `Edit(src/runtime/validation.py)`.
- `Edit(config/master-secrets.template.yaml)`, `Edit(config/*.yaml)`
  for live-trading configs (`config/accounts.yaml`,
  `config/risk_caps.yaml`).
- `Edit(/etc/claude/permissions.*.json)`, `Edit(/etc/claude/settings.json)`,
  `Edit(/etc/claude/vm-marker)`. The permission profile is
  immutable from inside the runner.
- `Bash(git push --force*)`, `Bash(git push:* main*)` to `main`
  (push to feature branches is fine; merging into `main` happens via
  `merge_pull_request`).
- `Bash(rm -rf*)`, `Bash(rm -fr*)`, `Bash(rm:*/.git/*)`,
  `Bash(rm:*trade_journal*)`, `Bash(rm:*master-secrets*)`.
- `Bash(sqlite3:*DROP*)`, `Bash(sqlite3:*DELETE FROM*)` against
  `trade_journal.db` or any path matching `*credentials*`, `*secret*`,
  `*key*`.
- `Bash(systemctl disable ict-trader-live*)`,
  `Bash(systemctl mask ict-trader-live*)`. Stopping the trader is Tier
  2; disabling/masking it (so it won't restart) is Tier 3.
- `Bash(curl:*api.anthropic.com*)`, `Bash(env)`, `Bash(printenv)`,
  any command that would echo secrets to the journal.
- Rotating keys: `JWT_SIGNING_KEY`, `WEBAPP_PASSWORD_SHA256`,
  `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, exchange API keys. Out
  of band only.

## 3. Audit trail

Every `/vm` and `/vm_write` invocation produces:
1. A journal line in `claude-vm-runner@<id>.service` with the prompt
   text, the resolved tier, the exit code, and the wall-clock duration.
2. A summary post to the Telegram operator chat (the same chat that
   issued the command). Tier 2 invocations also post the diff/output.
3. For Tier 2 PRs/commits: standard git history. No special audit
   beyond commit author = `ubuntu` and a `vm-runner` trailer in the
   commit message.

The runner **never** echoes the `ANTHROPIC_API_KEY`, the contents of
`.env`, or any file matching `*secret*` / `*credentials*` to either
stdout or Telegram. `secret_scan.py` runs as the last step of every
Tier 2 invocation; a non-clean scan aborts the response without
posting it.

## 4. Refusal protocol

If a prompt asks for a Tier 3 action, the runner refuses with:
```
TIER 3 BLOCKED: <action>
Reason: <one-line policy citation, e.g. "live-trading orders code is
immutable from VM-runner; rotate via PM laptop SSH only">
```

If a prompt is ambiguous between tiers, the runner picks the **lower**
tier and includes an `ASK_OPERATOR:` block in the response with the
ambiguous step. Never auto-escalate. Never assume a follow-up `YES` from
the operator means "and also that adjacent thing" — every escalation is
its own command.

## 5. Memory budget

The VM has 1 GB RAM shared between the live trader, the Telegram bot,
the web API, and now the runner. A 2 GB swap file backs spillover (added
by `scripts/vm_bootstrap.sh`).

The runner unit hard-caps memory at **400 MB** (`MemoryMax=400M` in the
unit). If Claude OOMs, it dies cleanly and the bot reports the kill;
the live trader is unaffected. **Never** raise `MemoryMax` from inside
the runner — that's Tier 3 (it's an `Edit(/etc/systemd/system/...)`).

`MemoryHigh=300M` triggers the kernel's memory pressure handling first,
giving the trader breathing room before the OOM.

## 6. Concurrency and dispatch path

The runner is a **oneshot** template unit (`claude-vm-runner@.service`).
Each Telegram invocation gets a unique `<id>` (UTC unix timestamp). Two
concurrent invocations are allowed but the bot serializes them at the
chat level: a second `/vm` or `/vm_write` while the first is running
gets a "busy — your prompt is queued" reply.

**Dispatch path (privilege boundary):**

1. Bot writes the prompt to `/run/claude/prompts/<id>.txt` as `ubuntu`.
2. Bot invokes `sudo -n /usr/local/bin/claude-vm-dispatch <id> <tier> <path>`.
   The sudoers drop-in `/etc/sudoers.d/claude-vm-runner` allows this
   exact command without a password and nothing else.
3. The wrapper validates inputs strictly (digits-only id, tier 1/2,
   prompt path under `/run/claude/prompts/<digits>.txt`), writes a
   per-invocation drop-in to `/run/systemd/system/claude-vm-runner@<id>.service.d/env.conf`
   with the tier profile + transcript path, and `systemctl start`s the
   template instance.
4. The unit runs `claude -p` as `ubuntu` with the tier profile, writing
   the transcript to `/var/log/claude-vm/<id>.log`.
5. On exit, the wrapper deletes the drop-in and reset-failed's the
   unit. The bot reads the transcript and posts it back to Telegram.

The wrapper is the *only* entry point with sudo. `systemd-run` and
`systemctl start` are not directly invocable by the bot.

Runner timeout: **5 minutes** (`TimeoutStartSec=300`). Beyond that, the
unit is killed and the bot posts the truncated transcript with a
`TIMEOUT` note.

## 7. Bootstrap and rotation

- One-time install: `scripts/vm_bootstrap.sh` (paste into Oracle Cloud
  Shell as `ubuntu`, follow the prompts for `ANTHROPIC_API_KEY` —
  written to `/etc/ict-trader/claude.env`, mode 600, owner
  `root:ubuntu`).
- API key rotation: replace the value in `/etc/ict-trader/claude.env`,
  run `sudo systemctl restart ict-telegram-bot`. No runner state to
  flush.
- Disable: `sudo systemctl mask claude-vm-runner@.service`. The bot
  detects the mask and tells the operator the feature is off.

## 8. What this does NOT replace

- Sprint workflow. S-014 PRs still go through the normal flow; the
  runner doesn't shortcut PR creation or merging unless the operator
  explicitly asks for that exact action.
- Deployment runbook. `docs/audit/sprint-013-deployment-runbook.md`
  remains authoritative for VM service changes; the runner is a
  *vehicle* for executing those steps, not a replacement for the
  procedure.
- The web sandbox session. This session (and any future web session)
  cannot SSH into the VM and has no mutation authority on it. The
  bridge from PM-side actions to VM-side actions is the Telegram bot,
  by design. PM-side sessions can read VM state via the diagnostic
  surface — see § 9.

## 9. PM-side / web-sandbox read access

PM-side Claude sessions (web sandbox, developer laptop) **cannot** SSH
into the VM and **cannot** take any mutating action against it directly
— restarts, config edits, code pushes to the VM filesystem, key
rotations, service control, all forbidden. There is no SSH key issued
to a PM-side session and there will not be one.

They **can** read the VM's diagnostic surface at
`https://<vm-host>:8001/api/diag/*`, gated by the bearer token
`DIAG_READ_TOKEN`. The surface is strictly read-only by construction:
GET routes only, parameterized SELECTs against `trade_journal.db`
opened with `mode=ro`, hard-coded allowlists for systemd units and log
file aliases, no shell-out except a fixed-argument `journalctl` /
`systemctl is-active` invocation against allowlisted unit names.

| Endpoint | Returns |
|---|---|
| `GET /api/diag/snapshot` | one-shot bundle: heartbeat, status.json, audit tail, recent `order_packages` + `trades`, vm_health, service states |
| `GET /api/diag/audit?limit=N` | tail of `runtime_logs/signal_audit.jsonl` |
| `GET /api/diag/journal?table={order_packages\|trades}&limit=N` | read-only SELECT |
| `GET /api/diag/status` | heartbeat + status.json + vm_health |
| `GET /api/diag/services` | `systemctl is-active` for every allowlisted unit |
| `GET /api/diag/journalctl?unit=<allowlisted>&lines=N` | systemd journal tail |
| `GET /api/diag/log_file?name={audit\|status\|heartbeat\|bot_log}&lines=N` | tail of allowlisted log file |

Every mutation — without exception — still goes through the Telegram
`/vm_write` path with explicit operator confirmation. Adding a mutating
route to `src/web/api/routers/diag.py` from a sandbox session is
**Tier 3** (immutable from sandbox, same trust class as
`src/runtime/orders.py`).

`DIAG_READ_TOKEN` lives in `/etc/ict-trader/web-api.env` (mode `0640`,
owner `root:ubuntu`) and rotates on the same schedule as
`JWT_SIGNING_KEY`. It is never pasted into chat; the operator hands the
URL + token to a PM-side session via a gitignored local file or shares
it through the Telegram operator chat for one-shot use.

If `DIAG_READ_TOKEN` is unset on the VM, the diag endpoints return
`503 diag_disabled` — the feature is opt-in.

### 9.a Sandbox sessions reach the diag surface via GitHub Actions

A Claude Code on the web session **cannot egress to the VM at all**
(the platform allowlist accepts `*.github.com`, `*.vercel.app`,
`*.anthropic.com`, etc., not `158.178.210.252:*`). The bridge is a
GitHub Actions workflow `vm-diag-snapshot` that:

1. Triggers on `issues.opened` filtered to label `vm-diag-request`
   (or on operator-driven `workflow_dispatch`).
2. SSH-tunnels to the VM with a key from repo secret `VM_SSH_KEY`.
3. Runs the *fixed-form* command
   `curl -sS --fail -H 'Authorization: Bearer …' http://127.0.0.1:8001/api/diag/<path>`.
4. Posts the JSON back as an issue comment from
   `github-actions[bot]` and closes the issue.

The trust contract is unchanged: only the GET endpoints in this
section are reachable through the relay (the path is regex-validated
against `^[A-Za-z0-9/?&=_.-]+$` before interpolation), and the
relay can't ship any non-curl command. Adding a mutating endpoint
to `src/web/api/routers/diag.py` from a sandbox session remains
**Tier 3** (immutable from sandbox).

See `docs/claude/diag-relay.md` for the full operator and
session-side flow + failure modes.

### 9.b Sandbox sessions reach a *narrow mutating surface* via operator-actions

A separate workflow `operator-actions.yml` extends the PM-side
bridge with a fixed allowlist of mutating actions: `status-check`,
`pull-latest-logs`, `restart-bot-service`, `reboot-vm`. There is no
freeform-command input. The contract — tiers, ping format, reboot
doctrine, sudoers requirements — lives in
`docs/claude/operator-actions.md`.

This is **not** a relaxation of the immutability rules above:

- All Tier-3 paths (strategy params, risk caps, account `mode`
  flips, live order code, key rotation) remain immutable from any
  sandbox session, regardless of the workflow being available.
- `restart-bot-service` and `reboot-vm` are Tier-2 in the
  PM-dispatch sense — Claude must ping the operator before
  triggering them. That ping is the same trust gate as `/vm_write`
  on the Telegram side.
- The workflow itself is the audit trail (artifact + run log + a
  repo-side record under `runtime_logs/operator_actions/`).
