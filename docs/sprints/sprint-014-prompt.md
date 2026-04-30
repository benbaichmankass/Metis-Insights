# Sprint S-014 — Web Client V1 (Home Dashboard)

> **Sprint type:** Feature sprint (lean). Phase 4 web client consuming the S-013 backend.
> **Owner:** Claude Code (autonomous). **PM:** Ben. **Tech Lead:** Perplexity.
> **Created:** 2026-04-30. **Predecessor:** S-013 (closed 2026-04-30).
> **Replaces** the original ROADMAP S-014 ("Component Tabs" mobile) with the web equivalent: a server-rendered dashboard hitting the JWT-protected APIs that landed in S-013.

---

## Sprint goal

Land a **read-only home dashboard** the operator can open from the `/webapp` Telegram link. Login → home view showing per-account live/dry status, overall + per-account P&L, active strategies, uptime, git SHA, and a 7-day equity sparkline. Live trader uptime preserved end-to-end.

---

## PM resolutions baked into this prompt

1. **Stack: HTMX + Jinja2 + Chart.js** (single committed static JS file). **No Node toolchain anywhere** — not on the VM, not on the dev machine. No bundler, no build step. Stable + leanest, matches the PM's "no VM-side deps that drift from repo merges" rule.
2. **Build artefact strategy: commit static assets directly** under `web/static/`. No CI build step in this sprint. *Roadmap-meeting follow-up:* revisit if bundle complexity grows past ~5 files.
3. **Equity sparkline data: new `/api/pnl/history` endpoint** that queries `trade_journal.db` directly per request (SSoT). No caching, no duplication, no parallel store.
4. **Hosting: loopback only** in this sprint. Reverse proxy + TLS + public exposure is its own follow-up ("S-014.5"); auth and exposure are different risk profiles.

---

## Read in order before touching code

1. `docs/claude/checkpoints/CHECKPOINT_LOG.md` — top entry (`CP-2026-04-30-04 — S-014 kickoff + bot regression blocker`).
2. `docs/sprint-summaries/sprint-013-summary.md` — especially "Architecture decisions" and "What this sprint did NOT do."
3. `docs/sprints/sprint-013-prompt.md` § "Auth contract" (binding for this sprint too).
4. `docs/audit/sprint-013-deployment-runbook.md` — operator's enable procedure; this sprint extends it.
5. `src/web/api/main.py`, `src/web/api/auth.py`, `src/web/api/routers/{status,pnl,auth}.py` — the S-013 backend you're consuming.

---

## Milestones

Eight PRs, ≤ 400 LOC each (HTML/JSON-LD aren't budget bloat; CSS counts). Self-merge per `CLAUDE.md` after CI green, except where flagged "PM review."

### M0 — Backend gap fill (one PR)

- **M0 PR #1** — `GET /api/pnl/history?days=N` (default `N=7`, max `N=90`). New router at `src/web/api/routers/pnl_history.py`. Reads `trade_journal.db` directly (SSoT — no caching, no parallel store). Same `Depends(require_session)` gate. Response shape:
  ```json
  {
    "schema_version": 1,
    "days": 7,
    "points": [
      {"date": "2026-04-24", "realized_usd": 12.50, "trades": 3}
    ],
    "as_of_utc": "2026-04-30T12:00:00Z"
  }
  ```
  Empty journal → `points: []` (200, not 503). DB unreachable → 503. Tests in `tests/test_web_api_pnl_history.py` covering: happy path with fixture journal across N days, empty journal, missing DB, corrupt DB, off-allowlist 403, missing token 401, `days` clamping (≤ 0 → 422, > 90 → 422).

### M1 — Frontend scaffold (two PRs)

- **M1 PR #1** — Static + template tree:
  - `web/templates/base.html` — base layout, includes header, slot for content.
  - `web/templates/login.html` — login form.
  - `web/templates/home.html` — placeholder cards (`<div hx-get="/ui/fragments/status">…`).
  - `web/static/css/app.css` — minimal CSS, dark-mode-friendly.
  - `web/static/js/htmx.min.js` — pinned vendored copy (HTMX 2.x).
  - `web/static/js/chart.umd.js` — pinned vendored Chart.js 4.x.
  - `web/static/js/auth.js` — small helper that injects `Authorization: Bearer <token>` on every HTMX request via `htmx:configRequest`, redirects to `/login` on 401, reads token from `localStorage`.
  - **No `npm`, no `node_modules`, no build step.** The vendored JS files are committed as-is; pin SHA-256 hashes in a comment at the top of each.

- **M1 PR #2** — FastAPI mounts:
  - `src/web/api/main.py` mounts `web/static` at `/static` and registers Jinja2 templates from `web/templates`.
  - New router `src/web/api/routers/ui.py` exposes:
    - `GET /` → redirects to `/home` if a session cookie exists, else `/login`.
    - `GET /login` → renders `login.html` (public route).
    - `GET /home` → renders `home.html` (auth-gated by checking the `Authorization` header **or** falling back to a same-origin token check via the auth.js helper; if neither, redirect to `/login`).
  - Add `/login` and `/static/*` to `PUBLIC_ROUTES` in `src/web/api/auth.py`. `/home` is **NOT** in `PUBLIC_ROUTES` — auth.js handles the redirect after the page loads if the local token is missing.
  - Tests: `GET /static/css/app.css` → 200; `GET /login` → 200 + HTML; `GET /home` without token → renders the page (auth.js does the gate client-side); `GET /` redirects.

### M2 — Login flow (two PRs, both PM REVIEW)

- **M2 PR #1** — Login page wires up:
  - The form posts JSON to `/api/auth/login` via `fetch` (in `auth.js`), receives the JWT, stores it in `localStorage` under key `ict_session_token`, then `window.location = "/home"`.
  - Logout button on the home page nav: clears `localStorage`, redirects to `/login`.
  - 60-second pre-expiry timer in `auth.js` decodes the JWT (no signature check client-side; we trust the server) and forces a redirect to `/login` 60 s before `exp`.
  - **PM review** — UI-side token storage + JWT handling.
  - Tests: contract tests via `tests/test_web_api_ui.py`: confirm `/login` HTML contains the expected form action, `auth.js` is referenced, and `chart.umd.js` is referenced. Behavioural correctness is verified manually via the smoke-test runbook in M4.

- **M2 PR #2** — Auth-aware HTMX requests:
  - `auth.js` listens for `htmx:configRequest` and adds `evt.detail.headers["Authorization"] = "Bearer " + getToken()`.
  - On `htmx:responseError` with status 401, clears the token and redirects to `/login`.
  - On 403, surfaces a "Not allowlisted" toast.
  - **PM review** — security-critical client behaviour.

### M3 — Home view (three PRs)

- **M3 PR #1** — Status panel HTMX fragment:
  - `GET /ui/fragments/status` (auth-gated) renders `web/templates/fragments/status.html` with data from `STATUS_PATH`. Polled by HTMX every 30 s (`hx-trigger="load, every 30s"`).
  - Cards: uptime, git SHA, active strategies (chips), per-account live/dry pills.
  - Tests: fragment HTML contains the expected fields; auth gate enforced.

- **M3 PR #2** — P&L panel HTMX fragment:
  - `GET /ui/fragments/pnl` renders `web/templates/fragments/pnl.html` from `build_pnl()`. Polled every 30 s.
  - Per-account cards: realised, unrealised, trades today.
  - Tests: fragment contains expected fields; off-allowlist 403; missing token 401.

- **M3 PR #3** — Equity sparkline:
  - `web/static/js/equity_chart.js` fetches `/api/pnl/history?days=7`, renders a Chart.js line chart into a `<canvas>` on the home page.
  - Chart re-renders every 5 minutes (longer cadence — daily P&L doesn't move tick-by-tick).
  - Tests: smoke test that `home.html` includes the canvas element + script tag; backend test that `/api/pnl/history` returns the documented shape (already covered in M0).

### M4 — Verification + close (one PR)

- **M4 PR #1** — Sprint summary + final checkpoint + ROADMAP update:
  - `docs/sprint-summaries/sprint-014-summary.md` per `CLAUDE.md`.
  - `docs/audit/sprint-013-deployment-runbook.md` gets a new "S-014 smoke test" appendix: log in via browser, see the home view, check the sparkline renders, test the logout button.
  - `ROADMAP.md` — S-014 marked done; S-015 (was the original S-014 component tabs) becomes the next backlog item.
  - `CP-2026-MM-DD-NN — S-014 SPRINT COMPLETE` to `CHECKPOINT_LOG.md`.

---

## Auth contract — UNCHANGED from S-013

- HS256, 1-hour TTL, `JWT_SIGNING_KEY` env, `ALLOWED_EMAIL` env, default-deny.
- `PUBLIC_ROUTES` extended in M1 PR #2 to add `/login` and `/static/*`. `/home` is NOT public — auth.js does the client-side redirect.
- `POST /api/auth/login` continues to return JSON `{access_token, token_type, expires_in}`. The frontend reads it and stores client-side.

## Guardrails (HARD STOPS)

1. Do **NOT** touch the S-013 auth contract beyond extending `PUBLIC_ROUTES` for the new public UI paths (`/login`, `/static/*`).
2. Do **NOT** install `node`, `npm`, `pnpm`, or any JS toolchain on the dev machine or the VM. **Vendor JS files as committed `.js` files** under `web/static/js/`. Pin a SHA-256 hash in a top-of-file comment so swaps require explicit review.
3. Do **NOT** expose the dashboard to the public internet. `ict-web-api.service` stays on `127.0.0.1:8001`. Reverse-proxy + TLS is "S-014.5", a separate sprint.
4. Do **NOT** touch `src/runtime/**`, `src/main.py`, `src/units/strategies/**`, `src/strategy_registry.py`, `src/core/**`, or any `config/*.yaml`.
5. Do **NOT** touch `src/bot/telegram_query_bot.py` — `/webapp` is already wired in S-013.
6. Tokens never appear in URLs, query strings, or any log line.
7. PR size ≤ 400 LOC excluding vendored JS. Vendored JS counts as one PR each — call them out in the PR description with the upstream version + SHA-256.
8. M2 PRs require **PM review** (login UI + token storage + auth-aware request injection).
9. Pacing: re-read this prompt and the DoD after every 2 merged PRs.

## Files Claude may modify

- `web/**` (new tree)
- `src/web/api/main.py` — only to mount `web/static` and `web/templates` and register the new `ui` router.
- `src/web/api/auth.py` — only to extend `PUBLIC_ROUTES`.
- `src/web/api/routers/pnl_history.py` (new), `src/web/api/routers/ui.py` (new).
- `tests/**`
- `docs/sprints/sprint-014-prompt.md` (this file, if it gets refined mid-sprint), `docs/sprint-plans/sprint-plan-2026-MM-DD.md` (new), `docs/sprint-summaries/sprint-014-summary.md` (new).
- `docs/audit/sprint-013-deployment-runbook.md` — only the M4 PR #1 appendix.
- `ROADMAP.md` (status updates only).
- `docs/claude/checkpoints/CHECKPOINT_LOG.md` (per-session entries).
- `requirements.txt` — only if a new Python dep is genuinely required (e.g. `jinja2` is a FastAPI extra; check before adding).

## Files OFF LIMITS

- `src/runtime/orders.py`, `src/runtime/risk_counters.py`, `src/runtime/notify.py`, `src/runtime/signal_writer.py`, `src/runtime/validation.py`.
- `src/main.py`, `src/units/**`, `src/strategy_registry.py`, `src/core/**`.
- `config/*.yaml`, `config/master-secrets.template.yaml`.
- `deploy/ict-trader-live.service` and its timer/heartbeat siblings, `deploy/ict-web-api.service` (no new units).
- `src/bot/telegram_query_bot.py`.
- Anything under `ml/`, `notebooks/`, `data/`.

## Definition of Done

- [ ] Vendored JS (HTMX, Chart.js) committed under `web/static/js/` with pinned versions + SHA-256 hashes; no Node anywhere.
- [ ] `GET /` redirects to `/login` (no session) or `/home` (with session).
- [ ] `GET /login` is reachable without a token; submitting valid credentials lands the operator on `/home` with a token in `localStorage`.
- [ ] Home view renders: status panel (uptime, git SHA, strategies, per-account live/dry), P&L panel (per-account realised/unrealised/trades today), 7-day equity sparkline.
- [ ] HTMX fragments poll every 30 s; equity sparkline refreshes every 5 min.
- [ ] Logout button clears `localStorage` and bounces to `/login`.
- [ ] 60-second pre-expiry timer redirects to `/login` before token `exp`.
- [ ] Off-allowlist token → toast "Not allowlisted"; missing token → redirect to `/login`.
- [ ] No token in URLs, query strings, or any log line. (`secret_scan.py` clean.)
- [ ] M2 PR #1 + M2 PR #2 PM-reviewed before merge.
- [ ] `docs/audit/sprint-013-deployment-runbook.md` has the S-014 smoke-test appendix.
- [ ] Live trader uptime preserved end-to-end.

## Concrete first action

After reading the docs above:

1. Confirm S-013 is fully on `main` (`git log --oneline | head -8` should show PRs #173 → #182 squashed).
2. Branch off latest `main` as `claude/s014-m0-pr1-pnl-history`.
3. Build M0 PR #1 (`/api/pnl/history`) — that's the only backend change before any frontend lands.

---

## Standing item — Telegram bot regression (open)

The PM reported `/help`-style commands "stopped working" on the production VM after S-013 landed on `main`. The previous session could not collect diagnostics because **PM lost SSH access to the Oracle VM (`ict-bot`, public IP `158.178.210.252`); all five private keys in their OCI Cloud Shell `~/.ssh/` were rejected.**

This is a sprint-blocker only if S-014 deploys are blocked too. Recommended posture:

- Treat S-014 as **decoupled** from the bot regression; it lives entirely behind the staging FastAPI service, no Telegram code is touched.
- Surface the bot regression as a **standing reminder** at the top of each session until the operator regains SSH and pastes the `journalctl -u ict-telegram-bot -n 100 --no-pager` tail. With that output the regression is almost certainly a 30-minute fix.
- Do **NOT** invent diagnostic theories without the journal. Three things were ruled out by local testing: (a) `src/bot/telegram_query_bot.py` imports cleanly with `python-telegram-bot 22.x`, (b) all 126 bot unit tests pass, (c) the bot does not transitively import any of the new S-013 web deps. Whatever broke the bot is environmental on the VM.

End of prompt.
