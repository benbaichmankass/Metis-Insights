# Operator action items — outstanding

Things that require operator hands (credentials, console access,
manual confirmations) and can't be done by an autonomous Claude
session. The bug log already references each item; this doc gives
you a one-pager checklist with the exact commands.

Refresh the timestamp at the top of each section once the action is
done, and append a one-line note to `docs/claude/bug-log.md` so the
ledger reflects the resolution.

---

## A. Revoke leaked OAuth tokens (S-014.5 cleanup, BUG-004)

**Why:** During S-014.5 the operator pasted an Anthropic OAuth token
into chat while debugging the VM dispatcher. That token is burned.
A replacement was set on the VM (`/etc/ict-trader/claude.env` →
`CLAUDE_CODE_OAUTH_TOKEN=…`) but the leaked one is still valid until
revoked.

**How:**

1. Open <https://console.anthropic.com/> → Settings → API Keys.
2. Look for any OAuth token / API key created on **2026-04-30** that
   you don't recognise. Revoke it.
3. While there, sanity-check that the **active** token (the one in
   `/etc/ict-trader/claude.env`) is the most recent one and is
   labelled clearly. Rename if needed.

**Verify the active token still works after revocation:**

```bash
# from the VM
sudo -u ubuntu /usr/local/bin/claude-vm-dispatch \
    1 1 /tmp/probe.txt   # sentinel; failure means the token's revoked
```

Or simpler: `/vm what time is it` from Telegram. If it returns the
expected `✅ exit 0`, the active token is intact.

**Status:** ✅ resolved (S-017). Operator confirmed all OAuth tokens in
the Anthropic console were created today by them; no leaked token to
revoke.

---

## B. Configure Bybit API key on the VM (CP-2026-04-30-05 § 5.4)

**Why:** The trader is generating sell signals every tick but every
order fails at `bybit requires "apiKey" credential` because the live
Bybit API key isn't set on the VM yet. **No live trades are
happening today.** This was flagged as a pre-existing gap during the
S-014.5 close — never resolved.

**How:**

1. Generate (or rotate) the Bybit API key for the production account
   from <https://www.bybit.com/app/user/api-management>. Required
   permissions: `Read` + `Trade` for `Derivatives v3` (USDT-perp).
   **Do not enable** `Withdrawals`.
2. SSH into the VM (or use Oracle Cloud Console connection if SSH
   keys aren't working — see S-014's CP-2026-04-30-04 for context).
3. Append to `/home/ubuntu/ict-trading-bot/.env.bybit_2`
   (the vwap account; bybit_1 is turtle_soup, separate file):

   ```bash
   BYBIT_API_KEY_2=<your-key>
   BYBIT_API_SECRET_2=<your-secret>
   ```

   File mode must be `0640 root:ubuntu` (`sudo chmod 640 .env.bybit_2 &&
   sudo chown root:ubuntu .env.bybit_2`).
4. Restart the trader:
   ```bash
   sudo systemctl restart ict-trader-live
   sudo journalctl -u ict-trader-live -n 100 --no-pager | grep -i 'bybit\|order'
   ```
5. From Telegram: `/balance` should now return non-zero balances and
   `/trades` should show real positions instead of "balance
   unavailable".

**Verify live with a tiny dry-run order first:**

Per `CLAUDE.md`, `MODE=LIVE` + `DRY_RUN=true` + `ALLOW_LIVE_TRADING=
false` lets the trader validate API auth without actually submitting
orders. Confirm `MAX_QTY` is small in `.env.bybit_2` (default 0.001)
before flipping `ALLOW_LIVE_TRADING=true`.

**Status:** ✅ resolved (S-017). Operator confirmed Bybit keys are
already loaded into the trader's process env via the per-account
`.env.bybit_*` files; `/balance` returns non-zero numbers on both
accounts. The autonomous smoke (T6/T7) consumes the same env files.

---

## C. Filter `httpx` URL logging so the Telegram bot token doesn't appear in plaintext (CP-2026-04-30-05 § 5.2)

**Why:** `python-telegram-bot` uses `httpx` internally. `httpx` logs
the full request URL at INFO level by default, and the URL contains
the bot token. Operator running `journalctl -u ict-telegram-bot`
sees lines like:

```
INFO httpx: HTTP Request: POST https://api.telegram.org/bot<TOKEN>/getUpdates "HTTP/2 200 OK"
```

The token is shell-readable to anyone with journal-read access.

**How (do in the next dev session, not now):**

This is a small code fix, not an operator action — adding it here so
it doesn't fall off. The fix lives in `src/bot/telegram_query_bot.py`
right after `import logging`:

```python
# Suppress httpx INFO so the Telegram bot token doesn't end up in
# journalctl in plaintext (operator-flagged in CP-2026-04-30-05).
logging.getLogger("httpx").setLevel(logging.WARNING)
```

After landing, verify:

```bash
sudo systemctl restart ict-telegram-bot
sudo journalctl -u ict-telegram-bot -n 100 --no-pager | grep -i httpx
# should be empty
```

**Status:** ✅ resolved in S-017 PR #222. The bot module now calls
`install_redacting_filter()` + `suppress_httpx_logging()` at startup,
matching the trader's `src/main.py` pattern.

---

## D. Verify `/opt/ict-trading-bot` exists on the VM, or fix the `ict-web-api` unit (S-016 H4 R2)

**Why:** `deploy/ict-web-api.service` declares
`WorkingDirectory=/opt/ict-trading-bot`, but every other unit uses
`/home/ubuntu/ict-trading-bot`. If `/opt/ict-trading-bot` isn't a
real directory or symlink on the VM, the web-api silently fails to
start. The H4 audit couldn't verify from the sandbox.

**How (one-line check):**

```bash
ls -la /opt/ict-trading-bot 2>&1 | head
sudo systemctl status ict-web-api --no-pager | head
```

- If `/opt/ict-trading-bot` exists (a real path or a symlink to
  `/home/ubuntu/ict-trading-bot`) and the unit reports `active`
  → no action.
- If the path doesn't exist OR the unit is `failed` →
  fix in one of two ways:
  1. **Symlink** (cheapest): `sudo ln -s /home/ubuntu/ict-trading-bot
     /opt/ict-trading-bot && sudo systemctl restart ict-web-api`.
  2. **Edit the unit**: change `WorkingDirectory=` to
     `/home/ubuntu/ict-trading-bot`, `daemon-reload`, restart.
     Commit the change to `deploy/ict-web-api.service` so future
     deploys aren't broken.

**Status:** ⏳ pending operator quick-check.

---

## E. Stale-branch prune (S-016 H6, optional)

**Why:** The repo has ~170 remote branches; most are squash-merged
`claude/*` from completed sprints. They clutter `git fetch` output
but are otherwise harmless.

**How:** read-only listing first:

```bash
scripts/list_stale_branches.sh | head -30
```

Bulk-prune anything older than 60 days that you recognise as merged:

```bash
STALE_DAYS=60 scripts/list_stale_branches.sh \
  | awk '{print $1}' \
  | while read b; do echo "would delete: $b"; done

# Once you're happy with the list, swap the echo for the real delete:
# git push origin --delete "$b"
```

**Caution:** the script does NOT verify a branch is merged — it
only checks tip age. Branches with in-progress work but a stale tip
(e.g. paused for a month) would also show up.

**Status:** ⏳ optional, operator-driven.

---

## Cross-references

- `docs/claude/bug-log.md` — the canonical ledger; update there
  when each item resolves.
- `CLAUDE.md` § "Always do" — covers the recurring operator-action
  cadence.
- `docs/claude/checkpoints/CHECKPOINT_LOG.md` — historical context
  for why each item exists (CP-2026-04-30-05 has the deepest trace).
