# Sprint S-015 — Web Client V2 (Component Tabs)

> **Sprint type:** Feature sprint (lean). Phase 4 web client — extends S-014's home dashboard with operator-iterable component tabs.
> **Owner:** Claude Code (autonomous). **PM:** Ben. **Tech Lead:** Perplexity.
> **Created:** 2026-05-06. **Predecessor:** S-014 closed 2026-05-06 (PRs #183 → #419 merged; CP-2026-05-06-S-014-COMPLETE).
> **Replaces** the previous draft of `sprint-015-prompt.md` (a 2026-04-30 strategy/model-improvement spec written before S-014 was reframed as the web sprint). That spec is queued under the recurring strategy-improvement cadence; it does not belong to M-S-015.

---

## 1. Goal

Extend the S-014 home dashboard with four operator-iterable tabs — **Strategies**,
**Accounts**, **Model Metrics**, **Runtime Logs & Bugs** — so the operator can
inspect each component of the live system from the browser without bouncing
through Telegram. The home view stays as-is (default landing); each tab is an
auth-gated route that renders HTMX fragments backed by the existing
`src/units/ui/` data loaders. Live trader uptime preserved end-to-end. No
write actions in this sprint — tabs are read-only views (operator-action
surfaces are S-016).

---

## 2. Dependencies

- **Sprint dependency** — S-014 merged on `main` (CP-2026-05-06-S-014-COMPLETE).
  Specifically: `web/templates/{base,login,home}.html`, `src/web/api/main.py`
  with the `ui` router mounted, `web/static/js/{auth.js,htmx.min.js,chart.umd.js}`,
  and the `Depends(require_session)` gate from S-013 must all be on `main`.
- **Sprint dependency** — S-013 auth contract (`docs/sprints/sprint-013-prompt.md`
  § "Auth contract") is unchanged for this sprint. JWT in `localStorage`,
  HS256, 1-hour TTL, `JWT_SIGNING_KEY` + `ALLOWED_EMAIL` envs; auth.js
  already injects `Authorization: Bearer …` on every HTMX request.
- **Infra dependency** — `src/units/ui/data_loaders.py` and
  `src/units/ui/processor.py` already expose the helpers each tab needs
  (`strategy_dashboard_data`, `list_accounts`, `account_balance`,
  `recent_trades_for`, `latest_backtests_per_model`, `backtest_history_for`,
  `recent_rejections`, `open_order_packages`, `recent_logs_for`). The sprint
  consumes these — it does **not** add new business logic to the UI unit
  unless a tab discovers a real gap.
- **Infra dependency** — `ict-web-api.service` keeps listening on
  `127.0.0.1:8001`. No reverse proxy, no public exposure (that's S-014.5,
  still queued).
- **External dependency** — none. The sprint runs entirely against committed
  code + the local sandbox; no live VM, no operator online during the
  session.

If any dependency fails verification at session start (S-014 not on `main`,
auth gate broken, UI helpers missing), the sprint stops and the prompt is
revised — do not paper over a missing dependency.

---

## 3. Deliverables

Concrete artefacts that exist after the sprint ships:

1. **Tab navigation scaffold** — `web/templates/base.html` gains a tab nav
   with four links (Strategies, Accounts, Model Metrics, Runtime Logs &
   Bugs) plus the existing Home; `src/web/api/routers/ui.py` registers
   four new auth-gated page routes (`/strategies`, `/accounts`,
   `/model-metrics`, `/runtime`).
2. **Strategies tab** — `web/templates/strategies.html` + fragment
   `web/templates/fragments/strategies.html`; `GET /ui/fragments/strategies`
   renders per-strategy cards (enabled flag, timeframe, symbols, signals
   today, P&L today, open positions) backed by
   `strategy_dashboard_data()`. HTMX poll every 30 s.
3. **Accounts tab** — `web/templates/accounts.html` + fragment
   `web/templates/fragments/accounts.html`; `GET /ui/fragments/accounts`
   renders per-account cards (mode live/dry, balance, open positions, last
   trade summary, recent rejections count) backed by `list_accounts()` +
   `account_balance()` + `account_open_positions()` + `account_last_trade()`.
   HTMX poll every 30 s.
4. **Model Metrics tab** — `web/templates/model_metrics.html` + fragment
   `web/templates/fragments/model_metrics.html`; `GET /ui/fragments/model-metrics`
   renders the latest-per-model backtest table + a 5-row history per
   strategy version backed by `latest_backtests_per_model()` +
   `backtest_history_for()`. Refresh every 5 minutes (slow-moving data).
5. **Runtime Logs & Bugs tab** — `web/templates/runtime.html` + fragment
   `web/templates/fragments/runtime.html`; `GET /ui/fragments/runtime`
   renders three sections: most-recent rejections (`recent_rejections`),
   open order packages (`open_order_packages`), and the bug-log tail
   parsed from `docs/claude/bug-log.md`. HTMX poll every 60 s.
6. **Auth-gated unavailable templates** — `fragments/<tab>_unavailable.html`
   per tab, mirroring the S-014 pattern, returned from the fragment when
   the underlying data source is missing or raises (covers DB-gone,
   yaml-missing, journal-corrupt).
7. **Smoke-test runbook appendix** — append S-015 section to
   `docs/audit/sprint-013-deployment-runbook.md`: log in, click each tab,
   verify it renders, verify auth gate (open in incognito → /login).
8. **Sprint summary** — `docs/sprint-summaries/sprint-015-summary.md`
   per `CLAUDE.md` § "Sprint Completion Checklist".
9. **Closing checkpoint** — `CP-YYYY-MM-DD-NN — S-015 SPRINT COMPLETE`
   appended to `CHECKPOINT_LOG.md`; ROADMAP S-015 → ✅ Done; M-S-015 moved
   to "Recently closed" in `milestone-state.md`; S-014.5 (or S-016 per PM)
   pulled into Active.

Each deliverable maps to at least one PR in § 4.

---

## 4. Checkpoints

One row per checkpoint, in expected merge order. PRs are ≤ 400 LOC each
(HTML + CSS counts; vendored JS does not — there's none added in this
sprint, the S-014 vendored copies are reused).

| #   | Checkpoint title                  | What completes by then                                                                                          | Risk class | Wall-clock | Gates                  |
|-----|-----------------------------------|-----------------------------------------------------------------------------------------------------------------|------------|------------|------------------------|
| T0  | Kickoff (this PR)                 | Replace stale prompt; CP-2026-05-06-S-015-01 appended; milestone-state Active sprint pointer updated.           | docs-only  | ≤ 30 min   | T1                     |
| T1  | M0 PR #1 — Tab nav scaffold       | `base.html` nav bar; four empty page templates + four UI router routes; tests assert each route returns 200 + auth gate. | infra      | ≤ 60 min   | T2, T3, T4, T5         |
| T2  | M1 PR #1 — Strategies tab         | `strategies.html` + fragment + `GET /ui/fragments/strategies`; tests assert HTML shape + auth + unavailable path. | infra      | ≤ 90 min   | T6                     |
| T3  | M2 PR #1 — Accounts tab           | `accounts.html` + fragment + `GET /ui/fragments/accounts`; tests as above; pll budget unchanged.                | infra      | ≤ 90 min   | T6                     |
| T4  | M3 PR #1 — Model Metrics tab      | `model_metrics.html` + fragment + `GET /ui/fragments/model-metrics`; tests as above; 5-min refresh cadence.     | infra      | ≤ 90 min   | T6                     |
| T5  | M4 PR #1 — Runtime Logs & Bugs tab | `runtime.html` + fragment + `GET /ui/fragments/runtime`; tests as above; bug-log tail parser + 5 unit tests.   | infra      | ≤ 90 min   | T6                     |
| T5b | Mid-sprint checkpoint             | After T3 (per pacing rule: every 2 merged PRs); audit drift, re-read prompt.                                    | docs-only  | ≤ 15 min   | T6                     |
| T6  | M5 PR #1 — Sprint close           | Smoke-test appendix + sprint summary + ROADMAP / milestone-state flip + closing checkpoint + Telegram ping.    | docs-only  | ≤ 60 min   | (none — sprint closed) |

**Total wall-clock estimate:** ≤ 7 hours across 6 PRs (one PR per tab plus
nav scaffold plus close). Sprint splits across multiple sessions per
`checkpoint-workflow.md` § "One task per session" — typically T1 in one
session, T2/T3 in the next, T4/T5 in the third, T6 closes.

### 4b. Unit boundary declaration

Per `CLAUDE.md` § "Architecture rules", the units this sprint touches:

| Unit                                   | Role in this sprint                                                                                                 |
|----------------------------------------|---------------------------------------------------------------------------------------------------------------------|
| `src/units/strategies/`                | **untouched** (no signal logic changes).                                                                            |
| `src/units/accounts/`                  | **untouched** (no risk / execution changes).                                                                        |
| `src/units/db/` (`src/data_layer/`)    | **reads** — UI helpers query the trade / package / signal logs through their existing public API.                   |
| `src/units/ui/`                        | **reads** primarily; **owns** small additive helpers only if a tab discovers a real gap (e.g. a bug-log tail parser). New helpers MUST stay strategy-agnostic and operate on already-public DB unit shapes. |
| `src/units/dashboards/`                | **untouched** (Streamlit views unchanged).                                                                          |
| `src/runtime/`                         | **untouched** — no order-path, no risk-counter, no notify, no signal-writer code touched.                           |
| `src/bot/`                             | **untouched** — Telegram bot is not part of this sprint.                                                            |
| `src/core/coordinator.py`              | **untouched**.                                                                                                      |
| `src/web/api/`                         | **owns** — adds `routers/strategies_fragment.py`, `routers/accounts_fragment.py`, `routers/model_metrics_fragment.py`, `routers/runtime_fragment.py`, plus four new page routes inside the existing `routers/ui.py`. |
| `web/` (templates + static)            | **owns** — adds four page templates + four fragment templates + four `*_unavailable.html` fragments + nav bar. CSS additions ride on the existing `web/static/css/app.css`. |

**Cross-unit imports:** none new. Fragment routers import only from
`src/units/ui/` (and FastAPI / Jinja2). No fragment imports
`src/units/strategies/`, `src/units/accounts/`, `src/runtime/`, or any
exchange SDK directly. The `Coordinator` is not in this sprint's call
graph — UI unit is allowed to read DB unit directly per architecture
rule 4.

---

## 5. Risk class & merge model

Every PR opened in this sprint maps to a class in `sprint-planning.md` § 5.

| Class           | Self-merge? | This sprint's PRs                                                                            |
|-----------------|:-----------:|----------------------------------------------------------------------------------------------|
| **infra**       | ✅           | T1 (nav scaffold), T2 (Strategies), T3 (Accounts), T4 (Model Metrics), T5 (Runtime Logs).    |
| **docs-only**   | ✅           | T0 (this kickoff PR), T5b (mid-sprint checkpoint), T6 (sprint close).                        |
| **strategy / model** | ❌      | _none._ This sprint does not touch `config/strategies.yaml` or any strategy code.            |
| **deploy / live**    | ❌      | _none._ This sprint does not touch `src/runtime/**`, `src/main.py`, `deploy/**`, or any live-trading code path. |

**Default rule:** every PR is self-merged after CI green, per
`CLAUDE.md` § "Merging Rules". The S-014 PM-review gates on M2 (login flow,
token storage, security-critical client behaviour) **do not apply** here —
S-015 reuses the M2 client behaviour as-is, no new security-critical
client code is added. Auth-aware request injection is already in
`auth.js`; this sprint just adds new endpoints behind the same gate.

If during the sprint a tab discovers it needs a write action (e.g. "edit
strategy parameter from the UI"), **stop**, file a ping-PR, and defer
the write to S-016. Read-only is the contract.

---

## 6. Success criteria

Measurable. Each criterion is something a script or a person can check
after the sprint closes.

- ✅ `PYTHONPATH=. pytest tests/test_web_api_*.py -q` returns 0; the four
  new tab-fragment test files (`test_web_api_strategies_fragment.py`,
  `test_web_api_accounts_fragment.py`, `test_web_api_model_metrics_fragment.py`,
  `test_web_api_runtime_fragment.py`) each have ≥ 4 tests covering happy
  path, auth gate, missing-source unavailable path, and HTML-shape
  contract.
- ✅ `python scripts/secret_scan.py` clean across every PR.
- ✅ `PYTHONPATH=. pytest --collect-only -q tests` shows the test count
  rose by ≥ 16 vs S-014's closing baseline (1741 collected) — i.e.
  ≥ 1757 collected at sprint close (4 tests × 4 tab fragment files,
  minimum).
- ✅ Each new page route (`/strategies`, `/accounts`, `/model-metrics`,
  `/runtime`) returns 200 with the expected HTML for an authed request
  and **either** a redirect to `/login` or a 401 for an unauthed
  request — matches S-014's home / page behaviour.
- ✅ Each new fragment route (`/ui/fragments/strategies`, `/accounts`,
  `/model-metrics`, `/runtime`) is gated by `Depends(require_session)`,
  returns the documented HTML shape on happy path, and falls back to
  the corresponding `*_unavailable.html` on data-source error.
- ✅ `docs/audit/sprint-013-deployment-runbook.md` has the S-015
  appendix; the operator can follow it end-to-end without external
  context.
- ✅ `docs/sprint-summaries/sprint-015-summary.md` exists with the DoD
  checkbox table, full PR list, architecture decisions, deferred items,
  3 lessons learned.
- ✅ `ROADMAP.md` S-015 row → ✅ Done; `milestone-state.md` Active
  milestone advanced to S-014.5 or S-016 (PM's call); `CHECKPOINT_LOG.md`
  has the closing entry with `S-015 SPRINT COMPLETE` in the title (fires
  the high-priority Telegram ping per `decomposition-rules.md` § 2.4).
- ✅ Live-mode invariant ✅ across every PR — `src/runtime/orders.py`,
  `src/runtime/pipeline.py`, `src/runtime/trading_mode.py`,
  `src/units/accounts/*`, `config/accounts.yaml`, `.env*` templates
  untouched. `scripts/check_dry_run_in_diff.py` clean every PR.
- ❌ No tab opens a write action (operator-action surface is S-016).
- ❌ No tab adds a new vendored JS library — Chart.js + HTMX from
  S-014 are sufficient.

Avoid vibes. If a tab "looks right" but no test asserts the HTML shape,
the criterion isn't met.

---

## 7. Hard guardrails

Inherited from `CLAUDE.md` standing rules plus sprint-specific:

1. Do **NOT** touch the S-013 auth contract — `auth.py` `PUBLIC_ROUTES` is
   the same set as after S-014 (`/login`, `/static/*` public; everything
   else gated). All four new page routes and four new fragment routes
   sit behind `Depends(require_session)`.
2. Do **NOT** install `node`, `npm`, `pnpm`, or any JS toolchain. This
   sprint adds **zero** new vendored JS — HTMX 2.0.4 and Chart.js 4.4.7
   from S-014 are the only client libs.
3. Do **NOT** expose the dashboard publicly. `ict-web-api.service` stays
   on `127.0.0.1:8001`. Reverse proxy + TLS is S-014.5.
4. Do **NOT** touch `src/runtime/**`, `src/main.py`, `src/units/strategies/**`,
   `src/units/accounts/**`, `src/strategy_registry.py`, `src/core/**`, or
   `config/*.yaml`.
5. Do **NOT** touch `src/bot/telegram_query_bot.py` or
   `src/bot/claude_bridge.py`.
6. Tokens never appear in URLs, query strings, or any log line — same
   secret-scan rule as S-014.
7. PR size ≤ 400 LOC excluding existing-vendored JS. CSS additions ride
   on `web/static/css/app.css` and count.
8. **No new business logic in `src/units/ui/`** unless a tab discovers a
   real gap — and even then the change must be additive and
   strategy-agnostic. The default for this sprint is "consume the
   existing helpers".
9. **No write actions.** If a tab UX itches for a "save" or "edit"
   button, defer to S-016 and document in the summary's "Deferred"
   section.
10. Pacing: re-read this prompt and the DoD after every 2 merged PRs
    (per `sprint-planning.md` § "Pacing"); append a checkpoint at the
    same cadence.
11. Live-mode invariant (`CLAUDE.md` § "Live-mode invariant"): every PR
    runs `scripts/check_dry_run_in_diff.py` before opening — clean is the
    bar; not-clean → ping-PR + stop.

### Files Claude may modify

- `web/templates/**` (new pages + fragments + nav bar update on `base.html`).
- `web/static/css/app.css` (additive — tab nav styling, per-tab card variants).
- `web/static/js/**` — only additive helpers if a tab strictly needs
  client-side behaviour (e.g. expand/collapse). No new vendored libs.
  Default: no JS changes.
- `src/web/api/main.py` — only to register the four new fragment routers.
- `src/web/api/routers/ui.py` — only to add the four new page routes.
- `src/web/api/routers/strategies_fragment.py` (new),
  `accounts_fragment.py` (new), `model_metrics_fragment.py` (new),
  `runtime_fragment.py` (new).
- `src/units/ui/data_loaders.py` / `processor.py` — additive helpers only
  if a tab discovers a real gap (e.g. a bug-log tail parser). If touched,
  call it out in the PR description.
- `tests/**` (new test files per fragment).
- `docs/sprints/sprint-015-prompt.md` (this file, mid-sprint refinements).
- `docs/sprint-summaries/sprint-015-summary.md` (new at T6).
- `docs/audit/sprint-013-deployment-runbook.md` (S-015 appendix at T6).
- `docs/claude/checkpoints/CHECKPOINT_LOG.md` (per-session entries).
- `docs/claude/milestone-state.md` (Active milestone updates).
- `ROADMAP.md` (status updates only).

### Files OFF LIMITS

- `src/runtime/**` (orders, risk_counters, notify, signal_writer,
  validation, pipeline, trading_mode).
- `src/main.py`, `src/strategy_registry.py`, `src/core/**`.
- `src/units/strategies/**`, `src/units/accounts/**`, `src/units/db/**`,
  `src/units/dashboards/**`, `src/units/trading_school/**`.
- `config/*.yaml`, `config/master-secrets.template.yaml`, `.env*`
  templates.
- `deploy/**` (no service file changes; web service config unchanged).
- `src/bot/**`.
- Anything under `ml/`, `notebooks/`, `data/`.

---

## 8. Hand-off

What the next sprint needs from this one:

- **S-014.5 (Web Client public exposure)** — gating condition
  `loopback dashboard validated by operator` flips to **true** the
  moment the operator runs the S-015 smoke-test appendix and confirms
  every tab renders. The smoke-test appendix in S-015 § T6 is the
  artefact that unblocks S-014.5.
- **S-016 (Secure API Key Management)** — first sprint that adds a
  **write** surface to the dashboard. Whatever pattern S-015 establishes
  for tab routing, fragment polling, and the `*_unavailable.html`
  fallback, S-016 reuses; whatever write pattern (CSRF, idempotency,
  audit trail) S-016 invents, S-015 does **not** preempt.
- **Recurring strategy improvement cadence** — the previous draft of
  `sprint-015-prompt.md` was a strategy/model improvement spec. That
  content is preserved at git history `7b5e03c~1:docs/sprints/sprint-015-prompt.md`
  (or the per-PR context of CP-2026-05-06-S-014-COMPLETE) for the next
  recurring strategy-improvement session — it does not block this
  sprint. The recurring cadence runs per
  `docs/sprints/recurring-strategy-improvement-prompt.md`.
- Known issues / deferred work to surface in T6's summary:
  - Operator-action layer (write actions from the dashboard) — S-016.
  - CSP headers — to ship with S-014.5.
  - Refresh-token flow — out of scope unless the 1-hour TTL bites
    operator usage.

---

## Concrete first action

After reading the docs above and confirming S-014 is on `main`:

1. `git fetch origin main && git status` — verify clean working tree on
   the kickoff branch (`claude/start-next-sprint-e6V6E` or
   `claude/s015-kickoff` per `git-workflow.md`).
2. Verify the S-014 deliverables are present on `main`:
   - `ls web/templates/{base,login,home}.html` → all three exist.
   - `ls web/static/js/{auth,htmx.min,chart.umd,equity_chart}.js` →
     all four exist.
   - `grep -n "require_session" src/web/api/routers/ui.py` → confirms
     the auth gate is wired.
3. T1 (M0 PR #1 — Tab nav scaffold) is the next concrete code PR. Branch
   `claude/s015-m0-pr1-tab-nav` off `origin/main`, add the nav bar to
   `base.html`, add four empty page templates + four UI router routes
   that return placeholder content + auth gate, ship tests, self-merge
   after CI green.

End of prompt.
