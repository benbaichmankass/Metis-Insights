# Rotating API keys via Colab — no SSH required

> **⚠️ Command interface updated (2026-05, #1933).** The Telegram commands this
> guide references (`/set_keys`, `/accounts_status`, `/smoke_test`) were
> **removed** when the bot went menu-driven. Open the notebook via the **Open in
> Colab** link below (not `/set_keys`); verify balances via the dashboard /
> Android **Accounts** view (or `GET /api/bot/accounts/balances`); run the smoke
> test via `ALLOW_LIVE_TRADING=1 python scripts/smoke_test_trade.py`. This
> operator-self-service Colab flow still works; the **Claude-driven** credential
> path is the `sync-vm-secrets` workflow. (BL-20260525-001.)

The simple workflow. You set your secrets once in Colab, then any time
you need to push new keys to the trading VM you just open the notebook
and click `Runtime → Run all`.

**Open in Colab:**
[`notebooks/operator/rotate_api_keys.ipynb`](https://colab.research.google.com/github/benbaichmankass/ict-trading-bot/blob/main/notebooks/operator/rotate_api_keys.ipynb)

**One-time setup:** ~10 minutes (set Colab Secrets, paste SSH key).
**Each rotation:** ~30 seconds (open Colab, Run all, done).

---

## What this replaces

The previous workflow needed: SSH into VM → pull repo → render `.env.live`
from a sops-encrypted master file → restart systemd. Five steps with
two CLI tools. This replaces all of that with one click.

The notebook reads your secrets from Colab Secrets (which are scoped
to your Google account, never leave Google's infrastructure unless
you push them somewhere), generates a fresh `.env.live`, pushes it
to the VM via SSH, and restarts the trader.

---

## One-time setup

### 1. Generate Bybit API keys

Two keys (or one if you only run one strategy for now). For each:
1. Bybit dashboard → **Account & Security → API Management → Create New Key**.
2. Permissions: ✅ **Read** + ✅ **Trade** only. **Do NOT** enable Withdraw.
3. Lock the key to your VM's IP if you have a static one.

You'll get an **API Key** and **API Secret**. Keep the tab open — you'll
paste them into Colab Secrets in the next step.

### 2. Set up Colab Secrets

Open the notebook (link above), then on the left sidebar click the **🔑
key icon** ("Secrets"). Add each of these by name:

#### Required

| Secret name | What to paste |
|---|---|
| `BYBIT_API_KEY_1`     | The first Bybit key (used by `bybit_1` / turtle_soup) |
| `BYBIT_API_SECRET_1`  | The first Bybit secret |
| `BYBIT_API_KEY_2`     | The second Bybit key (used by `bybit_2` / vwap) |
| `BYBIT_API_SECRET_2`  | The second Bybit secret |
| `TELEGRAM_BOT_TOKEN`  | Your Telegram bot token (from `@BotFather`) |
| `TELEGRAM_CHAT_ID`    | Your numeric chat id |
| `VM_SSH_HOST`         | The VM's hostname or public IP |
| `VM_SSH_USER`         | SSH user on the VM (usually `ubuntu`) |

For each secret, also flick on the **"Notebook access"** toggle so the
notebook can read it.

> The SSH **private key** is NOT a Colab Secret. You upload it as a file
> in the next step — no copy-pasting key contents.

#### Optional (skip if not used)

| Secret name | What to paste |
|---|---|
| `BREAKOUT_API_KEY_1`    | Prop-firm key (currently disabled in `accounts.yaml`) |
| `BREAKOUT_API_SECRET_1` | Prop-firm secret |
| `NEWS_API_KEY`          | NewsAPI key — only set if you want the news layer enabled |
| `TELEGRAM_CLAUDE_BOT_TOKEN` | Token for the Claude bridge bot (a SECOND Telegram bot, separate from `TELEGRAM_BOT_TOKEN`). Only required if running `ict-claude-bridge.service`. |
| `ANTHROPIC_API_KEY`     | Anthropic API key for the Claude bridge bot. Only required if running `ict-claude-bridge.service`. |
| `CLAUDE_MODEL`          | Override the model id for the Claude bridge (default `claude-opus-4-7`). |

> The Claude bridge service (`ict-claude-bridge.service`) is shipped
> disabled. The notebook only renders these three vars into `.env` when
> both `TELEGRAM_CLAUDE_BOT_TOKEN` and `ANTHROPIC_API_KEY` are set;
> enabling the service is a separate manual step on the VM.

### 3. Put your SSH private key in Google Drive (preferred)

The cleanest path: keep your VM SSH **private** key in the same Drive
folder as your encrypted master secrets. The notebook mounts Drive on
the **first** cell of `Run all` and reads the key from there.

1. Open Google Drive in a browser.
2. Navigate to (or create) `My Drive/ICT_Bot_Secrets/`.
3. Upload your VM SSH private key there.

**Filename:** the notebook accepts any of these (first match wins):

| Preferred | Also accepted |
|---|---|
| `ict-bot-ovm-private.key` | `vm_ssh_key`, `id_rsa`, `id_ed25519`, `id_ecdsa` |

Or, if your key has a different name and you don't want to rename:
add a Colab Secret called `SSH_KEY_FILE` with the exact filename. The
notebook will look for it first.

**Make sure it's the PRIVATE key**, not the public one (`.pub`). The
notebook checks the first line of the file and refuses anything that
doesn't start with `-----BEGIN`.

#### Fallback: file-picker upload

If your key file isn't in Drive (or Drive can't be mounted in this
session), the notebook automatically pops a **"Choose Files"** picker
in the cell output. Click it and select your key file from your
computer. The notebook uses it for this run only — the file is wiped
when the Colab session ends.

This is the automatic safety net: even if Drive setup is wrong, you
won't get stuck.

### 4. Confirm SSH from the VM works

You only need to do this once. From your laptop:
```bash
ssh -i ~/.ssh/id_ed25519 ubuntu@<your-vm-ip> echo ok
```
Should print `ok`. If it asks for a password, the key isn't authorized
on the VM yet — add the matching `.pub` to `~/.ssh/authorized_keys` on
the VM first.

The same key file must be the one you upload to Colab.

### 5. Confirm passwordless sudo for systemctl on the VM

The notebook restarts `ict-trader-live.service` via `sudo -n systemctl`
(the `-n` means "don't prompt for a password"). For this to work, your
VM user needs passwordless sudo for systemctl. On the VM:
```bash
echo "ubuntu ALL=(ALL) NOPASSWD: /bin/systemctl" | sudo tee /etc/sudoers.d/ict-trader
sudo chmod 440 /etc/sudoers.d/ict-trader
```

This is the same sudoers entry `scripts/deploy_pull_restart.sh` already
documents.

---

## Each time you rotate keys

1. Open the notebook (the **Open in Colab** link at the top of this doc; the old Telegram `/set_keys` link-fetch command was removed in #1933).
2. Update whichever Colab Secret you're rotating (Bybit key, etc.) **OR** if rotating the SSH key, replace the file in `My Drive/ICT_Bot_Secrets/` with the new one (same filename).
3. **`Runtime → Run all`**.

The first run in a fresh Colab session pops a one-click "Allow Drive
access" dialog. After that, no further interaction.

The notebook prints clear ✅/❌ for each step. If something fails, it
tells you what — see Troubleshooting below.

When the cells finish, verify (the `/accounts_status` + `/smoke_test` Telegram
commands were removed in #1933):
- **Balances** — check the dashboard / Android app **Accounts** view (or
  `GET /api/bot/accounts/balances`); every account should show a real balance.
- **Smoke test** — run `ALLOW_LIVE_TRADING=1 python scripts/smoke_test_trade.py`
  (see [`docs/runbooks/live-smoke-test.md`](../runbooks/live-smoke-test.md));
  each account should return ✅ `rejected_too_small`.

Done.

---

## Troubleshooting

The notebook stops at the first error and prints what failed. Common
ones:

### `Missing required Colab Secrets: BYBIT_API_KEY_1`

You haven't added that secret yet, or you've added it but the
"Notebook access" toggle is off. Open Tools → Secrets, verify each
required secret is present and the toggle is on.

### `❌ SSH connectivity failed`

- Wrong `VM_SSH_HOST` value (typo, or VM moved IPs).
- The uploaded SSH private key doesn't match the public key in the VM's `~/.ssh/authorized_keys`. Test from your laptop with the same key file: `ssh -i <path-to-the-uploaded-file> ubuntu@<vm-host> echo ok`.
- You uploaded the **public** key (`.pub`) by mistake — the notebook checks for this and refuses, but if you bypassed the check make sure the file's first line starts with `-----BEGIN OPENSSH PRIVATE KEY-----` (or `-----BEGIN RSA PRIVATE KEY-----`).
- VM's firewall blocking port 22 from Colab's outbound IPs (rare; Colab
  IPs are GCP, usually allowed). Try from your laptop first to confirm
  the key works at all.

### Notebook pops "Choose Files" — what now?

That's the automatic fallback when your key isn't found in Drive
(or Drive isn't mounted). Click **Choose Files**, pick your VM SSH
private key file from your computer, and the notebook continues.

Permanent fix: put the file at
`My Drive/ICT_Bot_Secrets/ict-bot-ovm-private.key` so future runs
find it without prompting.

### "No file uploaded" after the picker closed

You closed the picker without selecting a file. Re-run **just that
cell** (Step 1B) — it'll prompt again. Or place the key in Drive
and re-run all.

### `does not look like a private key`

The notebook's safety check rejected the uploaded/located file
because it doesn't begin with `-----BEGIN`. Most common cause: it's
the **public** key (the `.pub` file) instead of the private one.
Same directory on your laptop — make sure you grab the one *without*
the `.pub` extension.

### Drive mount didn't pop a dialog

The very first cell mounts Drive. If you see "Drive is NOT mounted",
either:

- The auth dialog opened in a popup that was blocked — re-run cell 1
  with popups allowed.
- You declined access — re-run and click Allow.
- A stale Colab session has a token cached but the mount silently
  fails. The cell auto-retries with `force_remount=True`; if that
  still fails, restart the runtime (`Runtime → Disconnect and delete
  runtime`) and try again.

If Drive truly can't mount, the file picker fallback will take over
in cell 1B — you can still complete the rotation.

### `does not look like a private key`

The notebook's safety check rejected the uploaded file because it
doesn't begin with `-----BEGIN`. Most common cause: you uploaded the
**public** key (the `.pub` file) instead of the private one. They're
in the same directory on your laptop — make sure you grab the one
*without* the `.pub` extension.

### `❌ atomic rename failed: Permission denied`

The SSH user can write under `~/ict-trading-bot/` but the file already
exists with different ownership. On the VM:
```bash
sudo chown ubuntu:ubuntu ~/ict-trading-bot/.env.live
```

### `❌ service restart failed: a password is required`

Passwordless sudo for systemctl isn't configured. See "Confirm
passwordless sudo" above.

### `Service state: failed`

Restart succeeded but the service died. The new `.env.live` may have
a value the bot rejects (e.g. `MAX_POSITION_USD=invalid`). SSH to the
VM and run:
```bash
sudo journalctl -u ict-trader-live.service -n 50 --no-pager
```
The first ERROR line names the bad value.

### `/accounts_status` still shows ❌ after Run all

Because PR2 of S-023 the message names the specific cause:
- **`missing env vars: BYBIT_API_KEY_1, BYBIT_API_SECRET_1`** — the
  secret didn't make it into the `.env.live`. Did the notebook print
  that name in step 2's "Generated .env.live" output? If not, the
  Colab Secret was empty — check it in Tools → Secrets.
- **`Bybit error retCode=10003: API key is invalid.`** — the key was
  written but Bybit rejected it. Most common: typo in the key, or IP
  restriction excluding the VM. Re-paste the key from the Bybit
  dashboard or remove the IP restriction temporarily to confirm.
- **`Too many visits!`** — rate limited; wait a minute and re-check.
- **`ConnectionError: timed out`** — VM-side network. Check `curl
  https://api.bybit.com` from the VM.

---

## Security notes

- Colab Secrets are stored encrypted by Google and only readable by
  notebooks you explicitly grant access to (per-secret toggle). They
  do not appear in the notebook source, the `.ipynb` file in the repo,
  or any output cell.
- The notebook's step 2 prints **only the variable names**, never the
  values. If you ever see a value in the output, that's a bug — file
  it.
- The SSH private key is written to the Colab session's `/tmp` only
  long enough to run `ssh` / `scp`. It's wiped at the end of the cell
  (in the `finally` block) and is gone the moment the Colab session
  ends.
- The `.env.live` on the VM is `chmod 600` (owner read/write only).
- If you ever paste a real key into a chat, screenshot, or screen
  share: disable it in the Bybit dashboard immediately, generate a
  new one, update the matching Colab Secret, re-run the notebook.

---

## What the notebook actually does

For curious operators (or your future self when something breaks):

1. **Step 1** loads each named secret via `google.colab.userdata.get()`
   and validates that everything required is present.
2. **Step 2** builds a `.env.live` content string in memory. Variables:
   - Production defaults (`ENVIRONMENT=production`, `DRY_RUN=false`,
     risk caps, etc.) — hardcoded in the cell, edit there if you want
     non-secret defaults to differ.
   - Telegram token + chat id from your Colab Secrets.
   - Per-account `BYBIT_API_KEY_<N>` / `BYBIT_API_SECRET_<N>` from
     your Colab Secrets — these match `config/accounts.yaml`'s
     `api_key_env` declarations exactly.
   - Legacy singular `BYBIT_API_KEY` / `BYBIT_API_SECRET` (kept for
     back-compat with code paths that still read the unsuffixed names;
     mirrors `scripts/render_env_from_master.py`).
3. **Step 3** writes the SSH private key + the `.env.live` to a Colab
   tempdir, scp's the env file to the VM as `.env.live.tmp`,
   atomically renames (`mv`) into place, restarts the systemd unit,
   and confirms `is-active`. The tempdir is wiped in a `finally`
   block.

Total push to the VM: one new file written (`~/ict-trading-bot/.env.live`)
and one systemd restart. Nothing else on the VM is touched.

---

## Related files

- `notebooks/operator/rotate_api_keys.ipynb` — the notebook itself.
- `config/accounts.yaml` — the `api_key_env` names that drive what env
  vars the notebook writes.
- `src/bot/data_loaders.py::credentials_check` — the function that
  produces the `missing env vars: …` diagnostic if a secret didn't
  land in `.env.live`.
- `src/runtime/api_reporting.py` — the function that pings Telegram
  with the direct Bybit retCode/retMsg if a key is wrong.
- ~~Telegram `/set_keys`~~ — **removed (#1933)**; use the **Open in Colab** link at the top of this doc.
