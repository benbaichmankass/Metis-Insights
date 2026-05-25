---
name: health-review
description: Autonomous layer-2 review of the LIVE ICT TRADING BOT's runtime health (NOT a code review or codebase audit). Claude pulls the live runtime state itself via the GitHub Actions diag relays (autonomous read access — no operator paste), grades the full pipeline + trainer center since the last review, scores every strategy decision (order package) in that window (persisting each score by order_package_id to comms/claude_strategy_scores.jsonl), reports a per-model status update, reviews recent sprint logs for doc correctness, validates DB integrity + data validity, and drains docs/claude/health-review-backlog.json. Emits a JSON response matching comms/schema/health_review_response.template.json. Use when the operator says "run the health review", "/health-review", or "do the layer-2 review". Do NOT invoke for code-quality audits or security reviews — those are the `review` / `security-review` skills.
---

# /health-review — autonomous layer-2 review of the live ICT bot

This skill reviews the **live trading system's runtime state**, not the
codebase. It is fully autonomous: Claude fetches the runtime state itself
via the diag relays, grades it, **persists per-decision scores (keyed by
order package)**, **drains the backlog**, and emits the response JSON. The operator pastes nothing,
downloads nothing, SSHes nowhere.

If the user asked for a *code* review, *codebase audit*, *security
review*, or *dependency check* — STOP, wrong skill. Point them at
`review` or `security-review`.

## What this review does (the autonomous pipeline)

1. **Establish the window** — review everything *since the last review*,
   not a fixed 6h slice (§ "The review window").
2. **Pull live runtime state** across that window via the diag relays
   (§ "Fetching runtime state").
3. **Grade full pipeline health** — signal→order→trade plumbing + the
   layer-2 dimensions (§ "Pipeline rubric").
4. **Grade trainer-center health** (§ "Trainer VM review").
5. **Score every strategy decision** (order package) in the window and
   **persist each score** by `order_package_id` to
   `comms/claude_strategy_scores.jsonl` (§ "Per-decision scoring").
6. **Review recent sprint logs** for doc correctness/clarity
   (§ "Sprint-doc review").
7. **Validate DB integrity + data validity** (§ "DB integrity & validity").
8. **Drain the health-review backlog** — triage every open item, fix what
   you can, record what you did (§ "Draining the backlog").
9. **Emit the response JSON** per the template (§ "Output").

## The review window — "since the last review"

The window runs from the last review to now. Determine "last review" in
this order:

1. The newest `reviewed_at` across the score rows in
   `comms/claude_strategy_scores.jsonl` (the durable per-decision log this
   skill writes). This is the canonical anchor. (NB: the 2026-05-25
   retroactive backfill rows all share one `reviewed_at`; the next live
   review's anchor is that backfill timestamp — page back if needed.)
2. If that file has only its `_meta` line (no reviews yet), fall back to
   the last 24h.

Cap the practical pull at the diag limits (audit `limit=600` ≈ 6h of
events at full cadence). If the gap since the last review exceeds what one
pull covers, page back with `since`/`until` on `journalctl` and note in
the response that older events were summarized, not enumerated. **Cover
the whole gap** — the intent is "every auto health-snapshot interval since
the last review," reconstructed from diag data.

> The cron `health-snapshot.yml` uploads per-run artifacts, but the
> sandbox has no MCP tool to download Action artifacts and the live
> `/api/diag/*` surface doesn't expose `artifacts/health/*.json` (fixed
> curl, no arbitrary bash). So the snapshots themselves aren't directly
> fetchable — you reconstruct the same runtime view from the diag relays
> over the full since-last-review window. That reconstruction IS the
> review's substance.

## Fetching runtime state (two transports, same JSON)

**Try direct HTTP first, fall back to the issue relay.**

**Transport A — direct (when configured).** If the session has
`DIAG_BASE_URL` + `DIAG_READ_TOKEN` and egress is allowed:
`scripts/ops/diag_fetch.sh '<path>'`. Exit `0` → JSON on stdout; exit `3`
→ fall back to B. Covers the **live VM only**.

**Transport B — GitHub-issue relay (always available).** Open an issue
labelled `vm-diag-request`, title `[diag-request] <path>`; the
`vm-diag-snapshot.yml` workflow SSHes, runs the fixed curl, comments the
JSON back, closes the issue. Poll `mcp__github__issue_read` (get_comments)
for the `github-actions[bot]` reply (~30–60s). Full contract:
`docs/claude/diag-relay.md`.

Required pulls (the same `<path>` works for both transports):

| Pull | Path | Use |
|---|---|---|
| Audit tail | `audit?limit=600` | ticks / `*_eval` signals / monitor events; filter to the window |
| Order packages | `journal?table=order_packages&limit=100` | signal→order plumbing |
| Trades | `journal?table=trades&limit=100` | order→trade plumbing; per-trade scoring; net positions; attribution |
| Status | `status` | heartbeat + status.json + `vm_health` (cpu/mem/disk) |
| Advisory log | `log_file?name=advisory_decisions&lines=200` | advisory-stage model scores (M11 S10); `present:false` ⇒ grade `advisory_scores` skip |
| Older windows | `journalctl?unit=ict-trader-live.service&since=<iso>&until=<iso>` | page back across a long gap |

If the relay returns curl exit 7 (`Failed to connect to 127.0.0.1`), the
web-api is down — fire `vm-web-api-recover` and retry once. If it still
fails, downgrade gracefully: emit the review with `api_errors` = concern,
`operator_attention_required: true`, and a note that the live pull
couldn't be performed. **Never fabricate findings** — a relay outage means
no data, not a green light.

Two live-VM dimensions are not directly fetchable on either transport:
**DB `integrity_check`** (no `sqlite3 PRAGMA` over diag) and **STORAGE**
(`verify_storage_setup.sh`). Grade those from what IS reachable (§ "DB
integrity & validity") and say so in the note.

## Pipeline rubric (full pipeline health)

Beyond freshness counts, judge **decision quality** across the window:

- **Signal → order plumbing.** Every actionable signal in the audit tail
  should produce an `order_packages` row within seconds. Gaps → `orders`
  concern.
- **Order → trade plumbing.** Every filled order should have a `trades`
  row. Orphans (filled order, no trade; trade, no parent order) → `trades`
  concern.
- **Side / size sanity.** Spot-check 3–5 orders: side matches signal
  direction; qty within the per-account `pos_size` cap in
  `config/accounts.yaml`; no absurd leverage.
- **SL/TP wiring.** Each order should carry SL+TP metadata. Missing →
  `watch`; systematic absence → `concern`.
- **Repeated rejections.** Consecutive `failed_exchange` /
  `failed_risk_gate` / `borrow_unavailable` on one symbol → `orders`
  concern (something upstream wedged).
- **Monitoring cadence.** `run_monitor_tick` events on the documented
  cadence; long gaps → `monitoring` concern.
- **Strategy silence.** Every strategy enabled in
  `config/strategies.yaml` should emit per-tick `*_eval` events. An
  enabled strategy with **zero `*_eval`** for > 1h of an active session →
  `strategy_silence` concern (name + duration). **`execution: shadow`
  strategies still run and still emit `*_eval`** — they just produce no
  trade rows, so the silence check applies to them but the trade-row
  checks do not.
- **State consistency.** For each account, YAML `mode` vs runtime `live`
  field in `runtime_status.json`. Drift → `state_consistency` concern.
- **Alert delivery.** Confirm the `AlertsQueue` is drained — known-trip
  events with no accompanying drain log → `alert_delivery` concern
  ("alerts queued, drainer silent — operator unnotified").
- **Net positions / attribution (M11).** Derive from the trades pull:
  net qty per symbol (signed sum of open `position_size`), and closed-trade
  win-rate/PnL grouped by `strategy_name`. Grade only `execution: live`
  strategies against trade-row expectations; a `shadow` strategy with no
  trades is correct, never `concern`.
- **Advisory scores / allocator path.** From the advisory log +
  `runtime_status.json` `CENTRALIZED_ALLOCATOR` flag — `skip` is the
  expected default for both until activated.

Status grades: `ok` (nothing to flag) / `watch` (bounded anomaly) /
`concern` (operator should look ⇒ `operator_attention_required: true`).
Overall: `healthy` (all ok) / `caution` (≥1 watch, no concern) /
`investigate` (any concern).

## Trainer VM review

The trainer (`158.178.209.121`) runs the ML lifecycle independent of the
live trader and is reviewed every run via the `trainer-vm-diag.yml` relay
(label `trainer-vm-diag-request`, arbitrary `cmd:` bash). One block
reproduces the whole trainer view:

```
cmd: |
  REPO=/home/ubuntu/ict-trading-bot
  echo "=== TRAINER SERVICE ==="
  systemctl is-enabled ict-trainer.service; systemctl is-active ict-trainer.service
  systemctl is-enabled ict-trainer.timer;   systemctl is-active ict-trainer.timer
  systemctl show ict-trainer.service --property=ExecMainStatus,ActiveEnterTimestamp,ActiveExitTimestamp
  echo "=== TRAINER RECENT LOG ==="
  journalctl -u ict-trainer.service -n 100 --no-pager
  echo "=== TRAINER CYCLE LOG ==="
  tail -n 30 "$REPO/runtime_logs/training_cycle.jsonl"
  echo "=== TRAINER DATASETS ==="
  ls -la "$REPO/datasets-out/" 2>/dev/null; tail -n 10 "$REPO/runtime_logs/trainer/dataset_builds.jsonl"
  echo "=== TRAINER REGISTRY ==="
  cd "$REPO" && .venv/bin/python -m ml list-models
  echo "=== TRAINER RESOURCES ==="
  df -h /home | tail -1; free -m | head -2
```

> Use `python -m ml list-models` — there is **no** `python -m ml.registry
> list` (the registry package has no `__main__`). Earlier versions of this
> skill had that wrong.

Grade three dimensions (each `ok`/`watch`/`concern`/`skip`; `skip` if the
trainer relay errors):

- **`trainer_service`** — `ok` if timer enabled+active and the last cycle
  (`cycle_end` in `training_cycle.jsonl`) is within the cadence window
  (≤24h for a daily timer) with `overall_rc=0`; `concern` on non-zero
  `ExecMainStatus`, persistent `FAILED`/`error` lines, or last run >72h.
- **`trainer_datasets`** — `ok` if `datasets-out/` has the expected
  families built within 72h; `concern` if no datasets dir or all builds
  error.
- **`trainer_registry`** — `ok` if ≥1 model at `shadow`+; `concern` if
  registry empty/error or all models stuck at `research_only` (training
  runs, nothing passes eval).

The trainer is **not** a live-trading blocker — escalate trainer issues
with lower urgency; don't set `operator_attention_required` for
trainer-only issues unless a `live_approved` model is involved.

### Model status report (per-model — REQUIRED every run)

Beyond the three roll-up grades above, **every review emits a per-model
status line for every model** in `python -m ml list-models`, collected in
`model_status[]` of the response. The point is a standing answer to "how
is each model doing?" — its latest training result and, when it exists,
its real shadow/live track record. For each model report:

- `model_id`, `stage` (`research_only|candidate|backtest_approved|shadow|
  advisory|limited_live|live_approved`), and registry `status`.
- **Last training-session result** — the eval metrics from the model's
  most recent run (`runs[-1].metrics`, or the top-level `metrics` block in
  `list-models`): the family's headline metric (classification →
  `macro_f1` + `accuracy`; regression → `mae`/`mse`; winrate → the rate),
  `n_eval`, and the run's timestamp + `code_revision`. **Flag run-over-run
  regression** (e.g. `macro_f1` fell vs the prior run in `runs[]`).
- **Live/shadow performance from trade data, when available** — if the
  model is at `shadow`+ summarise its prediction track record from
  `/api/diag/log_file?name=shadow_predictions` (or `/api/bot/shadow/stats`),
  and, for predictions joinable to closed trades, the realised
  win-rate/PnL of the trades it scored (via `/api/bot/trades/scores`).
  When there are **no predictions yet** (the common case while models sit
  at `shadow` candidate), say so plainly — `predictions: 0` is an honest
  status, never a gap to paper over. Distinguish **shadow** (observing,
  no order influence) from **advisory+/live** (influencing orders) in the
  note; a degrading model that *influences orders* is the urgent case.

Roll this up into a `trainer_models` finding (`ok`/`watch`/`concern`/
`skip`): `ok` when every model retrained in the last cycle with sane
metrics; `watch` when a model's headline metric degraded run-over-run, or
a `shadow` model still has zero predictions long after it was promoted to
shadow; `concern` (⇒ `operator_attention_required`) when an
`advisory`+/`live_approved` model — one that actually influences orders —
is degrading on live/shadow data. A registry of `candidate`/`shadow`
models with healthy metrics and zero predictions is `ok` (expected
pre-activation), not a concern.

## Per-decision scoring (training feedstock — PERSISTED, keyed by order package)

The score belongs to the **strategy DECISION**, so it is keyed by
`order_package_id` and persisted to `comms/claude_strategy_scores.jsonl`,
**not** the trade journal (operator decision 2026-05-25: the order package
is the artifact a strategy emits when it decides to act — the right anchor
for "how good was this decision", independent of whether/how it filled).
Cross-reference the executed `trade_id` (and the trade's outcome) on the
row when the package filled; leave it `null` for shadow / never-filled
packages (graded on setup quality only, `exit_quality: unknown`).

For **every order package decided in the window** (executed *or* shadow),
emit a grade in `trade_decision_grades[]` AND append the same grade —
keyed by `order_package_id` — to `comms/claude_strategy_scores.jsonl`
(one JSON object per line; rubric +
retroactive backfiller: `scripts/ops/score_order_packages.py`).
**Persisting is not optional** — the chat JSON is ephemeral; the jsonl is
the feedstock the next training cycle reads. (The older
`comms/claude_trade_scores.jsonl`, keyed by `trade_id`, is superseded by
this order-package-keyed log; it carried no real rows.)

Anchor each grade on the package's `signal_logic` blob
(`order_packages.signal_logic`) — judge the decision against its own
stated edge and (when filled) the fill/exit data, independent of dollar
outcome (a small win on a bad setup still grades poorly; a stop-out on a
textbook setup still grades fairly).

**Letter grade (one per trade):** `A` textbook · `B` good, one minor
deviation · `C` acceptable, EV marginal in hindsight · `D` poor (fired
against HTF / thin confidence, saved by noise) · `F` bad (should not have
fired, or should have stayed in). Mirror to `decision_grade_score`
A/B/C/D/F → 4/3/2/1/0 in the jsonl (matches the `review_journal` family).

**Three categorical labels (training-friendly):**
`entry_quality` ∈ optimal|acceptable|late|early|should_skip|unknown ·
`exit_quality` ∈ optimal|tp_appropriate|sl_appropriate|premature_exit|held_too_long|unknown ·
`risk_management` ∈ correct|oversize|undersize|sl_too_tight|sl_too_wide|unknown.

Use `unknown` honestly when the diag bundle lacked context — **do not
fabricate** a grade the data doesn't support. With many trades: grade all
closes + ≥1 representative per rejection cluster; if >20, surface the
low-grade cohort (C/D/F) first and aggregate the A/B cohort in one entry
listing the trade ids.

**Append discipline:** the jsonl is append-only. Before appending, skip
any `order_package_id` already present so re-runs don't double-write.
Append; never rewrite prior rows. (The full historical backfill was done
2026-05-25 via `scripts/ops/score_order_packages.py` over all
`order_packages`; routine runs only append packages decided since the last
review.)

## Sprint-doc review

Read the sprint logs under `docs/sprint-logs/` created since the last
review (newest few). For each, sanity-check: does it follow the canonical
template (`sprint-format` skill), does it report verified reality rather
than intent, and does any claim contradict a canonical doc or the live
state you just pulled? Record issues in `sprint_doc_review[]` with
severity `nit`/`drift`/`contradiction`. A `contradiction` against a
canonical doc is fixed in-place (Tier-1) or logged to the backlog — never
walked past.

## DB integrity & validity

- **`db_integrity`** — the diag relay can't run `PRAGMA integrity_check`,
  so grade from journal recency + counts: `age_seconds` of the newest
  `trades`/`order_packages` row should be small during active sessions
  (hours-stale while signals fire → `concern`); table totals
  non-decreasing run-over-run (a drop → truncation/restore `concern`);
  large `-wal` with a small main DB → `watch`. Note "integrity_check not
  fetched (relay can't run PRAGMA)".
- **`data_validity`** (new) — values are *sane*, not just present: no
  negative `position_size`/`pnl` where impossible, no null in required
  columns, timestamps monotonic (`opened_at ≤ closed_at`), closed trades
  carry an `exit_reason` + `pnl`, and net positions reconcile with open
  rows. Bad values → `watch`; systemic corruption signals → `concern`
  with `operator_attention_required: true`. Use the `db-wiring` skill's
  checks as the reference.

## Draining the backlog

Read `docs/claude/health-review-backlog.json` (the parking lot for minor
issues a session noticed but didn't fix). For each open item:

1. Triage: is it still valid? does its trigger apply now?
2. **Fix what you can** within this skill's allowed writes (docs,
   the backlog file itself). Anything needing a code/config change is
   *not* fixed here — restate it for the operator in `recommended_action`.
3. Edit the backlog file: mark fixed items resolved (or remove them),
   keep deferred items, drop invalid ones. Record each action in the
   response's `backlog_drain[]`.

This is the mechanism that keeps the backlog from rotting — every review
empties what it can.

## Follow-ups (legacy — still read for now)

Also read `comms/follow_ups.json` and evaluate each `open` entry's
`trigger_condition` against this window's data, folding results into
`anomalies` (prefixed with the `id`). **Do not write to follow_ups.json.**

> **Deferred:** retiring `comms/follow_ups.json` (migrating its open
> entries into the backlog) is intentionally **out of scope** until the
> dedicated comms/telegram cleanup session (operator decision 2026-05-24).
> Until then this skill reads both the backlog (drains it) and follow_ups
> (read-only).

## Output

Emit a single JSON object conforming to
`comms/schema/health_review_response.template.json`: `findings` (all
dimensions incl. `data_validity` and `trainer_models`), `anomalies`,
`backlog_drain`, `sprint_doc_review`, `trade_decision_grades`,
`model_status`, `recommended_action`, `operator_attention_required`. Set
`reviewed_at` to now (UTC ISO-8601), `reviewer` to `claude`.
`trade_decision_grades` is REQUIRED — `[]` only when the window genuinely
held zero closed/rejected trades. `model_status` is REQUIRED — one entry
per model in the registry (§ "Model status report"); `[]` only when the
trainer relay errored (then `trainer_models: skip`). Each `note` ≤120
chars, citing specifics (counts, ages, symbols/qtys, metrics) so the
operator can verify fast.

## What you DO write (and what you don't)

**Write (this is the autonomous spec):**
- Append per-decision scores (keyed by `order_package_id`) to
  `comms/claude_strategy_scores.jsonl`.
- Edit `docs/claude/health-review-backlog.json` to drain it.
- Fix Tier-1 doc contradictions surfaced by the sprint-doc / backlog pass.

**Do NOT:**
- Touch `src/`, `config/`, or any live-path file. Reviews don't trade.
- Modify `comms/follow_ups.json` (deferred).
- Open PRs / issues to *deliver* the review. (The read-only diag trigger
  issues — `vm-diag-request`, `trainer-vm-diag-request`,
  `vm-web-api-recover` — are the exception: they're how you fetch data and
  auto-close.)
- Ask the operator to paste/download/SSH a snapshot — that's a critical
  autonomy-mandate failure. Pull it yourself.
- Ask scoping questions — the scope is fixed (this file).

## If the relays are unreachable

The only legitimate stop condition. If the live diag relay fails even
after a `vm-web-api-recover` retry, emit the partial review with
`api_errors` = concern, `operator_attention_required: true`, and a note
that the live pull couldn't be performed — and still drain the backlog +
do the sprint-doc review (those are repo-local and don't need the VM). Do
not synthesize live findings without evidence.
