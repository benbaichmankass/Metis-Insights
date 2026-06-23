---
name: system-report
description: Master executive report that runs all three reviews (/health-review + /performance-review + /ml-review) together and synthesizes ONE consolidated, time-windowed report of everything the system did since the last report — technical health, every trade with a per-trade decision dossier (split real/paper/prop), the PnL trend, a market-context read, and the ML fleet. Renders a self-contained responsive HTML report (a stable GitHub link), pings it once, and surfaces it in both apps' Reports list. Use when the operator says "run the system report", "/system-report", "give me the daily/weekly/monthly report", or "what has the system been doing". Takes --window=since-last|daily|weekly|monthly (default since-last). NOT a replacement for the three skills (it invokes them) and NOT a code review.
---

# /system-report — consolidated executive system report

This is the **master** review session. It does not replace `/health-review`,
`/performance-review`, or `/ml-review` — it **runs all three** in report mode,
then synthesizes a single executive report with report-specific data the
individual reviews don't produce (per-trade dossiers, market context, per-class
PnL trend). One report, one Telegram ping, one HTML link, surfaced in both apps.

If the operator asked for ONE domain only — just system health, just trading
performance, or just models — STOP, use that single skill instead. This skill
is the all-three roll-up.

Fully autonomous: pull live state yourself via the diag relays (skill:
`diag-data`); the operator pastes/downloads/SSHes nothing.

## Scope (what this skill DOES)

1. **Establish the window** (§ "The window").
2. **Run the three reviews in report mode** — gather each review's full analysis
   and capture its response JSON, but **suppress each one's individual Telegram
   ping** (this skill sends one consolidated ping instead) (§ "Running the three
   reviews").
3. **Gather report-specific data** the reviews don't produce — per-trade
   dossiers, market context, per-class PnL + trend (§ "Report-specific data").
4. **Assemble the consolidated JSON** conforming to
   `comms/schema/system_report_response.template.json` (§ "Assemble").
5. **Render + write artifacts** via `scripts/reports/render_system_report.py`
   (§ "Render & deliver").
6. **Send ONE consolidated ping** with the report link (§ "Render & deliver").

The format is canonical in [`docs/reports/system-report-DESIGN.md`](../../docs/reports/system-report-DESIGN.md) —
read it; this file is the operating procedure.

## Out of scope (DO NOT do here)

- **Re-grading / re-deriving AT THE SYNTHESIS LAYER** — once a sub-review has
  produced its grades/analysis, take its JSON verbatim into the
  `health`/`performance`/`ml` sub-objects; don't second-guess or recompute them
  here. This is **NOT** a licence to skip the grading itself: the
  `performance-review` sub-review still MUST run its order-package grading scorer
  first (see "Running the three reviews"). "Don't re-grade" means "don't grade
  twice", **not** "don't grade".
- **Touching `src/`, `config/`, or any live-path file.** Reports don't trade.
- **Owning a new backlog.** This skill drains nothing of its own — the three
  sub-reviews drain their own backlogs when run. Surface the roll-up counts only.
- **Scheduling.** v1 is on-demand. Automatic daily/weekly/monthly is a documented
  phase-2 (a cron-triggered session) — don't try to wire a timer here.

## The window

`--window=since-last|daily|weekly|monthly` (default `since-last`):

| Window | `window_start` |
|---|---|
| `since-last` | the previous report's `reviewed_at` from `comms/reports/index.json` (newest entry, any window class); first-ever run → last 6h. |
| `daily` | `now − 24h` |
| `weekly` | `now − 7d` |
| `monthly` | `now − 30d` |

`window_end` = now. Record `prior_report_id` (the index entry you derived
`since-last` from, or the newest prior report of the same window class). The
prior-window comparison for the trend uses the immediately-preceding equal-length
window — pull `/api/bot/performance` for both the current and prior window where
the endpoint supports it, else compute from `/api/pnl/history`.

## Running the three reviews

Execute each sub-review per its own SKILL.md, against the **live** diag relays,
covering the report window:

- `.claude/skills/health-review/SKILL.md`
- `.claude/skills/performance-review/SKILL.md`
- `.claude/skills/ml-review/SKILL.md`

Capture each one's full response JSON into the `health`, `performance`, and `ml`
sub-objects of the consolidated payload **verbatim** — same shapes as
`comms/schema/{health,performance,ml}_review_response.template.json`.

**Ping suppression (important):** each sub-review normally ends with its own
`send-ping`. When run under `/system-report`, **do not fire the three individual
pings** — set each sub-object's `claude_channel_ping.delivered_via` to
`"suppressed (system-report)"`. This skill fires exactly one consolidated ping.

**Report mode suppresses ONLY the ping — it is NOT read-only mode.** Every other
thing a sub-review does, it STILL does, including its repo-local writes:
- the **`performance-review` MUST run its order-package grading step**
  (`scripts/ops/score_order_packages.py` over the live journal → append the new
  rows to `comms/claude_strategy_scores.jsonl`) **before** the consolidated
  report reads any `claudeScore`; and
- all three **drain their own backlogs**.

Record the roll-up in `consolidated.backlog_summary`. **Regression guard
(2026-06-23):** treating report mode as read-only silently dropped grading for a
week — the 06-22 and 06-23 system-reports synthesized per-trade dossiers from
grades last refreshed 06-18, so the dashboard "Claude-graded" count read 0 on
every recent package. Grading is a mandatory write-side step of every
system-report, not an optional refresh.

If a relay is unreachable even after a `vm-web-api-recover` retry, emit the
partial report (the failed domain's sub-object carries its own degraded grade)
with `overall_assessment` reflecting the gap — never fabricate findings.

## Report-specific data

Beyond the three reviews, gather (skill: `diag-data`; reuse the REST endpoints —
do not recompute what an endpoint already returns):

- **Per-class PnL + trend** — `/api/bot/performance?window=…` (real + its `paper`
  sub-block) and `/api/pnl/history` for the current AND prior equal-length
  window. Prop from `/api/bot/prop/{status,fills,reconcile}` (isolated journal,
  never `trades`). Fill `consolidated.pnl_by_class.{real,paper,prop}` incl.
  `trend` ∈ up/down/flat and `prior_window_pnl`.
- **Trade dossiers** — `/api/bot/trades/closed?since=<window_start>&include_paper=true`
  joined to `/api/bot/order-packages` (by `linkedTradeId`) for `signalLogic` +
  `meta` + `modelScores`, and to the performance review's A–F grade
  (`claudeScore` on order-packages / `comms/claude_strategy_scores.jsonl` by
  `order_package_id`). **Grading-freshness guard (mandatory):** before consuming
  any `claudeScore`, confirm the grading actually ran THIS session — the newest
  `reviewed_at` in `comms/claude_strategy_scores.jsonl` must fall at/after
  `window_start`, and every closed package in the window must now carry a grade.
  If the newest grade predates the window, the performance-review's grading step
  was skipped → STOP and run the scorer before synthesizing; otherwise the report
  (and the dashboard's "Claude-graded" count) reflects stale grades.
  **Adaptive depth:** for `since-last`/`daily` build a full
  dossier for every trade; for `weekly`/`monthly` mark only outliers
  `notable=true` (biggest win/loss, worst grade, any prop rule-distance event)
  and rely on `pnl_by_class.per_strategy` for the rest. Record the resolution in
  `dossier_coverage`.
- **Market context** — enumerate traded symbols live (`/api/bot/strategies` +
  `/api/bot/config` account/strategy `symbols` ∪ open-position symbols, never
  hardcoded). For each, pull `/api/bot/candles` over the window and fill
  open/close/high/low + `pct_change` + a one-line regime `note`. Null (not 0) on
  a candle-fetch failure.

Render any null as em-dash downstream — never `0`/"unknown".

## Assemble

Build the consolidated object per
`comms/schema/system_report_response.template.json`:

- `report_id` = `RPT-<UTCYYYYMMDD>-<HHMMSS>-<window>`, `reviewed_at` = now,
  `reviewer` = `claude`, `window`/`window_start`/`window_end`/`prior_report_id`.
- `overall_assessment` and `consolidated.roll_up_grade` = **worst-of** the three
  sub-reviews' `overall_assessment` (`investigate` > `caution` > `healthy`).
- `consolidated.headline` — one paragraph: what happened since the last report.
- `consolidated.operator_priorities[]` — top 3–5 actions distilled across all
  three (highest-severity first; carry each item's `tier` +
  `operator_action_required`).
- `consolidated.cross_review_notes[]` — patterns spanning domains (e.g. a health
  signal→order plumbing flag AND a performance rejection cluster on the same
  symbol).
- `consolidated.tier3_proposals_pending[]` — the Tier-3 items the sub-reviews
  proposed (never enacted), surfaced in one place.

## Render & deliver

1. Write the consolidated JSON to a temp file, then run:
   ```
   python3 scripts/reports/render_system_report.py <consolidated.json> --out-dir comms/reports
   ```
   It writes `comms/reports/<window>/<UTC-ts>/{report.json,report.html,report.md}`,
   updates `comms/reports/index.json` (newest-first), and prints the HTML path.
   **Commit** the new `comms/reports/**` files (so the GitHub link is live and the
   VM's `ict-git-sync` mirrors them for `/api/bot/reports`).
2. Set `artifacts.{json_path,html_path,md_path}`, `artifacts.github_link`
   (`https://github.com/benbaichmankass/ict-trading-bot/blob/main/<html_path>`),
   and **`artifacts.dashboard_link`** — the Streamlit Reports deep link
   `https://ict-trader-dashboard-z67ryan2ttrxjdvk6ozcjc.streamlit.app/?report=<report_id>`
   (the canonical dashboard base URL is recorded in `CLAUDE.md` § "Dashboard
   consumer") — on the payload (re-render or patch the written JSON so they're
   recorded).
3. **One** consolidated `send-ping` (per `docs/claude/telegram-pings.md`):
   ```
   action: send-ping
   target: claude
   priority: normal            # 'high' if any sub-review set operator_attention_required
   message: [system-report:<window>] roll-up <grade>: H:<h> P:<p> M:<m>. <dashboard_link>
   ```
   The `<link>` in the ping is the **`artifacts.dashboard_link`** (the Streamlit
   Reports deep link), NOT the GitHub blob — so tapping the ping opens the report
   inside the app, where the operator reads it and can Download the HTML. The
   `github_link` stays in `artifacts` as a secondary reference. Keep ≤200 chars.
   This is the only ping; the three sub-reviews' pings stay suppressed.

## What you DO write (and what you don't)

**Write:**
- `comms/reports/**` (the artifacts + index) — commit them.
- Whatever each sub-review writes when run (its own backlog drain, the
  performance review's `comms/claude_strategy_scores.jsonl`) — that's the
  sub-skill's owned write, not this skill's.
- The one consolidated ping (via `send-ping`, fallback
  `docs/claude/pending-pings.jsonl`).
- Read-only diag-trigger issues (`vm-diag-request`, `trainer-vm-diag-request`,
  `vm-web-api-recover`) — they auto-close.

**Do NOT:**
- Touch `src/`, `config/`, or any live-path file.
- Fire the three individual sub-review pings (suppressed — one consolidated ping).
- Invent a new backlog or write to the three review backlogs outside of running
  the sub-reviews themselves.
- Ask scoping questions (scope fixed here) or ask the operator to fetch state.
