# CA-A08 — code audit of the web API, Telegram bot and comms (`src/web/**`, `src/bot/**`, `src/comms/**`)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

> lane CA-A08 of the 2026-09-27 code audit · session `session_01TCatsCY5RDg1pNXWFdeqsn` · audited HEAD `f5353c3` (origin/main at 2026-09-27T13:15Z) · checklist row CA-A08

**The question:** does the code in scope do what its docstrings, its tests, `docs/api-tier-policy.md` and `docs/reference/bot-api-reference.md` say it does?

**Answer:** **19 open findings: 0 critical, 2 high, 8 medium, 9 low.** Machine-readable: [`CA-A08.findings.jsonl`](CA-A08.findings.jsonl).

**The gates themselves hold.** Every route's actual auth gate matches the tier the policy doc gives it. No unauthenticated route writes anything except the two documented carve-outs, and no secret or token value reaches a response body. The defects are:
- **collapsed states**: a failed read served as an empty 200;
- **latent fail-open paths**: gates that fail open or leak only under a misconfiguration;
- **doc drift**.

## What was measured, and how

- **Route-by-route auth table (MEASURED).** I walked the live FastAPI route tree: `src.web.api.main.app`, fastapi 0.141.1, descending into `_IncludedRouter.original_router`. For each route I recorded its dependency calls and the gate helpers called in its body. Population: 113 router routes + `/api/health` + 4 FastAPI docs routes.
  - `require_session`: 4 routes. These are `/api/status`, `/api/pnl` and the two `/api/bot/db/*` routes. All 4 are in Tier 2a of the policy.
  - `_require_diag_token` as the first statement: 26 of 26 `/api/diag/*` routes.
  - Fail-closed `DASHBOARD_API_TOKEN`: `prop/report` and `work/decision`.
  - Fail-open `_check_admin_token`: 3 `devices` routes.
  - No gate: everything else. That matches Tier 1, apart from the doc-wording findings below.
- **Live probes against `https://ict-bot.duckdns.org`** (unauthenticated GET/OPTIONS only, plus one deliberately-invalid login POST; no writes):
  - `/api/status`, `/api/pnl`, `/api/bot/db/tables` and `/api/diag/status` → 401.
  - `/api/bot/devices` → 401. **`DASHBOARD_API_TOKEN` is set on the VM**, so the devices fail-open is latent.
  - `/api/auth/login` → 500 `auth_unavailable`. The Tier-2a routes are still reachable by nobody, as the policy doc records.
  - `/`, `/login` and `/static/*` → 404.
  - `/docs`, `/redoc` and `/openapi.json` → 200. The whole route inventory, including `/api/diag/*`, is publicly listed; the diag routes are still token-gated.
  - CORS: the github.io origin is allowed. A foreign-origin preflight gets no allow-origin. Allowed methods are `GET, POST` only.
- **Tests run:**
  - `pytest tests/test_web_api_*.py` → **290 passed**.
  - `pytest tests/test_devices_router.py tests/test_check_api_tier_policy.py tests/test_check_web_api.py tests/test_diag_*.py tests/test_dashboard_data_contract.py` → **229 passed**.
- **Reproduction** for the diag high: a TestClient run with `list_accounts` monkeypatched. The result is in the finding's evidence field.

## High findings

| id | claim | pipeline |
|---|---|---|
| `…-diag-venue-reads-collapse-could-not-look` | 9 venue-truth diag routes (`exchange_positions`, `ib_open_orders`, `bybit_open_orders`, `alpaca_open_orders`, `broker_account_status`, …) return 200 `accounts: []` when `list_accounts()` raises **or** the requested `account_id` matches nothing. `ib_open_orders` returns `count: 0` in that case, against its own "null, never 0, when we could not look" contract. **Reproduced.** These are the independent venue surfaces audits use to prove "nothing rests on the broker". | `PI-20260927-XWFDEQSN-0001` |
| `…-critical-alert-dropped-on-send-failure` | `_drain_critical_alerts` pops each alert before sending it. `AlertManager.send_alert` swallows every send exception and logs only the exception type. So a failed Telegram send permanently drops e.g. "account auto-paused", and the drainer's own `logger.exception` branch is dead code. Established by reading the code; not executed. | `PI-20260927-XWFDEQSN-0002` |

## Medium findings

- **`auth-docstring-claims-default-deny`**: `auth.py` says the API is default-deny, enforced by tests. In fact 4 of 113 routes carry `require_session`, and `PUBLIC_ROUTES` is read by nothing at runtime.
- **`unauth-writes-unbounded-into-trade-journal`**: `POST learning/progress` accepts a `resource_id` of any length, and neither anonymous write route has a row cap or a body limit. Both write into `trade_journal.db`.
- **`bot-config-secret-denylist-gaps`**: the redaction regex misses `passphrase`, `private_key`, `webhook`, `auth_key`, `pin` and `bearer`. Its test only uses names the regex already matches, so it can never catch the gap. Latent: no such key exists today.
- **`strategies-route-serves-raw-config-unredacted`**: the policy says "secrets are not in this surface", but that holds only because of what `strategies.yaml` contains today, not because the code enforces it.
- **`tier1-reads-collapse-db-error-to-empty`**: 7 routes return an empty 200 on a failed read: `order-packages`, `strategies`, `strategy/attribution`, `logs`, `signals`, `diag/audit` and `diag/status`.
- **`notification-alert-banners-vanish-on-detector-error`**: the "trainer down", "account down" and "prop fills stale" banners disappear when their detector raises, with only a DEBUG log.
- **`comms-answer-reported-recorded-when-push-failed`**: Telegram replies "Recorded ✅" even when the git writeback push failed, and nothing retries it.
- **`comms-auth-fails-open-when-chat-id-unset`**: the comms gate fails open when the chat id is unset, while the menu gate in the same process fails closed.

## Low findings

- **`policy-doc-lists-nonexistent-ui-routes`**: the policy lists `/`, `/login` and `/static/*`, and all three are live 404s.
- **`policy-doc-internal-count-contradictions`**: the doc says "113 of 113" and "96" in the same sentence, and "16" diag routes where there are 26. It also has a duplicate `bybit_wallet_truth` row.
- **`cors-doc-omits-the-live-origin`**: the CORS section of `bot-api-reference.md` omits github.io, the only origin in live use.
- **`devices-delete-patch-unreachable-by-cors`**: the fail-open writes cannot even be called browser-direct.
- **`login-enumeration-and-fast-hash`**: login answers 403 vs 401 (an email oracle), uses unsalted SHA-256 and has no throttle. Fix this before setting the auth envs.
- **`ws-market-unbounded-connections-on-single-worker-pools`**: `/ws/market` has no connection cap.
- **`diag-async-routes-blocking-yaml-read-invisible-to-guard`**: 8 async diag routes call `list_accounts()` synchronously, which the async-blocking guard cannot see.
- **`dead-telegram-command-docstrings`**: docstrings describe `/audit`, `/roadmap`, `/new_session` and others, none of which are registered.
- **`kill-switch-keyboard-tests-always-skip`**: these tests skip whenever python-telegram-bot is not installed, which is the default in CI.

## Checked and found fine

These are summarised; the file:line detail sits with each reader's notes.

- **`db_explorer`**: the allowlist and redacted columns hold, including through the filter and order paths. Identifiers are validated against the schema. The connection is `mode=ro`.
- **All `/api/diag/*`**: every route opens its DB `mode=ro`. `journalctl` and `log_file` are allowlisted, with list-form subprocess calls, so there is no traversal or injection.
- **Path parameters**: `reports`, `roadmap/sprint`, `learning/courses`, `ml/runs`, `work/object` and `strategies/{name}` all use a regex plus a `relative_to` check.
- **`POST /api/bot/work/decision` and `POST /api/bot/prop/report`** fail closed (503 when the token is unset). One difference: `work/decision` compares with `hmac.compare_digest`; `prop/report` uses `!=`, a timing nit that is not filed.
- **Telegram**: every mutating handler and callback checks `is_authorised`, `_is_authorized` or `_chat_authorized` before acting (the exception is the unset-env case above). No bot token is logged, thanks to the redacting filter.
- **Provenance**: `unrealizedPnlSource` is classified correctly. R values and PnL are kept apart in `performance.py`.
- **`/api/bot/positions`**: field names match `bot-api-reference.md`.

## Reading coverage

The `src/web` and `src/bot`/`src/comms` slices were read by four reader sub-agents, one slice each. I re-read and confirmed every high and medium claim at the cited lines.
- **Read in full:**
  - `src/web/api/main.py`, `auth.py`, `routers/auth.py`, `diag.py` (3,704 lines), `work.py`, `dashboard.py`, `performance.py` and `notifications.py`.
  - The other 37 routers, all `src/web/api/_*.py` helpers and `src/web/runtime_status.py`.
  - All 13 `src/bot/*` files and all 6 `src/comms/*` files.
  - `docs/api-tier-policy.md`, for tiers, counts and CORS. Its long per-row notes were not all read.
- **Partly read:** `docs/reference/bot-api-reference.md`: the CORS section, the devices, diag and positions rows, and spot checks. The long field-by-field sections were not read.
- **Not read:** `src/main.py` beyond `_drain_critical_alerts` and the telegram-client builder (other lanes own it). The other repo's SPA code (`ict-trader-dashboard`).

## Could not settle

- **Field names:** whether every `bot-api-reference.md` field name matches what the code serves. Only `/positions`, `/stats` vs `/performance`, `work/*` and the diag spot checks were compared. Note `totalPnLMeasured` on `/stats` vs `totalPnlMeasured` on `/performance`: both are documented, so it is not a mismatch.
- **Comms traffic:** whether the comms request/answer channel carries live traffic today. This sets the real weight of the two comms findings.
- **Critical-alert failures:** whether critical-alert send failures have happened in production. That needs a trader-log search for `Alert failed:`, which was not run.
- **Guard cost:** `performance?window=all` and `/stats` run uncached full-table aggregates on public routes. A reader flagged this; it is not filed, because the cost at today's journal size was not measured.
