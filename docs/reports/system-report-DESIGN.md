# System Activity Report — design & format spec

**Status:** v1 (2026-06-22). On-demand only; scheduling is a documented phase-2.

## Why

The system already runs three separate review skills — `/health-review`,
`/performance-review`, `/ml-review` — each emitting structured JSON + a Telegram
ping. The operator wants those kept separate **and** a master report that runs
all three together and synthesizes a single, thorough **executive report** of
everything the system has done since the last report: technical health, every
trade with a reviewable decision dossier (split real/paper/prop), the PnL trend,
a market-context read, and the ML fleet — deliverable as an HTML link and
surfaced as a log of links in both apps.

This doc is the canonical **format**: sections, windows, adaptive depth, the
artifact layout, and the delivery contract. The output schema is
`comms/schema/system_report_response.template.json`; the renderer is
`scripts/reports/render_system_report.py`; the skill is
`.claude/skills/system-review/SKILL.md` (reframed 2026-06-23 — the **work is the
SYSTEM REVIEW**, the report is just its deliverable; `system-report` remains a
back-compat alias and the artifact name stays "report" everywhere it's
load-bearing). The review-coverage block (`consolidated.review_coverage`) the
skill's coverage guard requires is documented in the schema.

## Windows

`/system-report --window=<window>`:

| Window | Meaning | Range derivation |
|---|---|---|
| `since-last` *(default)* | The per-health-check delta (run several×/day) | `window_start` = the previous report's `reviewed_at` from `comms/reports/index.json` (any window class); first-ever run falls back to last 6h. |
| `daily` | Last 24h | `now − 24h`. |
| `weekly` | Last 7d | `now − 7d`. |
| `monthly` | Last 30d | `now − 30d`. |

`window_end` is always "now" (report time). The prior-window comparison
(trend ↑/↓) uses the immediately preceding equal-length window.

## Adaptive per-trade depth

The per-trade decision dossier is the heart of the report (review trades
one-by-one). Depth scales with window so a monthly report doesn't become
hundreds of full dossiers:

- `since-last`, `daily` → **full dossier for every trade** in the window.
- `weekly`, `monthly` → **summary table** (per-strategy aggregates in
  `pnl_by_class`) + **full dossiers only for `notable=true` outliers** (biggest
  win/loss, worst-graded, rule-distance events). `dossier_coverage` records how
  the rule resolved.

## Report sections (the format)

1. **Executive header** — window + range, "since last report" delta, the
   **roll-up grade** (worst-of the three reviews' `overall_assessment`), a
   one-paragraph headline, and the top 3–5 **operator priorities**.
2. **System & technical health** — from `/health-review` + `/api/bot/health/services`
   + `/api/bot/stats.vmHealth` + heartbeat: VMs up/down (live trader, trainer,
   IB gateway), service states, last-tick age, the health findings (heartbeat,
   ticks, plumbing, DB integrity, alert delivery, …).
3. **Trading activity & performance** — three strictly-separate sub-blocks
   **REAL / PAPER / PROP** (never blended — live-trade management contract P4),
   each: window PnL vs prior-window (trend), trades / win-rate / expectancy /
   profit-factor / max-DD, per-strategy + per-asset-class breakdown, then the
   **per-trade decision dossiers** (adaptive depth): symbol/dir, account+class,
   entry/exit/SL/TP/qty, PnL, hold, close reason, the **Claude A–F grade +
   rationale**, the **model scores** `{model:stage:score}`, **signal_logic** +
   **meta** (setup_type/killzone/bias), triggering signal. Prop sub-block adds
   rule-distance cushion (daily-loss / static-DD), tickets-emitted vs
   fills-reported, un-acted tickets.
4. **Market context** — one row per traded symbol (enumerated live, never
   hardcoded): window open/close/high/low + % change from `/api/bot/candles`,
   a one-line regime note, so performance reads against market behavior.
5. **ML / models** — from `/ml-review`: fleet by stage (advisory=influencing /
   shadow / candidate=research), per-model last-training metric + trend, shadow
   volume + drift (KS/PSI), realized track record when joinable, promotion/
   demotion recommendations, experiment proposals.
6. **Actions & backlog** — consolidated prioritized actions, operator-attention
   items, backlog-drain counts across the three backlogs, Tier-3 proposals
   awaiting approval, cross-review notes.
7. **Footer/provenance** — report_id, prior report, reviewer.

## Data sources (reuse — nothing new)

| Section | Source |
|---|---|
| Health | `/health-review` JSON + `/api/bot/health/{services,latest}` + `/api/bot/stats` |
| Real/paper perf + trend | `/api/bot/performance?window=…` (+ `paper` sub-block), `/api/pnl/history` |
| Trade dossiers | `/api/bot/trades/closed` + `/api/bot/order-packages` (join by `linkedTradeId`) + the performance review's A–F grade (`comms/claude_strategy_scores.jsonl`) |
| Prop | `/api/bot/prop/{fills,tickets,status,reconcile}` |
| Market context | `/api/bot/candles` (symbols via `/api/bot/strategies` / `/api/bot/config`) |
| ML | `/ml-review` JSON + `/api/bot/ml/*` + `/api/bot/shadow/*` |

Real / paper / prop split is **account_class-authoritative** (is_demo
fallback); prop is sourced from the isolated prop journal, never `trades`.

## Artifacts & index

```
comms/reports/<window>/<UTC-ts>/{report.json,report.html,report.md}
comms/reports/index.json   # {schema_version, reports:[{id,window,generated_at,window_start/end,roll_up_grade,headline,html_path,json_path,md_path}, ...]}  newest-first
```

Reports are **committed** (like `comms/reviews/`) so each HTML has a stable
GitHub link. Paths in the index are **relative to the repo root** so the API's
`repo_root() / rel_path` resolves them. The HTML is **self-contained**
(embedded CSS, no external assets) and **responsive** (mobile-first + one
desktop breakpoint) so the same file renders on a phone and a desktop.

## Delivery

1. The renderer writes the artifacts + updates the index.
2. The skill sends **one consolidated** `send-ping` (the three sub-reviews'
   individual pings are **suppressed** — `delivered_via:"suppressed
   (system-report)"`), carrying the report's GitHub link.
3. Both apps surface a **Reports** list (links to every report) via
   `GET /api/bot/reports`: the Streamlit dashboard (desktop) and the Android
   app (mobile, WebView).

## Out of scope (phase-2, documented not built)

- **Scheduled** daily/weekly/monthly runs — needs a cron-triggered Claude
  session (the reviews require an LLM): a GitHub Actions `schedule:` →
  launch a `/system-report --window=…` session. Tracked as a follow-up.
- **Email** delivery — no SMTP infra exists; the HTML link + in-app list cover
  v1. Email can wrap the same HTML later.
- **Bigdata.com** market-narrative enrichment of the market-context section
  (PM-session-only tool today).
