---
name: system-review
description: Master SYSTEM REVIEW session — the WORK is the review; the report is just its deliverable. Runs all three reviews (/health-review + /performance-review + /ml-review), actively reviews the whole system since the last review (technical health + every trade graded + strategy promotion/demotion readiness + ML training-cycle health + soak progress), DIAGNOSES bugs and PROPOSES fixes, raises flags when something has stalled or a gate is met, then synthesizes ONE consolidated, time-windowed system report (per-trade dossiers split real/paper/prop, PnL trend, market context, ML fleet) — a self-contained responsive HTML the apps surface in their Reports list, pinged once. Use when the operator says "/system-review", "/system-report", "run the system review/report", "give me the daily/weekly/monthly review", or "what has the system been doing / where do we stand". Takes --window=since-last|daily|weekly|monthly (default since-last). NOT a replacement for the three skills (it invokes them) and NOT a code review.
---

# /system-review — the master system-review session (deliverable: the system report)

> **⚠️ READ FIRST — WHAT THIS SESSION IS.** This is **FULL END-TO-END QA OF THE
> WHOLE SYSTEM**, NOT a report-generator and NOT a scan-and-sweep-under-the-rug
> exercise. Your job is to actively **HUNT** for issues across every layer (bugs,
> correctness gaps, money-at-risk conditions, silent regressions, stalled
> pipelines), **ROOT-CAUSE** them, **PROPOSE** the exact fix, decide the Tier-2/3
> calls **WITH the operator**, and then **FIX** them — this session. **Finding a
> fixable bug and logging it to a backlog as a post-it note instead of driving it
> to a fix is a REVIEW FAILURE** — that is exactly how bugs become operational
> catastrophes. The consolidated report is the *deliverable*, never the goal. You
> can ALWAYS weigh in with the operator — but raising the flags is YOUR job; never
> passively wait for the operator to point at the problem. This framing binds the
> three sub-reviews this session runs, too.

This is the **master review session**. The **review is the work**; the **report
is just the deliverable** it produces. It does not replace `/health-review`,
`/performance-review`, or `/ml-review` — it **runs all three** and then
synthesizes a single executive **system report** (per-trade dossiers, market
context, per-class PnL trend, ML fleet). One report, one Telegram ping, one HTML
link, surfaced in both apps' **Reports** list. (The on-disk / artifact / API
name stays "report" — `/api/bot/reports`, `comms/reports/`, the apps' Reports
tabs — only the *session* is the "review". `/system-report` is a back-compat
alias for this same session.)

If the operator asked for ONE domain only — just system health, just trading
performance, or just models — STOP, use that single skill instead. This skill
is the all-three roll-up.

Fully autonomous: pull live state yourself via the diag relays (skill:
`diag-data`); the operator pastes/downloads/SSHes nothing.

## This is a REVIEW (work), not a report (passive summary) — binding

A system review is a **work session**, same posture as the session-start
contract: **diagnose and fix / propose, don't just describe.** Summarizing
findings and moving on is the failure mode this skill exists to prevent. Every
run MUST actively:

1. **Grade every trade** since the last review (the `performance-review` scorer
   runs — see "Running the three reviews"; the grading-freshness guard is
   mandatory).
2. **Assess strategy promotion/demotion readiness** — for each strategy, where
   it stands vs its gate (KILL / DEMOTE_SHADOW / TUNE / HOLD / PROMOTE per the M7
   review packets + the shadow→advisory ladder). Surface what is *ready to
   promote* and what should be *demoted/killed*, with the evidence.
3. **Confirm the ML lifecycle is progressing** — training cycles actually ran
   since the last review, dataset builds succeeded, models are advancing, and
   every **soak is actually soaking** (accruing the volume/days it needs). If a
   training cycle is failing, a model is stuck, or a soak has stalled — or has
   already MET its promotion criteria and is just sitting there — **raise it as
   a flag**, don't let it pass silently.
4. **Find bugs and propose fixes** — a review that surfaces a pipeline/data/exit
   bug carries it to a *proposed fix* (Tier-1/2 fixed in a follow-up PR; Tier-3
   proposed to the operator with the exact change). Orphaned status, non-TP/SL/
   strategy closes, undelivered alerts, stalled soaks: bugs to drive, not notes
   to file.
5. **Raise flags loudly** — anything degrading (a strategy bleeding, a soak
   stalled, a gate met-but-unactioned, a training failure) goes in
   `operator_priorities` / `cross_review_notes` with `operator_action_required`
   set, not buried.
6. **Work the three review backlogs down — a HARD COMPLETION GATE, every
   open item, not a sample** — `docs/claude/{health,performance,ml}-review-backlog.json`
   are part of the job, not a tally. Each run, **triage EVERY open item in all
   three** (the sub-reviews each enforce their own 100%-triage gate — see their
   "Draining the backlog — a HARD COMPLETION GATE" sections; running them is how
   this step is done). For each: re-validate against the state you pulled, then
   *drain* — fix Tier-1 items in-place / Tier-2 in a follow-up PR and mark
   `resolved` with a full-timestamp `resolved_at`; carry Tier-3 items to the
   operator as an exact proposed change. An item may stay `kept_open` ONLY if it
   is genuinely soaking, blocked on future data, or a Tier-3 awaiting the
   operator — and then it MUST carry an update noting this run's re-validation +
   the blocker. **Triaging "the recent few" or the items this session touched is
   a review FAILURE** — the backlog IS the standing open-task list, so reporting
   a review "done" while open items sit unlooked-at is the exact lazy-incompetence
   failure this gate exists to stop. A report whose `backlog_summary` shows many
   open and ~zero triaged is that tell. Counting the backlog
   (the `backlog_counts.py` roll-up) is NOT the same as working it, and each
   domain's `count_untriaged` MUST be 0.

Producing the report is NOT the finish line — the review's findings being
*driven* (fixed, or put in front of the operator as an exact decision) is, and
the backlogs being *worked down* is. The **Review-coverage guard** below fails a
run that skipped the promotion / training / soak assessment or that shows no
backlog drive.

## Review-coverage guard (mandatory — 2026-06-23)

Before rendering, the consolidated payload MUST carry a populated
`consolidated.review_coverage` object proving the review actually covered its
mandate (not just the trade/health summary). Same enforcement pattern as the
grading-freshness guard. Required, non-empty:

- `review_coverage.strategy_promotion` — per-strategy promotion/demotion stance
  (ready-to-promote, demote/kill candidates, or "all HOLD") with evidence; pulled
  from the `ml`/`performance` sub-reviews + `/api/bot/strategies/{name}/review`.
- `review_coverage.ml_training_health` — did training cycles run since the last
  review? dataset builds OK? any failing/stuck cycle? **any
  `manifest_quarantine_tripped` / `manifest_quarantined` cycle event** (a
  single-manifest OOM the trainer escalated — BL-20260717-TRAINER-SINGLE-MANIFEST-OOM,
  requires a Rule-3 shrink/GPU/drop disposition — see the `/ml-review` rubric)?
  (from `/ml-review` + trainer relay).
- `review_coverage.soak_status` — each active soak (shadow models, conviction,
  exit-ladder) and whether it is accruing as expected, stalled, or has met its
  gate; flags for any stall / met-but-unactioned.
- `review_coverage.flags_raised[]` — the loud flags this review surfaced (may be
  empty only if genuinely nothing is degrading — state that explicitly).
- `review_coverage.account_reachability` — **mandatory** per-account up/down for
  every declared-live broker account (the "all declared-live, non-shelved" set:
  `mode: live` + a probeable exchange, excluding the dry/shelved `ib_live` /
  `oanda_practice` and the API-less `breakout_1`). Pull it from
  `/api/diag/exchange_positions` (positions=null ⇒ unreachable), the latch state
  (`runtime_logs/account_reachability_alert_state.json` via
  `account_reachability_alert.down_accounts()`), and `/api/bot/accounts/balances`
  (`api_ok`). **Any down live account is a MANDATORY `flags_raised[]` entry that
  fires its OWN standalone high-priority ping — it must NOT be buried only in the
  report body.** This is the explicit guard against the failure that motivated it:
  the IB gateway was dark across reviews and went unflagged.
- `review_coverage.backlog_drive` — proof the three backlogs were *worked*, not
  just counted: per domain, what you `drained` this run (the item ids you
  resolved) and `deferred` (ids left open + the reason each is legitimately not
  actionable now: soaking / future-data / Tier-3-awaiting-operator). **Per
  domain it MUST also carry `{open_at_start, triaged, count_untriaged}`, and
  `count_untriaged` MUST be 0 with `triaged == open_at_start`** — the completion
  gate mirroring the sub-reviews. If you drained nothing, this must say why every
  open item is non-actionable — "no time" / "didn't look" / triaging only "the
  recent few" is a review FAILURE, not a valid reason.

**STOP and complete the assessment if any of the five required keys
(`strategy_promotion`, `ml_training_health`, `soak_status`, `backlog_drive`,
`account_reachability`) is missing or empty, OR if any domain's
`backlog_drive.count_untriaged > 0`** — a review that can't show its
promotion/training/soak coverage, its *full* backlog drive (every open item
triaged, not a sample), *or its per-account reachability* has not actually run,
regardless of how complete the trade/health summary looks. (Relay-blocked data
is allowed only as an explicit `"unavailable: <reason>"` string — never silently
omitted.)

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
- **Owning a *new* backlog.** This skill creates no backlog of its own — but it
  is NOT exempt from draining: it MUST actively work the three sub-review
  backlogs down (mandatory action 6) and record the drive in
  `review_coverage.backlog_drive`. "Surface the roll-up counts" is the floor, not
  the job.
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

**GRADING IS MANDATORY — NO REVIEW IS COMPLETE WITHOUT A FRESH CLAUDE SCORE FOR
EVERY CLOSED TRADE IN THE WINDOW** (operator directive 2026-06-29). The grades
live in `comms/claude_strategy_scores.jsonl` (a repo file the API joins
last-wins), NOT the live DB — so "I can't reach the DB" is **never** an excuse to
skip grading. Two paths, by session type:
- **DB-bearing session (VM / desktop CLI):** run the canonical
  `scripts/ops/score_order_packages.py <trade_journal.db>` — it rewrites the full
  JSONL from the live `order_packages`.
- **Web / PM session (no DB file) — PRIMARY path: the `grade-closed-trades`
  system-action** (added 2026-07-06, see `docs/claude/system-actions.md`).
  Dispatch it (Tier-1, autonomous) via a labelled `system-action` issue with
  `action: grade-closed-trades` (+ optional `since:`/`limit:`/`include_open:`).
  It runs the SAME `_grade_package` rubric on the VM (where the DB already
  lives) and returns **only the ungraded delta** as NDJSON in the issue-comment
  reply — a bounded, small payload, unlike pulling the whole `trades` table
  through the diag relay (a full table runs ~650KB against the relay's ~55KB
  comment budget, which repeatedly truncated/failed full-window grading before
  this fix). Append the returned NDJSON rows to
  `comms/claude_strategy_scores.jsonl` and commit. The response never
  truncates silently — an oversized delta ends with a trailing
  `{"_delta_summary": ..., "truncated": true, "more_available": N}` line; raise
  `limit:` or re-dispatch if you see one.
- **Web / PM session — documented FALLBACK** (only if `system-actions.yml`
  itself is unavailable): pull the window's closed trades via
  `GET /api/diag/journal?table=trades` and run
  **`scripts/ops/grade_closed_trades_from_diag.py <trades.json> --since <window_start>`**
  — it APPENDS one grade per closed trade using the SAME `_grade_package` rubric
  (imported, not re-implemented), and last-occurrence-wins means it supersedes any
  stale open-status grade. (Prop rows are isolated — not in `trades`, not graded
  here.) Then **commit `comms/claude_strategy_scores.jsonl`.** This path is the
  one that previously hit the diag relay's comment-size wall for anything beyond
  a tiny window — prefer `grade-closed-trades` above.
  *(The 2026-06-29 incident this fixes: a web-session review skipped grading
  believing it needed live-DB write, shipping a report whose closed trades read
  ungraded. The diag grader removed that excuse; the system-action above removes
  the size-limit wall the diag grader then ran into.)*

Record the roll-up in `consolidated.backlog_summary` — **computed, never
hand-entered.** Run:
```
python3 scripts/reports/backlog_counts.py --since <window_start>
```
and copy its `{total, open, resolved, drained}` per domain straight into
`backlog_summary`. **Backlog-count regression guard (2026-06-23):** a
hand-assembled summary put the *total* in `health.open` ("132" when real open was
73) and left `performance`/`ml.open` null → "— open" (real opens 28 / 16) — even
though every count is exact from the backlog files. The open/total counts are
always computable; if `backlog_summary` carries a null `open`, or an `open` that
equals `total` for a domain whose file has resolved items, you guessed instead of
running the counter — STOP and run it. (`drained` is precise only when
`resolved_at` is a full ISO timestamp; a date-only `resolved_at` degrades it to
day granularity — write full timestamps when you resolve an item.)

**Grading regression guard (2026-06-23):** treating report mode as read-only
silently dropped grading for a week — the 06-22 and 06-23 system-reports
synthesized per-trade dossiers from grades last refreshed 06-18, so the dashboard
"Claude-graded" count read 0 on every recent package. Grading is a mandatory
write-side step of every system-report, not an optional refresh.

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
- `consolidated.monitoring[]` — the **Monitoring** section (2026-06-25): the
  backlog items the review is actively *watching* rather than acting on — things
  that need more time (`soaking` / `awaiting-data`) or a decision
  (`awaiting-decision` — a gate is met or it's operator-gated) or a recurring
  `verify`. Curate from the three backlogs' open items whose deferral reason is
  soak/data/decision (NOT stale-doc / code-fix items — those are *actionable*, so
  they belong in `operator_priorities` or a follow-up PR, not here). Each row:
  `{item_id, domain, category, detail, since, next_check}` where `next_check` is
  the concrete trigger that ends the wait (e.g. `n>=30 closed`, `next IB reset`,
  `operator go`). This is the human-readable "what are we waiting on" companion to
  `review_coverage.backlog_drive.deferred` (which is the audit trail).
- `consolidated.review_coverage` — **required** (the Review-coverage guard): the
  `strategy_promotion`, `ml_training_health`, `soak_status`, `flags_raised[]`, and
  `backlog_drive` (what was drained vs deferred + why) the review produced. A run
  with any of the four required keys (`strategy_promotion`, `ml_training_health`,
  `soak_status`, `backlog_drive`) missing/empty must STOP and complete the work
  before rendering.

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
   and **`artifacts.dashboard_link`** — the Reports deep link into the **new
   dashboard SPA** (GitHub Pages):
   `https://benbaichmankass.github.io/ict-trader-dashboard/?report=<report_id>`
   (the SPA reads the `?report=` query param on load and opens that report on
   the Reports page; the canonical dashboard base URL is recorded in `CLAUDE.md`
   § "Dashboard consumer"). The legacy Streamlit deep link
   (`https://ict-trader-dashboard-z67ryan2ttrxjdvk6ozcjc.streamlit.app/?report=<report_id>`)
   uses the same `?report=` scheme and still resolves while that app runs, but
   the SPA is now the primary target. Set on the payload (re-render or patch the
   written JSON so they're recorded).
3. **One** consolidated `send-ping` (per `docs/claude/telegram-pings.md`):
   ```
   action: send-ping
   target: claude
   priority: normal            # 'high' if any sub-review set operator_attention_required
   message: [system-report:<window>] roll-up <grade>: H:<h> P:<p> M:<m>. <dashboard_link>
   ```
   The `<link>` in the ping is the **`artifacts.dashboard_link`** (the SPA
   Reports deep link on GitHub Pages), NOT the GitHub blob — so tapping the ping
   opens the report inside the app, where the operator reads it and can Download
   the HTML. The
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
