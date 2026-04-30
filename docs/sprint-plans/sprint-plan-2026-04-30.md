# Sprint Plan — 2026-04-30 (S-013)

**Sprint:** S-013 — Secure Web Dashboard: Backend Scaffold & Home Status
**Created:** 2026-04-30
**Sprint prompt:** `docs/sprints/sprint-013-prompt.md` (binding)
**Main HEAD at sprint kickoff:** `6280d4e` (S-012 hotfix #3)

---

## Milestone overview

| ID | Title | LOC budget | Self-merge? | Depends on |
|---|---|---:|---|---|
| M0 PR #1 | Pre-flight: clear 17 pre-existing failing tests | ≤ 200 | yes | — |
| M1 PR #1 | Runtime status producer (`runtime_status.json`) | ≤ 250 | yes | M0 |
| M2 PR #1 | `GET /api/status` (no-op auth) | ≤ 400 | yes | M1 |
| M2 PR #2 | `GET /api/pnl` (no-op auth) | ≤ 400 | yes | M2 PR #1 |
| M3 PR #1 | `POST /api/auth/login` (issue JWT) | ≤ 350 | **PM review** | M2 PR #1 |
| M3 PR #2 | Enforce `require_session` on all routes | ≤ 250 | **PM review** | M3 PR #1 |
| M4 PR #1 | VM staging deployment runbook | ≤ 200 | yes | M3 PR #2 |
| M4 PR #2 | `/webapp` Telegram command + sprint summary + final checkpoint | ≤ 300 | yes | M4 PR #1 |

**Total: 8 PRs.**

---

## API shape (M2)

### `GET /api/status`

```json
{
  "schema_version": 1,
  "bot_uptime_s": 3725,
  "live": {"main": true, "prop_a": false},
  "strategies": ["turtle_soup", "vwap"],
  "git_sha": "6280d4e",
  "last_tick_utc": "2026-04-30T12:34:56Z"
}
```

- Status file path: `runtime_logs/runtime_status.json`.
- Missing/corrupt file → 503 with body `{"error": "status_unavailable"}`.

### `GET /api/pnl`

```json
{
  "schema_version": 1,
  "accounts": {
    "main":   {"realized_usd": 123.45, "unrealized_usd": -4.20, "trades_today": 7},
    "prop_a": {"realized_usd": 0.0,    "unrealized_usd": 0.0,   "trades_today": 0}
  },
  "as_of_utc": "2026-04-30T12:34:56Z"
}
```

- Reads from existing trade journal / signals DB. No schema migration in S-013.
- Empty journal → all-zero values (200, not 503). 503 only if the DB itself is unreachable.

---

## Auth contract (M3)

| Item | Value |
|---|---|
| Algorithm | HS256 (reject `alg: none`) |
| TTL | 3600 s (1 hour) |
| Signing key env | `JWT_SIGNING_KEY` |
| Allowlist env | `ALLOWED_EMAIL=ben.baichmankass@gmail.com` |
| Refresh token | not in S-013 |
| Public routes | `PUBLIC_ROUTES = {"/api/auth/login", "/api/health"}` (named set in code) |

JWT claim shape:
```json
{"email": "ben.baichmankass@gmail.com", "iat": 1714478096, "exp": 1714481696}
```

---

## Per-PR acceptance criteria

### M0 PR #1 — Test cleanup

- All 17 previously-failing tests now pass or are deleted with a one-line note in the PR description.
- `PYTHONPATH=. pytest tests/ -q --ignore=tests/test_main_loop.py` is fully green.
- No production code changed. Diff is tests-only.

### M1 PR #1 — Runtime status producer

- New module `src/web/runtime_status.py` exports `write_status(state) -> None` doing atomic write.
- One call site added in `src/runtime/pipeline.py` at end of tick. Diff to pipeline.py is ≤ 5 lines (one import, one call).
- Tests cover: shape, atomic write (no partial file visible mid-write), `git_sha` resolution from `git rev-parse --short HEAD` with fallback to env var `GIT_SHA`.
- File path: `runtime_logs/runtime_status.json` (directory created if missing).

### M2 PR #1 — `/api/status`

- New tree:
  - `src/web/api/__init__.py`
  - `src/web/api/main.py` — FastAPI app, single router import.
  - `src/web/api/auth.py` — `require_session` no-op decorator with `TODO(M3-PR-2)` comment.
  - `src/web/api/routers/__init__.py`
  - `src/web/api/routers/status.py` — `GET /api/status` handler.
- `deploy/ict-trader-web-api.service`: `EnvironmentFile=/etc/ict-trader/web-api.env`, `Restart=always`, `ExecStart=/usr/bin/python3 -m uvicorn src.web.api.main:app --host 127.0.0.1 --port 8001`.
- Tests in `tests/test_web_api_status.py`:
  - Happy path with fixture `runtime_status.json` → 200, shape matches.
  - Missing file → 503.
  - No-op decorator runs and returns control (regression guard for M3 PR #2).

### M2 PR #2 — `/api/pnl`

- New `src/web/api/routers/pnl.py`. Mounted in `main.py`.
- Tests in `tests/test_web_api_pnl.py`: happy path with fixture journal, empty-journal → all-zero, decorator passthrough.

### M3 PR #1 — JWT issuance (PM REVIEW)

- New `src/web/api/routers/auth.py` exposing `POST /api/auth/login`.
- Helpers in `src/web/api/auth.py`: `issue_token(email) -> str`, `decode_token(token) -> dict | None`.
- Reads `JWT_SIGNING_KEY` and `ALLOWED_EMAIL` from env at request time (not import time, to allow per-test env injection).
- Tests: valid creds → token whose `decode_token` returns the right email; non-allowlisted email → 403; missing env vars → 500 with no secret leakage.

### M3 PR #2 — Enforce auth (PM REVIEW)

- `require_session` flipped from no-op to actual `Authorization: Bearer <jwt>` parsing + `decode_token` + allowlist check.
- `PUBLIC_ROUTES` named set defined in `src/web/api/auth.py`.
- Tests: 401 without token, 401 with expired token (test fakes `time.time`), 401 with `alg: none` token, 401 with wrong-signature token, 403 with valid-signature token whose email is not on allowlist, 200 with valid token.

### M4 PR #1 — Runbook

- `docs/audit/sprint-013-deployment-runbook.md`:
  - Pre-flight: `git status` clean, `systemctl list-units 'ict-*'`.
  - Install env file `/etc/ict-trader/web-api.env` with `JWT_SIGNING_KEY`, `ALLOWED_EMAIL`, `WEBAPP_URL`.
  - `systemctl daemon-reload && systemctl enable --now ict-trader-web-api`.
  - Smoke: `curl -i localhost:8001/api/status` → 401; with valid JWT → 200.
  - Rollback: `systemctl disable --now ict-trader-web-api`.
  - Explicit warning: do not expose to public internet until S-014 ships a client.

### M4 PR #2 — `/webapp` Telegram + close sprint

- New handler in `src/bot/telegram_query_bot.py`. Reads `WEBAPP_URL` env. Set → URL as inline button. Unset → "Web dashboard not configured yet."
- One unit test covering both branches.
- `docs/sprint-summaries/sprint-013-summary.md` per `CLAUDE.md` § "Sprint Completion Checklist".
- Final `CP-2026-04-30-NN — S-013 SPRINT COMPLETE` appended to `CHECKPOINT_LOG.md`.

---

## Branch strategy

- Sprint kickoff (this docs PR): `claude/add-api-status-endpoint-FxNOg` (system-assigned).
- Each subsequent PR: branch off latest `main`, name `claude/s013-m{N}-pr{K}-{slug}`. Self-merge to `main` when CI green (or PM-reviewed for M3 PRs).

---

## Out of scope

- Frontend client (S-014).
- Refresh tokens, password reset, multi-operator allowlist.
- Native mobile (deferred indefinitely).
- Any change to live order placement, risk caps, or strategy logic.
