# ICT Bot Sprint Plan — Sprint 8 / S-013: Website UI with Secure Auth

**Sprint start:** 2026-04-30
**Sprint label:** S-013 (PM-facing name: "Sprint 8 — Website UI with Secure Auth")
**Owner:** Ben Baichman-Kass (PM) / Claude Code (autonomous executor)
**Project:** [the-lizardking/ict-trading-bot](https://github.com/the-lizardking/ict-trading-bot)
**Previous sprint:** [`sprint-plan-2026-04-28.md`](sprint-plan-2026-04-28.md) — S-012 Production Wiring Audit (completed 2026-04-29).

---

## Sprint Checkpoint

Resume state for Claude Code is tracked in
[`docs/claude/checkpoints/CHECKPOINT_LOG.md`](../claude/checkpoints/CHECKPOINT_LOG.md).
Rules: [`docs/claude/checkpoint-workflow.md`](../claude/checkpoint-workflow.md).

- **Current sprint phase:** Phase 0 — planning (M1 ROADMAP retarget done in
  `CP-2026-04-30-00`; planning docs land in `CP-2026-04-30-01`).
- **Last completed checkpoint:** `CP-2026-04-30-00` (ROADMAP retargeted to
  Phase 4 — Secure Web Dashboard).
- **Next checkpoint after planning docs:** `CP-2026-04-30-02` — **M2 PR #1**:
  read-only `/api/status` FastAPI endpoint with JWT scaffolding (smallest
  safe code subtask).
- **Branch:** `claude/sprint-8-secure-website-rqV3g` (one branch for the
  whole sprint; PR per checkpoint).
- **Blocked:** none.

Do not restart from M1 every session — read the checkpoint log first.

---

## Sprint Theme

The previous sprint (S-012) made the live trading layer coherent and
fully live on Bybit. This sprint pivots from "CLI + Telegram only" to a
**responsive web dashboard** that the PM can use from phone or laptop,
with secure auth locked to a single Google account, Telegram-mediated
whitelist alerts, and WebAuthn passkey re-auth. **No native mobile app.**
**No changes to the live trading runtime.** The web layer is purely
additive.

---

## Status entering this sprint

✅ **S-012 outcome (PRs #147–#168, 21 PRs):**
- Strategy roster reduced to `turtle_soup + vwap` across configs, code,
  and services.
- One strategy directory, one registry, one entrypoint.
- Phantom services (`ict-trader-bak`, `ict-trader-example`) removed +
  regression test.
- Live-mode hard guard at startup; risk caps proven by tests for both
  strategies; `max_dd_pct` intra-day reset implemented.
- 1153 tests pass, 17 pre-existing failures (S-009 carry, deferred).

⏳ **Carried into this sprint:**
- Nothing functional. Web layer is greenfield.

🔴 **Operational reality check entering S-013:**
- Live trader (`ict-trader-live.service`) is running on the VM with
  `ALLOW_LIVE_TRADING=true` and live order placement on Bybit. **The web
  layer must not touch any of it** in this sprint — read-only API,
  proxy-only access to bot state.

---

## Sprint Guardrails (HARD STOPS)

1. **Do not stop or restart the live trader.** `ict-trader-live.service`
   keeps running through the entire sprint. The web layer is a new,
   independent process.
2. **No new code in `src/runtime/orders.py`, `src/runtime/pipeline.py`,
   `src/main.py`, or `src/units/strategies/**`.** Web layer reads from
   the existing signals DB / `runtime_logs/` and the bot's existing
   in-memory state. If a read shape doesn't exist, add a new read-only
   helper in `src/web/` — never refactor the runtime.
3. **No secrets in the client bundle.** Allowlisted email lives only
   server-side. Telegram bot token only in the VM env. Google OAuth
   client secret only in server env. Verify with `secret_scan.py` before
   every push.
4. **One concern per PR**, ≤ 400 LOC diff (excluding lock files), self-merge
   per `CLAUDE.md`. Flag for PM review only when (a) new secret/API-key
   handling, (b) any change to live trading logic, (c) `deploy/` scripts.
5. **PR cadence:** target one PR per ordered task in the milestones below.
   If a task naturally splits, split it.
6. **Pre-filled VM context** (use as-is; do not invent new values):
   - `VM_HOST=158.178.210.252`
   - `VM_USER=ubuntu`
   - `REPO_DIR=/home/ubuntu/ict-trading-bot`
   - SSH key file: `ict-bot-ovm-private.key`
   - **Staging:** API on port `8001`, Web on port `3001`.
   - **Prod:** Nginx reverse proxy on `80/443` → web on `3000`,
     `/api/*` → `8001` (loopback only).
7. **Do not run training, full backtests, or any bot mutation** as part
   of this sprint. Read-only access only.

---

## Auth contract (non-negotiable — every milestone enforces this)

Lifted from `ROADMAP.md` Phase 4. Carry into every PR description.

1. **Google OAuth (only sign-in method).** NextAuth.js with Google
   provider. Allowlist = exactly **one** email
   (`ben.baichmankass@gmail.com`). Stored server-side; never in client
   bundle.
2. **Whitelist alert flow.** Any non-allowlisted login attempt:
   - server refuses the session,
   - server posts a Telegram message to the PM with `(email,
     device_fingerprint, ip_country_asn, timestamp)`,
   - message has inline `Approve` / `Deny` buttons.
   - `Approve` → email added to allowlist; requester can retry.
   - `Deny` → email added to denylist; requester sees generic refusal.
   - All decisions are logged.
3. **Device-persistent sessions.** After a successful Google OAuth +
   passkey on a device, set a long-lived `device_id` cookie
   (`HttpOnly`, `Secure`, `SameSite=Strict`, signed). Trusted-device
   record is stored server-side keyed by `(user_id, device_id)`.
4. **WebAuthn passkey re-auth.** Required:
   - on first login from any device (passkey enrolment),
   - on every fresh login on a trusted device,
   - after **30 minutes of inactivity** (idle timeout — JS heartbeat
     to `/api/heartbeat`).
5. **Read-only by default.** Every endpoint that mutates state
   (`/api/killswitch`, `/accounts` toggle, `/reload_strats`) requires a
   **fresh-passkey assertion** (passkey used in the last 5 minutes), in
   addition to the session.

---

## Milestones

### M1 — Roadmap update (✅ DONE in CP-2026-04-30-00)

`ROADMAP.md` retargeted from mobile-app phases to Phase 4 — Secure Web
Dashboard track (S-013 → S-016). Auth contract codified there.

### M2 — Backend API foundations (Claude, ~3–4 PRs)

Read-only FastAPI app under `src/web/api/`. Runs on its own systemd unit
(`ict-trader-web-api.service`) on staging port `8001`. Proxies the bot's
existing state — does not import the runtime as a process.

**M2 PR #1** — `/api/status` + JWT scaffolding (no UI, no auth yet).
Returns `{bot_uptime_s, live, strategies: [...], git_sha}`. Reads from
the runtime's existing heartbeat file. Includes `auth.py` with JWT
decode helpers but no enforcement yet (added in M3 PR #2).

**M2 PR #2** — `/api/pnl` (read-only, paginated). Shape derived from the
existing signals DB / `runtime_logs/`.

**M2 PR #3** — `/api/positions` and `/api/signals`. Same backing store.

**M2 PR #4** — `/api/killswitch` (POST). Behind a "auth required +
fresh-passkey" decorator that is currently a hard `403` until M4 wires
the assertion. Tests prove the `403` fires.

**M2 acceptance:**
- Endpoints return real data when run against the VM.
- Unit tests for happy path + auth-refusal path on each endpoint.
- New systemd unit at `deploy/ict-trader-web-api.service` (staging port
  `8001`); not enabled in prod yet.

### M3 — Google OAuth + Telegram whitelist alert (Claude + Colab, ~3 PRs)

**M3 PR #1** — Next.js scaffold under `web/` (TypeScript, Tailwind,
NextAuth.js). One page (`/`) that says "Sign in with Google". Google
provider configured; client secret read from env. Builds clean.

**M3 PR #2** — Allowlist enforcement + Telegram alert dispatch.
- Server checks the OAuth callback email against the allowlist before
  issuing a session.
- Non-allowlisted attempt → call into the Telegram bot (extends
  `src/bot/telegram_query_bot.py`) to post the alert with
  `Approve` / `Deny` inline buttons.
- Allowlist + denylist persisted in a small SQLite table at
  `data/web_auth.sqlite` (gitignored).
- Includes the M2 PR #1 JWT enforcement now active on the API.

**M3 PR #3** — Tailwind responsive dashboard skeleton (mobile-first):
top nav (collapses on mobile), placeholder cards for Status / PnL /
Positions / Signals. No data wiring yet (that's M6).

**M3 acceptance:**
- Allowlisted login succeeds end-to-end on staging.
- Non-allowlisted login fails AND triggers a Telegram alert (verified
  by Colab tunnel test against staging).
- Layout renders correctly on iPhone Safari portrait, iPad landscape,
  and 1440px desktop.

### M4 — Passkey + sessions (Claude, ~2 PRs)

**M4 PR #1** — WebAuthn enrolment + verification using
`@simplewebauthn/server` and `@simplewebauthn/browser`. Endpoints:
`/api/auth/passkey/register/options`, `.../register/verify`,
`.../authenticate/options`, `.../authenticate/verify`. Credentials
persisted alongside the allowlist in `data/web_auth.sqlite`. New users
are forced to enrol a passkey before the first session is issued.

**M4 PR #2** — Session lifecycle:
- Long-lived `device_id` cookie (signed; 1-year TTL; HttpOnly; Secure;
  SameSite=Strict).
- Session cookie (30-min sliding TTL).
- JS heartbeat to `/api/heartbeat` every 60 s while the tab is visible.
- Inactivity ≥ 30 min → server invalidates session; next request gets
  redirected to `/login` which requires passkey assertion (Google
  re-auth NOT required because the device is trusted).
- Mutation endpoints require a fresh-passkey assertion (≤ 5 min old);
  the decorator from M2 PR #4 is now wired up.

**M4 acceptance:**
- Passkey enrolment works on iOS Safari + Android Chrome + macOS
  Touch ID.
- Idle timeout triggers a passkey prompt at 30 min (e2e test via
  Playwright stub or Colab + headless Chrome).
- `/api/killswitch` rejects requests when the latest passkey assertion
  is older than 5 min.

### M5 — Telegram whitelist bot extension (Claude, ~2 PRs)

**M5 PR #1** — Extend `src/bot/telegram_query_bot.py`:
- New handler for `whitelist_request:{nonce}` callback queries
  (Approve / Deny inline buttons posted by M3 PR #2).
- On `Approve`: write email to `data/web_auth.sqlite` allowlist table
  via the same module M3 uses; reply edit confirms.
- On `Deny`: write to denylist; reply edit confirms.
- All decisions logged with `(timestamp, email, decision, pm_chat_id)`.

**M5 PR #2** — Idempotency + audit:
- Each whitelist request carries a server-generated nonce; the bot
  refuses to act on the same nonce twice.
- New endpoint `/api/auth/whitelist/audit` returns the decision log
  (allowlisted-only).

**M5 acceptance:**
- Approve/Deny round-trip works against staging.
- Replay of the same callback id is rejected.
- Approving an email lets that email log in on the next attempt.

### M6 — Frontend wiring + responsive polish (Claude + Colab, ~3 PRs)

**M6 PR #1** — Hook the dashboard cards to the M2 endpoints (SWR /
React Query). Status card live-updates via the heartbeat.

**M6 PR #2** — PnL chart (Recharts; equity curve with drawdown shading).
Positions table with sticky header. Signals feed (last 50, virtualised).
All data read-only.

**M6 PR #3** — Mobile polish: tap targets ≥ 44 px, charts collapse to
swipeable cards on `<sm`, dark/light auto.

**M6 acceptance:**
- Staging URL (`http://158.178.210.252:3001`) renders the full
  dashboard with real bot data on iOS Safari, Android Chrome, desktop
  Chrome, desktop Firefox.
- Heartbeat keeps the session alive while the tab is open; closing
  the tab + reopening after 30 min triggers passkey re-auth.

### M7 — Security audit + prod deploy (Claude, ~2 PRs)

**M7 PR #1** — Security audit doc at
`docs/audit/sprint-013-web-security-audit.md`:
- OWASP ASVS Level 1 checklist.
- CSP, strict CORS (single origin: prod hostname).
- HTTP security headers (HSTS, X-Frame-Options DENY, Referrer-Policy).
- Cookie flags audit.
- `secret_scan.py` clean + grep for the allowlist email anywhere in
  client bundle (must return zero).
- Rate-limit on `/api/auth/*` (e.g. 10 req / 5 min / IP).
- Audit log retention policy.

**M7 PR #2** — Prod deploy:
- Nginx reverse proxy (`deploy/nginx/site.conf` + new
  `ict-trader-web.service` for the Next.js process).
- HTTPS via Let's Encrypt (`certbot` cron documented in
  `docs/claude/deployment-ops.md`).
- Deployment runbook at
  `docs/audit/sprint-013-deployment-runbook.md`:
  pre-flight, certbot issuance, systemd unit install, Nginx reload,
  DNS check, smoke test, rollback.

**M7 acceptance:**
- HTTPS reachable on the prod hostname; certificate valid; HSTS set.
- Allowlisted login + passkey works against prod.
- Live trader untouched (`systemctl status ict-trader-live` =
  `active` throughout).

---

## Parallel Execution Plan

This sprint pairs Claude (precision PRs) with Gemini-in-Colab
(prototypes). Adjust as compute / availability dictate.

| Day | Claude (paid, precise) | Colab / Gemini (free, exploratory) |
|-----|------------------------|------------------------------------|
| 1 | M2 PR #1, #2 | UI wireframes; sketch responsive layout |
| 2 | M2 PR #3, #4 + M3 PR #1 scaffold | Telegram alert payload + button design |
| 3 | M3 PR #2, #3 | Test allowlist refusal + Telegram round-trip via SSH tunnel |
| 4 | M4 PR #1, #2 | Passkey UX testing on iOS / Android |
| 5 | M5 PR #1, #2 + M6 PR #1 | Dashboard data shape against real signals DB |
| 6 | M6 PR #2, #3 | Mobile polish QA across browsers |
| 7 | M7 PR #1, #2 | Smoke test against prod URL |

Slip is fine — checkpoints are PR-sized; each day's work resumes from
the latest entry in `CHECKPOINT_LOG.md`, not from this calendar.

---

## Definition of Done

The sprint is done **only** when every box below is true. Do not
declare completion early.

- [ ] `ROADMAP.md` Phase 4 contract is current and correct.
- [ ] `https://<prod-hostname>` serves the dashboard over HTTPS
      (HSTS set; certificate valid).
- [ ] Logging in with `ben.baichmankass@gmail.com` succeeds; logging
      in with any other Google account fails AND triggers a Telegram
      alert with working Approve / Deny buttons.
- [ ] Passkey enrolment is required on first login, on every fresh
      login on a trusted device, and after 30 min of inactivity.
- [ ] `/api/killswitch` returns `403` unless a passkey assertion
      ≤ 5 min old is presented.
- [ ] Dashboard renders correctly on iOS Safari, Android Chrome, and
      desktop. PnL / Positions / Signals show real bot data.
- [ ] `secret_scan.py` is clean. No allowlist email in any client
      bundle. No bot tokens or OAuth client secrets in the repo.
- [ ] `docs/audit/sprint-013-web-security-audit.md` and
      `docs/audit/sprint-013-deployment-runbook.md` exist and are
      filled in.
- [ ] Live trader (`ict-trader-live.service`) was never restarted by
      this sprint. `systemctl status` shows `active (running)`
      with the original start time preserved.
- [ ] Sprint summary at `docs/sprint-summaries/sprint-013-summary.md`
      with PR list, tests added, and 1–3 lessons learned.
- [ ] `CHECKPOINT_LOG.md` closing entry posted; `/sprintlet_complete
      S-013` Telegram ping fired.

---

## Decision Requests for the PM (mid-sprint)

Pause and ask via Telegram (`/sprintlet_status decision needed: <topic>`)
before acting on any of these:

1. **Prod hostname.** Suggested: a subdomain you already control with
   DNS pointed at `158.178.210.252` (e.g. `bot.<yourdomain>`). PM to
   confirm the exact hostname before M7 PR #1.
2. **DB choice for auth state.** Default plan is SQLite at
   `data/web_auth.sqlite` (zero ops, encrypted at rest via VM disk
   only). Switch to Postgres only if PM wants multi-process
   redundancy.
3. **Telegram alert chat.** Default plan is the existing PM chat used
   by `alert_manager.py`. Confirm or specify a separate chat for auth
   alerts.
4. **Passkey UX on Android Chrome (no Touch ID).** Default fallback is
   the device-bound platform authenticator (PIN or fingerprint).
   PM confirms acceptable.

---

## Files Claude is permitted to modify

- `src/web/**` (new tree)
- `web/**` (new Next.js project)
- `src/bot/telegram_query_bot.py` (whitelist handler additions only)
- `deploy/ict-trader-web-api.service` (new)
- `deploy/ict-trader-web.service` (new)
- `deploy/nginx/site.conf` (new)
- `tests/**`
- `docs/sprints/sprint-013-prompt.md`,
  `docs/sprint-plans/sprint-plan-2026-04-30.md`,
  `docs/sprint-summaries/sprint-013-summary.md`,
  `docs/audit/sprint-013-*.md`,
  `docs/claude/deployment-ops.md` (web reverse-proxy section).
- `data/.gitignore` (add `web_auth.sqlite`)
- `.gitignore` (add web build artefacts)

## Files OFF LIMITS

- `src/runtime/**`
- `src/main.py`
- `src/units/strategies/**`
- `src/strategy_registry.py`
- `config/strategies.yaml`, `config/units.yaml`, `config/accounts.yaml`
- `deploy/ict-trader-live.service` (and its timer / heartbeat siblings)
- `config/master-secrets.template.yaml`

---

## Pacing reminder

This sprint is multi-day and PR-sized. Prefer correctness over
throughput. After every two merged PRs, re-read this plan, the
checkpoint log, and the auth contract. If a blocker appears outside the
four decision-request items above, stop and post `/sprintlet_status
blocked: <reason>`.

End of sprint plan.
