# M1 P1-A..D follow-ups + M2 formal close-out — summary

> **Sprint type:** docs + small comms-hardening sprint (no live-trading code path changed).
> **Branch:** `claude/review-roadmap-hIO75`. **Closed:** 2026-05-08.
> **Goal:** Land the four P1 follow-ups from the S-048 fresh M1 audit (per
> `docs/audits/M1-comms-audit-followups-fresh.md`) and formally close M2
> (Web app source of truth — backend) whose work shipped under S-013 + S-014
> but never had its close paperwork filed.

## Outcome at a glance

| Item | Status |
|---|---|
| **P1-A** — Workplan correction (one-way ClaudeBot description in `docs/claude/workplan.md`) | ✅ Already landed pre-branch on `claude/update-roadmap-status-ZnLM9` (verified clean here) |
| **P1-D** — Operator commands `/new_session <sprint_id>` and `/test <strategy>` | ✅ This branch |
| **P1-B** — Stuck-request recovery alerts (one-time stuck alert + final pre-EXPIRED alert) | ✅ This branch |
| **P1-C** — Auto-hourly snapshot timer (`deploy/ict-hourly-snapshot.{timer,service}` + flock) | ✅ This branch |
| **M2** — Formal close-out (paperwork-only; backend was already complete) | ✅ This branch |
| P2 hygiene cluster | 📋 Filed for a future Janitor sprint per audit follow-ups doc |

## What landed

### P1-D — operator commands

  - `src/comms/templates.py` (new) — `make_new_session_request`,
    `make_test_strategy_request`, `commit_subject_for`. Operator-initiated
    requests (`source.actor == "operator"`) carrying the sprint id /
    strategy name in `source.task` and topic.
  - `src/bot/telegram_query_bot.py` — added `cmd_new_session` and
    `cmd_test_strategy` handlers (registered alongside the existing
    operator commands), plus matching `BotCommandSpec` rows for the
    `sprint` help category. Both handlers go through
    `RequestStore.create` for persistence and `GitPusher.commit_and_push`
    (gated by `COMMS_PUSH_ENABLED`) for propagation. The Telegram
    acknowledgement always includes the request id.
  - `tests/test_s051_session_test_commands.py` (new) — pin artifact
    creation, file naming, schema validity, commit-subject prefix.
  - `docs/claude/comms-architecture.md` — new § 11 documenting both
    commands; previous § 11 (References) renumbered to § 12.

### P1-B — stuck-request recovery alerts

  - `comms/schema/request.schema.json` — new top-level optional
    `stuck_alert_threshold` integer field (seconds; min 60, default
    86400 = 24 h), and new bot-managed `delivery.stuck_alert_sent_at`
    field for one-time-per-request idempotency.
  - `src/comms/models.py` — `Request.stuck_alert_threshold`,
    `effective_stuck_alert_threshold_s()`, `is_stuck()`, and
    `stuck_alert_already_sent()`. `DEFAULT_STUCK_ALERT_THRESHOLD_S`
    + `MIN_STUCK_ALERT_THRESHOLD_S` constants exported.
  - `src/bot/comms_handler.py` — `CommsPoller.poll_once` now fires
    a one-time stuck alert and a final pre-EXPIRED alert. A failed
    Telegram send leaves the stuck-alert marker unset (cycle retries
    next pass); the EXPIRED transition goes through even if the
    Telegram alert fails (silent expiry is worse than a missed alert).
  - `tests/test_s052_stuck_request_recovery.py` (new) — pin both
    alert paths + schema field round-trip + telegram-failure
    semantics.
  - `comms/README.md` — replaced the "manually edit the file"
    workaround in § "Stuck request? How to recover" with the new
    bot-side alert paths.

### P1-C — auto-hourly snapshot timer

  - `deploy/ict-hourly-snapshot.timer` (new) — `OnCalendar=hourly`
    with `RandomizedDelaySec=60` and `Persistent=true`.
  - `deploy/ict-hourly-snapshot.service` (new) — `Type=oneshot`
    invoking `scripts/send_hourly_now.py`. `SuccessExitStatus=0 75`
    so a benign flock race exits clean.
  - `scripts/send_hourly_now.py` — wrapped the dispatch in an
    `fcntl.flock` exclusive lock on `/tmp/ict-hourly-snapshot.lock`
    (override via `ICT_HOURLY_LOCK_PATH`). A second instance that
    races the first exits with code 75 (EX_TEMPFAIL) without
    dispatching.
  - `tests/test_s053_hourly_snapshot.py` (new) — pin lock acquisition,
    release on success, busy-exit code, and unit-file presence in
    `deploy/`.
  - `docs/runbooks/hourly-snapshot.md` (new) — operator install
    instructions + verification steps + troubleshooting.

### M2 close-out

  - `docs/claude/milestone-state.md` — flipped M2 from 🔄 PARTIAL to
    ✅ CLOSED in the M0..M10 status table; added M2 + this sprint to
    "Recently closed milestones"; updated the M2 deferred-close note.
  - `ROADMAP.md` — flipped M2 from 🔄 PARTIAL to ✅ CLOSED; flipped
    M1 from 🔄 PARTIAL to ✅ CLOSED; refreshed the "Last Updated"
    block.
  - This summary file.

## Architecture decisions

1. **Operator-initiated comms artifacts use `source.actor = "operator"`.**
   Distinct from Claude-authored requests so future consumers (M5,
   bootstrap logic) can filter on actor without parsing the topic.
2. **Stuck alert is advisory, not state-advancing.** A request stays
   in `sent` after the alert; the operator decides whether to reply,
   re-pend, or cancel. Re-alerting is suppressed via
   `delivery.stuck_alert_sent_at`.
3. **Expiry alert fires before the EXPIRED transition.** A failed
   Telegram send does NOT block the transition — silent expiry is
   the worse failure mode. The `request_expired` log event + history
   entry remain auditable.
4. **Hourly timer flock prevents double-fire.** `OnCalendar=hourly`
   plus `RandomizedDelaySec=60` plus `Persistent=true` can in theory
   deliver two firings (boot replay + on-time fire); the flock catches
   the race. Service unit's `SuccessExitStatus=0 75` makes the race
   benign in `systemctl status`.
5. **M2 close is paperwork-only.** The backend feed (`/api/bot/*`,
   CORS, Vercel rewrite proxy) shipped under S-013 + S-014; no new
   code in this PR. The diagnostic surface (`/api/diag/*`) is a
   separate workstream and explicitly stays outside M2 scope.

## What was NOT done (deliberate)

  - **Auto-resend after stuck-alert extension** — out of P1-B scope;
    filed for a future sprint per audit follow-ups.
  - **`pending-pings.jsonl` recovery** — different surface; the audit
    correction reaffirmed it's not in M1's two-way comms scope.
  - **M5 backtest workflow consumer** for `/test` — out of P1-D
    scope; M5 is a separate sprint.
  - **CommsPoller filter for operator-initiated artifacts** — out
    of P1-D scope. Operator-initiated requests still go through the
    normal deliver path (the operator sees a redundant Telegram menu
    after typing `/new_session` or `/test`); this is mildly awkward
    but harmless and can be tightened in a hardening pass.
  - **P2 hygiene cluster** — schema-drift envelope, comms log
    retention policy, missing test pins, command-name cosmetics.
    Bundled for a future Janitor sprint per the audit follow-ups
    doc.

## References

  - [`docs/audits/M1-comms-audit-2026-05-07-fresh.md`](../audits/M1-comms-audit-2026-05-07-fresh.md) — S-048 fresh audit (verdict PARTIAL).
  - [`docs/audits/M1-comms-audit-followups-fresh.md`](../audits/M1-comms-audit-followups-fresh.md) — P1-A..D scope.
  - [`docs/claude/comms-architecture.md`](../claude/comms-architecture.md) — operator-initiated commands § 11.
  - [`docs/runbooks/hourly-snapshot.md`](../runbooks/hourly-snapshot.md) — P1-C operator install runbook.
  - [`docs/claude/workplan.md`](../claude/workplan.md) — § "Telegram bots / @claude_ict_comms_bot / ClaudeBot workflow" (P1-A correction).
