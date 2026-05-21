---
name: health-review
description: Layer-2 review of the LIVE ICT TRADING BOT's runtime health (NOT a code review or codebase audit). Claude pulls the live runtime state itself via the GitHub Actions diag relays (it has autonomous read access — no operator paste needed); this skill grades it and emits a JSON response matching comms/schema/health_review_response.template.json. Use when the operator says "run the health review", "/health-review", or "do the layer-2 review". Do NOT invoke this skill for code-quality audits, security reviews, or repo-scope assessments — those are separate skills (review, security-review).
---

# /health-review — manual layer-2 review of the live ICT bot's runtime

**This skill reviews the live trading bot's runtime state, not the codebase.**
It runs whenever the operator invokes it. **Claude fetches the live
runtime state itself** via the GitHub Actions diag relays — the
operator does not paste, download, or fetch anything. This session is
an ephemeral sandbox with no direct network path to the VMs, but the
relays (`VM_SSH_KEY` + `DIAG_READ_TOKEN`, already in repo secrets)
give Claude autonomous read access. See CLAUDE.md § "STOP — Read this
before answering any 'what's running' question."

If the user asked for a *code* review, *codebase audit*, *security
review*, or *dependency check* — STOP. This is the wrong skill.
Direct them to the `review` or `security-review` skill instead.

## Where the inputs come from (2026-05-21 onwards)

**Claude pulls everything autonomously via the diag relays.** The
`health-snapshot.yml` workflow still runs on cron and uploads a
`health-snapshot-<run_id>` artifact for the deterministic Telegram
status ping, but this skill no longer depends on it: the sandbox has
no MCP tool to download Action artifacts, so the artifact is not the
review's input. Instead the skill reconstructs the same runtime view
from the relays it *can* drive (see "How Claude fetches runtime state
itself" below).

**Never ask the operator to paste, download, or SSH for a snapshot.**
That contradicts the autonomy mandate (CLAUDE.md: "Asking is a
critical failure of this document"). If a snapshot happens to be in
the chat already, treat it as a supplementary cross-check — but the
live diag pulls are the source of truth and run every time regardless.

There is **no longer** a Layer-1 LLM verdict, no auto-generated
`comms/requests/REQ-*.json`, no PR auto-merge, and no operator-paste
requirement.

## Inputs

- **Live diag relays (primary, always run)** — Claude pulls the live
  runtime state via `vm-diag-snapshot.yml` (live VM) and
  `trainer-vm-diag.yml` (trainer VM). This is the source of truth.
  See "How Claude fetches runtime state itself" below for the exact
  pulls and the snapshot-section → relay-endpoint mapping.
- `health_snapshot.txt` (optional, supplementary) — if the operator
  happens to paste a raw live-VM snapshot (sectioned with `=== NAME ===`
  headers: META, PROCESSES, HEARTBEAT, TICKS, SIGNALS, ORDERS, TRADES,
  POSITIONS, MONITORING, API, ERRORS, STORAGE, DB, AUDIT_LOG, VM, END),
  use it to cross-check the diag pulls. It is never required.
- `pipeline_test.json` (optional) — active dry-run smoke result.
  `warn` with note "plumbing-on-rejection path exercised" is the
  expected outcome when no exchange client is wired in.
- `trainer_snapshot.txt` (optional, supplementary) — if pasted, use it;
  otherwise Claude reconstructs the trainer view via `trainer-vm-diag.yml`
  (see "Trainer VM health review" below). Sectioned with
  `=== TRAINER <NAME> ===` headers (META, SERVICE, RECENT LOG, DATASETS,
  REGISTRY, RESOURCES, END). If the trainer VM is unreachable, grade all
  three trainer dimensions as `skip`.

## How Claude fetches runtime state itself

There are two transports for the same `/api/diag/*` read surface. Both
return identical JSON; the only difference is speed and setup. **Try
direct first; fall back to the issue relay.**

### Transport A — direct HTTP (preferred, when configured)

If this session's cloud environment has `DIAG_BASE_URL` and
`DIAG_READ_TOKEN` set (and Network access permits egress to the host),
fetch the diag surface directly in one shot — no GitHub round-trip:

```
scripts/ops/diag_fetch.sh 'audit?limit=600'
scripts/ops/diag_fetch.sh 'journal?table=trades&limit=100'
scripts/ops/diag_fetch.sh 'status'
```

The helper resolves `$DIAG_BASE_URL/api/diag/<path>` with the bearer in
a 0600 curl config (token never hits argv/logs). Exit `0` → JSON on
stdout, use it. Exit `3` → direct path unavailable (env unset, egress
blocked, or web-api down) → **fall back to Transport B**. The
`<path>` values are exactly the ones in the mapping table below.

`DIAG_BASE_URL` + `DIAG_READ_TOKEN` cover the **live VM only** — the
trainer VM has no `/api/diag/*` surface, so trainer dimensions are
always pulled via the `trainer-vm-diag` relay (see "Trainer VM health
review" below), regardless of direct config.

(Direct egress to a raw `http://IP:8001` may still be refused by the
platform proxy even at Network access = Full; if `diag_fetch.sh`
returns `3` despite the env vars being set, the robust fix is to point
`DIAG_BASE_URL` at an HTTPS hostname for the diag API. Until then the
relay fallback keeps the review working.)

### Transport B — GitHub-issue relay (fallback, always available)

The diag relays are issue-driven: open a labelled issue, the matching
GitHub Actions workflow SSHes to the VM, runs a fixed (live) or
arbitrary (trainer) read command, posts the result back as an issue
comment, and closes the issue. Poll `mcp__github__issue_read`
(`get_comments`) for the `github-actions[bot]` reply (~30–60 s).
Full contract + failure modes: `docs/claude/diag-relay.md` (live) and
`docs/claude/trainer-vm-mode.md` § 9 (trainer).

**Live VM** — `vm-diag-snapshot.yml`, issue label `vm-diag-request`,
title `[diag-request] <path>` (body ignored). Each `=== SNAPSHOT ===`
section maps to a diag endpoint (these `<path>` values are also what
you pass to `diag_fetch.sh` for the direct transport):

| Snapshot section | Diag relay pull |
|---|---|
| META / VM | `status` (carries `vm_health`: cpu/mem/disk) |
| PROCESSES | `services`, or `journalctl?unit=ict-trader-live.service&lines=50` |
| HEARTBEAT | `status`, or `log_file?name=heartbeat&lines=5` |
| TICKS | `audit?limit=600` → `pipeline_result` events |
| SIGNALS | `audit?limit=600` → `*_eval` events |
| ORDERS | `journal?table=order_packages&limit=100` |
| TRADES / POSITIONS | `journal?table=trades&limit=100` (open = `status='open'`) |
| MONITORING | `audit?limit=600` → `monitor` events |
| API / ERRORS | `journalctl?unit=ict-trader-live.service&lines=200` or `log_file?name=bot_log&lines=200` |
| AUDIT_LOG | `audit?limit=600` (freshness from newest `ts`); `log_file?name=audit&lines=1` |

Two live-VM snapshot sections have **no** diag-endpoint equivalent on
*either* transport — the `/api/diag/*` surface itself doesn't expose
them (no arbitrary bash, no `sqlite3 PRAGMA`), so direct HTTP doesn't
help here:

- **DB `integrity_check`** — not fetchable. Grade `db_integrity` from
  what *is* reachable: journal row recency (newest `trades` /
  `order_packages` row age) and the audit-log freshness. Note in the
  finding that `integrity_check` was not directly fetched. Only
  escalate to `concern` on stale-writes evidence, not on the absence
  of the integrity field.
- **STORAGE (`verify_storage_setup.sh`)** — not fetchable.
  `vm_health.disk` from `status` covers the disk-pressure angle; the
  mount/fstab detail is unavailable. Don't fail the review on its
  absence; mention it only if `vm_health.disk` is high.

If you genuinely need those two fields, the only path that computes
them is `health-snapshot.yml` (it runs `collect_health_snapshot.sh`
on the VM) — but its output lands in an Action artifact the session
can't download, so don't block the review on it.

**Trainer VM** — `trainer-vm-diag.yml`, issue label
`trainer-vm-diag-request`, body `cmd: <bash>` (arbitrary). Because it
runs arbitrary bash, Claude can reproduce the *entire*
`trainer_snapshot.txt` (see "Trainer VM health review" below for the
exact commands).

If the direct transport returns exit `3`, fall back to the relay. If
the relay then returns curl exit 7 (`Failed to connect to 127.0.0.1`),
the live web-api is down — fire `vm-web-api-recover` and retry once.
(A failing direct transport AND a relay exit 7 both point at the same
cause: `ict-web-api.service` is down, since both transports terminate
at that FastAPI process.) If it still fails after recovery, downgrade
gracefully (see the mandatory pre-review step below): emit the review
with a `concern` on `api_errors`, `operator_attention_required: true`,
and a note that the live pull could not be performed. Do not fabricate
findings.

## Other files this skill reads from the repo

- `comms/schema/health_review_response.template.json` — output shape.
- `comms/follow_ups.json` — running list of unresolved items earlier
  reviews flagged but couldn't fully resolve (e.g. waiting for a
  trigger condition to fire, deferred design decisions). Read every
  open entry; check whether its `trigger_condition` applies to this
  review window. Schema lives at
  `comms/schema/follow_ups.schema.json`.
- `config/accounts.yaml`, `config/strategies.yaml` — for the
  per-account / per-strategy sanity checks.

## Argument handling

The skill takes no required arguments. It runs once per invocation,
against whatever the operator pasted. The legacy multi-request mode
(reviewing N pending `comms/requests/REQ-*.json` files in one pass)
has been removed — that path was tied to the deleted auto-emission
flow.

If the operator passed an explicit free-form note as `$ARGUMENTS`
(e.g. `/health-review focus on bybit_2 rejections`), treat it as a
hint about what to weight in the review; don't fail if it's
unrecognized.

## Mandatory pre-review step — fetch the live 6-hour log window

**This is how the review gets its data.** Claude pulls the live audit +
journal tables via the diag surface — direct HTTP when the session is
configured for it (`scripts/ops/diag_fetch.sh`), else the GitHub-issue
relay (see "How Claude fetches runtime state itself" above and
`docs/claude/diag-relay.md`). This is not a complement to a pasted
snapshot, it *is* the input. (Even
when a snapshot is pasted, it can't substitute: `collect_health_snapshot.sh`
greps `*.log` for ticks/signals/orders/trades, but the live pipeline
writes `runtime_logs/signal_audit.jsonl` (NDJSON, not `.log`), so the
snapshot's `=== TICKS / SIGNALS / ORDERS / TRADES ===` sections
frequently read "no … logs in last 1440m" even mid-session. That's a
known collector limitation, so the diag pulls are authoritative.)

These pulls are the main substance of the layer-2 review: Claude must
look at the actual signals, orders, and trades produced over the recent
window and sanity-check both the technical pipeline (does each signal
that should have produced an order actually produce one? do orders that
fill become trades?) and the decision quality (are the signals
reasonable for the current market context? are the position sizes,
sides, and SL/TP wired through correctly?).

For each pull: run `scripts/ops/diag_fetch.sh '<path>'` first; on exit
`3`, fall back to opening a single `[diag-request]` issue per query and
polling `mcp__github__issue_read` for the workflow's reply comment.
Required pulls:

1. **6-hour audit tail** — `audit?limit=600` (≈100 events/hr cap).
   Tail of `runtime_logs/signal_audit.jsonl`. Filter the returned
   NDJSON to events whose `ts` is within the last 6h.
2. **Recent order packages** — `journal?table=order_packages&limit=100`.
   Compare against the audit tail: every `signal → order` transition
   should produce a row here.
3. **Recent trades** — `journal?table=trades&limit=100`. Same idea
   for `order → fill → trade`. Also used to derive M11 attribution
   dimensions (net_positions, strategy_attribution) — see below.
4. **Status snapshot** — `status` (heartbeat + status.json + vm_health).
   Cross-check against the embedded HEARTBEAT block.
5. **Advisory decisions log** (M11 S10) — `log_file?name=advisory_decisions&lines=200`.
   Returns the tail of `runtime_logs/advisory_decisions.jsonl` written by
   `Coordinator.log_advisory_scores()` when advisory-stage ML models are
   active. If `present: false`, advisory models are not wired — grade
   `advisory_scores` as `skip`. If present, check score freshness and
   whether all expected model IDs appear.

If the relay returns curl exit 7 (`Failed to connect to 127.0.0.1`),
the web-api is down — fire `vm-web-api-recover` and retry once. If
it still fails, downgrade gracefully: emit the review with a
`concern` on `api_errors` and `operator_attention_required: true`,
note that the 6h log review could not be performed, and stop. Do
not fabricate findings — a relay outage means no live data, not a
green light to guess.

### Follow-up log evaluation

After fetching the diag pulls and before grading, read
`comms/follow_ups.json`. For each entry with `status: "open"` (and
`snoozed_until` in the past or null):

1. Evaluate `trigger_condition` against this review window's diag
   data. Examples: "any rejection on bybit_2 in the 6h window",
   "any /health-review run", "next time the breaker fires."
2. If the trigger applies, perform the `expected_check`. Whatever
   you find — verified-good, verified-bad, or inconclusive — folds
   into the regular findings + `anomalies` array, prefixed with the
   `id` (e.g. `"FU-20260510-001: bybit_2 had 2 rejections, no breaker
   trip yet (threshold is 3)"`).
3. If the trigger doesn't apply (the conditions in this window
   don't match), skip silently — don't pad anomalies with
   "FU-X not triggered."
4. If the diag evidence satisfies `resolution_criteria`, surface it
   in `recommended_action` with phrasing like *"Close FU-XXX
   (resolved by …)."* The operator decides; don't auto-edit the
   file.

Do not write to `comms/follow_ups.json` from this skill. New
follow-ups discovered during a review go in the response's
`anomalies` array with a clear "open as new follow-up" hint in
`recommended_action`; the operator (or a separate skill) is
responsible for editing the file.

### Deriving M11 attribution dimensions from pull 3

The M11 refactor (merged 2026-05-20) added two new Tier-1 API endpoints:
`GET /api/bot/positions/net` and `GET /api/bot/strategy/attribution`. These
are not under `/api/diag/` so the diag relay cannot fetch them directly.
Instead derive the same data from the pull-3 trades table:

- **Net positions** — filter `journal?table=trades&limit=100` rows to
  `status='open'`; sum `position_size * (1 if direction='long' else -1)`
  per symbol across all accounts. This is the net_qty reported by the
  `/api/bot/positions/net` endpoint.
- **Strategy attribution** — group closed trades by `strategy_name`;
  compute win count (`pnl > 0`) and total. These are the `win_rate` and
  `total_pnl` values from `/api/bot/strategy/attribution`.

Use these derived views to populate the `net_positions` and
`strategy_attribution` finding dimensions.

### Sanity-check rubric for the 6-hour window

Beyond freshness counts, judge **decision quality**:

- **Signal → order plumbing.** For every signal in the audit tail
  with `outcome=actionable` (or equivalent), there should be a
  corresponding `order_packages` row within seconds. Gaps → `concern`
  on `orders`.
- **Order → trade plumbing.** Every filled order should have a row
  in `trades`. Orphaned orders (filled with no trade row, or trade
  rows with no parent order) → `concern` on `trades`.
- **Side / size sanity.** Spot-check 3–5 orders: does the side match
  the signal direction? Is the qty within the per-account cap in
  `config/accounts.yaml`? Is leverage reasonable (no `qty=1` BTC
  on a $200 account)?
- **SL/TP wiring.** Each order should carry SL and TP metadata
  (visible in `order_packages.metadata` or signal_audit). Missing
  → `watch`; systematic absence → `concern`.
- **Repeated rejections.** Multiple consecutive `failed_exchange`
  / `failed_risk_gate` / `borrow_unavailable` events on the same
  symbol → `concern` on `orders` (something upstream is wedged).
- **Monitoring cadence.** `run_monitor_tick` events should appear
  on the documented cadence. Long gaps → `concern` on `monitoring`.
- **Signal reasonableness.** This is the qualitative check. Are
  signals firing at sensible times (not 100 in 5 minutes, not 0 over
  6 hours during active sessions)? Are the strategies named in the
  audit consistent with what's enabled in `config/strategies.yaml`?
  Anomalies here go in the free-form `anomalies` array.

### Per-trade decision grading (training-data feedstock)

For **every closed (or rejected) trade** in the 6-hour window,
emit a structured grade in `trade_decision_grades[]` so future
training sessions have a labelled feedback signal beyond raw P&L.
The grade is independent of dollar outcome — a small win on a bad
setup is still graded poorly; a stop-out on a textbook setup is
still graded fairly.

**These grades are downstream training input for the AI-traders
baseline pipeline** ([`docs/AI-TRADERS-ROADMAP.md`](../../../docs/AI-TRADERS-ROADMAP.md)).
The current consumers:

- The `trade_outcomes` family ([`ml/datasets/families/trade_outcomes.py`](../../../ml/datasets/families/trade_outcomes.py))
  joins these grades to closed trades for the WS5-A outcome
  probability baseline.
- The `setup_labels` family ([`ml/datasets/families/setup_labels.py`](../../../ml/datasets/families/setup_labels.py))
  uses them for the WS5-C setup-quality scorer.
- Future post-trade-review (WS5-E) and prop-mission-policy
  (WS5-F) baselines will read them directly.

So this section is not optional decoration — it is the labelled
feedstock that retrains the next baseline run. Grade as if the
operator will inspect every row, because the model layer
eventually will.

Use the trade's `signal_logic` blob (in `trades.notes` or the
`order_packages.signal_logic` JSON) to anchor the call. That blob
carries the entry rationale (VWAP std-dev, HTF EMA / band, sweep
buffer, ATR multiplier, etc.) — judge the trade against its own
stated edge and the post-hoc fill / exit data we have.

**Letter grade rubric (one per trade):**
- `A` — Textbook. Setup-config aligned, HTF unblocked, R:R ≥ 1.5,
  hit TP cleanly, no premature exit. The kind of trade we want
  more of.
- `B` — Good. Same as A but with one minor deviation (slightly
  low confidence, R:R ~1.0, TP1 partial only, fill slippage).
- `C` — Acceptable. Setup fired correctly and risk was contained
  (e.g., stopped at SL with the documented multiplier), but the
  EV looks marginal in retrospect.
- `D` — Poor. Setup fired but went against HTF or had thin
  confidence; only saved by mean reversion or noise.
- `F` — Bad. Should not have fired at all (config mismatch,
  htf_blocked=true overridden, oversized, against published
  bias). Or should have stayed in (premature trail-stop on what
  would clearly have run further given the same signal logic).

**Three standardized categorical labels (per trade):**
- `entry_quality`: one of
  `optimal | acceptable | late | early | should_skip | unknown`
- `exit_quality`: one of
  `optimal | tp_appropriate | sl_appropriate | premature_exit |
  held_too_long | unknown`
- `risk_management`: one of
  `correct | oversize | undersize | sl_too_tight | sl_too_wide |
  unknown`

These three labels are the training-friendly fields; the letter
grade is a single rolled-up summary for human scanning.

**Per-trade entry shape (one object per trade):**

```json
{
  "trade_id": 1135,
  "timestamp": "2026-05-10T10:14:38+00:00",
  "symbol": "BTCUSDT",
  "direction": "long",
  "setup": "vwap",
  "entry_price": 80725.9,
  "exit_price": 80794.7,
  "stop_loss": 80700.41,
  "take_profit_1": 80784.64,
  "position_size": 0.002,
  "exit_reason": "tp_cross",
  "decision_grade": "A",
  "entry_quality": "optimal",
  "exit_quality": "tp_appropriate",
  "risk_management": "correct",
  "rationale": "≤ 240 chars — why this grade given signal_logic + outcome",
  "alternative_action": "≤ 160 chars — what we'd do differently next time, or 'none'"
}
```

Use `unknown` honestly when the diag bundle didn't carry enough
context to grade a dimension (e.g., truncated `signal_logic`,
missing exit price). **Do not fabricate** a grade where the data
doesn't support one — `unknown` + a short rationale is the
contract.

When the 6-hour window contains many trades, prefer per-trade
grades for closes + at least one representative grade per
rejection cluster. If there are >20 trades, batch the lowest-grade
cohort first (Cs, Ds, Fs) so the operator and the training
pipeline see the negative signal up front; aggregate the As / Bs
in a single summary entry that lists the trade ids covered.

The pipeline-test result in `artifacts/health/pipeline_test.json` is
an out-of-band dry-run of `safe_place_order`. A `warn` with note
"plumbing-on-rejection path exercised" is the **expected** outcome
when no exchange client is wired into the smoke; do not grade it
as `concern`.

## Trainer VM health review

The training center VM (`158.178.209.121`) runs the ML lifecycle
independent of the live trader. Include it in every health review.

### Fetching the trainer view yourself

If `trainer_snapshot.txt` wasn't pasted, reconstruct it via the
`trainer-vm-diag.yml` relay (issue label `trainer-vm-diag-request`,
body `cmd: <bash>` — arbitrary bash, see `docs/claude/trainer-vm-mode.md`
§ 9). One issue with a `cmd: |` block reproduces every section:

```
cmd: |
  REPO=/home/ubuntu/ict-trading-bot
  echo "=== TRAINER SERVICE ==="
  systemctl is-enabled ict-trainer.service; systemctl is-active ict-trainer.service
  systemctl is-enabled ict-trainer.timer;   systemctl is-active ict-trainer.timer
  systemctl show ict-trainer.service --property=ExecMainStatus,ActiveEnterTimestamp,ActiveExitTimestamp
  echo "=== TRAINER RECENT LOG ==="
  journalctl -u ict-trainer.service -n 100 --no-pager
  echo "=== TRAINER DATASETS ==="
  ls -la "$REPO/ml/datasets/built/"; tail -n 10 "$REPO/runtime_logs/trainer/dataset_builds.jsonl"
  echo "=== TRAINER REGISTRY ==="
  cd "$REPO" && .venv/bin/python -m ml.registry list
  echo "=== TRAINER RESOURCES ==="
  df -h /home | tail -1; free -m | head -2
```

If the trainer relay errors (SSH failure, host down), grade all three
trainer dimensions as `skip` and note the relay failure — same as the
`trainer_vm_not_reached: true` case.

### What the trainer snapshot contains

- `=== TRAINER SERVICE ===` — systemd unit and timer state. Key fields:
  `trainer_enabled`, `trainer_active`, `timer_enabled`, `timer_active`,
  plus `ActiveEnterTimestamp` / `ActiveExitTimestamp` from `systemctl show`.
- `=== TRAINER RECENT LOG ===` — last 100 journal lines for
  `ict-trainer.service`. The primary signal for training failures and
  progress.
- `=== TRAINER DATASETS ===` — `ls` of `ml/datasets/built/` and the last
  10 lines of `runtime_logs/trainer/dataset_builds.jsonl`. Shows which
  families exist and when they were last built.
- `=== TRAINER REGISTRY ===` — output of `python -m ml.registry list`.
  Shows all registered models and their current stage
  (`research_only`, `candidate`, `backtest_approved`, `shadow`,
  `live_approved`).
- `=== TRAINER RESOURCES ===` — disk and memory.

### Grading rubric

#### `trainer_service`

Evaluates whether the training cycle is running as expected.

- `ok` — `timer_enabled=enabled`, `timer_active=active`, and the service's
  `ActiveExitTimestamp` is within the expected cycle window (typically
  ≤ 24h ago for a daily timer). No `Failed` / `error` lines in recent log.
- `watch` — timer or service disabled, last successful run > 24h ago but
  < 72h, or service ran but produced no output (empty log section).
- `concern` — service `ExecMainStatus` is non-zero, timer inactive with no
  next elapse time, persistent `error` / `FAILED` / `exit code` lines in
  the journal, or last run > 72h ago.
- `skip` — trainer VM was unreachable (`trainer_vm_not_reached: true`).

#### `trainer_datasets`

Evaluates whether the WS5 dataset families are present and fresh.

- `ok` — `ml/datasets/built/` exists with all expected families present
  (`backtest_results`, `trade_outcomes`, `setup_labels`, `signal_features`,
  and any other registered families). Last build log shows success within
  72h.
- `watch` — some families present but one or more missing, or last build
  > 72h ago.
- `concern` — `no_datasets_dir: true` (bootstrap never completed or
  datasets wiped), or all build log entries show errors.
- `skip` — trainer VM unreachable.

Expected dataset families (check against `ml/datasets/built/` listing):
`backtest_results`, `trade_outcomes`, `setup_labels`, `signal_features`.
If the registry lists families that aren't built, that's `watch`.

#### `trainer_registry`

Evaluates whether models are being produced and progressing through the
promotion pipeline.

- `ok` — registry has at least one model at `shadow` stage or above.
  Model artifact IDs and timestamps are consistent with recent training
  runs visible in the service log.
- `watch` — models exist but none past `candidate` stage, or most recent
  model is > 7 days old with no new training run visible in the log.
- `concern` — `registry_empty_or_error: true`, or registry has models but
  all are `research_only` (training is running but nothing passes eval),
  or model artifact paths don't exist on disk.
- `skip` — trainer VM unreachable.

### How trainer findings affect `overall_assessment`

- Any trainer dimension `concern` → `overall_assessment` downgrades to
  `investigate` only if the live-bot findings don't already mandate it.
  Add an entry in `anomalies` naming the specific trainer issue.
- Any trainer dimension `watch` → `caution` if no other `concern` on the
  live side.
- All trainer dimensions `ok` or `skip` → no effect on overall assessment.

The trainer VM is **not** a blocker for the live trader — a down trainer
doesn't affect live trading. Escalate trainer issues with lower urgency
than live-bot `concern` findings (don't set `operator_attention_required`
for trainer-only issues unless a model that's `live_approved` is involved).

## Decision procedure

Grade the snapshot + the live diag pulls from the pre-review step
directly. There is no Layer-1 verdict to cross-check against — the
Layer-1 LLM path was removed in the 2026-05-12 cleanup; this skill
is the only grader. Use the sanity-check rubric above (signal→order,
order→trade, side/size sanity, SL/TP wiring, repeated rejections,
monitoring cadence, signal reasonableness).

Map findings to the layer-2 dimensions (these differ from layer 1):

- `heartbeat`  — freshness of `runtime_logs/heartbeat.txt`.
- `ticks`      — recency + status field of recent tick records.
- `signals`    — actionable signal volume vs the 24h baseline.
- `orders`     — submitted/rejected counts; any borrow-side errors.
- `trades`     — fills, orphaned trades, P&L attribution.
- `monitoring` — `run_monitor_tick` activity; verdict application.
- `sizing`     — position-size sanity vs per-account caps in
  `config/accounts.yaml`. (Look for over-sized opens, leverage
  drift, capacity-zero rejections.)
- `api_errors` — 4xx/5xx burst rates, repeated tracebacks. Combines
  layer 1's `api` + `errors` checks.
- `state_consistency` — for each account in `config/accounts.yaml`,
  compare YAML `mode` against the runtime `live` field in
  `runtime_logs/runtime_status.json` (and, when accessible, the
  Telegram process's `accounts_status` reply). Drift between the
  YAML declaration and any runtime view → `concern`. The two
  most common causes: (a) the runtime override dict has been
  mutated by a circuit-breaker auto-trip (look for the matching
  alert in `alert_delivery`); (b) the dashboard's read-projection
  defaulted dry because of a stale resolver bug — recheck once the
  runtime_status.py default-flip fix has landed.
- `alert_delivery` — verify the in-process `AlertsQueue`
  (`src/units/dashboards/alerts.py`) is being drained. Check (i)
  the diag tail / journalctl for any `alert_drainer:` log lines in
  the 6h window, (ii) whether known-trip events (auto-pauses,
  capacity-zero clusters, exception storms) have a corresponding
  Telegram message logged in `bot.log`. If known critical events
  fired but no drain log accompanied them → `concern` with note
  "alerts queued, drainer silent — operator unnotified".
- `strategy_silence` — every strategy enabled in
  `config/strategies.yaml` should produce per-tick audit events
  in `runtime_logs/signal_audit.jsonl` (`turtle_soup_eval`,
  `vwap_eval`, etc.). Count by `event` over the 6h window. Any
  enabled strategy with **zero `*_eval` events** for more than
  one hour during an active session → `concern` with the
  strategy name and the silence duration. This is the dimension
  the 2026-05-10 incident exposed: VWAP went silent for 8h, but
  because VWAP wasn't writing per-tick audit events at all the
  silence was indistinguishable from "no signal." Fixed in PR
  that adds `vwap_eval`; if a future strategy is added without
  an audit emitter, this check is what catches it.
- `db_integrity` — the diag relay can't run `PRAGMA integrity_check`
  (fixed-curl only, no `sqlite3`), so grade primarily from journal
  recency + counts via the pulls. If a `=== DB ===` block *was*
  pasted, use its `integrity_check`: should be `ok`; any other value
  (`malformed disk image`, `index <N> has wrong # of entries`, etc.)
  is an immediate `concern` with `operator_attention_required: true`.
  Without a pasted block, note "integrity_check not fetched (relay
  can't run PRAGMA)" and grade from the relay-reachable signals.
  Also weigh:
  - `age_seconds` — for a live trader this should be small (single
    digits during active sessions, ≤ a few minutes during quiet
    windows). An age > 1h while signals/ticks are firing in the
    diag pull means the trader has stopped writing to the journal
    → `concern`.
  - `<table>_total` counts — should be non-decreasing run over run.
    A drop indicates accidental truncation or a restore from an
    older snapshot.
  - `-wal` / `-shm` size — large WAL files (> 100 MB) with a small
    main DB suggest a stuck checkpoint; surface as `watch`.
  Grade `watch` for soft signals (large WAL, mtime modestly stale);
  reserve `concern` for integrity failures or hours-stale mtimes.
- `net_positions` — derived from pull-3 open trades (see "Deriving M11
  attribution dimensions" above). Grade:
  - `ok` — net qty per symbol is within expected range given open trades.
    No phantom positions (net qty with no matching open trade rows).
  - `watch` — net qty is larger than expected for the account's risk caps,
    or a symbol has open trades but the computed net rounds to zero (equal
    long/short, which is unusual given the strategies).
  - `concern` — net qty per symbol far exceeds the `pos_size` cap in
    `config/accounts.yaml`, or computed net disagrees with open trade
    count by more than rounding error (possible double-count / orphan).
- `strategy_attribution` — derived from pull-3 closed trades grouped by
  `strategy_name`. Cross-check against `config/strategies.yaml` enabled list.
  - `ok` — all enabled strategies have at least some closed trades in the
    trailing 7-day window; win rates are plausible (5–95%); no strategy
    showing 100% loss over ≥ 5 trades.
  - `watch` — one strategy has zero closed trades in the last 7 days (may
    just mean no signals fired); or win rate is at an extreme but sample is
    small (< 5 trades). Note the strategy name.
  - `concern` — a strategy has > 5 consecutive losses with no wins; or a
    strategy in `strategies.yaml` as `enabled: true` has never produced a
    trade row (wiring gap, not just silence).
- `advisory_scores` — grade from pull-5 (`log_file?name=advisory_decisions`):
  - `skip` — log absent (`present: false`); no advisory-stage models wired.
    This is the expected state for most installs (M11 S10 machinery is wired
    but only activates when a model reaches advisory stage).
  - `ok` — log is present; entries are recent (within the last 24h); all
    expected model IDs appear; score values are in [0, 1].
  - `watch` — log is present but last entry is > 24h old (model may have
    stopped predicting); or a model_id appears in the log but is absent from
    `config/strategies.yaml` shadow_model_ids.
  - `concern` — log is present but score values are all 0.0 or all 1.0 for
    > 20 consecutive rows (model output collapse); or OSError entries appear
    in the log indicating write failures in the advisory hook.
- `allocator_path` — check `runtime_logs/runtime_status.json` for the
  `CENTRALIZED_ALLOCATOR` flag value (written by the pipeline on startup):
  - `skip` — flag absent or `false` (legacy passthrough path is active;
    this is the expected default until the flag is explicitly enabled).
  - `ok` — flag is `true` and `runtime_logs/allocator_decisions.jsonl`
    exists with recent entries; typed dispatch path is confirmed active.
  - `watch` — flag is `true` but allocator_decisions.jsonl is absent or
    stale (typed path enabled but not writing decisions).
  - `concern` — flag value in runtime_status.json contradicts what the
    audit tail shows (e.g., flag=true but no typed packages in audit).
- `trainer_service` — is `ict-trainer.service` / timer running as expected?
  Grade from `=== TRAINER SERVICE ===` and `=== TRAINER RECENT LOG ===`.
  See "Trainer VM health review" § Grading rubric above.
- `trainer_datasets` — are WS5 dataset families present and recently built?
  Grade from `=== TRAINER DATASETS ===`. See rubric above.
- `trainer_registry` — are models in the registry and progressing through
  stages? Grade from `=== TRAINER REGISTRY ===`. See rubric above.
- `audit_log_freshness` — read the `=== AUDIT_LOG ===` block.
  `events_last_hour` should be > 0 during any active trading
  session (every tick writes a `pipeline_result` event). Zero
  events while the heartbeat is fresh → `concern` (writer crash or
  silent path divergence). `age_seconds > 600` while
  `heartbeat.age_seconds < 60` → same `concern`. If both the
  audit log and heartbeat are stale, that's `heartbeat`'s
  responsibility — don't double-report. The `last_event` line
  helps spot a writer that "looks" fresh because of a `touch` but
  has no real events flowing.

Status grades:
- `ok`       — no anomaly worth flagging.
- `watch`    — anomaly present but bounded; no immediate action.
- `concern`  — operator should look. ⇒ `operator_attention_required: true`.

`overall_assessment` mapping:
- `healthy`     — every finding is `ok`.
- `caution`     — at least one `watch`, no `concern`.
- `investigate` — any `concern`. ⇒ `operator_attention_required: true`.

## Active watch items (specific things to grade explicitly)

These are open architectural gaps from
[`docs/ARCHITECTURE-CANONICAL.md`](../../../docs/ARCHITECTURE-CANONICAL.md)
§ Known gaps that this skill should explicitly check in the snapshot
data each run. Add a finding for each item — `ok` if there's no
evidence of the issue in the current snapshot, `watch` / `concern`
if there is. Remove a row from this list when the underlying gap is
closed.

- **Reduce-only fill correlation (S-MSE-2 / Phase 2 follow-up).** The
  intent-mode dispatcher places reduce / close / flip legs with
  `setup_type='intent_reduce'` and `notes.intent_reduce=True`. The
  S-030 monitor reconciles fills by `symbol + qty + side + timestamp`
  and currently writes the reduce as a fresh row instead of updating
  the parent's `position_size`. Brief P&L double-counting can occur
  on the tick a reduce fires.
  **Grade by:** scan the TRADES section of the snapshot for any
  `setup_type='intent_reduce'` rows; for each such row, check whether
  a matching open parent row exists with `position_size` not yet
  decremented to reflect the reduce leg. If yes → `watch`
  (one tick of double-count is bounded) or `concern` if the parent
  stays mis-sized for more than one monitor cycle (~1 min).
  Reference the gap entry in ARCHITECTURE-CANONICAL.md when grading
  so the operator can land the reconciler fix (S-MSE-3) with full
  context. Until ICT scalp activates AND a real flip happens, expect
  every grade here to be `ok` with note "no intent_reduce rows
  observed yet".

- **Shadow-prediction observability for vwap (PR #1274 follow-up).** PR
  #1274 fixed a regression where the open-package self-suppression
  gate in `vwap.order_package` silenced shadow observation: any time
  the strategy held an open package, the gate aborted before
  `with_shadow_preds` ran, so models attached via `shadow_model_ids`
  saw zero signals. The fix moved the shadow call ahead of the gate.
  **Grade by:** if the 6h audit window contains any `vwap_eval` with
  `side != "none"` AND `config/strategies.yaml`'s
  `vwap.shadow_model_ids` is non-empty, pull
  `/api/bot/shadow/stats?model_id=<id>&stage=shadow` and confirm the
  count is non-zero over the same window. Mismatch (actionable vwap
  signals fired but shadow log empty) → `concern` on a new
  `shadow_observability` line in `anomalies` and reference FU-20260516-001.
  No-op when `shadow_model_ids` is empty (note that in anomalies so
  the operator sees observation is currently disabled).

## Output

**Single-request mode** (the user passed an explicit `REQ-…` or run
id): emit a single JSON object — no prose, no markdown fences, no
leading or trailing whitespace beyond the JSON. Conform to
`comms/schema/health_review_response.template.json` exactly. Populate
every field. Use the request's `request_id` verbatim. Set
`reviewed_at` to the current UTC ISO-8601 timestamp. Set `reviewer`
to `claude`.

**Multi-request mode** (default — N pending requests since last
review): emit a single JSON array containing N response objects, one
per pending request, sorted by request `created_at` ascending. Each
element conforms to the same schema as single-request mode. The
shared fields (`findings`, `anomalies`, `trade_decision_grades`,
`recommended_action`, `operator_attention_required`) are computed
once from the single diag-relay pull and replicated verbatim across
every entry; only `request_id` and `reviewed_at` differ per element
(every element uses the same `reviewed_at` — they were all reviewed
in the same pass).

When N=1, emit the single object form (not a one-element array) so
the response stays byte-identical to the legacy shape.

Schema reminder:

```json
{
  "request_id": "REQ-YYYYMMDD-HHMMSS-<slug>",
  "reviewed_at": "YYYY-MM-DDTHH:MM:SS+00:00",
  "reviewer": "claude",
  "overall_assessment": "healthy | caution | investigate",
  "findings": {
    "heartbeat":           {"status": "ok | watch | concern", "note": "..."},
    "ticks":               {"status": "ok | watch | concern", "note": "..."},
    "signals":             {"status": "ok | watch | concern", "note": "..."},
    "orders":              {"status": "ok | watch | concern", "note": "..."},
    "trades":              {"status": "ok | watch | concern", "note": "..."},
    "monitoring":          {"status": "ok | watch | concern", "note": "..."},
    "sizing":              {"status": "ok | watch | concern", "note": "..."},
    "api_errors":          {"status": "ok | watch | concern", "note": "..."},
    "state_consistency":   {"status": "ok | watch | concern", "note": "..."},
    "alert_delivery":      {"status": "ok | watch | concern", "note": "..."},
    "strategy_silence":    {"status": "ok | watch | concern", "note": "..."},
    "db_integrity":        {"status": "ok | watch | concern", "note": "..."},
    "audit_log_freshness": {"status": "ok | watch | concern", "note": "..."},
    "trainer_service":     {"status": "ok | watch | concern | skip", "note": "..."},
    "trainer_datasets":    {"status": "ok | watch | concern | skip", "note": "..."},
    "trainer_registry":    {"status": "ok | watch | concern | skip", "note": "..."},
    "net_positions":       {"status": "ok | watch | concern", "note": "..."},
    "strategy_attribution":{"status": "ok | watch | concern", "note": "..."},
    "advisory_scores":     {"status": "ok | watch | concern | skip", "note": "..."},
    "allocator_path":      {"status": "ok | watch | concern | skip", "note": "..."}
  },
  "anomalies": ["...free-form list..."],
  "trade_decision_grades": [
    {
      "trade_id": 0,
      "timestamp": "YYYY-MM-DDTHH:MM:SS+00:00",
      "symbol": "BTCUSDT",
      "direction": "long | short",
      "setup": "vwap | turtle_soup | ...",
      "entry_price": 0.0,
      "exit_price": 0.0,
      "stop_loss": 0.0,
      "take_profit_1": 0.0,
      "position_size": 0.0,
      "exit_reason": "tp_cross | sl_hit | trail | manual | rejected | ...",
      "decision_grade": "A | B | C | D | F",
      "entry_quality": "optimal | acceptable | late | early | should_skip | unknown",
      "exit_quality": "optimal | tp_appropriate | sl_appropriate | premature_exit | held_too_long | unknown",
      "risk_management": "correct | oversize | undersize | sl_too_tight | sl_too_wide | unknown",
      "rationale": "≤ 240 chars",
      "alternative_action": "≤ 160 chars, or 'none'"
    }
  ],
  "recommended_action": "what to do next, or 'none'",
  "operator_attention_required": false
}
```

`trade_decision_grades` is REQUIRED. Pass an empty array (`[]`) only
when the 6-hour window genuinely contained no closed or rejected
trades.

## Notes guidance

- Each `note` ≤ 120 chars. Reference specifics from the snapshot or
  live diag pulls (counts, ages, error classes, sample symbols / qtys)
  so the operator can verify quickly.
- Prefer counts from the live diag tail (`audit?limit=600` over the
  last 6h) over the snapshot's TICKS/SIGNALS/ORDERS/TRADES sections —
  the snapshot collector is known to under-report (see pre-review
  step above).
- An empty section in the snapshot is **not** automatically `watch`
  if the diag tail shows activity in the same window — grade by what
  the diag tail says, and add an anomaly noting the snapshot/diag
  disagreement so the collector bug stays visible.
- Don't fabricate data — if a diag pull failed and you couldn't
  verify a dimension, say so in the note (e.g. "audit pull failed,
  graded from snapshot only").

## What NOT to do

- Don't skip the 6-hour log review. The pre-review step is the
  substance of this routine; emitting a verdict from the snapshot
  alone is the failure mode this skill exists to prevent.
- Don't write any files. The response is plain-text JSON in the
  conversation. The operator pastes it into the comms request's
  answer per `comms/schema/response.schema.json`.
- Don't try to call the live trader, modify `config/accounts.yaml`,
  or touch anything under `src/`. Reviews are read-only.
- Don't open issues to *deliver* the review, open PRs, or commit
  changes — this is a sanity-review skill, not a remediation skill.
  (The read-only diag-relay trigger issues — `vm-diag-request`,
  `trainer-vm-diag-request`, `vm-web-api-recover` — are the
  exception: opening them is how the review fetches its data, and the
  workflows auto-close them.)
- **Don't ask the operator to paste, download, or fetch a snapshot.**
  Claude has autonomous read access via the relays; asking is a
  critical failure of the autonomy mandate. Pull the data yourself.
- Don't ask scoping questions. The scope is fixed: pull the live
  runtime state via the diag relays and emit the response JSON. If
  the user meant a code review, the skill description is wrong — they
  should invoke `review` or `security-review` instead.

## If the relays are unreachable

The only legitimate stop condition is a relay outage. If the live
diag relay fails even after a `vm-web-api-recover` retry (see the
mandatory pre-review step), emit the partial review with a `concern`
on `api_errors`, `operator_attention_required: true`, and a note
that the live pull could not be performed — don't synthesize findings
without evidence. A pasted snapshot, if present, can backfill the
non-live dimensions in that degraded case, but the diag relay being
down is itself the headline finding.
