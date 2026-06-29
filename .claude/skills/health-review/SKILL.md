---
name: health-review
description: Autonomous layer-2 review of the LIVE ICT TRADING BOT's TECHNICAL runtime health — pipeline plumbing, DB integrity, data validity, service state, alert delivery, sprint-doc drift. Reviews the cron health-snapshot report and reconstructs the same view from the diag relays since the last review. Drains docs/claude/health-review-backlog.json (system bugs / wiring gaps / minor doc drift). Does NOT score trades and does NOT review model performance — those moved to /performance-review and /ml-review respectively (2026-05-26 split). Use when the operator says "run the health review", "/health-review", or "do the layer-2 system review". NOT a code review or security audit.
---

# /health-review — technical/pipeline/data-health review of the live ICT bot

This is the **system-health** session of the three-way review split (the
others are `/performance-review` for trading + strategy scoring and
`/ml-review` for the training center + model lifecycle). It reviews the
**live trading system's runtime state**, not the codebase. Fully
autonomous: Claude fetches state itself through the diag relays, grades
plumbing + integrity, drains its backlog, and emits the response JSON.
The operator pastes nothing, downloads nothing, SSHes nowhere.

If the user asked for a *code* review, *codebase audit*, *security
review*, or *dependency check* — STOP, wrong skill. Point them at
`review` or `security-review`.

If the user asked about *strategy/trade performance*, *trade scoring*, or
*tweaks to consider* — STOP, wrong skill. Use `/performance-review`.

If the user asked about *model performance*, *training sessions*, or
*promote/demote a model* — STOP, wrong skill. Use `/ml-review`.

## Scope (what this skill DOES)

1. **Establish the window** — review everything *since the last
   health-review*, not a fixed slice (§ "The review window").
2. **Pull live runtime state** via the diag relays (§ "Fetching runtime
   state").
3. **Read the cron health report** — the artifacts surfaced by
   `/api/bot/health/{latest,history,services}` and the live VM's
   `artifacts/health/` snapshots (§ "The health report").
4. **Grade full-pipeline plumbing** — signal→order→trade wiring, monitor
   cadence, strategy silence, alert delivery, state consistency
   (§ "Pipeline rubric").
5. **Validate DB integrity + data validity** (§ "DB integrity &
   validity").
6. **Grade trainer-VM service health only** — is the timer running, is
   the unit healthy. Model/dataset/registry detail is **out of scope** —
   that's `/ml-review` (§ "Trainer service touch").
7. **Audit broker-account reachability (MANDATORY)** — confirm EVERY
   declared-live broker account is reachable (§ "Broker-account
   reachability"). A down live account is a can't-miss flag, never a
   line in the body.
8. **Review recent sprint logs** for doc correctness (§ "Sprint-doc
   review").
9. **Ingest the orphan-events log** (§ "Orphan-events ingest") — every
   NEW orphan trade row since the last review MUST be tracked + driven to
   reconciliation. Orphan is a problem to solve, never a resting status.
10. **Drain the health-review backlog** — triage every open item, fix
    what you can (§ "Draining the backlog").
11. **Emit the response JSON** + **post a one-line update to the Claude
    channel** (§ "Output" + § "Posting to the Claude channel").

## Out of scope (DO NOT do here)

- **Per-order-package trade scoring** — moved to `/performance-review`.
  `comms/claude_strategy_scores.jsonl` is no longer written by this
  skill.
- **Model status reports** — moved to `/ml-review`. No `model_status[]`
  in this skill's output.
- **Strategy tweak proposals** — `/performance-review`.
- **Promotion / demotion recommendations** — `/ml-review`.

## The review window — "since the last review"

The window runs from the last health-review to now. Determine "last
review" in this order:

1. The newest `reviewed_at` recorded in a prior health-review JSON
   (look at the Claude channel ping for the last review, or the
   newest `backlog_drain` action timestamp in
   `docs/claude/health-review-backlog.json`).
2. If neither is available, fall back to the last 24h.

Cap practical pulls at the diag limits (audit `limit=600` ≈ 6h at full
cadence). If the gap exceeds one pull, page back with
`since`/`until` on `journalctl` and note in the response that older
events were summarized, not enumerated. **Cover the whole gap.**

## Fetching runtime state (use the diag-data skill)

This skill is a **consumer** of `diag-data` and `git-actions`. Follow
those skills for the transport mechanics; this section lists the
specific pulls health-review needs.

**Required pulls (live VM, via `vm-diag-request` issue or direct HTTP):**

| Pull | Path | Use |
|---|---|---|
| Audit tail | `audit?limit=600` | ticks / `*_eval` signals / monitor events; filter to the window |
| Order packages | `journal?table=order_packages&limit=100` | signal→order plumbing only (NOT scoring) |
| Trades | `journal?table=trades&limit=100` | order→trade plumbing only (NOT scoring) |
| Status | `status` | heartbeat + status.json + `vm_health` (cpu/mem/disk) |
| Services | `services` | `systemctl is-active` per allowlisted unit |
| Older windows | `journalctl?unit=ict-trader-live.service&since=<iso>&until=<iso>` | page back across a long gap |
| Health snapshot — latest | (HTTP) `GET /api/bot/health/latest` via the `vm-health-snapshot-fetch` flavour of the diag relay, OR ride a direct call to `/api/bot/health/latest` when configured | the most-recent cron health snapshot the trader wrote |
| Health snapshot — history | `GET /api/bot/health/history?hours=N` | newest-first list of snapshots in the window |
| Health services | `GET /api/bot/health/services` | systemd state of `ict-trader-live` + `ict-web-api` |

**Trainer VM (light touch only — service health):**

Open a `trainer-vm-diag-request` issue with:

```
cmd: |
  systemctl is-enabled ict-trainer.service; systemctl is-active ict-trainer.service
  systemctl is-enabled ict-trainer.timer;   systemctl is-active ict-trainer.timer
  systemctl show ict-trainer.service --property=ExecMainStatus,ActiveEnterTimestamp,ActiveExitTimestamp
```

That's all health-review needs. **Do not** also pull
`training_cycle.jsonl`, `python -m ml list-models`, `dataset_builds.jsonl`,
or registry data — those belong to `/ml-review`. Pulling them here just
inflates the issue comment.

**On relay failure:** if the live relay returns curl exit 7, fire
`vm-web-api-recover` and retry once. If it still fails, downgrade
gracefully — emit the review with `api_errors` = concern,
`operator_attention_required: true`, and a note that the live pull
couldn't be performed. **Never fabricate findings.**

## The health report

The cron `health-snapshot.yml` writes per-run snapshots to
`artifacts/health/health_check_<TS>.json` on the live VM and surfaces
them through the `/api/bot/health/*` endpoints. Read both the
**latest** snapshot (current state) and the **history** for the window
(trend), then:

- Cross-check the snapshot's findings against your own diag pulls — if
  the snapshot says `heartbeat: ok` but your `status` pull shows a
  stale `heartbeat.txt`, the snapshot is lying and that's itself a
  `concern` (snapshot generator broken).
- For every `concern` in the snapshot history that has NOT recovered by
  the latest snapshot, surface it as an open anomaly.
- For repeating-and-recovering issues (e.g. transient API errors every
  few hours), grade `watch` not `concern` and note the cadence.

If `/api/bot/health/latest` returns `{present: false}`, the cron
generator hasn't run since this artifact dir was last cleared — note
it as `health_snapshot: watch` (not concern; the live diag pull is
the canonical view, the snapshot is a convenience).

## Pipeline rubric

Beyond freshness counts, judge **plumbing quality** across the window:

- **Signal → order plumbing.** Every actionable signal in the audit
  tail should produce an `order_packages` row within seconds. Gaps →
  `orders` concern.
- **Order → trade plumbing.** Every filled order should have a
  `trades` row. Orphans (filled order, no trade; trade, no parent
  order) → `trades` concern.
- **Side / size sanity.** Spot-check 3–5 orders: side matches signal
  direction; qty within the per-account `pos_size` cap in
  `config/accounts.yaml`; no absurd leverage. (This is a plumbing
  check, not a strategy-quality check — strategy quality is
  `/performance-review`.)
- **SL/TP wiring.** Each order should carry SL+TP metadata. Missing →
  `watch`; systematic absence → `concern`.
- **Repeated rejections.** Consecutive `failed_exchange` /
  `failed_risk_gate` / `borrow_unavailable` on one symbol → `orders`
  concern (something upstream wedged).
- **Monitoring cadence.** `run_monitor_tick` events on the documented
  cadence; long gaps → `monitoring` concern.
- **Strategy silence.** Every strategy enabled in
  `config/strategies.yaml` should emit per-tick `*_eval` events. An
  enabled strategy with **zero `*_eval`** for > 1h of an active session
  → `strategy_silence` concern. **`execution: shadow` strategies still
  run and still emit `*_eval`** — the silence check applies to them
  but trade-row checks do not.
- **State consistency.** For each account, YAML `mode` vs runtime
  `live` field in `runtime_status.json`. Drift → `state_consistency`
  concern.
- **Alert delivery.** Confirm the `AlertsQueue` is drained — known-trip
  events with no accompanying drain log → `alert_delivery` concern
  ("alerts queued, drainer silent — operator unnotified").

Status grades: `ok` (nothing to flag) / `watch` (bounded anomaly) /
`concern` (operator should look ⇒ `operator_attention_required: true`).
Overall: `healthy` (all ok) / `caution` (≥1 watch, no concern) /
`investigate` (any concern).

## Trainer service touch

Grade exactly one dimension: `trainer_service` ∈
`ok | watch | concern | skip`. `ok` when the timer is enabled+active
and the unit has not died with non-zero `ExecMainStatus`. Everything
else about the trainer (models, datasets, registry, training metrics)
is **`/ml-review`**'s job — do not duplicate it here.

The trainer is not a live-trading blocker. Don't set
`operator_attention_required` on a trainer-only issue unless an
`advisory`+/`live_approved` model is involved (and even then, that
finding belongs to `/ml-review` — this skill just notes "trainer
service stale, see /ml-review").

## Broker-account reachability (MANDATORY — 2026-06-29)

A supposed-to-be-live broker account reading **unreachable** (IB gateway
logged out, exchange API 401-ing, creds rotated out) is a money-at-risk
condition that must surface as a **loud, standalone flag** — not a line
buried in the report body. This section exists because the IB gateway was
in fact dark across one or more reviews and went unflagged.

**Scope — all declared-live, non-shelved accounts.** Check every account
with `mode: live` on a probeable exchange (`bybit` / `interactive_brokers`
/ `alpaca` / `oanda`). This excludes the intentionally-shelved dry accounts
(`ib_live` 2FA-blocked, `oanda_practice`) and the API-less `breakout_1`
prop bridge — the same set the in-process latch checks.

**How to read reachability** (any one is sufficient evidence of down):

- `GET /api/diag/exchange_positions` — per-account `positions: null` ⇒
  could-not-read (down); `[]` or a list ⇒ reachable.
- the in-process latch state file
  `runtime_logs/account_reachability_alert_state.json` (pull via
  `/api/diag/log_file` or read `account_reachability_alert.down_accounts()`)
  — any account with `down: true` is currently latched down.
- `GET /api/bot/accounts/balances` — `api_ok: false` for a live account.

**What to do when an account is down:**

1. It is a **MANDATORY** entry in the response's flags / a standalone
   high-priority Claude-channel ping (the in-process latch already pings
   Telegram on the cross-into-down; the review must ALSO surface it so a
   review run is never the thing that quietly skips it).
2. Recommend the fix inline: IB → `vm-ib-gateway-recover`; otherwise →
   check broker API/creds. If the down state is sustained, this is exactly
   the trigger to open/continue a remediation pass in THIS session.
3. Drive it — don't just note it. A live account dark for the whole window
   is a `concern`-grade finding with `operator_attention_required` set.

If all live accounts are reachable, say so explicitly ("all N live
accounts reachable") — an empty reachability finding must be a stated
verification, never an omission.

## DB integrity & validity

- **`db_integrity`** — the diag relay can't run `PRAGMA
  integrity_check`, so grade from journal recency + counts:
  `age_seconds` of the newest `trades`/`order_packages` row should be
  small during active sessions (hours-stale while signals fire →
  `concern`); table totals non-decreasing run-over-run (a drop →
  truncation/restore `concern`); large `-wal` with a small main DB →
  `watch`. Note "integrity_check not fetched (relay can't run PRAGMA)".
- **`data_validity`** — values are *sane*, not just present: no
  negative `position_size`/`pnl` where impossible, no null in required
  columns, timestamps monotonic (`opened_at ≤ closed_at`), closed
  trades carry an `exit_reason` + `pnl`, and net positions reconcile
  with open rows. Bad values → `watch`; systemic corruption signals →
  `concern` with `operator_attention_required: true`. Use the
  `db-wiring` skill's checks as the reference.
- **`db_write_path_integrity`** (Phase-4 guardrail) — run
  `scripts/check_db_integrity.py` (read-only, `mode=ro`) for the INV-1..5
  write-path invariants from
  `docs/audits/dashboard-truth-and-persistence-2026-06-16.md`. It separates a
  RECENT regression (`recent_count > 0` ⇒ `alert` ⇒ a live write-path bug, e.g.
  a row that just closed without `closed_at`/`pnl`/`account_class` or with a
  broken package link) from the LEGACY pre-backfill backlog (`total_count` only
  ⇒ informational, the P1-E backfill clears it). Grade any `recent_count > 0`
  as `concern`; legacy-only as `watch`/note. The hourly `ict-db-integrity.timer`
  already pings `[WARN] DB integrity: …` on a recent regression — cross-check
  that the alert fired. (Pull it via the diag relay or, when configured, run
  the checker directly against the live DB.)

## Sprint-doc review

Read the sprint logs under `docs/sprint-logs/` created since the last
review (newest few). For each, sanity-check: does it follow the
canonical template (`sprint-format` skill), does it report verified
reality rather than intent, and does any claim contradict a canonical
doc or the live state you just pulled? Record issues in
`sprint_doc_review[]` with severity `nit | drift | contradiction`. A
`contradiction` against a canonical doc is fixed in-place (Tier-1) or
logged to the backlog — never walked past.

## Compliance audit rotation (2026-06-02)

One repo section per review, rotated by day-of-week so the full repo is
audited against the **current** canonical rules
(`docs/CLAUDE-RULES-CANONICAL.md` § Generation Discipline) over a week.
This is the enforcement loop for Rule 2 (precedents-not-authoritative):
artifacts drift as rules evolve, and this rotation is how the drift
gets surfaced and queued for fix.

Pick the section by `weekday`:

| Weekday | Section |
|---|---|
| Mon | `docs/runbooks/` |
| Tue | `.github/workflows/` + `scripts/ops/` |
| Wed | `.claude/skills/` |
| Thu | `config/` |
| Fri | `src/units/accounts/` (broker integrations) |
| Sat | `src/runtime/` + `src/core/` |
| Sun | `src/units/strategies/` + `ml/` |

For each artifact in the day's section:

1. Run the bright-line scan from `before-asking-the-operator` (operator
   instructions that should be runner-dispatched) and
   `credentials-and-vm-mutations` (operator-attributed VM/credential
   work that should route through `sync-vm-secrets` or
   `system-actions`).
2. Cross-check against any rule in `docs/CLAUDE-RULES-CANONICAL.md`
   that the artifact category is subject to (tier, autonomy mandate,
   prime directive, generation discipline, ship-autonomously rule).
3. Per Rule 2 of Generation Discipline:
   - **Compliant** → no action.
   - **Non-compliant + the review session is shipping a fix for the
     containing system** → fix in the same PR.
   - **Non-compliant + non-blocking** → log to
     `docs/claude/health-review-backlog.json` with the artifact path,
     the specific rule it violates, the bright-line phrase or pattern
     observed, and a one-line suggested fix.

The audit findings appear in `compliance_audit` in the response JSON:

```json
"compliance_audit": {
  "section": "docs/runbooks/",
  "artifacts_scanned": 16,
  "findings": [
    {
      "artifact": "docs/runbooks/ib-integration.md",
      "rule": "before-asking-the-operator",
      "pattern": "operator-attributed systemd edit at line 87",
      "severity": "drift",
      "logged_to_backlog": "BL-20260603-001"
    }
  ]
}
```

This rotation does NOT touch artifacts outside the day's section — the
weekly cycle is the coverage guarantee, not a per-session full sweep.

## Orphan-events ingest (orphan is NEVER a resting status)

Operator directive (2026-06-24): an orphan trade row is a **red flag to be
reconciled**, not a status to accept. The trader writes one JSON line per
orphan-row creation to `runtime_logs/orphan_events.jsonl`
(`execution_diagnostics.enqueue_orphan_created_flag`: `account`, `symbol`,
`side`, `trade_id`, `origin`, `ts`) and fires a CRITICAL "initiate a
/system-review" Telegram red-flag at the same time.

Every health-review (and the master /system-review) MUST:

1. **Pull the tail** since the last review — `diag log_file?name=orphan_events`
   (relay) or the live VM file. Also cross-check the DB: any `trades` row still
   carrying an orphan marker (`setup_type='adopted_orphan'` /
   `strategy_name='orphan_adopt'`, or `status='orphaned'`) — query via the Data
   Explorer (`/api/bot/db/table/trades?filter_col=setup_type&filter_op=eq&filter_val=adopted_orphan`).
2. **For each orphan not already tracked**, append a `BL-…` item to
   `docs/claude/health-review-backlog.json` (origin, account/symbol, trade_id,
   the reconcile target if recoverable) so it is durably tracked — and **drive it
   to resolution**: reconcile to its real trade/order package, or, only after
   exhausting that, mark it explicitly `unreconciled` (never leave it resting as
   `adopted_orphan`).
3. **Flag loudly** in the review output if any orphan persisted unreconciled
   across the window — that is a standing failure of the no-resting-orphan
   invariant, not a routine item.

## Draining the backlog

Read `docs/claude/health-review-backlog.json` — the parking lot for
**system bugs, wiring gaps, minor doc drift** that prior sessions
noticed but didn't fix. (`/performance-review` and `/ml-review` have
their own backlogs — do not touch them here.) For each open item:

1. Triage: is it still valid? does its trigger apply now?
2. **Fix what you can** within this skill's allowed writes (docs, the
   backlog file itself). Anything needing a code/config change is
   *not* fixed here — restate it for the operator in
   `recommended_action`.
3. Edit the backlog file: mark fixed items resolved (or remove them),
   keep deferred items, drop invalid ones. Record each action in the
   response's `backlog_drain[]`.

## Posting to the Claude channel

Every health-review run ends with a **one-line update to the Claude
channel** (`@claude_ict_comms_bot`), per
[`docs/claude/telegram-pings.md`](../../docs/claude/telegram-pings.md).

**Primary path — `send-ping` system-action (use this).** Open a
`system-action`-labelled GitHub issue:

```
action: send-ping
target: claude
priority: normal      # or 'high' if operator_attention_required
message: /health-review — <overall_assessment>: <one-line summary>. <N> concerns, <M> watches. <recommended_action or "no action">.
```

The `system-actions` workflow SSHes to the VM and runs
`scripts/ops/send_ping_action.sh`; the bridge drains within ~5s.
Latency: ~30–60s, no git push needed. Full contract:
`docs/claude/system-actions.md` § `send-ping`.

**Fallback path — `pending-pings.jsonl`.** Only if the issue path is
unavailable: append a line to `docs/claude/pending-pings.jsonl` and
commit. The VM git-sync timer picks it up within ≤5 min. Hash-based
dedup prevents re-fires.

The ping is a status beacon, not the review itself — keep it ≤200
chars, cite the overall grade + concern count, and point the operator
at the response JSON (in chat) for detail.

## Output

Emit a single JSON object conforming to
`comms/schema/health_review_response.template.json`. The narrowed
shape (post-2026-05-26 split):

- `findings.*` — pipeline + DB + service dimensions only (no
  `trainer_models`).
- `sprint_doc_review[]`.
- `backlog_drain[]` — actions taken on
  `docs/claude/health-review-backlog.json`.
- `anomalies[]` — free-form notable items.
- `recommended_action` + `operator_attention_required`.

`trade_decision_grades[]` and `model_status[]` are **removed** from
this skill's output — they live in `/performance-review` and
`/ml-review` respectively.

Set `reviewed_at` to now (UTC ISO-8601), `reviewer` to `claude`. Each
`note` ≤120 chars, citing specifics (counts, ages, symbols/qtys) so
the operator can verify fast.

## What you DO write (and what you don't)

**Write:**
- Edit `docs/claude/health-review-backlog.json` to drain it.
- Fix Tier-1 doc contradictions surfaced by the sprint-doc / backlog
  pass.
- Append the Claude-channel ping (via `send-ping` system-action, or
  fallback `docs/claude/pending-pings.jsonl`).
- The read-only diag-trigger issues (`vm-diag-request`,
  `trainer-vm-diag-request`, `vm-web-api-recover`) — they auto-close.

**Do NOT:**
- Touch `src/`, `config/`, or any live-path file. Reviews don't trade.
- Append to `comms/claude_strategy_scores.jsonl` (that belongs to
  `/performance-review` now).
- Modify `docs/claude/performance-review-backlog.json` or
  `docs/claude/ml-review-backlog.json` (those belong to their
  respective skills).
- Modify `comms/follow_ups.json` (deferred until the comms cleanup
  session).
- Ask the operator to paste/download/SSH a snapshot — autonomy-mandate
  failure. Pull it yourself.
- Ask scoping questions — the scope is fixed (this file).

## If the relays are unreachable

The only legitimate stop condition. If the live diag relay fails even
after a `vm-web-api-recover` retry, emit the partial review with
`api_errors` = concern, `operator_attention_required: true`, and a note
that the live pull couldn't be performed — and still drain the backlog
+ do the sprint-doc review + post the Claude-channel ping (those are
repo-local and don't need the VM). Do not synthesize live findings
without evidence.
