# Sprint S-013 — Secure Web Dashboard: Backend Scaffold & Home Status

> **Sprint type:** Feature sprint (lean). Phase 4 Mobile App V1 pivoted from native (React Native / Flutter) to a secure web dashboard.
>
> **Owner:** Claude Code (autonomous).
> **PM:** Ben.
> **Tech Lead:** Perplexity.
> **Created:** 2026-04-30.
> **Replaces:** ROADMAP S-013 ("App Scaffold & Home Dashboard"). Same end product (read-only dashboard with overall P&L, system status, active strategies); web stack instead of native; no app-store gates.

---

## Sprint goal

Land a small, secure, **read-only HTTP status surface** that an authenticated browser client (built in S-014) can call to render the home dashboard. Live trader uptime is preserved across the full sprint window.

## Why this sprint exists

- The PM wants single-pane visibility over P&L, system status, and active strategies without opening Telegram or SSH-ing the VM.
- A web stack delivers the same UX as a native app at a fraction of the cost.
- Doing the **backend first**, behind auth, keeps risk low — no client surface ships until M3 lands JWT enforcement.
- The S-012 closing checkpoint (CP-2026-04-29-63) flagged 17 pre-existing test failures as the "first task of S-013". M0 below clears them so the rest of the sprint has an unambiguous "pytest green" DoD.

---

## PM resolutions baked into this prompt

1. **ROADMAP S-013 framing replaced** with this web-dashboard scope. Native app deferred indefinitely.
2. **Single-operator allowlist:** `ALLOWED_EMAIL=ben.baichmankass@gmail.com`. No second operator.
3. **JWT TTL:** **1 hour.** HS256 signing. No refresh token in S-013.
4. **M0 first.** The 17-test cleanup serializes ahead of any feature PR so DoD `pytest green` is meaningful.
5. **`/webapp` Telegram command** added to the scope (M4 PR #2). Adds a single new handler that returns the staging URL from `WEBAPP_URL` env, or a clean "not configured yet" message when unset.

---

## Milestones

One PR per `M_PR_#`. ≤ 400 LOC per PR (excluding lock files). Self-merge per `CLAUDE.md` after CI green, except where flagged "PM review" below.

### M0 — Pre-flight cleanup (must land first)

- **M0 PR #1** — Rewrite or delete the 17 pre-existing failing tests against current production signatures:
  - `tests/test_runtime_validation.py` (15 failing — `validate_startup()` is now zero-arg, reads env directly).
  - `tests/test_runtime_smoke.py::test_runtime_smoke_path` (same root cause).
  - `tests/test_print_runtime_profile.py::test_print_runtime_profile_outputs_summary` (`build_settings_from_env()` now zero-arg).
  - **Acceptance:** `PYTHONPATH=. pytest tests/ -q --ignore=tests/test_main_loop.py` is fully green. No production code touched.

### M1 — Runtime status producer (foundational)

- **M1 PR #1** — Add a `runtime_logs/runtime_status.json` writer in the live tick loop. Schema:
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
  - Atomic write (`tmp` file + `os.replace`).
  - Tests: schema correctness, atomic write semantics, "absent file" is permitted (first boot).
  - **Touchpoint in `src/runtime/`** is strictly additive: one writer call at the end of each tick. No business-logic changes. This is the one carve-out to the otherwise-frozen `src/runtime/` rule.

### M2 — Read-only API

- **M2 PR #1** — `GET /api/status`:
  - New tree: `src/web/api/__init__.py`, `main.py`, `auth.py`, `routers/status.py`.
  - `auth.py` exports a no-op `require_session` decorator with a `TODO(M3-PR-2)` comment naming the checkpoint that will flip it to enforcement.
  - Reads `runtime_logs/runtime_status.json` produced by M1.
  - Missing or corrupt file → clean **503**, never 500, never a stack trace.
  - Tests: happy path (fixture file present → 200 with expected shape), missing file → 503, no-op decorator passthrough (regression guard for M3 PR #2).
  - `deploy/ict-trader-web-api.service` (staging port 8001, `Restart=always`, references `src.web.api.main:app` via uvicorn). **Not enabled in prod by this PR.** PR description states this explicitly.

- **M2 PR #2** — `GET /api/pnl`:
  - Read-only P&L per account from the existing trade journal / signals DB.
  - Same auth scaffold (still no-op).
  - Tests: happy path, empty journal, decorator passthrough.

### M3 — Auth (turn the no-op into a wall) — PM REVIEW

- **M3 PR #1** — JWT issuance:
  - `POST /api/auth/login` accepts `{email, password}` (or magic-link token if cleaner — finalize in PR description).
  - Server-side allowlist check: `email == ALLOWED_EMAIL` env var.
  - Issues HS256 JWT with `exp = now + 3600s`, signed with `JWT_SIGNING_KEY` env var.
  - Reject `alg: none` explicitly.
  - No refresh token in this PR.
  - Tests: valid creds → token; non-allowlisted email → 403; wrong password → 401.
  - **PM review required** (new secrets handling).

- **M3 PR #2** — Enforce `require_session`:
  - Flip the no-op decorator from M2 to real enforcement on `/api/status`, `/api/pnl`, and any future routes.
  - Default-deny: new routes inherit enforcement unless explicitly listed in a single, code-reviewed `PUBLIC_ROUTES` set.
  - Tests: 401 without token, 401 with expired/wrong-signature/`alg=none` token, 200 with valid token, 403 if email decoded from token is no longer on allowlist.
  - **PM review required** (auth enforcement).

### M4 — Verification, deployment artefacts, Telegram bridge

- **M4 PR #1** — VM staging runbook (`docs/audit/sprint-013-deployment-runbook.md`):
  - Steps to enable `ict-trader-web-api.service` on staging port 8001.
  - Smoke test: `curl localhost:8001/api/status` returns 401, then 200 with valid JWT.
  - Rollback: `systemctl disable --now ict-trader-web-api`.
  - Explicit note: do not expose to public internet until S-014 ships a client.

- **M4 PR #2** — `/webapp` Telegram command + sprint summary + final checkpoint:
  - Add `/webapp` handler in `src/bot/telegram_query_bot.py`. Reads `WEBAPP_URL` from env. Unset → "Web dashboard not configured yet." Set → reply with the URL as an inline button.
  - One unit test covering both branches.
  - Sprint summary at `docs/sprint-summaries/sprint-013-summary.md` per `CLAUDE.md` § "Sprint Completion Checklist".
  - Final checkpoint `CP-2026-04-30-NN — S-013 SPRINT COMPLETE` to `CHECKPOINT_LOG.md`.

---

## Auth contract (binding)

- **Secrets stay server-side.** `JWT_SIGNING_KEY` and `ALLOWED_EMAIL` live in VM env only — never in repo, never in any client bundle, never in any log line, never in any error response.
- **Allowlist is the only authn gate.** A valid JWT whose `email` claim does not match `ALLOWED_EMAIL` → 403.
- **TTL = 1 hour** (`exp = iat + 3600`). No refresh in S-013.
- **Algorithm = HS256.** Reject `alg: none` explicitly. PyJWT's default rejects it; do not override.
- **Default-deny after M3 PR #2.** `PUBLIC_ROUTES` is a single named set in code; adding to it is a code change reviewed in a PR.

---

## Guardrails (HARD STOPS)

1. Do **NOT** stop or restart `ict-trader-live.service` at any point.
2. Do **NOT** touch `src/runtime/orders.py`, `src/runtime/risk_counters.py`, `src/runtime/notify.py`, `src/runtime/signal_writer.py`, `src/runtime/validation.py`. The M1 carve-out is one additive writer call in the tick loop only.
3. Do **NOT** touch `src/main.py` business logic, `src/units/strategies/**`, `src/strategy_registry.py`, `src/core/**`, or any `config/*.yaml`.
4. Do **NOT** enable `ict-trader-web-api.service` on the live VM during this sprint. Staging-port-only artefact.
5. Do **NOT** commit `JWT_SIGNING_KEY` or `ALLOWED_EMAIL` values. `.env.example` may add them as documented placeholders only.
6. Do **NOT** ship a frontend client in S-013. That's S-014.
7. PR size ≤ 400 LOC excluding lock files. One concern per PR.
8. Self-merge after CI green per `CLAUDE.md` — **except** M3 PR #1 and M3 PR #2 which require PM review (new secrets handling).
9. Pacing: pause and re-read this prompt + DoD after every 2 merged PRs.

---

## Files Claude may modify

- `src/web/api/**` (new tree)
- `src/web/__init__.py` (extend exports if needed)
- `src/runtime/pipeline.py` — **only** to add the M1 status-writer call (one line + import). Nothing else.
- `src/bot/telegram_query_bot.py` — **only** to register `/webapp` handler (M4 PR #2). No other handler changes.
- `tests/**`
- `deploy/ict-trader-web-api.service` (new)
- `docs/audit/sprint-013-deployment-runbook.md` (new)
- `docs/sprint-summaries/sprint-013-summary.md` (new)
- `ROADMAP.md` (status updates only)
- `docs/claude/checkpoints/CHECKPOINT_LOG.md` (per-session entries)
- `.env.example` (doc-only entries for `JWT_SIGNING_KEY`, `ALLOWED_EMAIL`, `WEBAPP_URL`)

## Files OFF LIMITS

- `src/runtime/orders.py`, `src/runtime/risk_counters.py`, `src/runtime/notify.py`, `src/runtime/signal_writer.py`, `src/runtime/validation.py`
- `src/main.py`, `src/units/**`, `src/strategy_registry.py`, `src/core/**`
- `config/*.yaml`, `config/master-secrets.template.yaml`
- `deploy/ict-trader-live.service` and its timer/heartbeat siblings
- Anything under `ml/`, `notebooks/`, `data/`

---

## Definition of Done (NON-NEGOTIABLE)

- [ ] M0 PR #1 merged: `PYTHONPATH=. pytest tests/ -q --ignore=tests/test_main_loop.py` is fully green.
- [ ] M1 PR #1 merged: `runtime_logs/runtime_status.json` produced live; schema test passes.
- [ ] M2 PR #1 merged: `/api/status` returns the documented shape; missing-data path returns 503 not 500.
- [ ] M2 PR #2 merged: `/api/pnl` returns per-account P&L; empty-journal path is clean.
- [ ] M3 PR #1 merged (PM-reviewed): JWT issuance works for the allowlisted email; rejects all other inputs.
- [ ] M3 PR #2 merged (PM-reviewed): `/api/status` and `/api/pnl` are 401 without a valid JWT, 200 with one, 403 for non-allowlisted emails. `alg: none` rejected.
- [ ] M4 PR #1 runbook exists.
- [ ] M4 PR #2 ships `/webapp` Telegram command, sprint summary, final checkpoint.
- [ ] `python scripts/secret_scan.py` clean across the sprint.
- [ ] No client bundle committed.
- [ ] Live trader uptime preserved end-to-end.

---

## Pacing reminder

Slow-and-correct beats fast-and-broken. After every 2 merged PRs, re-read this prompt and the DoD. If a blocker outside the M3 PM-review checkpoints appears, post `/sprintlet_status blocked: <reason>` and stop.

End of prompt.
