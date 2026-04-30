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

## Current known issue

The setup audit found committed Telegram and Bybit testnet credentials. Rotate them before pushing further changes.

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
