# S-AI-WS8-PART-2 — Shadow-predictions dashboard endpoints

**Date:** 2026-05-10
**Authority:** [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md), [`docs/sprint-logs/S-AI-WS8-PART-1.md`](S-AI-WS8-PART-1.md)
**Status:** ✅ COMPLETE

## Goal

Make `runtime_logs/shadow_predictions.jsonl` observable from the
Vercel dashboard, not just the SSH-and-CLI surface from PART-1.
Same inspector module powers both — no duplicate parsing — so the
dashboard view and the operator's CLI return identical data.

## Decisions

- **Two endpoints, mirroring CLI subcommands.**
  `GET /api/bot/shadow/predictions` (newest-N records) +
  `GET /api/bot/shadow/stats` (per-`(model_id, stage)` aggregate).
  Same filter surface as the CLI: `model_id`, `stage`, `since`,
  plus a `limit` on `/predictions`.
- **Shared core via `ml.shadow.inspector`.** The router imports
  `iter_records`, `filter_records`, and `aggregate` directly.
  Zero duplicate JSONL parsing or aggregation logic. PART-1's
  unit tests for those functions cover the underlying behaviour.
- **Response envelope, not bare list.** Both endpoints return
  `{log_present, log_path, records[], count}`. The `log_present`
  boolean lets the dashboard distinguish "no records matched yet"
  (file exists, empty / filtered out) from "shadow mode never
  fired" (file absent). Bare lists conflate these.
- **Unauthenticated GET (Tier 1).** Operational telemetry, no
  secrets in the audit log records. Matches the contract on
  `/api/bot/stats`, `/api/bot/logs`, etc. — restrict at the
  network layer (firewall + CORS), not application layer.
- **`SHADOW_PREDICTIONS_LOG` env override.** Same pattern as
  `TRADE_JOURNAL_DB` — operators can point the endpoint at an
  alternate log file for testing or post-mortem analysis without
  redeploying.
- **FastAPI `Query` validation.** `limit: int = Query(ge=1,
  le=1000)` gives 422 on out-of-range. `since` parsed in the
  handler via the same ISO-8601 rule as the CLI; 400 with a
  helpful message on bad input.
- **No pagination cursor.** Single-page `limit`-bounded result is
  fine for v1 — the audit log doesn't grow large enough to need
  cursor pagination until shadow mode is heavily used in
  production (long after PART-1 + PART-2 land). Filed for
  PART-2-FU if the audit log volume ever needs it.

## Deliverables

- `src/web/api/routers/shadow.py` (new) — two GET routes,
  envelope response shape, FastAPI `Query` validation, env-var
  log-path override. ~130 LOC.
- `src/web/api/main.py` — `shadow_router` import + `include_router`
  call.
- `CLAUDE.md` — two new rows on the Dashboard REST API table; one
  new file in the directory map; two new lines on the architecture
  diagram.
- `tests/test_web_api_shadow.py` (new) — 14 tests across
  `TestPredictionsEndpoint`, `TestStatsEndpoint`,
  `TestRouterMounted`:
  - Envelope when log missing (both routes).
  - Newest-first ordering.
  - `limit` cap.
  - `model_id` / `stage` / `since` filters.
  - `400` on bad `since`.
  - `422` on out-of-range `limit`.
  - Per-`(model_id, stage)` aggregation correctness.
  - Sort by count desc.
  - Aggregate `since` filter.
  - `first_seen` / `last_seen` ISO-8601 serialization.
  - `row_keys_seen` sorted in response.
  - Routes mounted in OpenAPI spec.
- `ROADMAP.md` — new ledger entry; WS8 status updated.

## Acceptance

- [x] `pytest tests/ml/ tests/runtime/ tests/test_web_api_shadow.py` —
      320 pass + 1 skipped (test_web_api_shadow skips on dev sandbox
      without `fastapi`; CI has it).
- [x] `ruff check` clean on router + main.py + test.
- [x] Both endpoints return `200` with the envelope shape when no
      records / log absent (no `404`s — empty is a valid state).
- [x] `400` on bad `since`; `422` on out-of-range `limit`.
- [x] Routes appear in `/openapi.json` (verified by test).
- [x] Inspector module reused, not re-implemented.

## Out of scope (filed for follow-ups)

- **WS8-PART-3 — Drift detector.** Compare shadow score
  distribution against deterministic decisions / realised
  outcomes; flag divergence over rolling windows. Needs
  `trade_outcomes` populated post-deploy.
- **Pagination cursor** if audit log volume eventually demands
  it.
- **WebSocket stream** for live shadow-score tail (the existing
  pattern is HTTP polling, which works fine for current cadence).
- **Authentication** — currently Tier 1 unauth like other
  `/api/bot/*` routes. If shadow scores ever become sensitive
  (e.g. reveal trader's view of own model), move behind the
  existing `DASHBOARD_API_TOKEN` bearer.
- **Audit log rotation** for `shadow_predictions.jsonl` — filed
  on WS7-PART-6.

## Live runtime impact

None until the live VM serves the new endpoints. The router is
included in the `FastAPI` app; on next deploy
(`pull-and-deploy` operator action) the routes become reachable
at `http://<live-VM>:8001/api/bot/shadow/{predictions,stats}`.
Reads from `runtime_logs/shadow_predictions.jsonl` (empty until
an operator opts a model into shadow mode). Zero impact on the
live trader process — the FastAPI process is the existing
`ict-web-api.service`, independent from `ict-trader-live.service`.
