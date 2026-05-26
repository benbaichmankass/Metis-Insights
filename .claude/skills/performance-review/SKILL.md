---
name: performance-review
description: Autonomous review of the ICT trading bot's TRADING PERFORMANCE — per-strategy aggregate stats, per-order-package decision grading, comparison against actual closed-trade PnL, and proposed tweaks to consider. Reviews the M13 AI-analyst insights log (/api/bot/insights/*) and cross-checks its claims against real data. Owns comms/claude_strategy_scores.jsonl (per-decision grading, append-only) and docs/claude/performance-review-backlog.json (future trading follow-ups / strategy tweak ideas). Use when the operator says "run the performance review", "/performance-review", "score the recent trades", or "how are the strategies doing". NOT for ML/model perf (use /ml-review) and NOT for system/pipeline plumbing (use /health-review).
---

# /performance-review — trading + strategy performance review

This is the **trading-performance** session of the three-way review
split (`/health-review` covers system/pipeline health,
`/ml-review` covers the training center + model lifecycle). It grades
every decision the strategies made in the window, compares the grades
to what actually happened in the trade book, surfaces the M13 AI
analyst's view of the same window, and proposes (never enacts) tweaks
to consider.

Strategy *parameter* / *risk-cap* changes are **Tier-3** — propose
them in `proposed_tweaks[]`; merging requires explicit operator
approval. This skill is the evidence-gathering and analysis step that
precedes that approval.

If the user asked about *technical/pipeline health* — STOP, use
`/health-review`. If the user asked about *model training / promotion
/ shadow predictions* — STOP, use `/ml-review`.

## Scope (what this skill DOES)

1. **Establish the window** — since the last performance-review
   (§ "The review window").
2. **Pull trade data** for the window via the diag relays
   (§ "Fetching trade data").
3. **Read the M13 insights cache** — the AI analyst's view of the same
   window (§ "M13 insights review").
4. **Aggregate per-strategy stats** — trade count, win-rate, PnL,
   average hold time, rejection ratio, per-account contribution
   (§ "Per-strategy aggregates").
5. **Grade every order-package decision** A/B/C/D/F + the three
   training-friendly labels, anchored on `signal_logic` (§ "Per-decision
   scoring").
6. **Verify scores against real outcomes** — small wins on bad setups
   stay graded poorly; stop-outs on textbook setups stay graded fairly
   (§ "Verification").
7. **Persist scores** to `comms/claude_strategy_scores.jsonl`
   (append-only, dedupe by `order_package_id`).
8. **Propose tweaks** — config/strategies.yaml params to consider, with
   evidence (§ "Proposed tweaks").
9. **Drain the performance-review backlog** — strategy follow-ups from
   prior sessions (§ "Draining the backlog").
10. **Emit the response JSON** + **post a one-line update to the Claude
    channel** (§ "Output" + § "Posting to the Claude channel").

## Out of scope (DO NOT do here)

- Pipeline plumbing / DB integrity / service state → `/health-review`.
- Model training / registry / shadow predictions → `/ml-review`.
- Editing `config/strategies.yaml`, `config/accounts.yaml`,
  `config/risk_caps.yaml`, or `src/runtime/*` — Tier-3, operator
  approval gate.
- Backtesting — the `backtesting` skill is the path; this skill may
  *reference* sweep outputs (`/api/bot/backtests/sweeps`) to anchor a
  tweak proposal but does not run sweeps.

## The review window

Window runs from the last performance-review to now. Determine "last
review" in this order:

1. The newest `reviewed_at` across rows in
   `comms/claude_strategy_scores.jsonl` (the canonical anchor — every
   review appends here).
2. If the file has only its `_meta` line (no reviews yet), fall back
   to the last 24h.

Cap practical pulls at the diag limits. If the gap exceeds one pull,
page back with the journal `since`/`until` parameters and note
truncation in the response.

## Fetching trade data (use the diag-data skill)

This skill is a consumer of `diag-data` + `git-actions`. The required
pulls:

| Pull | Path | Use |
|---|---|---|
| Order packages (decision-level) | `GET /api/bot/order-packages?since=<iso>&limit=500` | one row per decision; includes `claudeScore` from prior reviews so dedupe is trivial |
| Closed trades | `GET /api/bot/trades/closed?since=<iso>&limit=500` | realized PnL + exit reason |
| Journal — order_packages | `journal?table=order_packages&limit=200` (diag) | redundant cross-check; carries `signal_logic` blob |
| Journal — trades | `journal?table=trades&limit=200` (diag) | exit_reason, pnl, position_size |
| Audit tail | `audit?limit=600` (diag) | `*_eval` events for context around each decision |
| M13 insights — summary | `GET /api/bot/insights/summary` | latest AI-analyst summary of the window |
| M13 insights — recent | `GET /api/bot/insights/recent?limit=N` | per-trade analyst notes |
| M13 insights — per strategy | `GET /api/bot/insights/strategy/{name}` (each enabled strategy) | strategy-level analyst note |
| M13 insights — health | `GET /api/bot/insights/health` | data-window + cache age |
| Sweep mirror | `GET /api/bot/backtests/sweeps?limit=5` | trainer-VM sweeps that may justify a tweak proposal |

All pulls go through the **diag relay** (issue label
`vm-diag-request`, title is the path) OR direct HTTPS when the
session is configured for it. Do not SSH; do not ask the operator to
paste anything. If the relay fails, fire `vm-web-api-recover` once
and retry; if still failing, emit a partial review with a note —
**never fabricate**.

## Per-strategy aggregates

For each strategy with at least one decision in the window:

- `n_decisions` (order_packages emitted)
- `n_filled` (linked to a non-null `trades` row)
- `n_rejected` (status ∈ `failed_*`)
- `win_rate` (closed_filled rows where `pnl > 0` ÷ closed_filled rows)
- `pnl_total`, `pnl_avg_per_trade`
- `avg_hold_seconds` (closed_at − opened_at)
- `rejection_cluster` — most common rejection reason if rejections >
  filled
- `accounts_touched[]` — which accounts traded this strategy

`execution: shadow` strategies are included with the same fields;
their `n_filled` should be 0 (shadow does not place live orders) — if
not, that's an anomaly. Closed-trade attribution joins by
`trades.strategy_name`.

## Per-decision scoring (PERSISTED, keyed by order package)

The score belongs to the **strategy DECISION**, so it is keyed by
`order_package_id` and persisted to
`comms/claude_strategy_scores.jsonl`, **not** the trade journal
(operator decision 2026-05-25). Cross-reference the executed `trade_id`
(and the trade's outcome) on the row when the package filled; leave it
`null` for shadow / never-filled packages (graded on setup quality
only, `exit_quality: unknown`).

Anchor each grade on the package's `signal_logic` blob
(`order_packages.signal_logic`) — judge the decision against its own
stated edge and (when filled) the fill/exit data, independent of
dollar outcome.

**Letter grade (one per decision):**
- `A` — textbook
- `B` — good, one minor deviation
- `C` — acceptable, EV marginal in hindsight
- `D` — poor (fired against HTF / thin confidence, saved by noise)
- `F` — bad (should not have fired, or should have stayed in)

Mirror to `decision_grade_score` A/B/C/D/F → 4/3/2/1/0 (matches the
`review_journal` family the trainer ingests).

**Three categorical labels (training-friendly):**
- `entry_quality` ∈ `optimal | acceptable | late | early | should_skip | unknown`
- `exit_quality` ∈ `optimal | tp_appropriate | sl_appropriate | premature_exit | held_too_long | unknown`
- `risk_management` ∈ `correct | oversize | undersize | sl_too_tight | sl_too_wide | unknown`

Use `unknown` honestly when the diag bundle lacked context — **do not
fabricate**. With many decisions: grade all closes + ≥1 representative
per rejection cluster; if >20, surface the low-grade cohort (C/D/F)
first and aggregate the A/B cohort in one entry listing the
`order_package_id`s.

**Append discipline:** the jsonl is append-only. Before appending,
skip any `order_package_id` already present so re-runs don't
double-write. Append; never rewrite prior rows. Routine runs only
append packages decided since the last review.

The retroactive backfiller for historical windows is
`scripts/ops/score_order_packages.py` — re-use it, do not reinvent.

## Verification

The whole point of this skill is that scores match reality:

- For each filled, closed decision in the window, compare the grade
  to the realized outcome:
  - `A`/`B` graded decisions should skew positive on PnL over enough
    samples. An `A`/`B` cohort with negative aggregate PnL → flag as
    `grade_drift` in `anomalies`.
  - `D`/`F` graded decisions that consistently made money → flag the
    same; the grading rubric or the strategy is mis-aligned.
- Cross-check against the M13 analyst's notes for the same trades —
  where the analyst disagrees with the grade, prefer the data over
  either, and note the disagreement in `insights_review[]`.

## M13 insights review

The M13 AI analyst writes cached summaries to
`runtime_logs/insights/*.json` (regenerated every 10 min by
`ict-insights-generator.timer`). Read all four endpoints (or `cache_present:
false` if the generator hasn't run yet) and:

- Verify the analyst's claims against the same trade data you just
  pulled — does it cite a real `trade_id` / `order_package_id`? do
  its win-rate numbers match the aggregates? does it flag the same
  outliers?
- Record disagreements in `insights_review[]` with severity
  `nit | drift | contradiction`.
- If the cache is stale (>1h `cache_age_seconds` during active
  hours), note it as `insights_staleness: watch`. The generator is
  the responsible owner — not this skill — but staleness affects the
  dashboard users see.

## Proposed tweaks

This is the highest-value output of the skill. For each finding that
suggests a concrete parameter change, emit a `proposed_tweaks[]`
entry:

```
{
  "scope": "config/strategies.yaml::<strategy>.<param>",
  "current_value": ...,
  "proposed_value": ...,
  "evidence": "<= 240 chars — what in the window evidence supports this; cite trade_ids / order_package_ids / sweep summaries",
  "tier": 3,
  "risk_note": "<= 160 chars — what could go wrong, what to watch after applying"
}
```

These are **proposals**, not commits. The operator decides. If the
proposal is too uncertain to recommend, file it as a backlog item
instead (§ "Draining the backlog").

## Draining the backlog

Read `docs/claude/performance-review-backlog.json` — the parking lot
for **strategy follow-ups, tweak ideas to revisit, performance
puzzles** that prior sessions noticed but didn't have enough evidence
to act on. (Health/ML backlogs are not touched here.) For each open
item:

1. Triage: is it still valid? does the new window's data resolve it?
2. **Act on what you can** — if the new window's data closes the
   question, propose the tweak (or close as `invalid`); otherwise
   leave it open.
3. Edit the backlog file: mark resolved items resolved, keep
   deferred items, drop invalid ones. Record each action in
   `backlog_drain[]`.

New backlog items added by this skill are for **performance
follow-ups only** (not system bugs — those go to the health backlog).
Each item carries `id`, `opened_at`, `opened_by`, `source`, `title`,
`description`, `tier` (typically 3), `trigger_condition`,
`resolution_criteria`, `status` ∈ `open | resolved | invalid |
snoozed`.

## Posting to the Claude channel

Every performance-review run ends with a one-line update to
`@claude_ict_comms_bot`. Primary path:

```
action: send-ping
target: claude
priority: normal      # 'high' only if a strategy is consistently F-grading on live capital
message: /performance-review — <window>: <total decisions>, <win_rate>% WR, $<pnl>. <N> low-grade (D/F). <K> tweaks proposed. Top mover: <strategy>.
```

≤200 chars. Cite numbers. Point at the response JSON for detail.
Fallback path: append to `docs/claude/pending-pings.jsonl` and commit.
Full contract: `docs/claude/telegram-pings.md`.

## Output

Emit a single JSON object conforming to
`comms/schema/performance_review_response.template.json`:

- `reviewed_at`, `reviewer: "claude"`, `window_start`, `window_end`.
- `overall_assessment` ∈ `healthy | caution | investigate`.
- `strategy_performance[]` — per-strategy aggregates (§ above).
- `trade_decision_grades[]` — one entry per scored order package
  (**REQUIRED** when the window held decisions; `[]` only when truly
  empty).
- `insights_review[]` — agreements/disagreements with the M13 cache.
- `proposed_tweaks[]` — Tier-3 proposals with evidence.
- `backlog_drain[]` — actions taken on
  `docs/claude/performance-review-backlog.json`.
- `anomalies[]` — free-form notable items (grade drift, unexpected
  attribution, etc.).
- `recommended_action`, `operator_attention_required`.

Each `note`/`rationale` ≤240 chars; cite `order_package_id`,
`trade_id`, strategy name, and numbers so the operator can verify
fast.

## What you DO write (and what you don't)

**Write:**
- Append per-decision scores to `comms/claude_strategy_scores.jsonl`
  (keyed by `order_package_id`, dedup-on-append).
- Edit `docs/claude/performance-review-backlog.json` to drain + add
  new items.
- Post the Claude-channel ping (via `send-ping` system-action; fall
  back to `docs/claude/pending-pings.jsonl`).
- The read-only diag-trigger issues (`vm-diag-request`,
  `vm-web-api-recover`) — they auto-close.

**Do NOT:**
- Touch `src/`, `config/`, or any live-path file. **No exceptions** —
  param changes go in `proposed_tweaks[]` for operator approval.
- Modify `docs/claude/health-review-backlog.json` or
  `docs/claude/ml-review-backlog.json`.
- Touch the M13 insights cache (`runtime_logs/insights/*.json`) —
  that's the generator's territory; this skill only reads it.
- Ask the operator to paste/download/SSH a snapshot — autonomy
  violation. Pull it yourself.
- Ask scoping questions — the scope is fixed (this file).

## If the relays are unreachable

Same rule as `/health-review`: if the live diag relay fails even
after a `vm-web-api-recover` retry, emit a partial review with
`api_errors` in `anomalies[]`, `operator_attention_required: true`,
and a note that the live pull couldn't be performed. Still drain the
backlog and still post the Claude-channel ping (those are repo-local).
Do not synthesize trade data without evidence.
