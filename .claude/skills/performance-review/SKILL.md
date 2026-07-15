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
10. **Update the diversified paper-book tracker** — append a dated
    snapshot of the 10-cell paper cohort + review what's new
    (§ "Diversified paper-book tracker").
11. **Run the real-money allocation benchmark (RECURRING)** — does a
    ready-for-live strategy portfolio actually improve the live
    real-money book's risk-adjusted PnL (incl. via regime
    diversification), or is the capital better left where it is? This is
    the standing bar for (re-)adding a strategy to a live real-money
    account (§ "Real-money allocation benchmark").
12. **Emit the response JSON** + **post a one-line update to the Claude
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
| Order packages (decision-level) | `GET /api/bot/order-packages?since=<iso>&limit=500&include_paper=true` | one row per decision; **`include_paper=true` so PAPER + PROP packages are graded too, not just real-money** (operator directive 2026-06-22); includes `claudeScore` from prior reviews so dedupe is trivial |
| Closed trades | `GET /api/bot/trades/closed?since=<iso>&limit=500&include_paper=true` | realized PnL + exit reason (all funding classes) |
| Paper-book trades | `GET /api/bot/trades/closed?account_id=bybit_1&limit=500` | the diversified paper cohort's closed trades, for the tracker (§ "Diversified paper-book tracker"). `account_id=` returns that account incl. paper; pull the **full** book (no `since`) so the tracker's recency split + cumulative trajectory are correct |
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

**Batch the diag-labeled rows above into ONE `vm-diag-request` issue.**
Per the `diag-data` skill's default pattern (MB-20260706-CI-MINUTES —
every relay issue is its own billed Actions job; this repo hit its 2,000
min/month cap opening 427 issues in 5.5 days, mostly single-path relay
calls), put every diag path you need this run in the issue **body** as a
JSON array or one-per-line list (e.g. `journal?table=order_packages&limit=200`,
`journal?table=trades&limit=200`, `audit?limit=600` in one issue) instead
of three separate issues. The non-diag `GET /api/bot/*` rows above are
direct HTTPS/no-relay reads when the session is configured for it, so
they don't add to the issue count either way.

## Bucket records before aggregating (artifact pre-filter)

Paper (and some real) records are dominated by **technical artifacts** — intent
reduce/flip legs, netting-guard / hold-policy suppressions, reconciler closes with
no classifiable bracket, orphan flaps, and credential/funding refusals — NOT clean
strategy round-trips. Blending those into win-rate / expectancy makes every
aggregate wrong (a 2026-06-26 window was 0/48 gradeable; see
`docs/audits/order-packages-zero-qty-2026-06-26.md`). Run the pre-filter FIRST:

```
python scripts/analysis/classify_paper_records.py --limit 500 --format md   # on the VM
# or, from a diag-relay trades dump in a sandbox:
python scripts/analysis/classify_paper_records.py --json trades.json --reconstruct
```

It buckets each record (`src/analysis/paper_record_classifier.py`):

- **A — gradeable** (clean SL/TP / monitor exit): the **only** rows that drive
  per-strategy win-rate / expectancy below.
- **B — technical artifact**: exclude from the scorecard; surface the
  `by_category` counts as a *technical-health* note (route real bugs to the
  health-review backlog), not a strategy verdict.
- **C — reconstructable** (broker-truncated / open-at-edge, but entry+SL+TP
  present): reconstruct the would-be SL/TP outcome from candles
  (`src/analysis/trade_reconstruction.py`, first-touch; `--reconstruct`) →
  `reconstructed_win` / `reconstructed_loss` / `open_at_window_end`. Keep the
  *decision* grade (entry quality, R:R) and fold the reconstructed outcome into
  the strategy read, flagged as reconstructed (intrabar-ambiguous ties resolve
  pessimistically to SL by default).

Report each strategy's A/B/C split so a low win-rate that is really an
artifact-heavy record set is visible, not mistaken for a bad strategy.

## Per-strategy aggregates

Compute the aggregates below over **bucket-A rows only** (plus bucket-C
reconstructed outcomes, flagged). For each strategy with at least one decision in
the window:

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

**Web / PM session (no DB file):** dispatch the **`grade-closed-trades`**
system-action (Tier-1, `docs/claude/system-actions.md`) instead of pulling the
whole `trades` table through the diag relay. It runs
`score_order_packages.py --emit-delta-only` on the VM (where the DB lives) and
returns only the ungraded delta as NDJSON — bounded and small, unlike a full
`~650KB` table dump against the relay's `~55KB` comment budget. Append the
returned rows to `comms/claude_strategy_scores.jsonl` and commit; a truncated
delta always carries an explicit trailing `{"_delta_summary": ...,
"truncated": true}` marker, never a silent drop. `scripts/ops/
grade_closed_trades_from_diag.py` (feed it a `/api/diag/journal?table=trades`
pull) remains as a documented fallback for when the system-action path itself
is unavailable.

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

### Draining the strategy-refinement queue

Also drain **`docs/claude/strategy-refinement-queue.json`** — the active
pipeline for **`paper_ready`** cells (`docs/strategy-readiness-ladder.md`):
backtested edges that are net-of-fee positive but **not yet fold-robust**, so
they run on demo for soak AND are queued for *refinement* rather than shelved.
For each open item:

1. Triage: has its refinement hypothesis been tested yet? did a fresh
   backtest / k-fold land?
2. **Act on what you can** — if a re-sweep exists, re-tier with
   `scripts/ops/classify_strategy_tier.py` and either advance the cell
   (a `paper_ready → live_money_ready` Tier-3 proposal in `proposed_tweaks[]`,
   with the gate evidence), iterate the hypothesis, or mark it `rejected`
   with the evidence. If the refinement still needs a backtest, that's the
   `backtesting` skill — note it and leave the item `open`.
3. Edit the queue file: update `tier` / `status` / append evidence. Record
   each action in `backlog_drain[]` (tag the item id `SRQ-…`).

This is the onboarding half of the lifecycle; the M7 review gate
(`/api/bot/strategies/{name}/review`) handles already-live strategies. A
`paper_ready` cell is a **work item**, not a dead backtest — the whole point
is to refine the marginal-but-real edges instead of only ever shipping the
absolute winners.

## Diversified paper-book tracker

The 10-cell diversified alt book
(`config/research/diversified_paper_book.yaml`) was banked 2026-06-18 as
robustly +OOS in backtest, with one blemish: 2026-YTD was flat in
backtest. All 10 cells run **enabled + `execution: live` on the
`bybit_1` PAPER account**, so the live paper trade book is the standing
evidence for whether that flatness is **alpha-decay or noise**. This
step keeps that watch alive across reviews.

Every run:

1. **Fetch the cohort's closed trades** — the `account_id=bybit_1` pull
   above (full book, no `since`).
2. **Append a snapshot + read the delta** — pipe the rows to the
   tracker:
   ```
   python3 scripts/ops/paper_book_tracker.py --trades-json <rows.json>
   ```
   It filters to the 10 cohort cells, computes book + per-cell +
   per-family + recency-split aggregates, appends one snapshot line to
   `docs/research/paper-book-tracker.jsonl`, and prints the Δ vs the
   previous snapshot + a `decay_flag`. (Pass `--no-append` only for a
   dry read.)
3. **Review what's new** — judge the live paper trajectory:
   - Is the **book net-positive** and is the **recent window** holding
     up vs prior (decay watch)? A `decay_flag` (recent window
     net-negative + mean below floor over ≥10 trades) is a **caution**
     signal, not proof — note it and keep watching.
   - **Per-cell / per-family**: any cell that's a sustained net loser on
     live paper (not just a flat week) is a candidate for the
     `performance-review-backlog` — a future tweak or a
     `DEMOTE_SHADOW`/`KILL` proposal (Tier-3, *proposed* not enacted).
   - Cross-check against the backtest expectation (the families were
     complementary: trend wants low-moderate ADX, pullback wants high
     ADX). A family inverting its expected sign on live paper is a real
     finding.
4. **Record it** in the response under `paper_book_tracker` (§ Output)
   and, if a cell/family warrants follow-up, add a `SRQ-…` item to the
   backlog.

The tracker file is append-only history — **never rewrite prior
snapshot lines** (same discipline as the scores jsonl). Updating the
cohort itself (adding/removing a cell) is a Tier-3 config change to
`accounts.yaml`/`strategies.yaml` — propose it, don't edit the yaml
here; the cohort yaml is kept in sync *after* that lands.

## Real-money allocation benchmark (RECURRING)

**Operator directive (2026-07-15, set when `alpaca_live` was shelved to
`dry_run`):** the bar for putting a strategy on a **live real-money**
account is not "is it profitable in paper" — it's **"does adding it
actually improve the live real-money book's *risk-adjusted* outcome,
including via diversification across changing regimes, or is the capital
better left where it is (today: bybit-only)?"** A strategy that's
paper-green but adds nothing over the incumbent book — or worse, just
correlates with it — does not earn real-money capital. Run this every
review while any real-money account is shelved or any candidate is
queued for live promotion.

**The benchmark:**

1. **Incumbent book** = the current live real-money allocation (today
   `bybit_2`; `alpaca_live` is `dry_run` as of 2026-07-15). Pull its
   closed trades (`account_id=bybit_2`, real-money) over the window +
   the longer trailing window, risk-normalized to **R** so it compares
   across instruments.
2. **Candidate portfolio** = the ready-for-live strategies not currently
   on live real money — principally the shelved `alpaca_live` ETF sleeve.
   Its **paper-equivalent** record is the proxy: `alpaca_live` in
   `dry_run` still logs order-packages but does **not** fill, so use
   `alpaca_paper` fills for the same strategies as the realistic
   execution proxy (state this explicitly — it's a paper proxy, not live
   fills). Health-review's soak-surfacing (§ its "Soak decisions due")
   is the readiness signal for *which* candidates are eligible.
3. **Compare on risk-adjusted terms, not raw $** — expectancy-R, profit
   factor, and **max drawdown**, since paper $ notionals are inflated
   and not comparable to real-money $.
4. **Weigh the diversification benefit explicitly** — the operator's key
   question. Estimate the **correlation / co-drawdown** between the
   candidate sleeve's equity curve and the incumbent's across the
   window's regime shifts. A *decorrelated* sleeve can earn its place
   even with lower standalone expectancy if it **reduces blended
   drawdown / smooths equity** when the incumbent's regime turns
   unfavorable. Conversely a high-correlation sleeve that just tracks
   bybit adds risk without diversification — it does not clear the bar.
5. **Verdict (proposed, never enacted here):**
   - **Improves the blended book** (higher expectancy-R at equal-or-lower
     drawdown, OR materially lower drawdown via decorrelation) → surface
     a **Tier-3 re-arm / promotion proposal** with the evidence (the
     `mode: live` flip is operator-gated, shipped as a PR like the
     shelve — never flipped here).
   - **Doesn't clear the bar** → keep it shelved; capital stays where it
     is. Say so plainly with the numbers.

Record the verdict each run under `real_money_allocation_benchmark`
(§ Output), and add/refresh a `SRQ-…` backlog item so the decision is
tracked between reviews. This is **analysis + a proposed Tier-3 change**
only — it never edits `accounts.yaml`/`strategies.yaml`.

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
- `paper_book_tracker` — the diversified paper-book read: this run's
  snapshot summary (book `n`/`net_usd`/`win_rate`, recent-vs-prior,
  per-family net, `decay_flag`), the Δ vs the previous snapshot, and a
  one-line `assessment` (decay / noise / healthy). `null` only if the
  cohort pull failed (note it in `anomalies[]`).
- `real_money_allocation_benchmark` — the recurring benchmark verdict
  (§ "Real-money allocation benchmark"): `{incumbent, candidate,
  incumbent_expectancy_r, candidate_expectancy_r, incumbent_max_dd,
  candidate_max_dd, correlation_estimate, diversifies (bool),
  verdict ∈ improves|neutral|does_not_clear, proposed_action, note}`.
  `note` states the paper-proxy caveat. `null` (with an `anomalies[]`
  line) only when there's no live real-money book AND no queued
  candidate to benchmark.
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
- Append a snapshot line to `docs/research/paper-book-tracker.jsonl`
  (append-only, via `paper_book_tracker.py`; never rewrite prior lines).
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
