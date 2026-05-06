# Sprint S-013 — Web Dashboard API Deployment Runbook

> **Audience:** PM (Ben) running on the Oracle VM as the bot user (`ubuntu`).
> **Sprint:** S-013 — Secure Web Dashboard: Backend Scaffold & Home Status.
> **Scope:** Enable `ict-web-api.service` on staging port `8001` (loopback only).
>
> **DO NOT** expose the service to the public internet from this runbook. The
> S-014 web client and the S-013 hardening that allows public exposure are
> separate sprints.

---

## What this runbook does

1. Generates the three new auth secrets locally on the VM.
2. Writes them to `/etc/ict-trader/web-api.env` (chmod 600, root-owned).
3. Installs and starts `ict-web-api.service` bound to `127.0.0.1:8001`.
4. Smoke-tests the deployment from the VM itself.

The live trader (`ict-trader-live.service`) is **not touched** at any point.

---

## Pre-flight (≈ 30 s)

```bash
# 1. The repo on the VM should be on main with no local changes.
cd /opt/ict-trading-bot
git status              # → "nothing to commit, working tree clean"
git fetch origin main
git pull --ff-only origin main

# 2. Confirm the canonical service set is on disk.
ls deploy/*.service
# Expected (S-012 + S-013):
#   deploy/ict-env-check.service
#   deploy/ict-git-sync.service
#   deploy/ict-heartbeat.service
#   deploy/ict-telegram-bot.service
#   deploy/ict-trader-live.service
#   deploy/ict-web-api.service     ← S-013 M2 PR #1

# 3. Live trader status snapshot — must stay 'active' through this runbook.
systemctl status ict-trader-live --no-pager | head -5
```

If `ict-trader-live` is not active, **stop here** and resolve that first.

---

## Step 1 — Install Python deps (≈ 30 s)

The new web-API stack needs `fastapi`, `uvicorn`, `pyjwt`, `email-validator`.
They are already pinned in `requirements.txt`:

```bash
cd /opt/ict-trading-bot
sudo -u ubuntu pip install -r requirements.txt
python3 -c "import fastapi, uvicorn, jwt, email_validator; print('ok')"
```

---

## Step 2 — Generate auth secrets (≈ 1 minute)

These three values are **never committed** and **never logged**. Generate them
on the VM in an interactive shell that you trust:

```bash
# 2a. Signing key (32 random bytes hex = 64 chars). One per VM, rotate yearly.
python3 -c "import secrets; print(secrets.token_hex(32))"
# Copy the output — you'll paste it into the env file in Step 3.

# 2b. Password hash. The plaintext password is never stored anywhere; we only
#     keep its SHA-256 hex. Pick a strong unique password (passphrase ok).
python3 -c "import hashlib,getpass; print(hashlib.sha256(getpass.getpass('webapp password: ').encode()).hexdigest())"
# Type the password at the prompt; the SHA-256 hex prints.
# Copy the hex output — that's what goes in the env file. Do NOT save the
# plaintext anywhere.
```

---

## Step 3 — Install the env file (≈ 30 s)

```bash
sudo install -d -m 750 -o root -g root /etc/ict-trader
sudo tee /etc/ict-trader/web-api.env >/dev/null <<'EOF'
# /etc/ict-trader/web-api.env — S-013 web dashboard secrets.
# chmod 600, owned by root. Read by ict-web-api.service via EnvironmentFile.
JWT_SIGNING_KEY=<paste output from step 2a>
ALLOWED_EMAIL=ben.baichmankass@gmail.com
WEBAPP_PASSWORD_SHA256=<paste output from step 2b>
EOF
sudo chmod 600 /etc/ict-trader/web-api.env
sudo chown root:root /etc/ict-trader/web-api.env

# Sanity-check permissions:
sudo ls -la /etc/ict-trader/web-api.env
# Expect:  -rw------- 1 root root  …  /etc/ict-trader/web-api.env
```

> **⚠️ Replace the two placeholders before saving the file.** Leaving
> `<paste output from step 2a>` literally in the file will cause the API to
> 500 with `auth_unavailable` on every request.

---

## Step 4 — Install + enable the systemd unit (≈ 15 s)

```bash
sudo cp /opt/ict-trading-bot/deploy/ict-web-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ict-web-api
systemctl status ict-web-api --no-pager | head -10
# Expect:  Active: active (running)
#          Listening on 127.0.0.1:8001
```

---

## Step 5 — Smoke test (≈ 1 minute)

All curls below run **on the VM** because the service is loopback-only.

```bash
# 5a. Public health probe — must return 200 with no auth.
curl -i http://127.0.0.1:8001/api/health
# Expect:  HTTP/1.1 200 OK
#          {"ok":true}

# 5b. Protected route without a token — must default-deny with 401.
curl -i http://127.0.0.1:8001/api/status
# Expect:  HTTP/1.1 401 Unauthorized
#          {"detail":{"error":"invalid_session"}}

# 5c. Log in as the allowlisted operator. Replace <password> with the plaintext
#     password whose SHA-256 you stored in Step 2b.
TOKEN=$(curl -s -X POST http://127.0.0.1:8001/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"ben.baichmankass@gmail.com","password":"<password>"}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
echo "got token: ${#TOKEN} chars"
# Expect:  got token: ~190+ chars

# 5d. Status with the token — must return 200 + the runtime status JSON.
curl -i -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8001/api/status
# Expect:  HTTP/1.1 200 OK
#          {"bot_uptime_s": …, "live": {…}, "strategies": [...], …}

# 5e. P&L with the token — must return 200 + per-account totals.
curl -i -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8001/api/pnl
# Expect:  HTTP/1.1 200 OK
#          {"accounts": {"bybit_1": {"realized_usd": …, …}, …}, …}

# 5f. Off-allowlist email login — must 403.
curl -i -X POST http://127.0.0.1:8001/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"someone-else@example.com","password":"<password>"}'
# Expect:  HTTP/1.1 403 Forbidden
#          {"detail":{"error":"email_not_allowlisted"}}
```

If any of 5a–5e fails, jump to **Rollback** below.

---

## Step 6 — Verify the live trader is still healthy (≈ 15 s)

```bash
systemctl status ict-trader-live --no-pager | head -5
# Active: active (running) since … (≥ pre-flight timestamp; uninterrupted)

journalctl -u ict-trader-live -n 20 --no-pager
# No new errors; tick log entries continue normally.
```

The web-API enable should have **zero** effect on the live trader. If you see
any change in trader logs, treat it as a regression and proceed to Rollback.

---

## Rollback (≈ 10 s)

The web-API and the live trader are fully decoupled, so disabling the new
service is safe and immediate:

```bash
sudo systemctl disable --now ict-web-api
systemctl status ict-web-api --no-pager | head -5
# Expect:  Active: inactive (dead)

# Optional: keep the unit on disk for next time, or remove entirely:
# sudo rm /etc/systemd/system/ict-web-api.service && sudo systemctl daemon-reload
```

The env file at `/etc/ict-trader/web-api.env` can be left in place; it has no
side effects when the service is stopped.

---

## What this runbook does NOT do

- **Does not expose the API to the public internet.** Reverse-proxy + TLS
  termination (and any DNS / firewall changes) are out of scope until S-014.
- **Does not enable the `/webapp` Telegram command.** That requires the
  `WEBAPP_URL` env var and ships in S-013 M4 PR #2.
- **Does not change `ict-trader-live.service` or any other existing unit.**
  S-012 PR D2's single-process trader-side invariant still holds.

---

## Operational notes

- **Token TTL is 1 hour.** When it expires, the dashboard prompts the operator
  to log in again. There is no refresh-token flow in S-013.
- **Algorithm is HS256.** `alg: none` is rejected by `decode_token`.
- **Per-call env reads** mean rotating any secret is just:
  ```bash
  sudo $EDITOR /etc/ict-trader/web-api.env
  sudo systemctl reload-or-restart ict-web-api
  ```
- **Logs:** `journalctl -u ict-web-api -n 100` shows uvicorn access lines.
  None of the auth secrets, the plaintext password, or the password hash
  appear in any log line — see `tests/test_web_api_auth_login.py` for the
  no-leakage contract.

---

## Appendix — S-014 web client smoke test (added 2026-05-06)

After the S-014 PRs are on `main` and `ict-web-api.service` is restarted,
the operator should smoke-test the dashboard end-to-end. All steps run on
the operator's machine with an SSH tunnel forwarding 127.0.0.1:8001 from
the VM (or directly on the VM via `ssh -L`).

### Pre-conditions

- `ict-web-api.service` is `Active: active (running)` (per the main
  runbook above).
- `JWT_SIGNING_KEY`, `WEBAPP_PASSWORD_SHA256`, `ALLOWED_EMAIL` are set in
  `/etc/ict-trader/web-api.env`.
- The operator knows the plaintext password whose SHA-256 hash matches
  `WEBAPP_PASSWORD_SHA256`.
- A local browser can reach `http://127.0.0.1:8001/`.

### Step 1 — `/` redirects to `/home`, `/home` bounces to `/login`

Open `http://127.0.0.1:8001/` in a fresh browser profile (no
`localStorage` token).

- **Expect:** the URL bar lands on `/login` after the redirect chain
  (`/` → `/home` → client-side redirect to `/login`).
- The login card renders with email + password inputs, a "Sign in"
  button, and a hidden `#login-error` element.

### Step 2 — Off-allowlist email → 403 inline error

Submit the form with an email that is **not** `ALLOWED_EMAIL`. Use any
password.

- **Expect:** the inline `#login-error` shows "Not allowlisted." and the
  URL stays on `/login`. No token is written to `localStorage` (verify
  via DevTools → Application → Local Storage).

### Step 3 — Wrong password → 401 inline error

Submit the form with the correct allowlisted email but a wrong password.

- **Expect:** the inline error shows "Invalid credentials." URL stays on
  `/login`. No token written.

### Step 4 — Valid login → /home renders + sparkline appears

Submit the form with the correct email + password.

- **Expect:** browser navigates to `/home`. `localStorage` has a key
  `ict_session_token` containing a JWT (three dot-separated segments).
- **Status panel** loads within 1 s with uptime, git SHA, active
  strategies (chips), per-account live/dry pills.
- **P&L panel** loads within 1 s with per-account realised / unrealised
  / trades-today.
- **Equity card** shows either:
  - A line chart of cumulative realised P&L over the last 7 days, OR
  - The empty-state text "No P&L history yet" (if the trade journal is
    empty for the window).
- Both HTMX cards re-poll every 30 s; the equity chart refreshes every
  5 minutes (verify via the Network tab — `/ui/fragments/status` and
  `/ui/fragments/pnl` requests every 30 s, `/api/pnl/history` every
  300 s).
- **No token** appears in any URL, query string, or fetch URL anywhere
  in the Network tab. Authorization is always in the request header.

### Step 5 — Logout button

Click "Sign out" on the home page header.

- **Expect:** browser navigates to `/login`. `localStorage` no longer
  contains `ict_session_token`. Re-typing `/home` in the URL bar
  immediately bounces back to `/login`.

### Step 6 — 401 handling (auto-redirect)

Re-log in (Step 4). Then on the VM, restart the web service to rotate
the in-process state (or temporarily change `JWT_SIGNING_KEY` and
`systemctl reload-or-restart ict-web-api`). Wait for the next 30-s
HTMX poll.

- **Expect:** the next HTMX response is 401. `auth.js`
  `htmx:responseError` handler clears `localStorage` and redirects to
  `/login`. The operator sees `/login` within ~30 s of the key change.

### Step 7 — 403 handling (toast)

Re-log in. On the VM, change `ALLOWED_EMAIL` to a different value and
`reload-or-restart` the service. Wait for the next HTMX poll.

- **Expect:** the next HTMX response is 403. A small toast appears at
  the bottom-right of the page reading "Not allowlisted". The toast
  auto-dismisses after ~4 s. The operator stays on `/home` (token is
  still valid; only the allowlist changed). Subsequent polls keep
  showing the toast until the operator logs out or the allowlist is
  restored.

### Step 8 — Pre-expiry redirect (optional, slow)

Re-log in. Open DevTools → Application → Local Storage and copy the
JWT. Decode the middle segment (`atob(...)`) and note the `exp` Unix
timestamp. Wait until ~60 s before that timestamp.

- **Expect:** the page navigates to `/login` automatically (the
  `setTimeout` in `auth.js` fires `PRE_EXPIRY_MS` before `exp`). This
  test is slow (up to 1 hour) — usually skipped unless investigating a
  specific timer regression.

### What this smoke test does NOT do

- Does not exercise public-internet exposure — that's S-014.5.
- Does not exercise the `/webapp` Telegram command end-to-end
  (covered by the S-013 runbook above).
- Does not benchmark the dashboard under heavy poll load — the cards
  use cheap reads from already-warm DBs.

### If anything fails

Capture the failing step's HTML payload + Network tab entry + any
`journalctl -u ict-web-api -n 200 --no-pager` lines and file a bug per
`docs/claude/bug-log.md`. The web-API and the live trader are still
fully decoupled; rolling back the service per the main runbook above is
always safe.
