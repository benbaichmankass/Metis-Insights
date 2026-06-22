# Sprint Log: S-SYSTEM-REPORT-2026-06-22

## Date Range
- Start: 2026-06-22
- End: 2026-06-22 (in progress — bot-repo core landed; dashboard + Android phases follow)

## Objective
- Primary goal: add a **master `/system-report` skill** that runs all three
  existing reviews (`/health-review` + `/performance-review` + `/ml-review`)
  together and synthesizes ONE consolidated, time-windowed executive report —
  keeping the three skills separate.
- Secondary goals: the report is **deliverable as a self-contained responsive
  HTML link** (committed → stable GitHub link), pinged once, and surfaced as a
  **log of report links inside both apps** (Streamlit dashboard = desktop,
  Android = mobile).

## Tier
- Tier 1 — new skill + new file-backed read endpoint + stdlib renderer + docs.
  No `src/` order-path, `config/`, or live-trading file touched; the report is
  observe-only (read paths + committed artifacts).

## Starting Context
- The three-way review split (2026-05-26) — each review owns its scope, schema,
  and backlog and emits a Telegram ping. Operator wants them kept separate but
  rolled up into one report.
- Decisions locked with the operator: delivery = HTML artifact + GitHub link +
  Telegram ping + in-app Reports list (desktop + mobile); scope = format + skill
  + renderer + in-app surfacing (scheduling is phase-2); per-trade depth =
  adaptive by window.

## Repo State Checked
- Branch: `claude/system-activity-report-ukxapw` (all three repos).
- Canonical docs reviewed: CLAUDE.md, the three review SKILL.md + schema
  templates, `src/utils/paths.py`, `health_snapshots.py` (file-backed router
  pattern), `performance.py` (window helper), `daily_heartbeat.py` (stdlib idiom).

## Files and Systems Inspected
- Reviews: `.claude/skills/{health,performance,ml}-review/SKILL.md`,
  `comms/schema/{health,performance,ml}_review_response.template.json`,
  `docs/claude/{health,performance,ml}-review-backlog.json`.
- API: `src/web/api/main.py` (router registration), file-backed router pattern.
- Data sources mapped: `/api/bot/{performance,trades/closed,order-packages,
  candles,stats,strategies,ml/*,shadow/*}`, `/api/bot/prop/*`, `/api/pnl/history`,
  `/api/bot/health/*`.

## Work Completed (bot repo)
- **Output schema:** `comms/schema/system_report_response.template.json` — embeds
  the three reviews' responses verbatim + a `consolidated` block (roll-up grade,
  operator priorities, per-class PnL+trend real/paper/prop, market context,
  per-trade dossiers with adaptive depth, backlog roll-up, Tier-3 queue).
- **Renderer:** `scripts/reports/render_system_report.py` — pure, stdlib-only;
  consolidated JSON → self-contained **responsive** `report.html` + `report.md` +
  `report.json`; updates `comms/reports/index.json` (newest-first, repo-relative
  paths). Verified locally against a sample (all 7 sections render; real/paper/
  prop separate; dossiers as `<details>`; null→em-dash).
- **Read endpoint:** `src/web/api/routers/reports.py` — `GET /api/bot/reports`
  (index) + `GET /api/bot/reports/{id}` (one report's HTML). File-backed,
  Tier-1, path-traversal-guarded. Registered in `main.py`. Resolution + guard
  verified in isolation (fastapi not installed in sandbox).
- **Master skill:** `.claude/skills/system-report/SKILL.md` — runs the three in
  report mode (individual pings **suppressed**, one consolidated ping), gathers
  report-specific data, assembles + renders + delivers. Windows
  `since-last|daily|weekly|monthly`.
- **Wiring/docs:** SessionStart hook announcement (`.claude/settings.json`);
  CLAUDE.md API rows + a `/system-report` subsection; `docs/api-tier-policy.md`
  two Tier-1 routes; `docs/reports/system-report-DESIGN.md` (format spec);
  `comms/reports/README.md`.

## Remaining
- **Dashboard** (`ict-trader-dashboard`): a "Reports" tab in `streamlit_app.py`
  (list `/api/bot/reports` + embed HTML via `st.components.v1.html`); verify on
  the `claude/web-app-preview` app before any merge to `main`.
- **Android** (`ict-trader-android`): `/api/bot/reports` in `BotApi.kt` + a
  Reports drawer screen opening the HTML in a WebView; build via CI.
- **End-to-end:** run `/system-report --window=since-last` against the live diag
  relays and confirm real production trades appear in the dossiers (Generation
  Discipline Rule 3 — verify against live data before calling it done).

## Verification
- Renderer: `python3 scripts/reports/render_system_report.py <sample.json>` →
  HTML opened at desktop + mobile widths. PASS.
- Endpoint resolution + traversal guard: PASS (isolated import).
- `settings.json` + schema template: valid JSON. PASS.

## Out of Scope (documented phase-2)
- Scheduled auto daily/weekly/monthly runs (needs a cron-triggered Claude session).
- Email delivery (no SMTP infra; the HTML link + in-app list cover v1).
- Bigdata.com market-narrative enrichment of the market-context section.
