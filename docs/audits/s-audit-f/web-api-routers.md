# S-AUDIT-F — Web API surface (line-by-line)

Part of the M17 Full-System Audit (`docs/audits/full-system-audit-2026-06-28.md`).
Branch: `claude/audit-F-web-api-routers`.

**Slice:** `src/web/api/main.py` + every `src/web/api/routers/*.py` **except**
`accounts.py` and `diag.py` (already audited in Workstream-A/C and S-AUDIT-E).
Read IN FULL, line by line, against the "Dashboard REST API" table in root
`CLAUDE.md`, the null→"—" contract, and the real/paper/prop separation.

Legend: ✅ VERIFIED CLEAN · 🐞→✔ bug/drift fixed · 📝 doc-table drift · 🔎 note.

## Method

Each file read whole. Each route's behaviour checked against (a) its CLAUDE.md
contract row, (b) the null-not-zero rule (`Position`-shape contract: render null
as "—", never `0`/"unknown"), (c) the "real and paper never blended" P4 rule.
Live-state cross-checks where useful. Provenance (`git log -S`) run before
filing any field-vs-comment finding (PR #1358 rule).

## Files read in full (coverage)

- `src/web/api/main.py` ✅ — all 30 routers mounted; every endpoint in the
  CLAUDE.md table maps to a mounted router. CORS GET+POST, `DASHBOARD_ORIGIN`
  optional. `/api/health` trivial. Clean.
- `routers/dashboard.py` ✅ — `/stats`, `/logs`, `/positions`, `/signals`.
- `routers/bot_config.py` 🐞→✔ — `/config`. One stale note string (fixed).
- `routers/liquidity.py` ✅ — `/liquidity`.
- `routers/news.py` ✅ — `/news/recent`.
- `routers/exit_ladder.py` ✅ — `/exit-ladder/soak`.
- `routers/reports.py` ✅ — `/reports`, `/reports/{id}`. Path-traversal guard solid.
- `routers/prop.py` ✅ — `/prop/{report,fills,tickets,status,reconcile}`. Token-gate correct; canonical projection over order_packages.
- `routers/trades_closed.py` ✅ — `/trades/closed`.
- `routers/performance.py` 📝 — `/performance`. Behaviour clean; **doc-table under-documents the return shape** (fixed in CLAUDE.md).
- `routers/order_packages.py` ✅ — `/order-packages`.
- `routers/candles.py` ✅ — `/candles`.
- `routers/pnl.py` ✅ — `/api/pnl` (session-gated).
- `routers/pnl_history.py` ✅ — `/api/pnl/history` (no-session).
- `routers/status.py` ✅ — `/api/status` (session-gated).
- `routers/insights.py` ✅ — `/insights/{summary,recent,strategy/{name},health,history,usage}`.
- `routers/devices.py` ✅ — `/devices/*` (register/list/event-kinds/delete/patch).
- `routers/shadow.py` ✅ — `/shadow/{predictions,stats,drift}`.
- `routers/health_snapshots.py` ✅ — `/health/{latest,history,snapshot,services}`.
- `routers/trade_scores.py` ✅ — `/trades/scores`.
- `routers/strategies.py` ✅ — `/strategies`.
- `routers/strategy_review.py` ✅ — `/strategies/{name}/review`.
- `routers/strategy_tune.py` ✅ — `/strategies/{name}/tune`.
- `routers/backtests.py` ✅ — `/backtests`, `/backtests/sweeps`.
- `routers/attribution.py` ✅ — `/positions/net`, `/strategy/attribution`.
- `routers/pnl_exchange.py` ✅ — `/pnl/exchange`.
- `routers/auth.py` 🔎 — `/api/auth/login` (issuance only; undocumented in the table — see note).
- `routers/db_explorer.py` ✅ — `/db/tables`, `/db/table/{name}`. SELECT-only / identifier-validated / value-bound — injection-safe.
- `routers/training_center.py` 🔎 — `/ml/*`. Clean; one stale docstring path (minor, logged to backlog).

## Findings

### 🐞→✔ F1 (Tier-1, fixed) — `bot_config.py` stale Telegram-command note string

`GET /api/bot/config` returns a `trading_mode.note` field whose text reads:

> "…live_per_account is the pipeline's runtime view (Telegram /accounts
> dry|live overrides land here)…"

The legacy Telegram `/accounts dry|live` writer was **removed in #1933** (the
bot overhaul) — confirmed canonical in `docs/CLAUDE-RULES-CANONICAL.md`
§ Prime Directive "What this rules out": *"✅ Telegram `/accounts dry|live`
handler — DONE (#1933)"* — and verified absent from `src/bot/` (grep:
no handler). The only sanctioned mode-write wire is the `set-account-mode`
system-action. The note is **user-facing API response text** that misdescribes
how `live_per_account` is populated (it now reflects the pipeline's per-tick
runtime status, driven by the `set-account-mode` action, not a Telegram command).

- **Class:** code-vs-doc drift (stale comment surfaced in an API response).
- **Field beats comment** (PR #1358 rule): the live code is truth; fix the text.
- **Provenance:** the note predates `dd205383` (the last commit to touch the
  file, an unrelated orphan-flap change). No operator-approved commit asserts
  the Telegram command still exists — the canonical doc says it was removed.
- **Fix:** reword the note to describe the `set-account-mode` wire. No
  behavioural change (string content only). Tier-1.

### 📝 F2 (Tier-1, fixed) — `/api/bot/performance` doc-table under-documents the return shape

The root-`CLAUDE.md` "Dashboard REST API" row for `GET /api/bot/performance`
documents the return as:
`{window, since, totalTrades, wins, losses, winRate, totalPnl, expectancy,
perStrategy:[…], equity:[…]}`.

The route (`performance.py::_aggregate` + `get_performance`) actually returns,
**additionally**: `error`, `totalR`, `expectancyR`, `rTradeCount`, `rCoverage`,
`profitFactor`, `maxDrawdown`, `perAssetClass:[…]`, and the additive
`paper`/`demo` sub-blocks (same shape, paper-account rows). `perStrategy` rows
also carry `totalR`/`expectancyR`/`rTradeCount` beyond the documented
`{name,trades,wins,winRate,totalPnl,expectancy}`.

These extra fields are load-bearing for the dashboard exec summary (the
dashboard `CLAUDE.md` already references `profitFactor`/`maxDrawdown`/
`perAssetClass`), so the *bot's* contract table — the canonical source — is the
one that drifted (incomplete), not the code.

- **Class:** doc-table drift (the table is the contract; it under-documents the
  real shape). The dashboard CLAUDE.md is ahead of the bot CLAUDE.md table.
- **Fix:** extend the `/api/bot/performance` row in the bot `CLAUDE.md` table to
  list the full returned shape (incl. the R-metrics, profitFactor, maxDrawdown,
  perAssetClass, and the paper/demo sub-blocks). Doc-only, Tier-1.
- **Real/paper separation:** VERIFIED correct — the top-level block is
  real-money-only (`_NOT_PAPER_PREDICATE`); paper rides in additive
  `paper`/`demo` sub-blocks via `_PAPER_PREDICATE`; never summed together. P4 OK.

### 🔎 F3 (note, logged to backlog) — `/api/auth/login` not in the REST API table

`routers/auth.py` mounts `POST /api/auth/login` (token issuance for the
session-gated `/api/pnl` + `/api/status`). It is not listed in either the
"Dashboard REST API" or "Diagnostic API" table in `CLAUDE.md`. The dashboard
consumes only the no-session read paths, so this is invisible to the consumer —
but the table claims to enumerate the API surface. Minor inventory gap; logged
to the health-review backlog (not worth a doc PR churn this pass).

### 🔎 F4 (note, logged to backlog) — `training_center.py::get_registry` docstring path

The `/api/bot/ml/registry` docstring says the rows come "from
`ml/registry-store/registry.jsonl`", but the code reads the mirror path
`runtime_logs/trainer_mirror/registry.jsonl` (`_mirror_root() / "registry.jsonl"`),
which is what the module's own mirror-layout header documents. The body
docstring path is stale/misleading. Minor; logged to backlog.

## Verification of the contract invariants (no findings — recorded as checked)

- **null-not-zero (`Position` contract):** VERIFIED across every router.
  `dashboard.py` `/positions` returns `unrealizedPnl=None` (not 0) on broker
  unavailable + local-mark fallback, `stopLoss`/`takeProfit`/`pattern`/`options`
  null when absent. `trades_closed.py` returns `realizedPnl: null` for
  reconciler-incomplete NULL-pnl rows (explicit 2026-06-04 de-coercion).
  `performance.py` `profitFactor`/`maxDrawdown`/`totalR` are `None` (not 0) when
  uncomputable. `order_packages.py`/`backtests.py` coerce-to-None helpers. No
  fabricated-zero contract bug found.
- **real/paper/prop separation (P4):** VERIFIED. `/stats`, `/performance`,
  `/trades/closed`, `/order-packages`, `/pnl/history`, `/strategies`,
  `/strategy/attribution` all use the canonical
  `src.web.api._clean_trades` predicates (`not_paper_predicate` /
  `paper_predicate` / `exclude_reconciler_predicate` /
  `exclude_superseded_predicate`) — single source of truth, no duplicated/drifted
  split logic. Prop is isolated in its own `prop_*` tables (router `prop.py`),
  never blended into real/paper KPIs.
- **injection safety:** VERIFIED. `db_explorer.py` is SELECT-only, `mode=ro`,
  identifiers validated against the live schema, values bound. `training_center.py`
  + `strategy_review.py` + `strategy_tune.py` + `insights.py` validate
  strategy/model/run ids against `[a-z0-9_]+` / `_SAFE_ID` to keep path lookups
  traversal-safe. `reports.py` resolves artifact paths under `comms/reports/`
  with a `relative_to` traversal guard.
- **best-effort / never-5xx Tier-1 reads:** VERIFIED. Every read router degrades
  to an empty/zeroed envelope (200) on missing/locked/corrupt DB, except the
  deliberate S-067 503 on `/stats` + `/api/pnl` structural DB failure (so the
  dashboard shows a real outage badge instead of fabricated zeros) — which is
  the documented contract.

## Disposition

- **F1, F2** — Tier-1 fixes shipped this slice (DRAFT PRs per the audit
  protocol; comment/doc-only, no behavioural change to what any route returns).
- **F3, F4** — minor; logged to `docs/claude/health-review-backlog.json`.
- **No real (behavioural) bugs, no dead/zombie routes, no Tier-3 proposals.**
  Every mounted router is reachable + consumed; no orphan endpoints.
