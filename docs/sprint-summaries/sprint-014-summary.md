# Sprint S-014 — Web Client V1 (Home Dashboard)

> **Sprint type:** Feature sprint (lean). Phase 4 web client consuming the S-013 backend.
> **Owner:** Claude Code (autonomous). **PM:** Ben. **Tech Lead:** Perplexity.
> **Created:** 2026-04-30. **Closed:** 2026-05-06.
> **Goal:** Land a read-only home dashboard the operator can open from the `/webapp` Telegram link: login → home view with per-account live/dry status, overall + per-account P&L, active strategies, uptime, git SHA, and a 7-day equity sparkline. Live trader uptime preserved end-to-end.

## Outcome at a glance

| DoD checkbox | Status | Closed by |
|---|---|---|
| Vendored JS (HTMX, Chart.js) committed under `web/static/js/` with pinned versions + SHA-256 hashes; no Node anywhere | ✅ | M1 PR #1 (#192) |
| `GET /` redirects to `/login` (no session) or `/home` (with session) | ✅ | M1 PR #2 (#193) |
| `GET /login` reachable without a token; valid credentials land operator on `/home` with token in `localStorage` | ✅ | M2 PR #1 (#415, PM-approved) |
| Home view renders: status panel + P&L panel + 7-day equity sparkline | ✅ | M3 PR #1 (#195) + M3 PR #2 (#196) + M3 PR #3 (#414) |
| HTMX fragments poll every 30 s; equity sparkline refreshes every 5 min | ✅ | M3 PR #1, #2 (#195, #196), M3 PR #3 (#414) |
| Logout button clears `localStorage` and bounces to `/login` | ✅ | M1 PR #1 (#192) — `wireLogout()` in auth.js |
| 60-second pre-expiry timer redirects to `/login` before token `exp` | ✅ | M2 PR #1 (#415) — `scheduleExpiryRedirect()` |
| Off-allowlist token → toast "Not allowlisted"; missing token → redirect to `/login` | ✅ | M2 PR #2 (#418) — `htmx:responseError` handler |
| No token in URLs, query strings, or any log line (`secret_scan.py` clean) | ✅ | every PR + CI guard |
| M2 PR #1 + M2 PR #2 PM-reviewed before merge | ✅ | Operator approved both via session message 2026-05-06 |
| `docs/audit/sprint-013-deployment-runbook.md` has the S-014 smoke-test appendix | ✅ | M4 PR #1 (this PR) |
| Live trader uptime preserved end-to-end | ✅ | guardrail #1; no live-trading code path changed |

## PRs merged

| PR | Title |
|---|---|
| [#183](https://github.com/the-lizardking/ict-trading-bot/pull/183) | S-014 M0 PR #1: GET /api/pnl/history for equity sparkline |
| [#190](https://github.com/the-lizardking/ict-trading-bot/pull/190) | S-014 side fix: /signals Markdown parse failure → plain text |
| [#192](https://github.com/the-lizardking/ict-trading-bot/pull/192) | S-014 M1 PR #1: frontend scaffold (templates + vendored HTMX/Chart.js) |
| [#193](https://github.com/the-lizardking/ict-trading-bot/pull/193) | S-014 M1 PR #2: FastAPI mounts for UI router + static tree |
| [#195](https://github.com/the-lizardking/ict-trading-bot/pull/195) | S-014 M3 PR #1: GET /ui/fragments/status (auth-gated) |
| [#196](https://github.com/the-lizardking/ict-trading-bot/pull/196) | S-014 M3 PR #2: GET /ui/fragments/pnl (auth-gated) |
| [#414](https://github.com/the-lizardking/ict-trading-bot/pull/414) | S-014 M3 PR #3: equity sparkline JS (+ S-014 state correction) |
| [#415](https://github.com/the-lizardking/ict-trading-bot/pull/415) | S-014 M2 PR #1: login form fetch + JWT pre-expiry timer (PM-approved) |
| [#418](https://github.com/the-lizardking/ict-trading-bot/pull/418) | S-014 M2 PR #2: HTMX 401/403 handler + toast (PM-approved) |
| (this PR) | S-014 M4 PR #1: sprint summary + smoke-test appendix + ROADMAP/milestone-state flip |

Plus mid-sprint admin: PR #191, #194, #197 (checkpoints, 2026-04-30); PR #413, #416, #417 (ping-PR + checkpoints, 2026-05-06).

## Architecture decisions

1. **HTMX + Jinja2 + Chart.js, no Node toolchain.** Vendored JS files committed directly under `web/static/js/` with SHA-256 banners. No `npm`, no `node_modules`, no build step on the dev machine or VM.
2. **Static assets committed; no CI build step.** Revisit if bundle complexity grows past ~5 files.
3. **`/api/pnl/history` reads `trade_journal.db` directly** (single source of truth — no caching, no parallel store).
4. **Loopback-only hosting** in this sprint. Reverse proxy + TLS + public exposure is its own follow-up sprint (S-014.5).
5. **JWT in `localStorage`**, server-side `Depends(require_session)` is the source of truth. Pre-expiry timer is a UX optimisation, not a security gate.
6. **Toast helper uses `textContent`**, no `innerHTML` — no XSS surface from the 403 path.

## What this sprint did NOT do

- No reverse proxy, TLS, public exposure (S-014.5).
- No refresh-token flow — operator re-logs in every hour (matches S-013 token TTL).
- No write actions from the dashboard (read-only). Operator-action layer is S-016 (Secure API Key Management).
- No CSP headers — third-party scripts are already excluded (everything is vendored), so the immediate XSS surface is small. CSP is part of the public-exposure follow-up.

## Tests added

| Test file | New / extended | What it covers |
|---|---|---|
| `tests/test_web_api_pnl_history.py` | new (M0) | `/api/pnl/history` happy path, empty journal, missing DB, corrupt DB, off-allowlist 403, missing token 401, `days` clamping. |
| `tests/test_web_api_ui.py` | extended | `/login`, `/home`, `/`, `/static/*` routes; auth.js login wire + pre-expiry timer + HTMX 401/403 handler; toast CSS. |
| `tests/test_web_api_status_fragment.py` | new (M3) | `/ui/fragments/status` HTML shape + auth gate. |
| `tests/test_web_api_pnl_fragment.py` | new (M3) | `/ui/fragments/pnl` HTML shape + auth gate. |

## Files in the new web tree

- `web/templates/` — `base.html`, `login.html`, `home.html`, `fragments/{status,pnl,status_unavailable,pnl_unavailable}.html`.
- `web/static/css/app.css` — single-file dark theme, ~250 lines including the toast styles.
- `web/static/js/htmx.min.js` — vendored HTMX 2.0.4 with SHA-256 banner.
- `web/static/js/chart.umd.js` — vendored Chart.js 4.4.7 with SHA-256 banner.
- `web/static/js/auth.js` — login wire, JWT decode, pre-expiry timer, HTMX 401/403 handler, logout, toast.
- `web/static/js/equity_chart.js` — first-party (no banner needed); fetches `/api/pnl/history?days=7` and renders cumulative-realised P&L into the canvas.
- `src/web/api/routers/pnl_history.py` — backend gap-fill; reads `trade_journal.db` per request.
- `src/web/api/routers/ui.py` — `/`, `/login`, `/home` routes + Jinja2 wiring.

## Deferred items

- **S-014.5** — public exposure (reverse proxy + TLS + DNS). Queued in milestone-state.md.
- **CSP headers** — to ship with S-014.5 alongside the public exposure work.
- **Refresh-token flow** — not in scope; revisit if token TTL friction shows up in operator usage.

## Lessons learned

1. **Sprint pauses leave stale "next action" pointers.** S-014 was paused for 6 days mid-flight (BUG-056 hotfix + Hardening Session 3 + M-S0 docs). The closing CP for M-S0 mistakenly pointed at "create `pnl_history.py`" which had already shipped on 2026-04-30. Fix: when activating a previously-paused milestone, the **first action** of the resuming session is to audit the actual on-disk state vs the sprint prompt, not to follow a stale next-action pointer.
2. **Local `main` drifts behind `origin/main` in long-running sandboxes.** Mid-session a working branch was accidentally based off a 51-commit-stale local `main`. Fix: always `git fetch origin main` and base new branches off `origin/main` explicitly (`git switch -C <branch> origin/main`); fast-forward local `main` after each merge. Add this to `git-workflow.md`.
3. **Ping-PR/work-PR separation works.** Operator caught the PM-review gate on M2 PR #1 via the merged ping-PR (#416) and approved both M2 PRs in one session message. The split keeps operator review traceable to a separate commit, distinct from the change being reviewed. Continue using this pattern for all PM-review PRs.

## Final state

- Active milestone advances from M-S-014 → S-015 (Web Client V2 — Component Tabs).
- ROADMAP S-014 row → ✅ Done.
- Live trader untouched across the sprint.
