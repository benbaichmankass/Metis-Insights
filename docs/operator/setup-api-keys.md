# Setting up API keys for the ICT trading bot

> **⚠️ Command interface updated (2026-05, #1933).** The Telegram verification
> commands this guide references (`/accounts_status`, `/smoke_test`) were
> **removed** when the bot went menu-driven. Where you see them below, use the
> current surface instead: **account balances** → the dashboard / Android app
> **Accounts** view or `GET /api/bot/accounts/balances`; **smoke test** →
> `ALLOW_LIVE_TRADING=1 python scripts/smoke_test_trade.py` (see
> [`docs/runbooks/live-smoke-test.md`](../runbooks/live-smoke-test.md)).
> Credential propagation to the VM is the `sync-vm-secrets` workflow (see
> [`.github/workflows/sync-vm-secrets.yml`](../../.github/workflows/sync-vm-secrets.yml)).
> (BL-20260525-001.)

End-to-end walkthrough for wiring real Bybit API keys into the bot,
post-S-023. After this you should see a real USDT balance for every
account (dashboard / Android **Accounts** view) and have Telegram alerts on any
future API failure.

**Total time:** ~15 minutes if Bybit account already exists.
**Touchable surface:** your local laptop (filling the template),
Google Drive (encrypted master file), the Oracle VM (rendered `.env`
and systemd unit). Nothing goes through CI or chat.

## Downloadable template

The fill-in template lives at:
[`docs/operator/account-keys.fill-in.yaml`](./account-keys.fill-in.yaml)

Copy it from the repo (raw GitHub link will work):
```bash
curl -sLO https://raw.githubusercontent.com/benbaichmankass/ict-trading-bot/main/docs/operator/account-keys.fill-in.yaml
```

This contains only the per-account credential blocks. You'll merge
them into your existing master file (which has telegram, risk, news,
runtime defaults, etc. — those stay as they are).

---

## Prerequisites

You should already have:

- [x] `sops` installed locally (`sops --version`)
- [x] Your age private key at `~/.config/sops/age/keys.txt` (or in Drive at `ICT_Bot_Secrets/age-keys.txt`)
- [x] `master-secrets.sops.yaml` in `My Drive/ICT_Bot_Secrets/`
- [x] SSH access to the Oracle VM
- [x] A Bybit account with funds in it
- [x] **Important:** the age private key has NOT been pasted into chat or shared. (If it has, rotate it before continuing — see the bottom of this doc.)

If any of those is missing, see `docs/claude/google-drive-master-secrets.md` first.

---

## Step 1 — Create Bybit API keys

Do this twice — once for `bybit_1` (turtle_soup), once for `bybit_2` (vwap).
If you only run one strategy for now, do one and leave `enabled: false`
for the other in the master file.

1. Log into [Bybit](https://www.bybit.com/).
2. **Account & Security → API Management → Create New Key**.
3. Choose:
   - **System-generated API Keys**.
   - Name: `ict-bot-bybit-1` (or `-2`).
4. **Permissions** — exactly these:
   - ✅ **Read** — Wallet, Position, Orders
   - ✅ **Trade** — Unified Trading: Orders
   - ❌ **Withdraw** — leave OFF. The bot does not need it; turning it off limits damage if a key leaks.
   - ❌ **Subaccount management** — leave OFF unless you actually use subaccounts.
5. **IP restriction** — strongly recommended. Set it to the Oracle VM's static IP. If you don't have a static IP, at minimum lock to your VM's region (e.g. `Frankfurt - eu-frankfurt-1`).
6. Save. Bybit shows the API key + secret **once**. Copy both immediately into a temporary scratch file (you'll paste them into the master template in step 2, then delete the scratch file).

Repeat for the second key. Make sure the wallets each key sees match what its strategy expects:
- `bybit_1` wallet should hold **BTC + USDT** (turtle_soup MTF strategy needs both).
- `bybit_2` wallet should hold **USDT only** (vwap mean-reversion).

If you don't have separate subaccounts, you can use the same Bybit account for both — but in that case the strategies will both size against the same wallet, which compounds risk. Better practice: use Bybit subaccounts (free) so each strategy has its own wallet.

---

## Step 2 — Fill in the template

On your local laptop (NOT on the VM):

```bash
# 1. Get the template.
cd ~/secure/ict-bot     # or wherever you keep your master file
curl -sLO https://raw.githubusercontent.com/benbaichmankass/ict-trading-bot/main/docs/operator/account-keys.fill-in.yaml

# 2. Open it in an editor. NEVER paste these values into chat / GitHub.
nano account-keys.fill-in.yaml
```

For each `REPLACE_ME_*`, paste the matching value from your scratch file:

```yaml
bybit:
  accounts:
    bybit_1:
      api_key:    "<your-bybit-1-key>"        # e.g. fJ29xK...
      api_secret: "<your-bybit-1-secret>"
    bybit_2:
      api_key:    "<your-bybit-2-key>"
      api_secret: "<your-bybit-2-secret>"
```

If `prop_breakout_1` doesn't apply to you, leave `enabled: false` and the
placeholders — the render script will skip it cleanly.

Save and close the editor. **Now delete the scratch file you wrote in step 1.**

---

## Step 3 — Merge into your existing master file

Decrypt your existing master file:

```bash
cd ~/secure/ict-bot
sops master-secrets.sops.yaml > master-secrets.yaml
```

This opens the decrypted plaintext as `master-secrets.yaml` for editing.

Open `master-secrets.yaml`, find the `bybit:` block, and add the
`accounts:` section under it (alongside the existing `live:`, `testnet:`,
`current_account:`, `vwap_strategy:` blocks). Paste the contents of
`account-keys.fill-in.yaml` directly. Result:

```yaml
bybit:
  testnet:
    api_key: REPLACE_ME
    ...
  live:
    api_key: <existing>
    ...
  current_account:
    ...
  vwap_strategy:
    ...
  active_strategy_account: "vwap_strategy"

  # NEW:
  accounts:
    bybit_1:
      api_key:    "<real key>"
      api_secret: "<real secret>"
      account_note: "turtle_soup — wallet should hold BTC + USDT"
    bybit_2:
      api_key:    "<real key>"
      api_secret: "<real secret>"
      account_note: "vwap — wallet should hold USDT only"

# NEW (under top level, alongside `bybit:`):
breakout:
  accounts:
    prop_breakout_1:
      api_key:    "REPLACE_ME_BREAKOUT_PROP_1_API_KEY"
      api_secret: "REPLACE_ME_BREAKOUT_PROP_1_API_SECRET"
      enabled:    false
```

Save.

---

## Step 4 — Re-encrypt and clean up

```bash
# Re-encrypt with the same age public key the existing file uses.
# sops reads the recipient list from master-secrets.sops.yaml's existing
# header; just decrypt + re-encrypt round-trip:
sops --encrypt --age $(grep "^age1" master-secrets.sops.yaml | head -1 | cut -d: -f2 | tr -d ' "') master-secrets.yaml > master-secrets.sops.yaml

# Confirm decryption still works:
sops --decrypt master-secrets.sops.yaml | grep -c "bybit_1"
# should print 1 or more

# Delete the plaintext file. THIS IS IMPORTANT.
shred -u master-secrets.yaml    # or rm -P on macOS

# Delete the fill-in template too — it has your real keys in it.
shred -u account-keys.fill-in.yaml
```

Upload `master-secrets.sops.yaml` back to `My Drive/ICT_Bot_Secrets/`,
overwriting the old one.

---

## Step 5 — SSH to the VM and re-render the .env

```bash
ssh ubuntu@<your-vm>
cd ~/ict-trading-bot

# Make sure the VM has the latest code from main (S-023 PRs):
git pull --rebase origin main

# Pull the encrypted master file from Drive (however you sync — rclone, gdrive cli, etc.).
# Place it at the path you usually use; example:
ls -la ~/secure/ict-bot/master-secrets.sops.yaml

# Re-render the .env.live with the per-account credentials.
python scripts/render_env_from_master.py \
    --master ~/secure/ict-bot/master-secrets.sops.yaml \
    --age-key-file ~/.config/sops/age/keys.txt \
    --profile vwap_btcusd_live \
    --out .env.live \
    --allow-live
```

**Read the output carefully.** It will print:
```
Profile : vwap_btcusd_live
Output  : .env.live
Written : 23 variables
Keys    : ENVIRONMENT, EXCHANGE, ..., BYBIT_API_KEY_1, BYBIT_API_SECRET_1, BYBIT_API_KEY_2, BYBIT_API_SECRET_2
```

If you see warnings like:
```
Warnings (operator should review):
  ! account 'bybit_1' (bybit): api_key still a placeholder; BYBIT_API_KEY_1 not written
  ! account 'prop_breakout_1' (breakout): master block has enabled: false; skipping
```
…the first one is real (you missed pasting `bybit_1`'s real key). The
second is fine if you don't have a prop account.

Confirm the env vars actually landed:
```bash
grep '^BYBIT_API_KEY_' .env.live
# Expected: two lines, BYBIT_API_KEY_1=... and BYBIT_API_KEY_2=...
```

If only one line appears, fix the master file and re-render.

---

## Step 6 — Restart the trader and verify

```bash
# Restart so systemd picks up the new .env.live.
sudo systemctl restart ict-trader-live.service

# Check the service is up.
sudo systemctl status ict-trader-live.service --no-pager
# Look for: Active: active (running)

# Watch the first tick to make sure no missing-creds errors:
sudo journalctl -u ict-trader-live.service -f --since "1 minute ago"
# Press Ctrl-C after you see "Tick result: ..." with no errors.
```

Open Telegram and run:
```
/accounts_status
```

Expected output:
```
📋 Accounts Status (risk + live API)

🟢 bybit_1 (bybit / regular)
  🔌 API: ✅ Balance $1,247.32 USDT
  💵 Daily PnL: $+0.00 / limit $100
  📦 Max pos: $500 | Open: 0

🟢 bybit_2 (bybit / regular)
  🔌 API: ✅ Balance $512.04 USDT
  💵 Daily PnL: $+0.00 / limit $100
  📦 Max pos: $500 | Open: 0
```

If you see ❌ for any account, the new diagnostic now tells you exactly
why — see "Troubleshooting" below.

---

## Step 7 — Smoke-test before going live

Before letting the bot place real strategy orders, run a smoke test.
(The `/smoke_test` Telegram command was removed in #1933; the smoke test
now runs via the script + one-shot unit — see
[`docs/runbooks/live-smoke-test.md`](../runbooks/live-smoke-test.md)):

```
ALLOW_LIVE_TRADING=1 python scripts/smoke_test_trade.py
```

This sends a deliberately too-small order to each account. Bybit will
reject it for being below the minimum lot size, and the bot will return
`✅ rejected_too_small`. That confirms:

- Credentials are valid (Bybit accepted them, just rejected the size).
- The bot can reach the API.
- The order-submission path works end-to-end.

If any account returns `❌ error: missing API credentials` or
`❌ Bybit error retCode=...`, that's actionable — fix and retry.

Once both accounts return ✅, you're cleared for live trading.

---

## Troubleshooting

The new diagnostic gives you a specific reason per account. Each maps
to a fix:

### `missing env vars: BYBIT_API_KEY_1, BYBIT_API_SECRET_1`

The .env.live didn't get rendered for this account. Re-run step 5 and
read the warnings — you probably left a `REPLACE_ME` in the master file.

### `Bybit error retCode=10003: API key is invalid.`

The key was rendered but Bybit rejected it. Three common causes:
1. Typo when pasting — re-create the key in Bybit and re-do step 2.
2. IP restriction is on but doesn't include the VM's IP. Either add the IP, remove the restriction, or check `curl ifconfig.me` from the VM.
3. The key was created on testnet but the bot is configured for mainnet (or vice versa). Check `BYBIT_TESTNET` in `.env.live` — should be `false` for live.

### `Bybit error retCode=10006: Too many visits!`

Rate-limited. Usually transient. If it persists, you've got too many
clients on the same IP — Bybit accounts are limited to 600 req/min.
Stagger your strategies or split into subaccounts.

### `ConnectionError: timed out`

Network. Check the VM has DNS + outbound HTTPS to api.bybit.com:443.

### `account_balance(...): missing api_key_env`

Configuration drift between accounts.yaml and what you set up. Compare
`config/accounts.yaml` with the keys you actually put in the master
file — they must match (e.g. accounts.yaml says `bybit_1` so the master
file needs `bybit.accounts.bybit_1`).

### Telegram alert spam

The new `report_api_failure` is rate-limited (1 alert per fingerprint
per 5 minutes, hard cap 30/hour) so a flapping API can't flood you.
You'll see a single alert with `+N suppressed` appended to the next
message that gets through. If you're getting more than that, check
that PR1 (S-022) is actually in your running code — `git log
--oneline | head -5` should include the S-022 PRs.

---

## Security: what to do if a key was exposed

If at any point the API key or age private key ended up in a place it
shouldn't (chat, screen share, repo, screenshot, etc.):

1. **Immediately disable the key** in the Bybit dashboard. Do this
   first, even before reading the rest of these steps. It's free and
   irreversible.
2. Generate a new Bybit key and re-do steps 1-6 with the new key.
3. If the **age private key** was exposed, generate a new age key
   (`age-keygen -o new-keys.txt`), re-encrypt the master file with the
   new public key (`sops updatekeys` on the .sops.yaml after editing
   `.sops.yaml` config), and delete the old key everywhere.
4. Run `python scripts/secret_scan.py` to confirm no key landed in any
   tracked file.

---

## What changed under the hood (S-023)

Before S-023:
- `accounts.yaml` declared `BYBIT_API_KEY_1` etc., but the render script never wrote those env vars (it wrote `BYBIT_API_KEY` singular).
- `_load_yaml_accounts` silently dropped the `api_key_env` field when projecting account dicts to its output.
- `/accounts_status` showed a single generic message that conflated three different failure modes.
- A failed Bybit API call was logged locally but never alerted.

After S-023 (the changes you're benefiting from in this walkthrough):
- Render script reads `accounts.yaml` and emits per-account env vars.
- `_load_yaml_accounts` preserves `api_key_env` / `api_secret_env`.
- `/accounts_status` shows the specific reason per account.
- Every API failure pings Telegram with the direct retCode + retMsg.

If anything in this walkthrough doesn't match what you see, your VM may
not be on the latest main yet. Check `git log --oneline | head -5` on
the VM and pull if needed.
