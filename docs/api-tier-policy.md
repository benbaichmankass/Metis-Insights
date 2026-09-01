# API tier policy

> **Purpose:** single source of truth for which `ict-web-api.service` routes
> are unauthenticated reads (Tier 1), session-gated reads / mutations
> (Tier 2), token-gated diagnostics (Tier 2.5), and operator-controls / risk
> surface (Tier 3 — explicit gates, never auto-callable).
>
> **Authority:** this file is the human-facing inventory. The runtime gate
> is the actual `Depends(require_session)` (or token check) wired in each
> router. If they disagree, fix the code OR fix this file in the same PR
> — they must move together.
>
> ## ✅ Enforced by `api-tier-policy-guard`
>
> A route defined under `src/web/api/routers/` **must** carry a row in this
> file. [`scripts/check_api_tier_policy.py`](../scripts/check_api_tier_policy.py)
> checks it in CI (diff-scoped, in the `guards` job); `--all` is the standing
> audit and `--list` prints measured coverage.
>
> **Coverage, computed rather than counted: 102 of 102 routes documented (100%).**
> *Population — every `@router.<verb>("...")` under `src/web/api/routers/`
> joined to its `APIRouter(prefix=...)`. Verified against the live FastAPI
> route table (`app.routes`): the enumerator finds exactly those 96 with no
> false positives, and the only live routes it does not cover are the five
> defined outside `routers/` — `GET /api/health` (`main.py`) and FastAPI's
> four built-in docs routes.*
>
> **Why the guard exists, and why the number above is machine-derived.** From
> S-063 (2026-05-09) to 2026-08-09 this file had no CI check behind it, so
> every route added in that window could land without a row and **none of them
> announced itself** — the inventory reached **60% incomplete** while still
> calling itself the single source of truth. That is structural, not neglect:
> every sibling inventory that stays correct (`canonical-doc-coherence`,
> `provenance-consumer-guard`, `new-table-wiring-guard`,
> `canonical-db-resolver`) has a guard; this one did not.
>
> The completeness figure was miscounted **twice** while the previous warning
> banner was being written — 77% → 69% (once the Tier-2.5 family row was
> credited) → 60% (after that PR's own backfill). This backfill re-derived it
> from the guard's own code path and got **92 routes, 36 documented**, where
> the 2026-08-09 hand count recorded **90 and 36**: the same documented count,
> two routes apart on the denominator, two hours later. That delta is not
> worth reconciling — it is the argument. **A hand-counted completeness claim
> is stale the moment anyone edits the file, including the person writing the
> claim.** Do not restate the number above by hand; run `--list`.
>
> **Origin:** S-063 (2026-05-09). Created when `/api/pnl/history` was
> dropped from the JWT gate so the dashboard's Performance tab could consume
> it without a login flow (login is S-065). The tier split existed implicitly
> before then; this file makes it explicit.

---

## Tier 1 — public read, no session required

Endpoints a consumer hits directly without a JWT. **70 of the 96 routes**;
`_check_admin_token` / `_require_diag_token` / `require_session` appear in
none of them.

**Adding a route here is a code change reviewed in a PR.** The route must be:

1. **Read-only — never mutates state, never triggers an order.** Two routes
   are a deliberate, narrow exception (`POST /api/bot/devices/register`,
   `POST /api/bot/learning/progress`): both are *unauthenticated client
   self-service writes* to an observability table — no trading impact, no
   order path, no notification, no secret in the store. They are marked in
   the table. **A write that touches money, an order, config, or a
   notification does not qualify** and belongs in Tier 2.
2. Cheap — no expensive joins, no full-table scans without a window.
3. Safe to expose to anyone who can reach the API. Treat the threat model as
   "the dashboard URL leaked to a hostile party".

**Transport note (corrected 2026-08-09):** the rationale here used to describe
the Vercel rewrite proxying `/api/*` with CORS as the gate. That stack was
**retired 2026-05-12** and purged from the repo (see `CLAUDE.md` § "Dashboard
consumer"). Today's consumers are the **Streamlit** app (server-side upstream
call, so CORS is not load-bearing for it), the **Svelte SPA** on GitHub Pages
(browser-direct), and the **Android** app. The tier is unchanged — these are
unauthenticated reads — but the transport rationale was two-and-a-half months
stale and read as current.

| Endpoint | Source | Notes |
|---|---|---|
| `GET /api/health` | `src/web/api/main.py` | Liveness check. Always public. Defined outside `routers/`, so outside the guard's population. |
| `GET /api/bot/accounts/balances` | `routers/accounts.py` | Latest per-account balance snapshot from `trade_journal.db::balance_snapshots` (JSON file fallback; `source` records which served). **Read-only and connection-free — never opens an exchange socket.** |
| `GET /api/bot/allocator/soak` | `routers/allocator.py` | M18 P0c capital-allocator shadow-soak tail: would-pick vs actually-routed + regret, on ≥2-candidate ticks. Observe-only; nothing reads it back. |
| `GET /api/bot/backtests` | `routers/backtests.py` | Rows from `trade_journal.db::backtest_results`. ⚠️ **HISTORICAL ONLY — the writer was removed 2026-08-20** (the M5 `/test` consumer ran one hardcoded ICT engine regardless of the strategy named and stamped fabricated `0.0` metrics). The route is deliberately KEPT so the Streamlit Backtesting tab and Android `BotApi.kt:1903` do not 404; the rows are kept as a record and their zeros must not be read as measured. Headline metrics only; `limit` clamped 1..200; missing DB / table both collapse to `[]`. Real backtests: `/api/bot/backtests/sweeps`. |
| `GET /api/bot/backtests/sweeps` | `routers/backtests.py` | Strategy-improvement / validation sweeps mirrored from the trainer VM (`runtime_logs/trainer_mirror/backtests/`). File-backed; newest-first by date. |
| `GET /api/bot/candles` | `routers/candles.py` | OHLCV from the same exchange the strategies trade the symbol on (Bybit / IBKR), via the signal builders' own fetcher. **The one Tier-1 route that reaches an external venue** — bounded by a short in-process cache and a shared single-worker executor that serialises IB access. Best-effort: empty `candles` + `error` on any failure. |
| `GET /api/bot/config` | `routers/bot_config.py` | **Added S-064 (2026-05-09).** Effective config view (accounts, strategies, risk caps, halt flag, live/dry per account). Allowlist for accounts; recursive secret-key denylist for strategy params. **Never echoes `api_key_env` / `api_secret_env` values** — the redaction is what keeps this Tier 1. |
| `GET /api/bot/db/tables` | `routers/db_explorer.py` | Federated read-only schema overview across `trade_journal.db` + the `trainer_store.db` sidecar; each table tagged with its owning `db`. **Default-deny table allowlist** (`_TABLE_ALLOWLIST`): a table not named there is absent from this listing and 404s on the read. ⚠️ The previous note here — *"Neither DB holds a secret"* — was **FALSE**: `device_tokens.token` holds raw FCM push tokens and was world-readable, unauthenticated, until 2026-09-01. `device_tokens` is now excluded. `BL-20260901-DB-EXPLORER-IS-UNGATED-AND-REACHES-DEVICE-TOKENS-RAW-TOKEN-COLUMN`. |
| `GET /api/bot/db/table/{table}` | `routers/db_explorer.py` | One paginated page of a table. **SELECT-only** on a read-only (`mode=ro`) connection; table/column identifiers validated against the live schema (no identifier injection), filter values bound; `limit` 1..500; 404 on unknown table **or on any table absent from `_TABLE_ALLOWLIST`**. Columns in `_REDACTED_COLUMNS` are dropped from the schema AND from the SELECT projection, so they are neither returned nor filterable/orderable — the latter matters because `filter_state` + `total` would otherwise be a brute-force oracle for a hidden value. |
| `GET /api/bot/devices/event-kinds` | `routers/devices.py` | The canonical push event-kind taxonomy (`src.runtime.mobile_push.event_kinds`), so the Android Notifications screen needn't mirror the list. Static data, no device rows, **ungated** — unlike its siblings in the Tier-2 token table below. |
| `POST /api/bot/devices/register` | `routers/devices.py` | ⚠️ **WRITE — Tier-1 carve-out (1) above.** Upsert a device by its FCM token; idempotent on token. **No gate** (`_check_admin_token` is not called here): a device must be able to enrol itself before it holds any credential. The raw token is never echoed back — only `token_suffix` (last 8 chars). Unknown subscription kinds → 400. |
| `GET /api/bot/exit-ladder/soak` | `routers/exit_ladder.py` | ExitPlan laddered-vs-single-target shadow soak (dynamic-take-profit P3). Observe-only — nothing reads it back to drive an exit. |
| `GET /api/bot/exposure/soak` | `routers/exposure.py` | **Added 2026-08-09 (#8684).** Gross-exposure observation soak + a per-account `max_multiple` roll-up (read it beside `measured_n`). Observe-only; connection-free. |
| `GET /api/bot/exit-interval/soak` | `routers/exit_interval.py` | **Added 2026-08-16.** The M20 exit-loop's **cross-process** inter-evaluation intervals — `summary.max_interval_ms` beside `intervals_measured` **and** `processes_seen`. Exists because `exit_loop_health`'s max lives in a module global that resets every deploy, so a max over one process is systematically LOW (measured 2026-08-16: 50044.3 ms across 5 processes vs 40648.6 ms on the newest alone — a 15.7 pp understatement). `processes_seen == 1` means you are reading the per-process number again under a cross-process label. Observe-only; reads a file the exit loop appends. |
| `GET /api/bot/fc-geometry/soak` | `routers/fc_geometry.py` | M19 D1 fc-geometry soak: placed SL/TP beside the decision-time quantile-forecast snapshot. Observe-only. |
| `GET /api/bot/gpu/spend` | `routers/gpu_spend.py` | M19 spot-GPU burst spend vs the $10/month cap, from the committed `comms/gpu_spend_ledger.json`. Best-effort: missing/garbled ledger → zeroed envelope, never a 5xx. |
| `GET /api/bot/health/latest` | `routers/health_snapshots.py` | **Added 2026-05-11 (#820).** Most recent `artifacts/health/latest.json`, as `{present, path, snapshot}`. |
| `GET /api/bot/health/history` | `routers/health_snapshots.py` | **Added 2026-05-11 (#820).** Newest-first snapshots within the window; `hours` clamped 1..336 (default 24); `include_payload=true` embeds each full JSON. |
| `GET /api/bot/health/snapshot` | `routers/health_snapshots.py` | **Added 2026-05-11 (#820).** Tail of `artifacts/health/health_snapshot.txt`; `lines` clamped 1..5000 (default 200). |
| `GET /api/bot/health/services` | `routers/health_snapshots.py` | **Added 2026-05-11 (#820).** `systemctl show` state for the allowlisted bot units. **The unit allowlist is hardcoded** — no arbitrary `systemctl` query surface (that stays behind Tier 2.5 `/api/diag/services`), which is the whole reason this one is Tier 1. |
| `GET /api/bot/insights/summary` | `routers/insights.py` | M13 AI-analyst narrative. **Cache-only read path — the router never calls Anthropic and never imports the SDK**; it serves whatever the generator timer last wrote. Cache miss → 200 placeholder envelope. No API key is reachable from this route. |
| `GET /api/bot/insights/recent` | `routers/insights.py` | Same cache-only contract; echoes the requested `limit` back as `requested_limit` so a consumer can compare it against what the cache reflects. |
| `GET /api/bot/insights/strategy/{name}` | `routers/insights.py` | Same cache-only contract. `name` is validated against `[a-z0-9_]+` before the lookup, keeping the read inside the insights dir (400 otherwise). |
| `GET /api/bot/insights/health` | `routers/insights.py` | Same cache-only contract. |
| `GET /api/bot/insights/history` | `routers/insights.py` | Newest-first rows from `trade_journal.db::insights_history`; `table_present:false` when the generator has not written yet. |
| `GET /api/bot/insights/usage` | `routers/insights.py` | Calendar-month LLM spend + per-endpoint split from `trade_journal.db::insights_usage`. Spend figures only — no key, no prompt content. |
| `GET /api/bot/learning/curriculum` | `routers/learning.py` | The committed `comms/learning/curriculum.json`. Best-effort: `present:false` on a missing/garbled file (consumers fall back to a bundled copy). |
| `GET /api/bot/learning/courses` | `routers/learning.py` | Index of the interactive audio+quiz courses. |
| `GET /api/bot/learning/courses/{course_id}` | `routers/learning.py` | One course. `course_id` validated `[a-z0-9][a-z0-9_-]*` and resolved strictly under `comms/learning/courses/` — **no path traversal** (400). |
| `GET /api/bot/learning/progress` | `routers/learning.py` | Per-resource learning progress from `trade_journal.db::learning_progress`. Degraded envelope (never a 5xx) on a DB error. |
| `POST /api/bot/learning/progress` | `routers/learning.py` | ⚠️ **WRITE — Tier-1 carve-out (1) above.** Upsert one resource's progress. **Deliberately unauthenticated** so both the dashboard and the Android app can record progress without holding `DASHBOARD_API_TOKEN` (the same shape as `POST /devices/register`). Operator observability only: no trading impact, no order path, no notification, no secret in the store. 400 on a bad status / missing id. |
| `GET /api/bot/liquidity` | `routers/liquidity.py` | **Added S-064 (2026-05-09).** Per-symbol liquidity zones from `runtime_logs/liquidity_state.json`; `limit` / `sweeps_limit` clamped 1..100; missing file → 200 with empty arrays. |
| `GET /api/bot/logs` | `routers/dashboard.py` | Merged tail of the pipeline audit log + `outcomes.jsonl`. `limit` 1..1000 (default 100); `since` / `level` optional. **Operator-facing log text — the one Tier-1 route where a future log line could leak something; keep secrets out of the logs, not out of this route.** |
| `GET /api/bot/ml/status` | `routers/training_center.py` | Trainer mirror status. File-backed read of `runtime_logs/trainer_mirror/`; no trainer-VM contact. |
| `GET /api/bot/ml/cycle` | `routers/training_center.py` | Trainer cycle events, same mirror. |
| `GET /api/bot/ml/sessions` | `routers/training_center.py` | Per-manifest training sessions, same mirror. |
| `GET /api/bot/ml/registry` | `routers/training_center.py` | The model registry (stage ladder `candidate → shadow → advisory`). **Read-only — promotion is Tier-3 and never happens over HTTP.** |
| `GET /api/bot/ml/builds` | `routers/training_center.py` | Dataset-build health, same mirror. |
| `GET /api/bot/ml/db_pulls` | `routers/training_center.py` | Live→trainer DB sync history, same mirror. |
| `GET /api/bot/ml/runs/{model_id}/{run_id}` | `routers/training_center.py` | Per-run metrics. Both ids are path-validated before the artifact read. |
| `GET /api/bot/news/recent` | `routers/news.py` | M9 news-layer shadow-soak tail (per-signal decision / adjustment / veto + the top source articles read). `present:false` until the layer is active. |
| `GET /api/bot/notifications` | `routers/notifications.py` | The can't-miss banner feed both apps render on Overview (trainer down, account down, operator warnings, unreconciled orphans, trades opened). **Connection-free and best-effort** — any source failure omits that banner kind rather than 5xx-ing. |
| `GET /api/bot/order-packages` | `routers/order_packages.py` | Decision-level view (one row per order package) + the per-model ML scores and Claude grade recorded at signal time. Backtest + paper rows filtered by default. |
| `GET /api/bot/pairs/soak` | `routers/pairs.py` | M22 D2 market-neutral pairs-sleeve soak (per-bar spread/z decision + placement/close outcome, incl. the `half_open` leg state). Observe-only. |
| `GET /api/bot/performance` | `routers/performance.py` | Windowed aggregate trade analytics computed **in SQL over the full window (uncapped)** — the replacement for consumer-side rollups over the 200-row `/trades/closed` cap. Windowed by design, so it satisfies rule 2 above. Zeroed envelope (HTTP 200 + `error`) on unknown window / DB error. |
| `GET /api/bot/pnl/exchange` | `routers/pnl_exchange.py` | Exchange-truth P&L attribution (FIFO lot pairing) from `runtime_state/exchange_fills.sqlite`. `days` 1..90 (default 7). |
| `GET /api/bot/pnl/exchange/fills` | `routers/pnl_exchange.py` | The individual exchange fill **rows**, newest-first. `symbol` is the exact stored venue form and is **bound, never interpolated**; `limit` 1..1000; `truncated` declares a page that hit the cap so a short list is never mistaken for a complete population. |
| `GET /api/bot/pnl/broker-truth` | `routers/pnl_broker_truth.py` | Authoritative per-account lifetime realized PnL from the committed `comms/broker_truth_ledger.json`. **Not a money-DB rewrite** — records account-level truth beside the journal's figure. Best-effort: missing ledger → `present:false`. |
| `GET /api/bot/positions` | `routers/dashboard.py` | Open positions from `trade_journal.db`. Paper rows excluded by default (`include_paper=true` adds them). |
| `GET /api/bot/positions/net` | `routers/attribution.py` | Signed net qty per symbol across live accounts (S11/M11). Best-effort: a missing/corrupt DB returns an empty list, never a 5xx. |
| `GET /api/bot/prop/fills` | `routers/prop.py` | Inbound prop fill/close reports, newest-first. **Read half of the prop bridge — the write half is Tier 2 (below).** |
| `GET /api/bot/prop/tickets` | `routers/prop.py` | Outbound prop tickets the bot emitted, newest-first. |
| `GET /api/bot/prop/status` | `routers/prop.py` | Latest account-status snapshot + computed rule-distance (daily-loss limit, static-DD floor) from the account's prop ruleset. Nulls anything not derivable from the snapshot. |
| `GET /api/bot/prop/reconcile` | `routers/prop.py` | Un-acted tickets (emitted, past `valid_until`, no fill reported back) — the P3 drift alert. |
| `GET /api/bot/reports` | `routers/reports.py` | **Added 2026-06-22.** Newest-first index of consolidated system reports from `comms/reports/index.json`. `limit` clamped 1..500; `window` filters. File-backed, no DB table. |
| `GET /api/bot/reports/{report_id}` | `routers/reports.py` | **Added 2026-06-22.** One report's metadata + its rendered self-contained `report.html`. 404 on unknown id; artifact paths validated under `comms/reports/` — **no path traversal**. |
| `GET /api/bot/roadmap` | `routers/roadmap.py` | The parsed product roadmap (milestone table + sprint-log index). Best-effort: a missing/garbled `ROADMAP.md` degrades to an empty envelope. Short in-process cache keyed on file mtimes. |
| `GET /api/bot/roadmap/sprint/{sprint_id}` | `routers/roadmap.py` | One sprint log parsed into sections. `sprint_id` validated `[A-Za-z0-9._-]+` and resolved strictly under `docs/sprint-logs/` — **no traversal** (400); `present:false` on unknown id. |
| `GET /api/bot/work` | `routers/work.py` | The work store (`docs/claude/work/`): intents → objects → steps, lifecycle roll-up, typed `blocked_on` edges. Best-effort: a missing/garbled store degrades to an empty envelope, never a 5xx. Short in-process cache keyed on file mtimes. **Read-only — the control half (answering decisions, the read gate) is Phase H and is NOT here.** A file that fails to parse is reported in `readErrors`, never dropped. |
| `GET /api/bot/work/object/{object_id}` | `routers/work.py` | One work object in full. `object_id` validated `^[A-Za-z0-9][A-Za-z0-9._-]*$` (leading alphanumeric, so `..` is refused at the door) and resolved strictly under `docs/claude/work/objects/` — **no traversal** (400); `present:false` on unknown id, and `present:false` **with an `error`** when the file exists but cannot be parsed. |
| `GET /api/bot/shadow/predictions` | `routers/shadow.py` | Tail of `runtime_logs/shadow_predictions.jsonl` (S-AI-WS8-PART-2), newest-first. |
| `GET /api/bot/shadow/stats` | `routers/shadow.py` | Per-`(model_id, stage)` aggregates over the same log. Mirrored at Tier 2.5 as `/api/diag/shadow_stats` because the diag relay can only reach `/api/diag/*`. |
| `GET /api/bot/shadow/drift` | `routers/shadow.py` | Window-over-window score-distribution drift (KS + PSI) over the same log (S-AI-WS8-PART-3). |
| `GET /api/bot/signals` | `routers/dashboard.py` | Recent ICT detections from `signal_audit.jsonl`, each with the drawable zones the strategy already logged. |
| `GET /api/bot/stats` | `routers/dashboard.py` | Aggregated bot stats — pnl24h, totalPnL, openTrades, winRate, status, datasource, vmHealth. Real-money only; paper rides an additive sub-block. |
| `GET /api/bot/strategies` | `routers/strategies.py` | Per-strategy config, live-runtime status, per-account routing, lifetime stats, descriptions, changelog. Config values only — **secrets are not in this surface** (`accounts.yaml` credentials are env-var *names*). |
| `GET /api/bot/strategies/{name}/review` | `routers/strategy_review.py` | Newest M7 strategy-review packet incl. its action badge (`KILL`/`DEMOTE_SHADOW`/`TUNE`/`HOLD`/`PROMOTE`). **Read-only: a Tier-3 action is *read* here, never enacted.** Name validated `[a-z0-9_]+`. |
| `GET /api/bot/strategies/{name}/tune` | `routers/strategy_tune.py` | Newest M8 parameter-sweep results. Each carries an **advisory** Tier-3 value proposal; **the harness never writes config and neither does this route.** |
| `GET /api/bot/strategy/attribution` | `routers/attribution.py` | Per-strategy lifetime closed-trade stats + live open count (S11/M11). Real-money only (excludes paper AND prop, per the "real and paper never blended" contract). |
| `GET /api/bot/trades/closed` | `routers/trades_closed.py` | **Added S-557 (2026-05-09).** Closed non-backtest trades, newest-first; `limit` clamped 1..200 (default 50). Each row carries `pnlProvenance` so a consumer can caveat a fabricated/unverified figure. Paper excluded by default. |
| `GET /api/bot/trades/scores` | `routers/trade_scores.py` | **Added 2026-05-11 (#820).** Per-trade shadow-prediction score aggregates, joining each trade's open→close window against the shadow log. `limit` clamped 1..200. |
| `GET /api/pnl/history` | `routers/pnl_history.py` | **Added S-063 (2026-05-09) — the route this file was created for.** Per-day realised P&L; `days` clamped 1..90; `account_id` scopes to one account. Paper excluded. Distinct from the session-gated `GET /api/pnl` below. |
| `WS /ws/market` | `routers/market_ws.py` | **WebSocket** (P2b) — pushes live candle + open-position/uPnL snapshots on a ~2s server loop so the Android app streams instead of polling. **Read-only, no order path**; reuses the `/candles` fetcher and `/positions` reader. IB-pacing-aware cadence (crypto ~2s, IBKR futures ~8s). **Access tier is 1; its *deploy* was Tier-2** (a new live service surface) — those are different questions and the deploy tier is not an access gate. |
| `GET /` · `GET /login` · `GET /static/*` | UI surfaces / `app.mount` | Login redirect target, login page, static assets. Outside `routers/`, so outside the guard's population. |

---

## Tier 2 — session-gated reads + mutations

### 2a — JWT (`require_session`)

HS256, 1h TTL, allowlisted email. Neither the Streamlit dashboard nor the
Android app calls these today — both consume only no-session routes — so the
gate is currently invisible to every live consumer. `POST /api/auth/login`
mints the token and is itself in `PUBLIC_ROUTES` because you need it to get a
token in the first place.

| Endpoint | Source | Notes |
|---|---|---|
| `POST /api/auth/login` | `routers/auth.py` | Mints a JWT for the allowlisted email. Public so an unauthed caller can authenticate. 500 with a generic `auth_unavailable` body — **no secret-name leak** — when the auth env vars are unset. |
| `GET /api/status` | `routers/status.py` | Detailed runtime status. `Depends(require_session)`, verified in the handler signature. |
| `GET /api/pnl` | `routers/pnl.py` | Per-account P&L (realized + unrealized). `Depends(require_session)`. |

The `PUBLIC_ROUTES` set in `src/web/api/auth.py` enumerates the routes that opt
out of `require_session`. Adding a route there is a code change reviewed in a PR.

### 2b — `DASHBOARD_API_TOKEN` bearer (writes + client management)

**This is not the Tier-2 JWT and not the Tier-2.5 diag token — it is a third
bearer**, and the distinction is load-bearing because the two gates below
**fail in opposite directions when the env var is unset**. Recorded here rather
than folded into Tier 1 or 2.5, because "which token, and what happens when it
is missing" is exactly what a tier inventory is for.

| Endpoint | Source | Gate when `DASHBOARD_API_TOKEN` is **unset** | Notes |
|---|---|---|---|
| `POST /api/bot/prop/report` | `routers/prop.py` | **503 — fail-CLOSED** | Ingests an inbound prop fill/close or account-status report-back. **A genuine Tier-2 mutation: DB write + operator notification.** `_require_write_token` refuses with 503 when the token is unset rather than accepting anonymous writes, so a dropped `.env` value (e.g. a VM migration) can never reopen an anonymous write hole (BL-20260705-DASHBOARD-API-TOKEN-UNSET). Missing / wrong-scheme / wrong bearer → 401. |
| `GET /api/bot/devices` | `routers/devices.py` | **serves — fail-OPEN** | Registered devices; raw FCM tokens never exposed (only `token_suffix`). `_check_admin_token` returns silently when the token is unset, so in a deployment without it this read behaves as Tier 1. 401 on present-but-wrong bearer. |
| `DELETE /api/bot/devices/{id}` | `routers/devices.py` | **serves — fail-OPEN** | Revokes a device (lost phone). A **mutation** behind the permissive `_check_admin_token`, so with the token unset it is an unauthenticated delete. Blast radius is one push-token row — no money, no order path — which is why it has not been hardened to the prop route's fail-closed shape. 404 on unknown id. |
| `PATCH /api/bot/devices/{id}/subscriptions` | `routers/devices.py` | **serves — fail-OPEN** | Replaces a device's per-kind push subscription prefs. Same permissive gate and same one-row blast radius as the DELETE. Unknown kinds → 400; 404 on unknown id. |

> **Known divergence, recorded rather than silently reconciled:** `CLAUDE.md`
> § "Dashboard REST API" labels the three `devices` rows **Tier 1**, on the
> strength of the permissive default. Both descriptions are of the same code —
> that table documents the *contract a consumer sees*, this one documents the
> *gate mechanism*. The runtime gate is authoritative for tiering, so they are
> listed here. Whether the fail-open default is right for the DELETE/PATCH is
> a live question, not a settled one; it is not changed in this PR because
> tightening a gate is a runtime change (Tier 2), not a docs backfill.

---

## Tier 2.5 — token-gated diagnostics

The `/api/diag/*` surface is read-only but uses a **separate** bearer token
(`DIAG_READ_TOKEN`) instead of the dashboard's JWT. This is a PM-side /
operator-script surface, not a dashboard surface — see
`docs/claude/vm-operator-mode.md` § 9 and `docs/claude/diag-relay.md`.

If `DIAG_READ_TOKEN` is unset on the VM, every `/api/diag/*` route returns 503
(**closed by default**). Bad/missing bearer → 401. Verified 2026-08-09: all 16
routes on this router call `_require_diag_token` — no exceptions.

> **Family-row convention.** The first two rows name diag leaves (`audit`,
> `journal`, …) after a sibling full path rather than spelling each out.
> `api-tier-policy-guard` resolves a bare leaf against the last full path in
> the same row, so this shorthand counts as documentation. A checker that
> matched exact strings would credit none of it and report a gap the file does
> not have — which is precisely what the 2026-08-09 naive count did (76%
> missing against a true 69%).

| Endpoint family | Source | Notes |
|---|---|---|
| `GET /api/diag/snapshot`, `audit`, `journal`, `status`, `services`, `journalctl`, `log_file` | `routers/diag.py` | Token-gated SELECT-only or shell-safe diagnostic reads. |
| `GET /api/diag/audit_query`, `db_info`, `version`, `shadow_stats`, `ib_state`, `exchange_positions`, `broker_account_status` | `routers/diag.py` | **Backfilled 2026-08-09.** Added piecemeal between 2026-05 and 2026-07 and never rowed here — part of the completeness gap this file's guard now prevents. Same token gate, same read-only contract. |
| `GET /api/diag/timers` | `routers/diag.py` | **Added 2026-08-22 (BL-20260821-NO-READ-SURFACE-FOR-TIMER-SCHEDULE).** Per allowlisted `.timer` in `_CANONICAL_UNITS`: its SCHEDULE, not merely its state — `on_calendar` / `on_monotonic` / `next_elapse_*` / `last_trigger`. `/api/diag/services` reports only `active`, so `ict-exchange-fills-pull.timer` read identical whether it fired hourly or daily, and that difference was a measured money-path defect. Scope is DERIVED from `_CANONICAL_UNITS`, never a second hand-kept list. Three read states, never collapsed: `read` / `could_not_look` (systemctl absent or timed out) / and a `schedule_state` of `calendar` / `monotonic` / `no_schedule` / `unknown` — MOST of this fleet is monotonic, so an empty `TimersCalendar` is the CORRECT answer for it, not a failure. Read-only, no socket, no order path. |
| `GET /api/diag/bybit_wallet_truth` | `routers/diag.py` | **Added 2026-08-31 (operator directive).** LIVE account-level wallet truth recomputed from Bybit's own transaction log, replacing a hand-pasted UM CSV export whose figure froze on 2026-07-13 (`BL-20260830-BROKER-TRUTH-LEDGER-STALE-59-REAL-MONEY-CLOSES-UNRECONCILED`). Pure read: one read-only SQLite open on the venue-truth store, no socket, no order path. Read `state` beside the money — four states, never collapsed, and `realized_usd` is `None` (never `0.0`) unless `measured_api`. |
| `GET /api/diag/exposure` | `routers/diag.py` | **Added 2026-08-09 (#8678).** Per-account gross exposure, served as the identical `RiskManager.report()["exposure"]` the enforcing side reports through — deliberately not a reconstruction. Connection-free; never consults policy to compute. |
| `GET /api/diag/position_telemetry` | `routers/diag.py` | **Added 2026-08-17 (M31 P3).** The read half of `position_telemetry` — P2 shipped the writer with no consumer. Adds `lifecycle` (four never-collapsed states via a LEFT join to `trades`; the table itself has no status column, so a closed row is byte-shaped like an open one), `peak_pct_of_cap`, and `arm_reach`. One read-only SQLite connection, no socket, no order path, cannot refuse a trade. A lever that READS this to change an exit is M31 P5 and Tier-3. |
| `GET /api/diag/tick_cost` | `routers/diag.py` | **Added 2026-08-09 (#8688).** Per-tick wall-clock cost of the trader's hook chain (`max_ms` beside `ticks_measured`). Pure file read. Measurement only — enforces no budget. |
| `GET /api/diag/ib_open_orders` | `routers/diag.py` | **Added 2026-08-16 (#9612, BL-20260814-NO-IB-OPEN-ORDERS-READ-SURFACE).** The resting IB orders the broker actually holds, per account. Every other consumer of IB order state REDUCES it before anyone sees it (`has_protective_orders` → a boolean, `protection_coverage` → a covered quantity), so a stripped take-profit could not be contradicted from any session; this reduces nothing. Three states, never collapsed (`read_state` ∈ `not_ib` / `could_not_look` / `orders_read`); `count` is `null`, never `0`, when we could not look. Opens a brief **read-only** client per account and places NO order. |
| `GET /api/diag/bybit_open_orders` | `routers/diag.py` | **Added 2026-08-22 (BL-20260820-PROTECTION-COVERAGE-IS-PRICE-BLIND criterion 5).** The resting Bybit orders **and position-level protection**, per account — the `ib_open_orders` sibling, for the same reason: every consumer of Bybit protection state REDUCES it first. `_bybit_position_protection` returns a covered QUANTITY and its Full-mode branch returns `covered_qty == size` on any `stopLoss` string that is non-empty and not `"0"`, so the PRICE is never read and a stop anywhere grades fully covered. That row was written about IB and left Bybit explicitly unchecked; **`bybit_2` is mainnet, where the IB instance was `ib_paper`.** Carries BOTH collections because Full mode has **no resting order** (the stop is on the position row) and an orders-only surface would grade a protected position naked. Reads the `StopOrder` **and** `Order` filters — a resting limit take-profit is an `Order`. Three states, never collapsed (`read_state` ∈ `not_bybit` / `could_not_look` / `orders_read`); counts are `null`, never `0`, when we could not look; an unset venue price is `null`, never `0.0`. Read-only: grades nothing, re-arms nothing, places NO order. |
| `GET /api/diag/alpaca_open_orders` | `routers/diag.py` | **Added 2026-08-25 (BL-20260818-NO-BRACKET-READ-SURFACE-FOR-BYBIT-OR-ALPACA, the Alpaca half).** The resting Alpaca orders + open positions, per account — the third and last sibling of `ib_open_orders` / `bybit_open_orders`, for the same reason: every existing consumer REDUCES the state first (`has_protective_orders` → a boolean, `protection_state` → a pair of booleans), so neither verdict could be contradicted from any session. Measured 2026-08-25 via `scripts/ops/exit_path_coverage.py`, Alpaca was the ENTIRE remaining gap: of 15 open trades whose broker-bracket state was unobservable, **12 were alpaca** (`alpaca_paper` 6, `alpaca_portfolio` 6) and the other 3 an `ib_paper` gateway that was not answering. ⚠️ **Do NOT read this payload as the Bybit one — Alpaca has no position-level protection**; `/v2/positions` carries no protective level, so the resting ORDERS are the whole story, and the payload states that as `position_level_protection_supported: false` rather than leaving it to be inferred from an absence. Nested bracket legs are emitted with their `parent_id` (`nested=true`), since a flattener that kept only top-level orders would report a bracketed position as unprotected. Three states, never collapsed (`read_state` ∈ `not_alpaca` / `could_not_look` / `orders_read`); an unset price is `null`, never `0.0`. ⚠️ **The two sub-reads fail INDEPENDENTLY**: the orders read answers the protection question and its failure nulls the payload, but a POSITIONS outage leaves that answer intact and reports `positions: null` + `positions_state: "could_not_look"` with `position_count` `null` — never `[]`/`0`, which would claim the account is flat. Read-only: grades nothing, re-arms nothing, places NO order. |
| `GET /api/diag/bybit_wallet_truth` | `routers/diag.py` | **Added 2026-08-31 (`BL-20260830-BROKER-TRUTH-LEDGER-STALE-59-REAL-MONEY-CLOSES-UNRECONCILED`).** Per-account realized wallet truth recomputed from Bybit's OWN ledger rows in the venue-truth store (`bybit_transaction_log`, filled by the `pull-bybit-transaction-log` action + hourly timer). **It exists to retire a hand-pasted CSV from the real-money reconciliation path.** `comms/broker_truth_ledger.json` is populated BY HAND from an operator's UM export, so it froze on 2026-07-13 while `bybit_2` kept trading — 48 days and 59 closed real-money trades unreconciled, the journal under-recording by **11.0x** (-$23.77 against -$262.52). ⚠️ **Wallet truth sums `change`, NOT `cashFlow`** (which excludes fees), EXCLUDING `TRANSFER_IN`/`TRANSFER_OUT`: dropping that exclusion makes `bybit_2` read **+$4,737.48** instead of -$262.52, a $5,000 sign flip. Four states, never collapsed — `not_pulled` (nothing has ever been pulled: **we did not look**) / `no_rows_in_window` (we looked; the account was flat) / `unreadable` / `measured_api` — and `realized_usd` is `None`, **never `0.0`**, unless measured, because a genuinely flat window reports a measured `0.0` and the two must stay distinguishable. Non-USD rows are COUNTED (`non_usd_rows`) and never converted. Pure read of a local SQLite store: opens no socket, touches no `trade_journal.db` table, places no order. ⚠️ `journalTrust` on `/api/bot/trades/closed` still reads the FROZEN ledger — switching it to this figure is a deliberate follow-up, gated on a first live pull being inspected. |
| `GET /api/diag/venue_session` | `routers/diag.py` | **Added 2026-08-17 (BL-20260817-VENUE-SESSION-HAS-NO-READ-SURFACE).** The IB venue-session gate's verdict, per account+symbol. The gate is **fail-permissive on `unknown`**, so `state: "open"` and a permanently-broken gate are indistinguishable from outside — and the plausible break (`US/Eastern` is a tzdata legacy link absent from slim installs, and COMEX/CME report exactly that) is invisible in the verdict alone. **`tz_source` ∈ `zoneinfo` / `pytz` / `unresolved` is the field that answers it**, beside `tz_resolved_name` (the alias that actually worked). Read state three-ways like its `ib_open_orders` sibling (`not_ib` / `could_not_look` / `session_read`). Calls `reqContractDetails` on a brief **read-only** client and places NO order. |

---

## Tier 3 — operator controls / risk surface (NOT YET BUILT)

Reserved for the eventual halt / live-dry / restart / order-cancel controls.
These will require:

- A real session (Tier 2 JWT) AND
- An explicit per-action confirmation gate (`?confirm=YES` or a short-lived
  signed action token), AND
- An audit log entry per call with the caller's email + IP.

**No route on the API is Tier 3 today** — re-verified 2026-08-17 across all 96
routes. S-065 will land the first one (halt). Until then, every mutating
operator action goes through the `system-actions.yml` GitHub workflow, whose
allowlist is the real Tier-3 surface.

Note that several Tier-1 routes *read* Tier-3 material — `/strategies/{name}/review`
serves a `KILL`/`PROMOTE` badge, `/strategies/{name}/tune` serves an advisory
parameter proposal, `/ml/registry` serves the promotion ladder. **Reading a
Tier-3 decision is Tier 1; enacting one is not on this API at all.**

---

## Cross-references

- [`scripts/check_api_tier_policy.py`](../scripts/check_api_tier_policy.py) —
  the guard that keeps this file complete. `--list` for coverage, `--all` for
  the standing audit.
- `src/web/api/auth.py` — `PUBLIC_ROUTES` constant + `require_session` dependency.
- `CLAUDE.md` § "Dashboard REST API" — the **contract** (shapes, nullability,
  data sources). This file is the **tier** inventory; that one is the payload
  inventory. Where they overlap, neither is a substitute for reading the gate.
- `docs/claude/vm-operator-mode.md` § 9 — diag-token contract.
- `docs/claude/system-actions.md` — the GitHub-workflow allowlist for mutating
  ops actions.
- `docs/sprints/sprint-063-prompt.md` — context for the S-063 auth decision.
