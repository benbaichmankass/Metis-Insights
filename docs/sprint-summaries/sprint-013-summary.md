# Sprint S-013 — Secure Web Dashboard: Backend Scaffold & Home Status

> **Sprint type:** Feature sprint (lean). Phase 4 Mobile App V1 pivoted from native to a secure web dashboard.
> **Owner:** Claude Code (autonomous). **PM:** Ben. **Tech Lead:** Perplexity.
> **Created:** 2026-04-30. **Closed:** 2026-04-30.
> **Goal:** Land a small, secure, **read-only HTTP status surface** that an authenticated browser client (S-014) can call to render a home dashboard. Live trader uptime preserved end-to-end.

## Outcome at a glance

| DoD checkbox | Status | Closed by |
|---|---|---|
| M0: 17 pre-existing failing tests cleared; suite unambiguously green | ✅ | M0 PR #1 |
| M1: `runtime_logs/runtime_status.json` produced live; schema test passes | ✅ | M1 PR #1 |
| M2: `/api/status` returns documented shape; missing data → 503 | ✅ | M2 PR #1 |
| M2: `/api/pnl` returns per-account P&L; empty journal clean | ✅ | M2 PR #2 |
| M3: JWT issuance works for allowlisted email; rejects all other inputs | ✅ | M3 PR #1 (PM-reviewed) |
| M3: `/api/status` + `/api/pnl` are 401 without token, 200 with one, 403 for non-allowlisted | ✅ | M3 PR #2 (PM-reviewed) |
| M4: VM staging runbook ships | ✅ | M4 PR #1 |
| M4: `/webapp` Telegram command + sprint summary + final checkpoint | ✅ | M4 PR #2 (this one) |
| `secret_scan.py` clean across the sprint | ✅ | every PR |
| No client bundle committed | ✅ | by design — S-014 scope |
| Live trader uptime preserved end-to-end | ✅ | guardrail #1; no `ict-trader-live` change |

## PRs merged

| PR | Title |
|---|---|
| [#173](https://github.com/benbaichmankass/ict-trading-bot/pull/173) | S-013 kickoff: sprint prompt, plan, ROADMAP update, kickoff checkpoint |
| [#174](https://github.com/benbaichmankass/ict-trading-bot/pull/174) | S-013 M0 PR #1: clear 17 pre-existing failing tests |
| [#175](https://github.com/benbaichmankass/ict-trading-bot/pull/175) | S-013 M1 PR #1: runtime status producer |
| [#176](https://github.com/benbaichmankass/ict-trading-bot/pull/176) | S-013 M2 PR #1: GET /api/status (no-op auth) |
| [#177](https://github.com/benbaichmankass/ict-trading-bot/pull/177) | S-013 M2 PR #2: GET /api/pnl (no-op auth) |
| [#178](https://github.com/benbaichmankass/ict-trading-bot/pull/178) | S-013 M3 PR #1: POST /api/auth/login + JWT helpers (PM REVIEW) |
| [#179](https://github.com/benbaichmankass/ict-trading-bot/pull/179) | S-013 session checkpoint: CP-2026-04-30-02 |
| [#180](https://github.com/benbaichmankass/ict-trading-bot/pull/180) | S-013 M3 PR #2: flip require_session to enforcement (PM REVIEW) |
| [#181](https://github.com/benbaichmankass/ict-trading-bot/pull/181) | S-013 M4 PR #1: VM staging deployment runbook |
| #182 | S-013 M4 PR #2: /webapp Telegram + sprint summary + final checkpoint (this PR) |

**Total:** 10 PRs (1 kickoff + 1 M0 + 1 M1 + 2 M2 + 2 M3 + 1 mid-sprint checkpoint + 2 M4).

## Tests added

| Test file | Count | Coverage |
|---|---:|---|
| `tests/test_s013_runtime_status.py` | 11 | Atomic JSON producer — schema, uptime tracking, live override semantics, default-dry, only-enabled strategies surfaced, missing-yaml graceful, both git_sha fallbacks, atomic-replace invariant, exception swallowing |
| `tests/test_web_api_status.py` | 13 | `GET /api/status` — happy path, 503 missing/corrupt file, public `/api/health`, **+10 enforcement tests** (missing/non-Bearer/empty/garbage/expired/`alg=none`/wrong-sig/off-allowlist/missing signing key/missing allowed-email) |
| `tests/test_web_api_pnl.py` | 9 | `GET /api/pnl` — realised/unrealised aggregation, UTC bucketing, backtest-row exclusion, empty journal, missing DB, corrupt DB, legacy `live` account surfacing, **+2 enforcement tests** (missing token, off-allowlist) |
| `tests/test_web_api_auth_login.py` | 16 | `POST /api/auth/login` + helpers — happy path, 401/403/422/500, parametrised secret-leakage guard across all three env vars, JWT round-trip, tampered/expired/`alg=none`/missing-key decode, constant-time password verify, `PUBLIC_ROUTES` invariant, log-in→status round-trip |
| `tests/test_s013_webapp_command.py` | 4 | `/webapp` Telegram handler — unconfigured / blank / configured / not-authorised |
| **Total new** | **53** | |

Tests **updated** during the sprint:
- `tests/test_s012_service_consolidation.py` — `EXPECTED_SERVICES` extended to include `ict-web-api.service` with an inline rationale comment.

Tests **deleted** in M0 PR #1:
- `tests/test_runtime_validation.py`, `tests/test_runtime_smoke.py`, `tests/test_print_runtime_profile.py` (17 stale tests; canonical replacements live in `tests/test_validation.py` and `tests/test_s012_live_mode.py`).

Suite at sprint end: **1239 passed, 2 skipped, 0 failed** on the M4 PR #2 branch (was 1153 / 17 failed at sprint start).

## Files added

- `src/web/runtime_status.py` — atomic JSON producer.
- `src/web/api/__init__.py`, `main.py`, `auth.py`, `routers/__init__.py`, `routers/status.py`, `routers/pnl.py`, `routers/auth.py`.
- `deploy/ict-web-api.service` — staging-only systemd unit on `127.0.0.1:8001`.
- `docs/audit/sprint-013-deployment-runbook.md` — six-step VM enable + smoke-test procedure.
- `docs/sprints/sprint-013-prompt.md`, `docs/sprint-plans/sprint-plan-2026-04-30.md` — binding sprint prompt + plan.

## Files modified

- `src/runtime/pipeline.py` — strictly additive M1 carve-out: one import + one `write_status()` call at end of `run_pipeline()`. No business-logic change.
- `src/bot/telegram_query_bot.py` — registered `cmd_webapp`, added the `BotCommand("webapp", …)` entry, added a line to the `/help` text.
- `requirements.txt` — `fastapi`, `uvicorn`, `httpx`, `pyjwt`, `email-validator`.
- `.env.example` — documented `JWT_SIGNING_KEY`, `ALLOWED_EMAIL`, `WEBAPP_PASSWORD_SHA256`, `WEBAPP_URL` (placeholders only).
- `ROADMAP.md` — Phase 4 reframed; S-011/S-012 marked done; S-013 in progress; S-014/S-015 renumbered.
- `tests/test_s012_service_consolidation.py` — `EXPECTED_SERVICES` updated.

## Architecture decisions

1. **Service named `ict-web-api.service`, not `ict-trader-web-api.service`.** The dashboard backend is not trader-side: it binds to a loopback port and only reads a JSON file. Naming it without the `ict-trader-` prefix keeps S-012 PR D2's single-process invariant test (`test_only_one_trader_side_unit`) honest.
2. **Default-deny on the API.** `PUBLIC_ROUTES = {"/api/auth/login", "/api/health"}` is a single named set in `src/web/api/auth.py`. Adding to it is a code change reviewed in a PR. Every other route attaches `Depends(require_session)`.
3. **Per-call env reads.** `JWT_SIGNING_KEY`, `ALLOWED_EMAIL`, `WEBAPP_PASSWORD_SHA256` are read inside each request handler, not at import time. Tests can monkeypatch and the systemd `EnvironmentFile` updates without a process restart.
4. **HS256 + 1h TTL, no refresh.** PyJWT's `algorithms=["HS256"]` rejects `alg: none` automatically. No refresh-token flow in S-013 — the operator re-authenticates once per hour.
5. **SHA-256 password (not bcrypt/argon2).** Single-operator, gated by allowlist (only one email gets to attempt a password at all), behind a loopback systemd unit. No public attack surface. Trade-off accepted for simplicity + zero extra deps.
6. **Error responses must not leak which env var is missing.** Parametrised test in `tests/test_web_api_auth_login.py` deletes each of the three auth env vars in turn and asserts the response body contains none of (env-var name, signing key, password plaintext, password hash).

## /webapp Telegram bridge

`/webapp` reads `WEBAPP_URL` from env. Unset/blank → "Web dashboard not configured yet." Set → an inline button "🔐 Open dashboard" linking to the URL. The link is only safe to publish once M3 PR #2 enforcement is live, so M4 PR #2 ships **after** M3 PR #2.

## What this sprint did NOT do (deferred)

- **S-014: web client v1.** Browser-side login form, home dashboard rendering of `/api/status` + `/api/pnl`, equity curve. Backend is ready for it.
- **Public exposure.** The dashboard is loopback-only on the VM until S-014 ships a client and the operator decides on a reverse-proxy + TLS plan.
- **Refresh tokens / multi-operator allowlist / password reset.** Not required for S-013's single-operator scope.
- **S-016 (was S-015): API key management UI.** Backlog.

## Lessons learned

1. **PM resolutions belong in the sprint prompt, not in conversation.** Baking the four PM decisions (single-operator allowlist, 1h TTL, M0 first, `/webapp` command) into `docs/sprints/sprint-013-prompt.md` made every later PR self-contained — the prompt was the single source of truth and the per-PR descriptions could quote it without re-litigating.
2. **Stale roadmap is a leading indicator of stale planning docs.** The original prompt referenced `sprint-013-prompt.md` and `sprint-plan-2026-04-30.md` that didn't exist, plus a `CP-2026-04-30-02` checkpoint that was a forward reference. Cohesive prompt drafting + ROADMAP refresh **before** the first feature PR (PR #173) saved real backtracking — every later PR could cite a binding plan.
3. **Sprint names tied to product framing rot.** ROADMAP S-013 was originally "App Scaffold & Home Dashboard — React Native or Flutter scaffold". The product reality (web dashboard, no native) had drifted; renaming Phase 4 to "Secure Web Dashboard" up front avoided having every downstream PR claim "we're building S-013 even though it doesn't match the roadmap title".

## Suggested CLAUDE.md improvements for the next sprint

1. Add a "stale-prompt detection" rule: if a session prompt references docs that don't exist, **stop and surface the discrepancy before any code change**. The first session of S-013 nearly silently invented a sprint plan from a prompt that didn't match reality; catching this at minute 1 saved hours.
2. Add a "PM-review hand-off pattern" to `docs/claude/session-workflow.md`: when a PR is flagged for PM review (secrets / live trading / `deploy/`), push as draft, append a session-end checkpoint *immediately*, and stop. Don't try to stack the next PR locally — the next PR's correctness depends on PM-reviewed code that may change in review.

## Deferred items

* **None at sprint scope.** Every M0 → M4 milestone shipped.
* **VM run of the runbook is the PM's call** — `docs/audit/sprint-013-deployment-runbook.md` ships the procedure; the actual VM enable is operator-side.
* **S-014 unblocked.** Web client v1 can start whenever PM picks the next sprint.
