# Sprint S-013 — Website UI with Secure Auth (PM-facing name: "Sprint 8")

> **Sprint type:** Multi-day, PR-per-checkpoint, additive. The web layer is
> a brand-new tree; the live trading runtime is **off-limits**.
> **Owner:** Claude Code (autonomous).
> **PM:** Ben.
> **Tech Lead:** Perplexity (this prompt).
> **Created:** 2026-04-30.
> **Branch:** `claude/sprint-8-secure-website-rqV3g` (one branch for the
> whole sprint; one PR per checkpoint; PRs target `main`).
> **Plan companion:** [`docs/sprint-plans/sprint-plan-2026-04-30.md`](../sprint-plans/sprint-plan-2026-04-30.md).
> **Non-negotiable goal:** Stand up a responsive (mobile + desktop)
> website that gives the PM secure, read-only visibility into the live
> bot, plus a small set of mutation controls gated by passkey re-auth.
> Auth is locked to one Google account, with a Telegram-mediated
> whitelist alert flow for any other login attempt.

---

## Why this sprint exists (context for Claude)

The previous sprint (S-012) made the live trading layer coherent and
fully live. The PM's next requirement is a way to monitor the bot from
phone or laptop without opening Telegram or SSH. The earlier roadmap
assumed a native mobile app (former S-013/S-014/S-015); that is now
retired in favour of a single responsive **website** with browser-native
auth (Google OAuth + WebAuthn passkeys). See `ROADMAP.md` Phase 4 for
the binding contract.

The PM's explicit constraints:

1. **One website**, mobile-first responsive — no app-store work.
2. **One Google account** (`ben.baichmankass@gmail.com`) is allowed to
   sign in. Anyone else triggers a Telegram alert with inline
   `Approve` / `Deny` buttons; PM can promote them on the spot.
3. **Persistent device login**: once a device is trusted, it stays
   logged in between sessions. But every fresh login (and every 30-min
   idle window) requires a passkey assertion.
4. **No mutation without a fresh passkey**: kill-switch, dry/live
   toggle, strategy reload all require a passkey assertion ≤ 5 min old.
5. **Live trader untouched.** This sprint is purely additive.

---

## Confirmed evidence base (read before writing code)

- `ROADMAP.md` — Phase 4 contract is the source of truth for the auth
  model. Do not deviate.
- `docs/sprint-plans/sprint-plan-2026-04-30.md` — milestone breakdown
  and acceptance criteria.
- `docs/claude/checkpoints/CHECKPOINT_LOG.md` — the most recent entry
  tells you which checkpoint to resume.
- `src/bot/telegram_query_bot.py` — existing Telegram bot; the
  whitelist alert handlers extend this file.
- `src/bot/alert_manager.py` — existing Telegram dispatch helper; reuse,
  do not duplicate.
- `runtime_logs/` and the existing signals DB — read-only data sources
  for the dashboard.
- `deploy/*.service` — existing systemd units; the new web units sit
  alongside them and never replace them.

---

## Sprint scope (in)

- **M2** read-only FastAPI endpoints + JWT scaffolding (new tree
  `src/web/`).
- **M3** Next.js + Tailwind app (new tree `web/`), NextAuth.js Google
  OAuth, allowlist enforcement, Telegram whitelist alert dispatch.
- **M4** WebAuthn passkey enrolment + verification, 30-min idle
  timeout, device-trust cookie, fresh-passkey assertion for mutations.
- **M5** Telegram bot extension for whitelist Approve / Deny
  callbacks, idempotency, audit log.
- **M6** Frontend wired to the API (Recharts PnL, positions, signals),
  mobile polish.
- **M7** Security audit doc, prod deploy via Nginx + Let's Encrypt,
  deployment runbook.

## Sprint scope (out)

- Any change to `src/runtime/**`, `src/main.py`,
  `src/units/strategies/**`, `src/strategy_registry.py`,
  `config/{strategies,units,accounts}.yaml`,
  `deploy/ict-trader-live.service`.
- New strategies, new symbols, new exchanges.
- Native mobile app (retired by this sprint).
- Browser web push notifications (deferred to "Items Under
  Consideration").

---

## Guardrails (HARD STOPS)

1. **Do NOT stop or restart `ict-trader-live.service`** at any point.
   `systemctl status ict-trader-live` should show the same start
   time at the end of the sprint as at the beginning.
2. **Do NOT import the runtime** from the web layer. Read from the
   signals DB / `runtime_logs/` / heartbeat file. If a read shape is
   missing, add a thin read-only helper in `src/web/` — never refactor
   `src/runtime/**`.
3. **Do NOT put secrets in the client bundle.** Allowlist email,
   Telegram bot token, Google OAuth client secret, JWT signing key —
   all server-only. Run `python scripts/secret_scan.py` before every
   push. Add a CI grep that fails if `ben.baichmankass@gmail.com`
   appears in `web/.next/**` or `web/out/**`.
4. **Do NOT change live order-placement logic.**
5. **Do NOT paste secrets into chat, PRs, or commit messages.**
6. **PR size limit:** one concern per PR, ≤ 400 LOC diff per PR
   (excluding `package-lock.json` / `pnpm-lock.yaml` and generated
   Next.js types). Self-merge per `CLAUDE.md` rules.
7. **Commit cadence:** one PR per ordered checkpoint. If a checkpoint
   naturally splits, split it and write a new checkpoint entry.
8. **Time pacing:** prefer correctness over throughput. After every
   two merged PRs, re-read this prompt, the plan, and the auth
   contract.

---

## Auth contract (carry into every PR description)

1. **Google OAuth (only sign-in method).** NextAuth.js with the Google
   provider. Allowlist = `ben.baichmankass@gmail.com` only,
   server-side.
2. **Whitelist alert flow.** Non-allowlisted login attempt → server
   refuses session + posts a Telegram alert with `(email,
   device_fingerprint, ip_country_asn, timestamp)` and inline
   `Approve` / `Deny` buttons.
3. **Device-persistent sessions.** Long-lived `device_id` cookie
   (`HttpOnly`, `Secure`, `SameSite=Strict`, signed). Trusted-device
   record stored server-side keyed by `(user_id, device_id)`.
4. **WebAuthn passkey re-auth required:**
   - on first login from any device (enrolment),
   - on every fresh login on a trusted device,
   - after 30 minutes of inactivity (JS heartbeat → `/api/heartbeat`).
5. **Fresh-passkey assertion (≤ 5 min)** required for all mutating
   endpoints (`/api/killswitch`, `/accounts` toggle,
   `/reload_strats`).

---

## Checkpoint sequence

Each entry below is one checkpoint = one PR (unless it splits naturally;
then add sub-checkpoints in the log). All target branch
`claude/sprint-8-secure-website-rqV3g`.

### CP-2026-04-30-01 — Planning docs (this checkpoint)

Write this prompt and `sprint-plan-2026-04-30.md`. Run `secret_scan.py`
and `repo_inventory.py`. No code changes.

### CP-2026-04-30-02 — M2 PR #1: `/api/status` + JWT scaffolding

Smallest safe code subtask. Add `src/web/api/__init__.py`,
`src/web/api/main.py`, `src/web/api/auth.py` (JWT helpers; no
enforcement yet), `src/web/api/routers/status.py`. Endpoint
`/api/status` returns `{bot_uptime_s, live, strategies, git_sha}` from
the runtime heartbeat file. Add `deploy/ict-trader-web-api.service`
(staging port `8001`). Tests for happy path + missing-heartbeat.

**Acceptance:** `curl http://localhost:8001/api/status` against a
heartbeat fixture returns 200 with the expected shape; pytest passes;
no enforcement on `/api/killswitch` yet (does not exist).

### CP-2026-04-30-03 — M2 PR #2: `/api/pnl`

Read-only paginated PnL from the signals DB. Tests for shape, paging,
and DB-missing case (clean 503).

### CP-2026-04-30-04 — M2 PR #3: `/api/positions` + `/api/signals`

Same backing store. Tests for both.

### CP-2026-04-30-05 — M2 PR #4: `/api/killswitch` (auth-stubbed)

POST endpoint returns hard `403` until M4 wires the passkey decorator.
Test proves the `403` fires and that the decorator runs before any
side effect.

### CP-2026-04-30-06 — M3 PR #1: Next.js + Tailwind scaffold

`web/` Next.js project, TypeScript, Tailwind, NextAuth.js installed.
One page (`/`) that says "Sign in with Google". Builds clean. Add
`web/.gitignore`, `.gitignore` entries for `web/.next/`, `web/out/`,
`web/node_modules/`.

### CP-2026-04-30-07 — M3 PR #2: Allowlist + Telegram alert dispatch

Server-side allowlist check on the OAuth callback. Non-allowlisted →
calls into a new helper in `src/bot/telegram_query_bot.py` to post the
alert with inline buttons. SQLite at `data/web_auth.sqlite` (gitignored)
holds allowlist + denylist tables. M2 PR #1's JWT enforcement is
flipped on for the API.

### CP-2026-04-30-08 — M3 PR #3: Responsive dashboard skeleton

Tailwind layout: top nav (collapses on mobile), placeholder cards. No
data wiring yet.

### CP-2026-04-30-09 — M4 PR #1: WebAuthn enrolment + verification

`@simplewebauthn/server` + `@simplewebauthn/browser`. Endpoints:
`/api/auth/passkey/register/options`, `.../register/verify`,
`.../authenticate/options`, `.../authenticate/verify`. Credentials
persisted in the SQLite DB. Enrolment is required before the first
session is issued.

### CP-2026-04-30-10 — M4 PR #2: Session lifecycle + idle timeout

Long-lived `device_id` cookie. Session cookie with 30-min sliding TTL.
JS heartbeat every 60 s. Idle ≥ 30 min → invalidates session; next
request redirects to `/login` (passkey only — Google re-auth not
required because device is trusted). Mutation decorator from M2 PR #4
now wired to require fresh-passkey ≤ 5 min.

### CP-2026-04-30-11 — M5 PR #1: Telegram whitelist callback handlers

Extend `src/bot/telegram_query_bot.py` with handlers for
`whitelist_request:{nonce}` callbacks. Approve writes to allowlist;
Deny writes to denylist; replies edited to show the decision.

### CP-2026-04-30-12 — M5 PR #2: Idempotency + audit log

Server-generated nonce per request; bot refuses replay.
`/api/auth/whitelist/audit` endpoint returns the decision log
(allowlisted-only).

### CP-2026-04-30-13 — M6 PR #1: Hook dashboard cards to API

SWR / React Query. Status card live-updates via heartbeat.

### CP-2026-04-30-14 — M6 PR #2: PnL chart + tables

Recharts equity curve with drawdown shading. Positions table (sticky
header). Signals feed (last 50, virtualised).

### CP-2026-04-30-15 — M6 PR #3: Mobile polish

Tap targets ≥ 44 px. Charts collapse to swipeable cards on `<sm`.
Dark/light auto.

### CP-2026-04-30-16 — M7 PR #1: Security audit doc

`docs/audit/sprint-013-web-security-audit.md`. OWASP ASVS L1, CSP,
strict CORS, security headers, cookie flags, rate limit on
`/api/auth/*`, audit log retention. Grep proof that allowlist email
is absent from the client bundle.

### CP-2026-04-30-17 — M7 PR #2: Prod deploy

Nginx reverse proxy (`deploy/nginx/site.conf`),
`deploy/ict-trader-web.service` (Next.js prod process),
HTTPS via Let's Encrypt, deployment runbook at
`docs/audit/sprint-013-deployment-runbook.md`.

### CP-2026-04-30-18 — Sprint close

Run full suite, secret scan, repo inventory. Sprint summary at
`docs/sprint-summaries/sprint-013-summary.md`. Final checkpoint entry.
Telegram `/sprintlet_complete S-013`.

---

## Definition of Done

The sprint is done **only** when every box below is true. Do not
declare completion early.

- [ ] Prod URL serves the dashboard over HTTPS (HSTS set; cert valid).
- [ ] `ben.baichmankass@gmail.com` can log in; any other Google account
      cannot AND triggers a working Telegram alert with `Approve` /
      `Deny` buttons that round-trip correctly.
- [ ] Passkey enrolment is required on first login; passkey assertion
      is required on every fresh login and after 30 min of inactivity.
- [ ] `/api/killswitch` returns `403` unless a passkey assertion ≤ 5
      min old is presented.
- [ ] Dashboard renders correctly on iOS Safari, Android Chrome,
      desktop Chrome, desktop Firefox. Real bot data shown.
- [ ] `secret_scan.py` clean; allowlist email absent from
      `web/.next/**` and `web/out/**`.
- [ ] `docs/audit/sprint-013-web-security-audit.md` and
      `docs/audit/sprint-013-deployment-runbook.md` exist and are
      filled in.
- [ ] `systemctl status ict-trader-live` shows `active (running)` with
      the same start time at the end of the sprint as at the
      beginning.
- [ ] `docs/sprint-summaries/sprint-013-summary.md` exists with PR
      list, tests added, and 1–3 lessons learned.
- [ ] `CHECKPOINT_LOG.md` closing entry posted; Telegram
      `/sprintlet_complete S-013` fired.

---

## Decision requests (PM may need to weigh in mid-sprint)

Pause and post `/sprintlet_status decision needed: <topic>` before
acting on any of these:

1. **Prod hostname.** Suggested: a subdomain you already control with
   DNS pointed at `158.178.210.252` (e.g. `bot.<yourdomain>`).
2. **DB choice for auth state.** Default: SQLite at
   `data/web_auth.sqlite`. Switch to Postgres only if PM wants
   multi-process redundancy.
3. **Telegram alert chat.** Default: existing PM chat used by
   `alert_manager.py`. Confirm or specify a separate chat.
4. **Passkey UX on Android Chrome (no Touch ID).** Default fallback:
   device-bound platform authenticator (PIN / fingerprint). PM
   confirms acceptable.

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
- `.gitignore` (web build artefacts)

## Files OFF LIMITS

- `src/runtime/**`
- `src/main.py`
- `src/units/strategies/**`
- `src/strategy_registry.py`
- `config/strategies.yaml`, `config/units.yaml`, `config/accounts.yaml`
- `deploy/ict-trader-live.service` and its timer / heartbeat siblings
- `config/master-secrets.template.yaml`

---

## Pacing reminder

Multi-day sprint. PR-per-checkpoint. After every two merged PRs,
re-read this prompt, the sprint plan, and the auth contract. If a
blocker appears outside the four decision-request items above, stop
and post `/sprintlet_status blocked: <reason>`.

End of prompt.
