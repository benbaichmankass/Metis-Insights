# Sprint Log: S-PROP-MONITOR-PULSE-2026-06-22

## Date Range
- Start: 2026-06-22
- End: 2026-06-22

## Objective
- Primary goal: Add a periodic "still monitoring" pulse for every OPEN prop trade — a reassurance heartbeat (default every 15 min) so the operator knows the system is actively tracking the position between report-backs, without replacing the real-time prop_fill/prop_closed events.
- Secondary goals: Make the pulse self-verifiable; give a chat-driven way to record prop fills; close the manual-bridge loop so future tickets always elicit an ingestable report-back.

## Tier
- Tier 2 (trader-loop runtime change + new notification path); doc/tooling pieces Tier 1.
- Justification: `src/main.py` loop hook + a new FCM/Telegram notification path is runtime; the relay workflow, journal logging, and ticket-text change are observability/tooling/content. No order-path or risk/strategy/account-mode change.

## Starting Context
- Active roadmap items: M6/M7 Breakout prop manual-bridge (S-DTP-EXITPLAN row; scalable prop-accounts architecture).
- Prior sprint reference: S-PROP-INBOUND-LOOP (prop_fill/prop_closed inbound ingest, P2/P3).
- Known risks at start: prop is a manual bridge with no broker feed; the per-tick order_monitor never sees prop positions.

## Repo State Checked
- Branch or commit reviewed: `main` (bb94655 → be98f41 across the session).
- Deployment state reviewed: live trader `ict-bot-arm`; restarted 13:56 (#4166) and 15:41 (#4181+#4192).
- Canonical docs reviewed: CLAUDE.md, CLAUDE-RULES-CANONICAL, ARCHITECTURE-CANONICAL, ROADMAP.

## Files and Systems Inspected
- Code files inspected: `src/prop/{prop_journal,prop_report,prop_reconcile,breakout_notify,breakout_executor,breakout_ticket,multi_account_ticket}.py`, `src/runtime/mobile_push/{__init__,notifier,event_kinds}.py`, `src/runtime/order_monitor.py`, `src/main.py`, `src/utils/paths.py`.
- Config files inspected: `config/accounts.yaml` (prop account `breakout_1`).
- Deployment files inspected: `.github/workflows/{vm-diag-snapshot,bootstrap-labels,system-actions,vm-devnull-deploy-bootstrap}.yml`.
- Docs inspected: CLAUDE.md, docs/claude/diag-relay.md, health-review-backlog.json.
- Services or timers inspected: `ict-trader-live.service`, `ict-web-api.service`, `ict-devnull-guard.timer`.
- GitHub Actions workflows inspected: vm-diag-snapshot, system-actions, bootstrap-labels.

## Work Completed
- **#4166** — `src/prop/prop_monitor_pulse.py` (new): derives open prop positions from `prop_fills` (latest status open/filled per ticket / account+symbol+direction key), enriches levels from the linked ticket, rate-limits per position via `runtime_logs/prop_monitor_pulse.json` (pruned to live keys). New `prop_monitor` event kind. `emit_prop_monitor_pulse`/`render_monitor_message` in breakout_notify. Hooked into `src/main.py` after the order_monitor tick. Baseline; cadence knob `PROP_MONITOR_PULSE_SECONDS` (default 900, `<=0` pauses).
- **#4181** — `prop_monitor_pulse` INFO logging (per-fired-pulse + per-tick `open/fired/skipped` scan summary) so the pulse is visible in `journalctl`; added `prop/{fills,tickets,status,reconcile}` to the `vm-diag-snapshot` read-only `/api/bot/*` allowlist.
- **#4189** — `.github/workflows/prop-report.yml` (new): issue-driven POST relay to `/api/bot/prop/report` (the diag relay is GET-only). Injection-safe (base64 hop, jq object-validation, on-VM token). + `prop-report` label.
- **#4192** — embedded the report-back JSON block (placed/skipped/closed, pre-filled account_id/symbol/direction/ticket_id) into the outbound trade-setup ticket (`render_ticket`), threaded account_id+ticket_id through `emit_prop_signal`/`ticket_to_fields`/`emit_prop_ticket` + the multi-account renderer — closes the manual-bridge loop (root cause of the empty `prop_fills`).
- Incidental: repaired the live VM's clobbered `/dev/null` (was blocking all deploys) via `vm-devnull-deploy-bootstrap`; `ict-devnull-guard.timer` now active.

## Validation Performed
- Tests run: `tests/test_prop_monitor_pulse.py` (9), `tests/test_prop_breakout_ticket.py` + `test_prop_breakout_notify.py` + `test_breakout_prop_wiring.py` (21 combined), `test_workflow_yaml_valid.py`, `test_system_actions_workflow.py` — all pass; ruff clean. CI green on all four PRs.
- Dry-runs or staging checks: n/a.
- Manual code verification: reviewed the generated `prop-report.yml` for injection safety before merge.
- Live verification: recorded the operator's real open ETHUSDT long (breakout_1, entry 1767.3, qty 0.73) via the relay (HTTP 200, matched ticket `prop-manual-dca8e89072d7`, fired PROP FILL); journal confirms `prop_monitor_pulse: open=1 ... (interval=900s)` and the first pulse fired; operator confirmed the Telegram pings landed.
- Gaps not yet verified: none material.

## Documentation Updated
- Rules doc updates: CLAUDE.md env-var table (`PROP_MONITOR_PULSE_SECONDS`) + PM-side "Workarounds shipped" bullet (prop-report relay).
- Architecture doc updates: none required.
- Trade pipeline doc updates: none.
- Roadmap updates: this row (S-PROP-MONITOR-PULSE).
- GitHub Actions doc updates: docs/claude/diag-relay.md (prop report-back write counterpart).
- Subsystem doc updates: event_kinds docstring (PROP_MONITOR).
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- None. `canonical-doc-coherence` passes; touched docs match reality.

## Risks and Follow-Ups
- Remaining technical risks: none material; pulse is best-effort + isolated, never affects the order path.
- Remaining product decisions (Tier 3): none.
- Blockers: none.

## Deferred Items
- `prop-report` relay rejects an issue body that wraps the JSON in prose (it validates the whole body as one JSON object). Backlog `BL-20260622-PROP-REPORT-PROSE`.
- `prop_monitor` event kind not mirrored in Android `EventKind.kt` (delivers on a default channel). Backlog `BL-20260622-PROP-MONITOR-ANDROID`.

## Next Recommended Sprint
- Drain the two backlog items; consider graduating the report-back loop to an automated executor.
