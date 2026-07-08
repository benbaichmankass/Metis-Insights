# Sprint Log: S-NOTIF-STREAMLINE-2026-07-08

## Date Range
- Start: 2026-07-08
- End: 2026-07-08

## Objective
- Primary goal: **Streamline the operator notification surface** per operator
  direction — make the hourly snapshot a single Telegram message, consolidate
  the prop-monitor pulse to one hourly ping, and add a durable operator-alerts
  banner feed so `execution_diagnostics.enqueue_*` alerts surface on the app
  `/api/bot/notifications` banner (not only Telegram).
- Secondary goals: fix the live bugs the notification noise surfaced —
  (a) the recurring "won't flatten" QQQ Alpaca close-failure, (b) a prop-pulse
  phantom-open showing a *closed* trade as still-monitored, and (c) the trainer
  VM's recurring OOM-kill (MB-20260705-TRAINER-OOM).

## Tier
- Mixed: Tier 1 (notifications/observability/docs/tests) + Tier 2 (order-path
  close code + live deploy) + trainer-VM autonomous ops.
- Justification: notification rendering and the app-banner feed are Tier-1
  observability; the `AlpacaClient.close()` change and the `pull-and-deploy` are
  Tier-2 order-path/deploy (operator-directed this session — "merge and deploy
  and investigate why we're not able to close it"); the trainer swap resize is
  autonomous trainer-VM territory per the VM-authority split.

## Starting Context
- Active roadmap items: M15 execution-path hardening (the 2026-07-07
  Alpaca-pipeline audit, `S-ALPACA-PIPELINE-AUDIT-2026-07-07`); notification
  streamlining (operator-requested 2026-07-08).
- Prior sprint reference: `S-ALPACA-PIPELINE-AUDIT-2026-07-07.md` (the SLV
  phantom-close remediation, 4 merged PRs) — this session is its continuation
  plus the notification track.
- Known risks at start: live money at risk on the money accounts; the QQQ
  stuck-close was generating ~15-min operator noise; the trainer had gone
  SSH-dead post-OOM across prior reviews.

## Repo State Checked
- Branch or commit reviewed: `main` (session start ~`9accd7d`; the notification
  fixes merged as `d3a26335`; trainer swap durable as `cbd9fcd`; backlog note as
  `205f115`). Development branch `claude/full-system-review-2rv55p`.
- Deployment state reviewed: verified via `/api/diag/version` — live web-api ran
  `9accd7de` pre-deploy and `d3a26335` post-`pull-and-deploy`; trader restarted
  onto `d3a26335` at 16:32:20 UTC.
- Canonical docs reviewed: `CLAUDE.md`, `docs/CLAUDE-RULES-CANONICAL.md`,
  `docs/ARCHITECTURE-CANONICAL.md`, `ROADMAP.md`, `docs/ml/training-center.md`.

## Files and Systems Inspected
- Code files inspected: `src/runtime/hourly_report.py`,
  `scripts/send_hourly_now.py`, `src/prop/prop_monitor_pulse.py`,
  `src/units/accounts/alpaca_client.py` (`close()`,
  `_cancel_open_orders_for_symbol`, `_open_orders_for_symbol`, `positions()`),
  `src/runtime/order_monitor.py` (`_send_close_to_exchange`, `_apply_update`
  close path, `_check_broker_naked_equity_positions`),
  `src/units/ui/telegram_format.py` (`render_html`/`_truncate`).
- Config files inspected: `deploy/training-vm-cloud-init.yaml`
  (`ict-trainer.service` memory caps + runcmd).
- Deployment files inspected: `deploy/training-vm-cloud-init.yaml`;
  `/etc/systemd/system/ict-trainer.service.d/memory.conf` (on the trainer VM,
  via relay).
- Docs inspected: `docs/ml/training-center.md`,
  `docs/claude/ml-review-backlog.json`, `docs/claude/health-review-backlog.json`.
- Services or timers inspected (via relay): live `ict-trader-live.service` +
  `ict-web-api.service`; trainer `ict-trainer.service` (+ `.timer`,
  `-catchup`, `-publish`, `-forecast`, `ict-drift-retrain`).
- GitHub Actions workflows inspected: `vm-diag-snapshot.yml` (batched diag
  relay), `trainer-vm-diag.yml`, `system-actions.yml` (`pull-and-deploy`).

## Work Completed
- **Durable operator-alerts banner feed (PR #5979, merged).** Added a
  bounded-ring `runtime_logs/operator_alerts.jsonl` writer
  (`execution_diagnostics._append_operator_alert`) wired into the operational
  `enqueue_*` functions, and an `_operator_alert_banners()` source in
  `notifications.py` so `enqueue_*` alerts (incl. the QQQ close-failure) surface
  on `/api/bot/notifications`. 6 new tests.
- **Watchdog 2-observation flat-confirm (PR #5982, merged).** The stuck-strategy
  watchdog no longer finalizes a position as flat on a single reading;
  `_PENDING_WATCHDOG_FLAT_CONFIRM` requires a second confirming observation.
- **QQQ re-arm-vs-close guard + first one-message hourly attempt (PR #5984,
  merged, `9accd7d`).** `_TICK_ACTIVE_CLOSE_SYMBOLS` stops
  `_check_broker_naked_equity_positions` re-arming an OCO on the *same tick* a
  close is running; `send_hourly_now.py` concatenated the two hourly halves.
- **Single-message hourly snapshot (PR #5997, merged, deployed `d3a26335`).**
  The #5984 concatenation still spilled to two messages because each half is
  already `_truncate`-capped at 4096. New `build_combined_hourly_report` renders
  strategy + training + accounts through ONE `render_html` call, so the shared
  truncation guarantees exactly one Telegram message. `send_hourly_now.py`
  dispatches the single builder.
- **Prop-pulse phantom-open fix (PR #5997).** `prop_monitor_pulse._position_key`
  now canonicalizes direction (`buy→long`, `sell→short`) via
  `_canonical_direction`, so a position reported `buy` on the open and `long` on
  the close shares one akd key and its close is no longer invisible
  (BL-20260708-PROP-PULSE-DIRECTION-ALIAS). Logged the closed ETHUSD prop trade
  (fill id 19, net −78.76) via the `prop-report` relay.
- **Alpaca close flatten-retry (PR #5997).** `AlpacaClient.close()` retries the
  `DELETE /v2/positions` within `ALPACA_FLATTEN_RETRY_S` (default 6s), re-cancelling
  any freshly re-armed resting order, on an `insufficient qty available` reject.
- **Trainer OOM fix (live + PR #6007, merged `cbd9fcd`).** Root-caused the
  recurring `ict-trainer.service` OOM-kills (2026-07-07 08:48Z, 2026-07-08
  07:06Z) to the service **main process** exceeding `MemoryMax=5G` + only-2G
  swap (~7G ceiling) — `OOMPolicy=continue` only contains a per-manifest
  *subprocess* OOM. Host memory was otherwise healthy (5.8G total, ~370M used).
  Grew `/swapfile` **2G → 8G** live on the trainer (fstab-persisted, verified
  `Swap: 8.0Gi`); made it durable in the trainer cloud-init `runcmd` +
  `docs/ml/training-center.md`.

## Validation Performed
- Tests run (sandbox): `test_prop_monitor_pulse.py` (13 pass),
  `test_hourly_dispatch.py` + `test_hourly_report.py` (37 pass),
  `test_s053_hourly_snapshot.py`, `test_alpaca_wiring.py` (38 pass),
  `test_alpaca_naked_rearm.py` + `test_p3_close_wiring.py` (45 pass); combined
  touched-suite run 71 pass. `ruff` clean; silent-empty guard passes locally.
- CI: PR #5997 — all 19 checks green (incl. pytest-run) before merge;
  PR #6007 — 15 checks green; PR #6009 — 11 checks green.
- Deploy verification: `/api/diag/version == d3a26335` post-deploy; trader
  `active`. Trainer swap: relay run confirmed `Swap: 8.0Gi` after, fstab entry
  present, disk 45G/38G used/7.8G free.
- **Gaps not yet verified:**
  - **The QQQ paper position did NOT flatten.** Post-deploy re-check (diag #6008,
    16:56Z) shows QQQ/`alpaca_paper` still open (16 shares, −$190) with fresh
    `available: 0` close failures after the restart — the flatten-retry did not
    free the shares (a resting protective OCO stop-leg reserves them; the cancel→
    `qty_available` release outlasts the 6s window and/or the naked-sweep re-arms
    between attempts). Logged as BL-20260708-ALPACA-CLOSE-QTY-AVAILABLE. Paper
    money — deprioritized.
  - **Trainer OOM fix not yet confirmed end-to-end.** The next nightly
    `ict-trainer.timer` cycle (~2026-07-09 00:43Z) completing without an oom-kill
    is the real verification; a best-effort self-check is scheduled but the
    container is ephemeral (may not fire). Backlog-tracked either way.
  - Earlier-session banner/trainer-down work (see below) is cited from repo
    state + CLAUDE.md, not personally re-verified live this pass.

## Documentation Updated
- Rules doc updates: none needed.
- Architecture doc updates: none needed (no schema/contract change).
- Trade pipeline doc updates (`docs/TRADE-PIPELINE.md`): none (no pipeline stage
  changed; close-path change is a bounded retry inside the existing stage).
- Roadmap updates: this log's status row (see close-out).
- GitHub Actions doc updates: none.
- Subsystem doc updates: `docs/ml/training-center.md` — added the swap-headroom
  bullet (2G→8G, why `OOMPolicy=continue` didn't cover the main-process kill).
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- None. `scripts/ci/check_canonical_doc_coherence.py` passes all 4 structural
  invariants. The new `ALPACA_FLATTEN_RETRY_S` is consistent with its siblings
  (`ALPACA_CLOSE_CONFIRM_S`/`ALPACA_CANCEL_SETTLE_S` are call-site + ROADMAP/
  sprint-log documented, not in the CLAUDE.md env table).

## Risks and Follow-Ups
- Remaining technical risks: the Alpaca close-race is a real close-path defect
  that could also strand a *real* `alpaca_live` position on a slow cancel — the
  proper fix (gate the flatten on the position's actual `qty_available`, needs
  read-only order/qty broker visibility) is logged
  (BL-20260708-ALPACA-CLOSE-QTY-AVAILABLE).
- Remaining product decisions (Tier 3): none open from this session.
- Blockers: none.

## Deferred Items
- **QQQ / Alpaca close `qty_available`-gated flatten** — the correct fix, plus a
  possible read-only `/api/diag/exchange_orders` to see what holds the shares.
  (BL-20260708-ALPACA-CLOSE-QTY-AVAILABLE, health-review backlog.)
- **Trainer deeper OOM fix** — run each manifest in its own subprocess so the
  orchestrator's RSS stays low and a single manifest OOM is fully contained by
  `OOMPolicy=continue` (avoids swap-thrash). (MB-20260705-TRAINER-OOM update,
  ml-review backlog.)
- **Confirm `dataset_builds.jsonl` path** (original path check MISSING) —
  carried in the same ml-review item.

## Next Recommended Sprint
- Suggested next: confirm the trainer nightly cycle completed OOM-free (mark
  MB-20260705-TRAINER-OOM resolved or escalate to the subprocess-isolation fix),
  then implement the `qty_available`-gated Alpaca flatten.
- Why next: the trainer verification is time-bound (next 00:43Z cycle) and the
  Alpaca close-race is a latent real-money risk.
- Required verification before starting: trainer relay pull of the post-cycle
  `ict-trainer.service` result + `free`/`swapon`; a `/api/diag/exchange_orders`
  (or Alpaca MCP) read of what holds the QQQ shares before touching `close()`.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries (post-compaction
      files read in full; earlier-session items flagged as repo-state/CLAUDE.md-cited).
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage changed, so `docs/TRADE-PIPELINE.md` + Trade Process tab
      were not touched (bounded retry inside the existing close stage).
- [x] Roadmap status was checked (updated at close-out).
- [x] Contradictions were recorded (none found; coherence checker green).
- [x] Remaining unknowns were stated clearly (QQQ not flattened; trainer cycle
      not yet end-to-end verified).
