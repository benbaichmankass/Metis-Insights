# Security and secrets

## Never commit

- Telegram bot tokens.
- Telegram chat IDs if private.
- Bybit/Binance API keys or secrets.
- Bybit subaccount API keys (e.g. `vwap_strategy`, `current_account`) — these live
  only in the encrypted master secrets file and are never to be committed in plaintext.
- `.env` files.
- Colab userdata exports.
- SSH keys.
- `master-secrets.yaml` (plaintext master secrets file).
- `master-secrets.sops.yaml` (encrypted master secrets — contains ciphertext but keep it in Drive only).
- `age-keys.txt` (age private key — keep only in Drive and a password manager).
- Any generated `.env.*` files — these are runtime artifacts, not source files.

## Required storage

Use `.env`, Colab userdata, GitHub secrets, or VM environment variables.

`DIAG_READ_TOKEN` (the bearer for `/api/diag/*` read-only endpoints)
lives in `/etc/ict-trader/web-api.env` on the VM, rotated on the same
schedule as `JWT_SIGNING_KEY`. It is a read-only-scope token; granting
it write capability is Tier 3. See `docs/claude/vm-operator-mode.md`
§ 9.

For encrypted-at-rest master secrets, use the Google Drive SOPS workflow:
see `docs/claude/google-drive-master-secrets.md`.

## Claude must not

- Read or print decrypted secret values.
- Log, echo, or expose the contents of any `.env*` file.
- Print the output of `sops --decrypt` beyond confirming success/failure.
- Commit generated `.env.*` files — they are runtime-only artifacts.

## Generated env files

Files produced by `scripts/render_env_from_master.py` are:

- Written with `chmod 0600` (owner read/write only).
- Listed in `.gitignore` — never tracked.
- Deleted from Colab after copying to the target host.

## If a secret was committed

1. Revoke/rotate it first.
2. Remove or sanitize the current file.
3. Commit the cleanup.
4. Consider history rewrite only with explicit approval because it requires force-push coordination.

## httpx logs Telegram bot tokens in request URLs

**Root cause (discovered 2026-04-27):** `python-telegram-bot` uses `httpx` internally. At the default `INFO` log level, `httpx` emits full request URLs including the bot token:

```
https://api.telegram.org/bot<TOKEN>/getMe
https://api.telegram.org/bot<TOKEN>/sendMessage
```

These lines appear in stdout and any log aggregator attached to the process.

**Mitigations applied:**

1. `src/utils/log_redact.py` — `RedactingFilter` strips tokens from every log record before it reaches a handler. Installed on the root logger at startup.
2. `src/main.py` — calls `suppress_httpx_logging()` to raise `httpx` and `httpcore` loggers to `WARNING`, preventing the URL lines from being emitted at all.
3. `src/bot/alert_manager.py` — `print()` calls replaced with `logging`; `suppress_httpx_logging()` called before each send.

**If a token is observed in logs:**

1. Rotate the token immediately in BotFather.
2. Update the `.env` / VM environment variable.
3. Grep log files for the old token pattern and purge or archive them.

**Never log:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `BYBIT_API_KEY`, `BYBIT_API_SECRET`.

## Resolved finding (closed)

The setup audit once flagged committed Telegram + Bybit **testnet** credentials.
Those were rotated and the finding is closed (S-017). There is no standing
known leak: the `secret-scan` CI guard is a required check on `main` and blocks
any committed secret, so this is enforced going forward, not a manual reminder.

---

## Sandbox-side Telegram pings (S-021)

Claude Code sandboxes don't get the VM's `claude.env` by default, so
sandbox sessions can't ping Telegram directly — they have to fall back
to the `pending-pings.jsonl` queue + VM git-sync round-trip (≤ 5 min).
The harness-env path closes this gap when the operator opts in.

### How it works

1. The committed `.claude/settings.json` contains a `Stop` hook that
   reads the topmost `## CP-…` entry from `CHECKPOINT_LOG.md` and
   runs `scripts/notify_session.py` with that checkpoint id + title.
2. `notify_session.py` calls `src.runtime.notify.send_via_alert_manager`,
   which reads `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` from the
   process env. When unset, it logs a warning and exits 0 — the
   pipeline never crashes on missing creds.
3. The operator (per-machine) drops a `.claude/settings.local.json`
   alongside it with the actual tokens. That file is **gitignored**
   (`.gitignore` line 73) and Claude Code reads it AFTER `settings.json`,
   so the env vars win and are exposed to all subprocesses including
   the Stop hook.

### Operator setup (one-time, per machine)

```bash
cp .claude/settings.local.json.example .claude/settings.local.json
# edit and fill in TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID from the
# decrypted master-secrets.yaml → telegram.prod section
chmod 600 .claude/settings.local.json
```

### Rules

- **Never paste tokens into chat or commit them.** The whole point of
  this path is to keep secrets in a gitignored file the harness reads
  on session start. Pasting them in chat puts them in the
  conversation transcript — which is harder to rotate cleanly than a
  file on disk.
- **The committed `settings.json` carries the hook only — no env.**
  Putting empty placeholder env values in the committed file would
  override real env vars to blank strings; instead the env keys live
  in the `.local.json.example` template the operator copies + fills in.
- **Token redaction still applies.** `src/utils/log_redact.py` strips
  tokens from log records before any handler sees them; the Stop
  hook's `2>/dev/null` swallows stderr anyway, but the redacting
  filter is the defense in depth.
- **The Stop hook is non-blocking.** It returns exit 0 on missing
  creds, missing CHECKPOINT_LOG entry, or any subprocess failure.
  Claude Code never gets stuck on a broken hook.

---

## VM-resident Claude — secrets handling (S-014.5)

The runner has its own narrow secrets surface: the Anthropic API key.
The other secrets (Telegram bot token, exchange API keys, JWT signing
key, web app password hash) are read by sibling services and the runner
must NEVER touch them.

### Files and modes

| Path | Mode | Owner | Contents |
|---|---|---|---|
| `/etc/ict-trader/claude.env` | `0640` | `root:ubuntu` | `ANTHROPIC_API_KEY=...` |
| `/etc/claude/permissions.read.json` | `0644` | `root:root` | tier-1 policy (immutable to runner) |
| `/etc/claude/permissions.write.json` | `0644` | `root:root` | tier-2 policy (immutable to runner) |
| `/etc/claude/vm-marker` | `0644` | `root:root` | host id, ocid prefix, bootstrap utc |
| `/var/log/claude-vm/<id>.log` | `0640` | `ubuntu:ubuntu` | per-invocation transcript |
| `/usr/local/bin/claude-vm-dispatch` | `0755` | `root:root` | privileged dispatch wrapper (immutable to runner) |
| `/etc/sudoers.d/claude-vm-runner` | `0440` | `root:root` | passwordless sudo for the wrapper only |

The runner runs as `ubuntu`, can **read** `claude.env` (group), and
cannot **write** any of the policy files (root-owned, world-readable
only). This is defense in depth — the policy files are also in the
Tier 3 deny list.

### Hard rules

1. The runner **never** echoes the contents of `claude.env`, `.env`,
   `master-secrets*`, or any path matching `*credential*`/`*secret*`.
   The Tier 3 patterns in `src/bot/vm_runner.py` refuse the prompt
   before Claude even spawns.
2. `Bash(env)` and `Bash(printenv:*)` are denied in both tier profiles.
   Even a tier-2 confirmed invocation cannot dump the process env.
3. Telegram replies are checked through `secret_scan.py` before posting
   for tier-2 invocations. Non-clean → reply suppressed, alert posted
   instead with the offending pattern type (not the value).
4. API key rotation is **out of band only**: SSH to the VM as `ubuntu`,
   `sudo $EDITOR /etc/ict-trader/claude.env`, `sudo systemctl restart
   ict-telegram-bot`. Never request rotation through `/vm_write`; the
   runner will refuse via the Tier 3 `(ANTHROPIC|TELEGRAM|JWT|WEBAPP).*KEY`
   pattern.
5. Transcripts under `/var/log/claude-vm/` are kept for **30 days** via
   logrotate. After that, they're deleted. They are NOT shipped off-VM —
   the operator chat is the audit channel for offsite copies.

### Threat model

* **Compromised Anthropic API key:** the worst case is unauthorised LLM
  spend. The key has no path to trading orders or exchange APIs.
  Rotation per § 4 above.
* **Compromised Telegram bot token:** an attacker who has the bot token
  AND the operator chat id can dispatch `/vm_write` and pass the
  confirmation step. Mitigation: chat-id allowlist (already enforced),
  Tier 3 hard blocks on the worst actions, and the operator notices a
  shadow tier-2 invocation in the bot's reply history.
* **Compromised `ubuntu` user on the VM:** game over for everything,
  including the runner. The runner is not a privileged escalation
  vector beyond what `ubuntu` already has — it just makes the existing
  surface remotely actionable. Same threat model as the live trader.
