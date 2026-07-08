# System-actions — tiered production-mutation bridge

> Companion to `docs/claude/vm-operator-mode.md` (VM-resident `/vm`
> tiers) and `docs/claude/diag-relay.md` (PM-side **read-only**
> bridge). This doc covers the third axis: a **narrow, allowlisted
> set of mutating actions** a PM-side / web-sandbox session can drive
> on the VM via the GitHub Actions workflow `system-actions`.

---

## 1. Why this exists

Before this surface, a PM-side session (web sandbox, dev laptop) had
two mutually exclusive options when something went wrong on the VM:

1. **Read-only diag** (`/api/diag/*` via `vm-diag-snapshot.yml`) — fine
   for diagnosis, useless for recovery.
2. **Wake the operator on Telegram** and ask them to SSH in — the
   only mutation path, but adds human latency to every recovery.

`system-actions.yml` is the small, audited middle path: a fixed
allowlist of recovery / hygiene actions that don't touch strategy
behaviour, with pre/post verification baked into the workflow.

It does **not** replace the operator. Tier-2 actions (anything that
restarts a live runtime or reboots the box) still require the
operator to either click "Run workflow" themselves, **or to grant
explicit in-conversation Tier-2 approval that Claude carries into the
issue-driven dispatch** — see § 4.

Two dispatch paths, identical allowlist + audit:

- **`workflow_dispatch`** — operator clicks "Run workflow" in the
  Actions UI. The original path; still available.
- **Issue-driven** — sandbox session opens an issue with label
  `system-action`, body encoding `action: <name>` + `reason: <text>`.
  The workflow runs, posts the result back as an issue comment, and
  closes the issue. Same allowlist enforcement, same audit artifact.
  Required when the sandbox needs to dispatch autonomously and `gh`
  / `run_workflow` MCP tools are unavailable.

---

## 2. Allowlist

Adding an action requires a PR that updates this doc, the workflow's
`inputs.action.options` list, the wrapper mapping in
`system-actions.yml`, the priority case in
`scripts/ops/notify_run.sh`, and the `EXPECTED_ACTIONS` constant in
`tests/ops/test_operator_actions_workflow.py`.

| Action | Tier | Wrapper | Mutates? |
|---|---|---|---|
| `status-check` | 1 | `scripts/ops/status_check.sh` | no |
| `list-listening-ports` | 1 | `scripts/ops/list_listening_ports.sh` | no |
| `gateway-logs` | 1 | `scripts/ops/gateway_logs.sh` | no |
| `pull-latest-logs` | 1 | `scripts/ops/pull_logs.sh` | no |
| `pull-and-deploy` | 2 | `scripts/ops/pull_and_deploy.sh` | git worktree + systemd units |
| `restart-bot-service` | 2 | `scripts/ops/restart_bot.sh` | systemd unit only |
| `reboot-vm` | 2 (last resort) | `scripts/ops/reboot_vm.sh` | full host |
| `enable-closed-flat-invariant` | 2 | `scripts/ops/enable_closed_flat_invariant.sh` | `.env` (`CLOSED_FLAT_INVARIANT_ENABLED=true`) + restart `ict-trader-live.service` |
| `disable-closed-flat-invariant` | 2 | `scripts/ops/disable_closed_flat_invariant.sh` | `.env` (remove `CLOSED_FLAT_INVARIANT_ENABLED`) + restart `ict-trader-live.service` |
| `enable-m5-consumer` | 2 | `scripts/ops/enable_m5_consumer.sh` | `.env` (`M5_CONSUMER_ENABLED=1`) + restart `ict-telegram-bot.service` |
| `disable-m5-consumer` | 2 | `scripts/ops/disable_m5_consumer.sh` | `.env` (`M5_CONSUMER_ENABLED=0`) + restart `ict-telegram-bot.service` |
| `set-mobile-push-secrets` | 2 | `scripts/ops/set_mobile_push_secrets.sh` | `.env` (`FCM_SERVICE_ACCOUNT_JSON=<value>`) + restart `ict-trader-live.service` — thin wrapper around `set-env` that pins `env_key=FCM_SERVICE_ACCOUNT_JSON` + `service=ict-trader-live.service` and pulls the value from `secrets.FCM_SERVICE_ACCOUNT_JSON`. Use this to rotate the FCM service-account credential without the chance of accidentally targeting the wrong env key or unit. The credential never transits the issue body or run log. No params. |
| `enable-insights-generator` | 2 | `scripts/ops/enable_insights_generator.sh` | `systemctl daemon-reload` + `systemctl enable --now ict-insights-generator.timer` — activates the M13 S1 AI Analyst generator timer so `runtime_logs/insights/*.json` cache files start filling every ~10 min. Read-only of the trader; never touches `config/*.yaml`, the order path, or `ict-trader-live.service`. Prereq: the unit files must already be installed on the VM (auto-installed by `scripts/install_systemd_units.sh` during the prior `pull-and-deploy`). Idempotent. |
| `disable-insights-generator` | 2 | `scripts/ops/disable_insights_generator.sh` | `systemctl disable --now ict-insights-generator.timer` — stops the timer. Hard disable; a soft disable (timer still scheduled but each fire exits immediately) is `INSIGHTS_ENABLED=0` in `.env`, which the runbook documents. Idempotent. |
| `inspect-insights` | 1 | `scripts/ops/inspect_insights.sh` | Tier-1 read-only diagnostic for the M13 AI Analyst. Reports the cache dir contents (`ls -la runtime_logs/insights/`), a head sample of each cache file, total + last-24h count of `insights_history`, the 10 most-recent history rows, monthly `insights_usage` spend + per-endpoint split, the timer + service systemctl state, the next + last fire, and the last 50 journal lines. No DB writes, no live-trading side effects. Used to verify activation after `enable-insights-generator` and during routine health-reviews. |
| `kick-insights` | 1 | `scripts/ops/kick_insights.sh` | Tier-1 manual-fire: runs `systemctl start ict-insights-generator.service` once, off the timer schedule. The unit is a oneshot so the wrapper completes synchronously; the action's comment-back includes the last 80 journal lines + the 5 newest `insights_usage` + `insights_history` rows. Useful for verifying provider changes (e.g. enabling the Gemini API in GCP) without waiting up to 15 min for the next scheduled fire. Same write surface as a timer-fired cycle — no other side effects. |
| `enable-signal-dual-write` | 2 | `scripts/ops/enable_signal_dual_write.sh` | `.env` (`SIGNAL_DUAL_WRITE_DISABLED=false`) + restart `ict-trader-live.service` — hydrates `trade_journal.db::signals` per eval (S-034). Adds a SQLite write on the trading hot path; re-enable only when the table is needed. |
| `disable-signal-dual-write` | 2 | `scripts/ops/disable_signal_dual_write.sh` | `.env` (`SIGNAL_DUAL_WRITE_DISABLED=true`) + restart `ict-trader-live.service` — rollback / pipeline-lag escape hatch (JSONL stays the source of truth). |
| `backfill-pnl-nulls` | 2 | `scripts/ops/backfill_pnl_nulls_action.sh` | `UPDATE trades SET pnl, pnl_percent WHERE status='closed' AND pnl IS NULL AND <complete inputs>` in `trade_journal.db`. No service touched. Idempotent (SQL guard `WHERE pnl IS NULL`). Filters: `status='closed'`, `COALESCE(is_backtest,0)=0`, full price/size triple, known direction. |
| `backfill-orphan-pnl` | 2 | `scripts/ops/backfill_orphan_pnl_action.sh` | `UPDATE trades SET status='closed', exit_price, pnl, pnl_percent, notes, exit_reason='backfill_closed_pnl_recovery' WHERE status='orphaned' AND exit_reason='stuck_strategy_watchdog' AND exit_price IS NULL` in `trade_journal.db`. Recovers each row's real close fill from Bybit V5 `/v5/position/closed-pnl` via `account_closed_pnl_for_trade` (PR #1299). No service touched. Idempotent (SQL guard `WHERE status='orphaned'`). Bybit retains closed-pnl records for 7 days only — older orphans are listed in the skip section and remain `status='orphaned'`. Full runbook: `docs/runbooks/backfill-orphan-pnl.md`. |
| `backfill-closed-null-pnl` | 2 | `scripts/ops/backfill_closed_null_pnl_action.sh` | `UPDATE trades SET exit_price, pnl, pnl_percent, notes, exit_reason='backfill_closed_pnl_recovery' WHERE status='closed' AND pnl IS NULL AND COALESCE(is_backtest,0)=0` in `trade_journal.db`. Covers the reconciler-fallback shape (`order_monitor.py:3131-3151`) where status='closed' was stamped without computing PnL when the broker close-pnl lookup failed. Re-uses `backfill_orphan_pnl.py`'s `_plan_row` / `_apply_updates` / silent-credential-failure warning — same Bybit V5 `/v5/position/closed-pnl` recovery as `backfill-orphan-pnl`, just a widened candidate filter. No service touched. Idempotent (SQL guard `WHERE pnl IS NULL`). Bybit's 7-day retention is the limiting factor; older rows are listed in the skip section. Added 2026-06-04 reporting-cleanup sprint (#2774). |
| `mark-reconciler-incomplete` | 2 | `scripts/ops/mark_reconciler_incomplete_action.sh` | `UPDATE trades SET exit_reason='reconciler_incomplete' WHERE status='closed' AND pnl IS NULL AND exit_reason='reconciler_filled' AND COALESCE(is_backtest,0)=0` in `trade_journal.db`. The "be honest" pass after `backfill-closed-null-pnl` exhausts what Bybit retention can recover: re-stamps the residual rows so their `exit_reason` matches wire-side honesty (`realizedPnl: null`, `/performance` excludes them from aggregates). No PnL writes, no notes mutation, no other column changes — `exit_reason` only. No service touched. Idempotent (`exit_reason='reconciler_incomplete'` rows no longer match). Added 2026-06-04 reporting-cleanup sprint. |
| `backfill-account-class` | 2 | `scripts/ops/backfill_account_class_action.sh` | `UPDATE trades SET account_class=?, is_demo=?` for every row, keyed by `account_id → account_class` derived from `config/accounts.yaml` (via `load_accounts_dict`). The `account_class` column (paper/real_money) was added 2026-06-15 as the single source of truth for the paper/real reporting axis; pre-existing rows are NULL, and the pre-fix `ib_paper` account stamped its PAPER trades as `is_demo=0` (polluting real-money PnL) — this action retro-corrects them. Wrapper runs a DRY-RUN preview then `--apply` (wraps `scripts/ops/backfill_account_class.py`); idempotent (re-running once correct is 0 changes); defensively ensures the column exists first. No service touched. Added 2026-06-15 account_class sprint. |
| `backfill-closed-at` | 2 | `scripts/ops/backfill_closed_at_action.sh` | `UPDATE trades SET closed_at=?` for historical `status='closed' AND closed_at IS NULL` rows (non-backtest), deriving the value from the same chain the read path uses (linked `order_packages.updated_at` via EITHER `op.linked_trade_id` or `trades.order_package_id`, else `notes.closed_at`; never fabricated). The `closed_at` column (added 2026-06-16, P1-B) is the single source of truth for a trade's close timestamp — every close path now stamps it going forward, so this one-shot repair makes old rows match the new write-path and the read path stops deriving on the fly. Runs with `--also-account-class` (operator widest-scope directive 2026-06-17), so the SAME audited pass also closes any remaining `account_class` gap (delegates to `backfill_account_class.py`). Wrapper runs a DRY-RUN preview (counts scanned/fillable/left-NULL + a sample) then `--apply`; idempotent (`AND closed_at IS NULL` guard); defensively ensures the column exists first. No service touched. Added 2026-06-17 dashboard-truth Phase P1-E (wraps `scripts/ops/backfill_closed_at.py`). |
| `migrate-closed-at-iso` | 2 | `scripts/ops/migrate_closed_at_to_iso_action.sh` | Normalises existing `trades.closed_at` **epoch-ms** rows (and `notes.closed_at`) to ISO-8601 (`BL-20260620-RECONCILER-CLOSEDAT-MS`). The reconciler-filled close path historically wrote Bybit's `updatedTime`/`execTime` as a raw epoch-ms string (e.g. `"1782128223798"`) into the ISO column; the writer was fixed (PR #4168) and the read endpoints guard it (PR #4162), and this one-shot rewrites the already-persisted ms rows so the column is uniformly ISO. **Distinct from `backfill-closed-at`** which fills `closed_at IS NULL` rows — this converts the OPPOSITE case (populated as ms). Wrapper runs a DRY-RUN preview (counts scanned / ms→ISO + a sample) then `--apply`; idempotent (only all-digit ≥12-char values are touched, so a re-run is a no-op); no service touched. Added 2026-06-22 (wraps `scripts/ops/migrate_closed_at_to_iso.py`). |
| `backfill-shadow-predictions` | 2 | `scripts/ops/backfill_shadow_predictions_action.sh` | Replays every historical trade in `trade_journal.db` against every `target_deployment_stage=shadow` model and writes `runtime_logs/shadow_predictions_backfill.jsonl` (the `ml backfill-shadow-predictions` CLI; writer truncates each run). **Observational only** — no trade-journal mutation, no service restart, no exchange calls. Read by `/api/bot/trades/scores` (`backfill_kind`) so the dashboard shows shadow decisions for the full live history. Registry root + output path resolve through the same Python the live shadow factory uses, so no path drift. Added 2026-05-21 alongside the shadow auto-wire fix (#1630). |
| `pull-mes-ibkr-history` | 2 | `scripts/ops/pull_mes_ibkr_history.sh` | Paced IBKR historical pull for MES, run ON the live VM (shares the one IB gateway on a DISTINCT clientId 450, `pause_s=20`, `use_rth=false`, ~365d of 5m+15m → `/data/bot-data/ibkr_datasets/market_raw/MES/...`, synced to the trainer for the regime models — MB-20260528-002). **Secondary by construction:** the wrapper **detaches** (returns immediately; the ~20-30 min paced run survives), re-execs under `nice -n 19 ionice -c3`, and **aborts if the live trader heartbeat is stale (>10 min)** so it never adds gateway load during a live-trading incident. No trade-journal mutation, no service restart, no order-path touch. Monitor via `diag log_file?name=ibkr_mes_pull`. Best run in the CME maintenance break / weekend. Added 2026-05-28. |
| `pull-mes-ibkr-history-daily` | 2 | `scripts/ops/pull_mes_ibkr_history.sh` | Same wrapper + the same live-gateway guards as `pull-mes-ibkr-history`, but baked to a **DAILY multi-year** pull (`MES_TIMEFRAMES=1d`, `MES_HIST_START=2019-05-06` ≈ MES inception, `MES_MAX_CONTRACTS=28` to stitch the quarterly expiries back to 2019, `DATASET_VERSION=v003` — must be `vNNN`, digits only, per `metadata.py`) → `/data/bot-data/ibkr_datasets/market_raw/MES/1d/v003/data.jsonl`. The `ibkr_offvm` adapter stitches dated MES expiries for depth. Added 2026-06-01 to validate `mes_trend_long_1d` (the execution:shadow daily long-only diversifier) on **native MES** rather than the SPX-CFD proxy before any shadow→live. |
| `pull-ibkr-history` | 2 | `scripts/ops/pull_mes_ibkr_history.sh` | **Generalized symbol-parameterized** sibling of `pull-mes-ibkr-history` — same wrapper, same live-gateway guards (detach, `nice -n 19 ionice -c3`, live-first heartbeat abort, distinct clientId 450, `pause_s=20`, single-instance lock), but the `symbol:` / `timeframes:` / `hist_start:` / `dataset_version:` / `max_contracts:` come from the issue body so the **metals sleeve (MGC/MHG)** can be backfilled the same way MES is → `/data/bot-data/ibkr_datasets/market_raw/<SYMBOL>/<tf>/<version>/data.jsonl`. `symbol` is allowlisted to the IB futures roots `_build_contract` maps (`MES`/`MGC`/`MHG`); `timeframes` to `1m 5m 15m 30m 1h 4h 1d`; `hist_start` must be `YYYY-MM-DD`, `dataset_version` `vNNN`, `max_contracts` an int. Blank params fall back to the wrapper defaults. No trade-journal mutation, no service restart, no order-path touch. Monitor via `diag log_file?name=ibkr_mes_pull`. Added 2026-07-07 to backtest the metals sleeve (`mgc_trend_1h` / `mgc_pullback_1d` / `mhg_pullback_1d`) on native IBKR history before any shadow→live. |
| `set-account-mode` | 2 | `scripts/ops/set_account_mode.sh` | in-place edit of `config/accounts.yaml` `mode:` for the named account + restart `ict-trader-live.service`. Added 2026-05-12 in response to the silent-flip incident (see § 2.1). |
| ~~`enable-mes` / `disable-mes`~~ | — | *removed 2026-05-22* | **Deleted — these were a forbidden second gate.** The `MULTI_SYMBOL_ENABLED` env they flipped no longer exists; the symbol set is derived from `config/accounts.yaml` (`_resolve_tick_symbols` unions every configured account's `symbols`). Per the "one switch per account" rule, the only way to gate MES is the account's `mode:` (via `set-account-mode` on `ib_paper` → stops execution, signals still log) or removing its `strategies` / `symbols` in a PR. |
| `fix-data-dir` | 2 | `scripts/ops/fix_data_dir.sh` | strips `DATA_DIR=` / `TRADE_JOURNAL_DB=` overrides from `.env` (backup retained), rsyncs `/home/ubuntu/ict-trading-bot/data/{runtime_logs,runtime_state,artifacts,data}/` → `/data/bot-data/<same>/` to align with the systemd drop-in's canonical mount, renames the legacy split path with a `MIGRATED-<ts>` suffix, then restarts every canonical unit. Added 2026-05-12 in response to the path-bifurcation incident (see § 2.2). |
| `send-ping` | 1 | `scripts/ops/send_ping_action.sh` | **No mutation, no restart.** Enqueues one immediate Telegram message via `scripts/send_ping.py` (`target=claude` default → @claude_ict_comms_bot; the bridge drains within ~5 s). This is the autonomous "Claude wants to say something NOW" path — far faster than the ≤5-min `pending-pings.jsonl` git-relay. Params: `message:` (required), `priority:` (low\|normal\|high\|urgent, default normal), `target:` (claude\|trader, default claude). The transparency notify is skipped for it (the action IS the message). Added 2026-05-24. |
| `send-prop-test-ping` | 1 | `scripts/ops/send_prop_test_ping_action.sh` | **No mutation, nothing journaled, no exchange socket.** Fires ONE synthetic, clearly-labelled TEST prop ticket through the REAL prop-account ping path — `scripts/prop/send_test_ping.py` → `src.prop.breakout_executor.emit_prop_ticket` → `emit_prop_signal` (typed FCM push + the prop Telegram bot). Exercises the Breakout prop "trade flow" up to and including the notification (ruleset resolution → per-account leg + sizing → ticket render → fan-out) without touching the execute path, so no order package is written. Params: `symbol:` (optional, default `SOLUSDT`), `strategy:` (optional, default `trend_donchian_sol`). Safe to run repeatedly. Added 2026-06-17 to verify the prop ping end-to-end (operator sees the ticket land in Telegram + Android). |
| `set-env` | 2 | `scripts/ops/set_env.sh` | Idempotent single-key upsert into the VM `.env` (preserves all other lines/comments) + restart the named `service:` so systemd re-reads its `EnvironmentFile`. The autonomous "Claude owns + configures the VM env" path. Params: `env_key:` (required, `^[A-Z][A-Z0-9_]*$`), `env_value:` (omit for secret-backed keys — see below), `service:` (allowlisted unit, or `none` to skip restart). **Values are never logged or recorded in the audit JSON.** Secret-backed keys (e.g. `TELEGRAM_CLAUDE_BOT_TOKEN`) take their value from the matching `secrets.<KEY>` GitHub Actions secret when `env_value` is blank, so the secret never transits the (public) issue body or run log. Added 2026-05-24. |
| `pause-autoheal` | 2 | `scripts/ops/pause_autoheal.sh` | `systemctl disable --now ict-liveness-watchdog.timer` — pauses the per-minute liveness watchdog (stale-heartbeat alert **and** auto-restart of `ict-trader-live.service`). Added 2026-06-05 for the restart-loop incident: when the trader's first pipeline tick runs longer than the autoheal window (e.g. a logged-out IB Gateway making every MES fetch time out, inflating the tick past ~3 min), the watchdog restarts the trader before it can complete a tick + write a heartbeat, so the heartbeat stays permanently stale and the autoheal fires forever (self-perpetuating loop). Pausing lets the running instance finish its slow first tick, write a heartbeat, and stabilise. **Pauses the dead-man switch** — resume promptly. Idempotent; does not touch `ict-trader-live.service` or any config. |
| `resume-autoheal` | 2 | `scripts/ops/resume_autoheal.sh` | `systemctl enable --now ict-liveness-watchdog.timer` — symmetric undo of `pause-autoheal`; restores the dead-man switch + autoheal. Run once the trader is confirmed heartbeating (no boot-grace applies, so a still-stale heartbeat would autoheal on the next streak). Idempotent. |
| `sync-clock` | 2 | `scripts/ops/sync_clock.sh` | Diagnose + correct live-VM clock drift. Added 2026-06-05 after the VM clock was found ~6.5 s behind (pybit `ErrCode 10002`, exceeds Bybit `recv_window`) and NTP wasn't disciplining it even post-reboot. Reads `timedatectl status` + the NTP daemon's source/offset (`chronyc tracking/sources` or `timedatectl timesync-status`; no sudo), then `systemctl enable --now` + `restart`s the time daemon (chrony / systemd-timesyncd) to force a fresh sync. **Limited to `systemctl`** (the only NOPASSWD sudo), so it cannot `date -s`/`chronyc makestep`; if the daemon's sources are unreachable (offset unchanged, `NTPSynchronized!=yes`), NTP egress (UDP 123) is likely blocked at the OCI security list — the one external step. No trade-path/config impact. |
| `flatten-ib-position` | 2 | `scripts/ops/flatten_ib_position_action.sh` | One-shot guarded flatten of a single IB exchange position. Body params: `account: <id>` + `symbol: <SYM>` (both required) + optional `apply: true` (default **dry-run**). Reads the LIVE exchange position via the bot's read path, and on `apply: true` places the close through the unified `close_open_position` (IB: cancel the resting protective bracket/OCA legs, then an opposing reduce market order **clamped to the live qty** so it can never flip), using a process-unique OPS clientId (9900-range) distinct from the trader's execution id (496/497) + the read range (9000–9899). Then re-reads to verify flat. The journal row is left for the trader's reconciler to close-on-disappear. Built 2026-06-19 for the BL-20260618-RECONCILE-DUP residual (the stranded `ib_paper` −232 MGC short the IBKR-futures reduce path couldn't self-clean). DRY-RUN previews without touching the broker; only `apply: true` mutates. Wraps `scripts/ops/flatten_ib_position.py`. |
| `flatten-bybit-position` | 2 | `scripts/ops/flatten_bybit_position_action.sh` | One-shot guarded flatten of a single **Bybit** exchange position — the Bybit sibling of `flatten-ib-position`. Body params: `account: <id>` + `symbol: <SYM>` (both required) + optional `apply: true` (default **dry-run**). Reads the LIVE exchange position via the bot's read path (`account_open_positions`), and on `apply: true` places the close through the unified `close_open_position` (Bybit: a **reduce-only** opposing market order sized to the live qty — `reduceOnly=True` means it can only shrink to flat, never flip; no resting bracket to cancel because Bybit SL/TP are position-attached and clear with the position). Then re-reads to verify flat. The journal row is left for the trader's reconciler to close-on-disappear. The python script guards `exchange==bybit`, so a non-Bybit account is refused. **Authenticates with whatever Bybit key is currently in the VM `.env`** (via `load_runtime_secrets`) — so before a different-account key rotation it closes on the OLD account that still holds the position; run it BEFORE `rotate-account-keys`. DRY-RUN previews without touching the broker; only `apply: true` mutates. Wraps `scripts/ops/flatten_bybit_position.py`. |
| `reconcile-orphan-history` | 2 | `scripts/ops/reconcile_orphan_history_action.sh` | Historical orphan-flap reconciliation (orphan-flap hardening #5). Optional body param `apply: true` (default **dry-run**). Collapses the phantom flap duplicates a position left behind (the classic case: one MGC/MHG position flapping into N phantom `adopted_orphan` closed trades, each with a fabricated PnL — the −$20,127 incident) so each physical position is ONE row: keeps the canonical (the live OPEN row if any, else earliest), reconciles it to its originating order package when recoverable (else flags it `unreconciled` — the honest red-flag terminal state), and **void-flags** the phantom duplicates `reconcile_status='superseded'` (preserved for audit, excluded from analytics — never deleted). Clustering is conservative (time-gapped per `(account,symbol,direction)`; a duplicate linking a *distinct* real package is never collapsed; an OPEN row is never void-flagged). DRY-RUN prints the full per-cluster KEEP/VOID plan without writing; `apply: true` writes after a timestamped DB backup. Pure journal hygiene — never closes/opens an exchange position. For broker-API accounts (bybit) run `backfill-orphan-pnl` FIRST to recover the real exit/PnL on the canonical row. Wraps `scripts/ops/reconcile_orphan_history.py`. |
| `supersede-options-adoption-artifacts` | 2 | `scripts/ops/supersede_options_adoption_artifacts_action.sh` | One-shot journal hygiene for the 2026-06-27 options-account orphan-adoption incident. Optional body param `apply: true` (default **dry-run**). Before the #4858 + #4867 fixes, the reverse reconciler adopted `alpaca_options_paper` `us_option` legs as equity `adopted_orphan` trades and the local-PnL sweep priced them with the equity formula (`local_markprice` × qty × `contract_value_usd=1.0`), fabricating phantom paper PnL (the −$845 figure). Those code paths are fixed, so no NEW artifacts are produced; this cleans up the **historical** rows that still carry the fabricated PnL by void-flagging them `reconcile_status='superseded'` (excluded from analytics). Precise predicate — **paper only** (`is_demo=1`), `setup_type='adopted_orphan'`, `account_id='alpaca_options_paper'`, `status='closed'`, notes carrying the `pnl_source=local_compute` marker, not already superseded; optional `ids:` allowlist narrows further. **Real-money rows are categorically excluded.** DRY-RUN lists the matched rows + their fabricated PnL without writing; `apply: true` writes after a timestamped DB backup. Idempotent; never closes/opens an exchange position, never deletes a row. Wraps `scripts/ops/supersede_options_adoption_artifacts.py`. |
| `supersede-reset-orphan-artifacts` | 2 | `scripts/ops/supersede_reset_orphan_artifacts_action.sh` | One-shot journal hygiene for the 2026-07-07 **alpaca_paper external reset** (`BL-20260707-ALPACA-RESET`). Optional body params `apply: true` (default **dry-run**) + `ids: <csv>` (allowlist). The paper account was reset externally (Alpaca re-seeded a default ETF portfolio the bot never opened); the reverse reconciler adopted the unfamiliar positions as **bare** `adopted_orphan` trades (`strategy_name='orphan_adopt'`, NULL `order_package_id`) and the local-PnL sweep priced them with the equity formula, fabricating phantom PnL (the 1360-share SLV short adopted **twice** as trades 3265+3266 at −693.6 each = −1387.2). The live-path fix (PR #5951 reset-detection) stops NEW strategy-attributed reset artifacts; this void-flags the **historical** bare phantoms `reconcile_status='superseded'` (excluded from analytics). Precise predicate — **paper only** (`is_demo=1`), `setup_type='adopted_orphan'`, **`strategy_name='orphan_adopt'` + `order_package_id IS NULL`** (bare, NOT a genuinely-reattached orphan — those keep their real strategy + package + `reconcile_status='reconciled'` and are categorically excluded, e.g. trade 3250), `status='closed'`, `pnl_source=local_compute` marker, not already superseded, default `account_id='alpaca_paper'`; `ids:` narrows further. **Real-money rows are categorically excluded.** DRY-RUN lists matched rows + fabricated PnL; `apply: true` writes after a timestamped DB backup. Idempotent; never closes/opens an exchange position, never deletes a row. Wraps `scripts/ops/supersede_reset_orphan_artifacts.py`. |
| `fix-prop-mislinked-close` | 2 | `scripts/ops/prop_fix_mislinked_close_action.sh` | One-shot prop-journal hygiene for the 2026-07-06 mis-linked ETH prop close (`BL-20260706-PROP-CLOSE-MISLINK`). Optional body param `apply: true` (default **dry-run**). Before PR #5744, a prop CLOSE with no explicit `ticket_id` linked to the *newest* open-status ticket — a never-placed `emitted` **signal** rather than the `filled` **position**: the 2026-07-06 ETH close (`prop_fills` id 17) hit the emitted ticket `prop-manual-849ece101a3c` instead of the filled position ticket `prop-manual-5bc393741ec4`, marking a phantom closed and leaving the real position open. #5744 stops recurrence; this repairs the rows already written with the clean end state (no artifacts): (1) relink `prop_fills` id 17 to `…5bc393741ec4`, (2) that ticket `filled`→`closed`, (3) the phantom `…849ece101a3c` `closed`→`expired`. **Guarded + idempotent** — each op fires only when its expected current value holds, so re-running after apply is a clean no-op. Touches only `prop_fills` / `prop_tickets` (the prop journal is isolated from real-money/paper KPIs); never a `trades` row, never an exchange position. DRY-RUN prints the 3-op plan without writing; `apply: true` writes after a timestamped DB backup. Wraps `scripts/ops/prop_fix_mislinked_close.py`. |
| `purge-cloudflared` | 2 | `scripts/ops/purge_cloudflared.sh` | Purge the retired `ict-cloudflared-tunnel.service` from the live VM. The Cloudflare tunnel was retired in the React→Streamlit dashboard pivot and removed from the repo in #3233 — but `install_systemd_units.sh` is install-only, so an already-installed unit kept running; once the operator disconnected the Cloudflare account (2026-06-10) it just retries a dead tunnel (harmless to trading — nothing routes through it — but pointless churn on the 2-core box). Runs `systemctl disable --now` + removes the unit file(s) + token drop-in + `daemon-reload` + `reset-failed`. **Fully idempotent** — if the unit was never installed every step is a no-op and it exits 0 with a "nothing to purge" report, so it's safe to run blind. Touches only `ict-cloudflared-tunnel.service`; the live stack + config are untouched. |
| `scrub-env-noncompliant` | 2 | `scripts/ops/scrub_env_noncompliant.sh` | Strips every line from `.env` that systemd's `EnvironmentFile` parser would reject (anything that isn't blank, a comment, or `KEY=...` with `KEY` matching `^[A-Za-z_][A-Za-z0-9_]*$`). The original is backed up to `${REPO_DIR}/.env.bak.<UTC-ts>` (mode 600) before the rewrite; the audit JSON records only counts (`kept`, `stripped`, `total`) and the backup path — never the stripped content. Then restarts `service:` (default `ict-trader-live.service`, allowlist same as `set-env`). Idempotent: a clean file exits 0 with `stripped=0` and no restart. **Use case:** a multi-line value (e.g. a service-account JSON's `private_key` field) was pasted directly into `.env` and is now bleeding into the journal on every restart as `Ignoring invalid environment assignment '<line>'` warnings. Removing the lines changes runtime behaviour zero ways (systemd was already ignoring them) and only stops the journal bleed. Added 2026-05-27 after the FCM-credential bleed exposed a PEM private key in the `pull-and-deploy` journalctl tail on issue #2157. |

**Docker is intentionally absent.** The repo's canonical runtime is
systemd (`deploy/*.service` units installed via
`scripts/install_systemd_units.sh`). The root-level `Dockerfile`
predates the systemd switch and is not part of the live deploy. If
Docker ever becomes canonical, add `restart-docker-stack` here and
to the workflow at the same time.

### 2.1 set-account-mode and the Tier-3 boundary

`set-account-mode` is a **deliberate, named exception** to the
Tier-3 rule that strategy / risk / account-mode changes never flow
through this workflow. It exists because the 2026-05-12 silent-flip
incident demonstrated that the only previously-available paths to
flip an account from `live` to `dry_run` (the in-process breaker
in `src/core/coordinator.py`, the Telegram `/accounts` command, an
operator SSH session) could mutate the runtime override dict
without an audit record that surfaced cleanly to the operator. Per
the Prime Directive in [`docs/CLAUDE-RULES-CANONICAL.md`](../CLAUDE-RULES-CANONICAL.md)
(2026-05-12), live is the only default; any transition off live
must be operator-driven and audited via this allowlisted, Telegram-
notified path.

This carve-out covers **only** the `mode:` field of
`config/accounts.yaml`. Every other Tier-3 path stays off-limits to
this workflow:

- Strategy parameter changes (`config/strategies.yaml`)
- Risk caps (`src/runtime/risk_counters.py`, `config/risk_caps.yaml`)
- Live order code (`src/runtime/orders.py`)
- Anthropic (Claude-on-VM) key rotation — out-of-band only. (Exchange
  *account* keys are not forbidden here: they're applied by the
  `rotate-account-keys` carve-out below, sourced from Actions secrets.)
- Disabling/masking `ict-trader-live.service`

If you want any of those, you do not want this workflow. Open a PR.

### 2.2 fix-data-dir and the canonical-source rule

`fix-data-dir` is the second named exception, scoped narrowly to
**deployment alignment** of the runtime data directory. It addresses
the 2026-05-12 path-bifurcation incident: the VM's `.env` carried
`DATA_DIR=data/` (a relative path predating the OCI block-storage
migration), so `src/utils/paths.py` resolved it to
`/home/ubuntu/ict-trading-bot/data/runtime_logs/`. Meanwhile every
reader process driven by the systemd drop-ins (canonical:
`Environment=DATA_DIR=/data/bot-data`) looked at
`/data/bot-data/runtime_logs/`. The result was a writer/reader
split-brain that manifested as a phantom heartbeat-writer silent
failure, a phantom mode-flip on `bybit_2` (stale runtime_status
being read by every consumer except the trader), and a real
ict-web-api + ict-claude-bridge crashloop (those units couldn't
find the files at canonical paths).

The operator directive that drove this exception:

> *"ENV is not the canonical source of anything. There's
> architecture and there's the README, and there's the CLAUDE.md
> — those are the canonical documents. If the ENV doesn't comply
> with anything, then the ENV needs to be changed. The ENV is a
> product of our work; it is not what decides how the work gets
> done."*

`fix-data-dir` enforces that rule mechanically: it strips the
conflicting `.env` overrides so the systemd drop-in's declaration
wins on the next service start, then migrates the data that landed
at the wrong path. The companion CI alert is in `src/utils/paths.py`
(`_alert_on_relative_data_dir`): every process that boots with a
relative `DATA_DIR` emits a CRITICAL log line + outcomes ping so
the misalignment is visible the moment it re-emerges. The
`scripts/render_env_from_master.py` companion fix removes `DATA_DIR`
from `_runtime_defaults` so future renders don't re-introduce the
override.

This carve-out covers **only** the `DATA_DIR=` and
`TRADE_JOURNAL_DB=` env-var overrides in `.env`. Every other Tier-3
path (strategy params, risk caps, live order code, key rotation,
unit disable/mask) stays off-limits as documented in § 2.1.

---

## 3. Tier policy (PM-side dispatch)

Mirrors the existing `docs/claude/operating-protocol.md` decision
tiers but applied to *workflow dispatch* rather than PR merge.

### Tier 1 — autonomous

Claude may dispatch these without operator approval:

- `status-check`
- `list-listening-ports`
- `pull-latest-logs`
- `inspect-closed-pnl`
- `bybit-account-audit`
- `strategy-performance-audit`
- `monitor-miss-analysis`
- `vwap-backtest-sweep`
- `send-ping`
- `send-prop-test-ping` — fires one synthetic TEST prop ticket through the
  real `emit_prop_signal` path (FCM + prop Telegram bot); notify-only, nothing
  journaled
- `generate-strategy-review-packets` — fires
  `scripts/ml/strategy_review_packet.py` against the live
  `trade_journal.db` and writes M7 review packets
  (JSON + Markdown) under
  `runtime_logs/strategy_reviews/<UTC-date>/`. Read-only with respect
  to the trade journal (`mode=ro`); no order-path interaction. Issue
  body fields: `strategy: <name[,name,…]>` OR `all_btc: true`,
  optional `window_days: <int>` (default 7), `shadow_soak_days:
  <int>` (default 0, only matters when the matrix would emit
  `promote`), and `print_packets: true` (default off; when truthy
  the wrapper also cats each packet's Markdown summary in the
  issue-comment reply — useful for sandbox sessions that can't curl
  the live VM directly and need the matrix's `reasons[]` /
  per-regime cell table inline). The wrapper echoes each packet's
  `proposed_action` in the issue-comment reply so the operator gets
  a one-line verdict per strategy without a follow-up curl. Gate doc:
  [`docs/strategy-review-gate.md`](../strategy-review-gate.md).
- `grade-closed-trades` — added 2026-07-06 to fix a recurring
  size-limit failure in every `/system-review` / `/performance-review`
  session's mandatory grading pass. The Claude decision grade is a
  **pure deterministic rubric** (`scripts/ops/score_order_packages.py::
  _grade_package` — no LLM call), so it can run wherever the DB
  already lives instead of pulling the whole `trades` table back to a
  web/PM session. This action runs
  `score_order_packages.py --emit-delta-only` against the live
  `trade_journal.db` and the VM's read-only `ict-git-sync` mirror of
  `comms/claude_strategy_scores.jsonl`, and returns **only the new
  (ungraded) rows** as NDJSON in the issue-comment reply — a bounded
  delta (typically tens of KB) instead of the full journal dump
  (~650KB, which routinely exceeded the diag relay's ~55KB comment
  budget and blocked full-window grading). **Read-only end to end:**
  sqlite `mode=ro`; the score file is only ever read to compute the
  skip-set — nothing is written or committed on the VM (its
  `VM_GIT_DEPLOY_TOKEN` credential is Contents:Read-only by design;
  see § "Live-VM git-fetch credential" in the bot `CLAUDE.md`). Issue
  body fields: optional `since: <ISO_TS>` (only packages created at/
  after this timestamp), `limit: <int>` (default 300 — never
  truncates silently: an exceeded limit surfaces a trailing
  `{"_delta_summary": ..., "truncated": true, "more_available": N}`
  NDJSON line, mirroring the diag relay's `(truncated, N more bytes)`
  convention), and `include_open: true` (widen scope beyond
  `order_packages.status='closed'` to every ungraded package,
  matching `score_order_packages.py --append`'s scope). The caller
  (a review session) appends the returned delta to
  `comms/claude_strategy_scores.jsonl` in a normal PR. **Fallback:**
  `scripts/ops/grade_closed_trades_from_diag.py` (feed it a
  `/api/diag/journal?table=trades` pull) remains in the repo for the
  rare case this system-action path itself is unavailable — see that
  script's docstring for the size-limit history it was originally
  built to work around.

`send-ping` is non-mutating (it enqueues one Telegram message, no
restart) so it sits at Tier 1 — this is the autonomous path for Claude
to post an immediate update or "waiting on you" ping to the operator's
channel. The rest are read-only analysis wrappers (they query the journal / Bybit /
backtest harness and emit a summary; no journal mutation, no service
restart). Pre-conditions: none beyond the standard "session has a clear reason
to run it" (a flagged issue, a CI failure on `vm-diag-snapshot`,
operator request, scheduled health check). The wrapper itself is
read-only.

Post-action: Claude reads the artifact, summarises in the relevant
issue / PR / Telegram thread, then stops.

### Tier 2 — pre-dispatch ping (PM-side Claude only)

Tier-2 actions:

- `pull-and-deploy`
- `restart-bot-service`
- `reboot-vm`
- `enable-closed-flat-invariant`
- `disable-closed-flat-invariant`
- `enable-m5-consumer`
- `disable-m5-consumer`
- `backfill-pnl-nulls`
- `backfill-orphan-pnl`
- `backfill-closed-null-pnl`
- `mark-reconciler-incomplete`
- `backfill-monitor-closed-pnl`
- `revert-backfill-monitor-closed-pnl`
- `rebuild-pnl-from-bybit`
- `backfill-shadow-predictions`
- `backfill-account-class`
- `backfill-closed-at`
- `migrate-closed-at-iso`
- `set-account-mode`
- `fix-data-dir`
- `rotate-account-keys`
- `init-diag-token`
- `set-env`
- `scrub-env-noncompliant`
- `pause-autoheal`
- `resume-autoheal`
- `flatten-ib-position`
- `flatten-bybit-position`
- `reconcile-orphan-history`
- `supersede-options-adoption-artifacts`
- `supersede-reset-orphan-artifacts`
- `fix-prop-mislinked-close`
- `reset-daily-risk-state`

`reset-daily-risk-state` deletes the `daily_risk_state` row for a given
`account_id` from `trade_journal.db`, clearing the INTRADAY_DRAWDOWN
counters without a full service restart. Requires `account: <id>` in the
issue body. Script: `scripts/ops/reset_daily_risk_state.sh`.

`pause-autoheal` / `resume-autoheal` stop / start
`ict-liveness-watchdog.timer` (the per-minute dead-man switch +
autoheal). They are Tier-2 because pausing the watchdog removes the
auto-restart safety net while paused; always resume once the trader is
confirmed heartbeating. The incident rationale is in the § 2 allowlist
row.

`set-env` mutates the VM `.env` and restarts a bot service, so it is
Tier-2 (requires a `reason`). It is the autonomous path for Claude to
own and configure VM environment variables — e.g. wiring
`TELEGRAM_CLAUDE_BOT_TOKEN` / `TELEGRAM_CLAUDE_THREAD_ID` for the Claude
update channel — without an operator hand-off. Secret values come from
GitHub Actions secrets, never the issue body.

`pull-and-deploy` is a thin wrapper around `scripts/deploy_pull_restart.sh`
(the canonical script the `ict-git-sync` timer also calls). It fetches
`origin/main`, hard-resets the VM worktree to it, optionally reinstalls
deps, and bounces `ict-trader-live.service` + `ict-telegram-bot.service`.
Use this when you've just merged a fix and want it on the VM **now**
rather than waiting for the next git-sync tick. It does **not** mutate
anything that wasn't already authorized through the upstream PR + Tier
gates — the merge gates are still where strategy / risk / live-routing
changes get authorized.

`set-account-mode` is the explicit, audited path for flipping a
per-account `mode:` field. The pre-dispatch ping format in § 7
includes a `Target:` line listing the account + new mode so the
operator can confirm intent before the action fires.

`fix-data-dir` is the explicit, audited path for aligning the VM's
`.env` to the systemd drop-in's canonical `DATA_DIR=/data/bot-data`.
It stops every canonical unit, strips the `.env` override, migrates
any split-path content, and brings the services back up. See § 2.2
for the trust-contract rationale.

**For PM-side Claude (web sandbox / dev laptop):** must not dispatch
without an operator ack. The ack flow is:

1. Claude opens an issue (or appends to an open ping thread) using
   the message format in § 7.
2. Operator replies "Approve" — **or grants the ack inline in
   conversation**, which is equivalent intent. The conversation log
   itself is the audit trail for the ack; the issue body Claude
   subsequently opens captures the dispatched action + reason.
3. Dispatch path:
   - **Issue-driven (preferred when sandbox lacks a `run_workflow`
     tool):** Claude opens an issue with label `system-action` and a
     body that encodes the agreed `action:` + `reason:`. Workflow runs,
     posts result back, closes the issue. Same allowlist + audit as
     `workflow_dispatch`.
   - **Operator-click:** operator triggers `workflow_dispatch` from
     the Actions UI with the agreed `action` + `reason`.

Either path lands the same audit bundle. The ack must precede the
dispatch by Claude in either case.

**For autonomous dispatchers (operator, Perplexity):** the
pre-dispatch ping is waived (§ 3.5). The post-dispatch notification
is **not** waived — see § 5.5.

Why the PM-side ping is required: even though the action itself is
narrowly scoped, the *blast radius* of restarting the live trader
(open positions held by the trader process, in-flight orders) is
not provable from inside the workflow. PM-side Claude does not own
that judgement; an autonomous dispatcher does, by trust contract.

### Tier 3 — never via this workflow

Out of scope for `system-actions` regardless of approval:

- Strategy parameter changes (`config/strategies.yaml`)
- Risk caps (`src/runtime/risk_counters.py`, `config/risk_caps.yaml`)
- Live order code (`src/runtime/orders.py`)
- Anthropic (Claude-on-VM) key rotation — out-of-band only. (Exchange
  *account* keys are not forbidden here: they're applied by the
  `rotate-account-keys` carve-out below, sourced from Actions secrets.)
- Disabling/masking `ict-trader-live.service` (stopping is Tier-2 in
  the VM-runner protocol; **disabling/masking is Tier 3** there too)

**Exceptions** (named, audited carve-outs only):

- `set-account-mode` for the `mode:` field of `config/accounts.yaml`.
  Rationale + contract in § 2.1.
- `fix-data-dir` for the `DATA_DIR=` / `TRADE_JOURNAL_DB=` overrides
  in `.env`. Rationale + contract in § 2.2.
- `rotate-account-keys` **applies** an exchange account key that the
  operator has placed in the GitHub Actions secrets
  (`BYBIT_API_KEY_<n>` / `BYBIT_API_SECRET_<n>`): it re-renders the VM
  `.env` from those secrets and restarts the trader. The human step is
  updating the secret value; Claude only dispatches the apply. Tier-2
  (credential-touching + restart → operator OK in chat). *Generating* a
  new key at the exchange remains the human's job.

  > **Canonical path note (2026-06-02):** `rotate-account-keys` is the
  > **legacy Bybit-only** credential path. The canonical broker-credential
  > propagation workflow is now
  > [`.github/workflows/sync-vm-secrets.yml`](../../.github/workflows/sync-vm-secrets.yml)
  > — it declares the full known credential set (`REQUIRED_SECRETS` +
  > `OPTIONAL_SECRETS`) and mirrors Actions secrets to the live trader's
  > `.env` over SSH `SendEnv` (values never reach the run log). Adding a
  > new broker appends its env-var names there, not a new per-broker
  > workflow. The operator originates the secret **value**; propagation is
  > `sync-vm-secrets`'s job — see the `credentials-and-vm-mutations` skill.
  > `rotate-account-keys` stays in place as the legacy Bybit path pending a
  > migration PR.

Everything else above stays Tier-3. If you want any of those, you
do not want this workflow. Open a PR.

---

## 3.5 Dispatcher trust contract

The tier rules above describe the **action's** blast radius. Whether
a given dispatcher must ping the operator before triggering an action
depends on the dispatcher's trust class. Three classes exist today:

| Dispatcher | Tier-1 | Tier-2 |
|---|---|---|
| **Operator** (Ben, in browser) | autonomous (you're the human) | autonomous (you're the human) |
| **Perplexity** (granted 2026-05-08) | autonomous | autonomous |
| **PM-side Claude** (web sandbox / dev laptop) | autonomous | **must ping operator first** (§ 7 format) |
| **VM-resident Claude** (`/vm`, `/vm_write`) | n/a — uses the Telegram dispatcher path, not this workflow | n/a — same |

Tier-2 set for the table above is `pull-and-deploy`,
`restart-bot-service`, `reboot-vm`,
`enable-closed-flat-invariant`, `disable-closed-flat-invariant`,
`enable-m5-consumer`, `disable-m5-consumer`,
`backfill-pnl-nulls`, `set-account-mode`, and `fix-data-dir`.

Two corollaries that read as drift but are intentional:

1. **Perplexity ≠ Claude on this axis.** Perplexity's autonomy grant
   for Tier-2 was an explicit operator decision on 2026-05-08 based
   on Perplexity's separate trust contract; it is **not** a
   precedent for PM-side Claude sessions, which still ping for
   Tier-2.
2. **The action's tier is unchanged regardless of dispatcher.** A
   Tier-2 action is Tier-2 because of its blast radius, not because
   of who triggers it. The dispatcher table only changes the
   pre-dispatch handshake, not the post-dispatch verification or
   audit requirements (§ 5, § 6, § 5.5) — those apply to **every**
   run.

Adding a fourth dispatcher to this table requires a PR that
documents:
- the dispatcher's trust contract (where their authorization comes
  from)
- which tier(s) they're autonomous for
- what their notification path back to the operator is (§ 5.5)

---

## 4. Reboot is last resort

The reboot doctrine is explicit because the cost of a wrong reboot
is the highest of any action here:

1. **Try `status-check` first** to confirm the failure mode.
2. **Try `restart-bot-service` next** if the failure is contained
   to the trader process.
3. **Only escalate to `reboot-vm`** when:
   - the trader unit refuses to come back after restart, AND
   - the failure pattern indicates a host-level issue (kernel log
     errors, network stack unresponsive, `systemd-tmpfiles` disk
     pressure, OOM-killer thrashing), AND
   - the operator has acked the Tier-2 ping for `reboot-vm`.

Why: a reboot drops every SSH session, kills any in-flight `/vm`
runner mid-execution, and depends on systemd auto-start to bring
all services back cleanly. If a unit's `[Install]` section is wrong
or a dependency loops, recovery requires manual Oracle Cloud
Console intervention — which the PM-side session cannot drive. See
`docs/audit/sprint-013-deployment-runbook.md`.

The wrapper uses `shutdown -r +1` (1 min delay) rather than
`reboot` (immediate). The minute-of-grace lets the operator abort
with `sudo shutdown -c` if something looks wrong in the log
preview that streams while the workflow is running.

---

## 5. Audit trail

Every workflow run produces:

1. **An artifact** (`system-action-<action>-<run_id>.zip`)
   containing:
   - `audit-bundle.json` — structured: action, reason, tier, exit
     code, pre-state, post-state, output excerpt. For
     `set-account-mode` the bundle also carries `account_id` and
     `mode` at the top level so the audit reads cleanly without
     scanning the action-output excerpt.
   - `pre-state.json` — the diag `/api/diag/status` bundle from
     before the action (or `diag_skipped` / `diag_unreachable`)
   - `post-state.json` — same, after the action
   - `action-output.txt` — full stdout/stderr of the wrapper
2. **A run-log preview** in the workflow's "Execute action wrapper"
   step (capped at 4 KB).
3. **A repo-side audit record** at
   `runtime_logs/operator_actions/<utc-ts>-<action>.json` written by
   the wrapper itself. Picked up by the next `ict-git-sync` cycle
   and visible to PM-side sessions via the diag relay's
   `log_file?name=…` route (file alias to be added if frequent
   inspection is needed; today the file is fetchable via the
   workflow artifact route end-to-end).

Retention: GitHub artifact retention is 30 days. Repo-side
`runtime_logs/operator_actions/*.json` records are retained
indefinitely (they are tiny — < 1 KB each).

### 5.5 Transparency rule (always-notify)

**Operator directive, 2026-05-08:** *autonomy is complemented by full
transparency.* Every system-actions run notifies the operator,
**regardless of dispatcher class or action tier**, and regardless of
whether operator action was needed.

This is the binding rule:

- A Tier-1 action dispatched autonomously by Perplexity → operator
  is notified.
- A Tier-2 action dispatched autonomously by Perplexity → operator
  is notified (the pre-dispatch ping is what's waived for an
  autonomous dispatcher; the post-dispatch update is **not**).
- A Tier-2 action dispatched by PM-side Claude after operator ack
  → operator is notified again on completion (the pre-dispatch
  approval doesn't substitute for a completion update).
- An action that fails or is deferred (exit 1 / exit 3) → operator
  is notified, with the failure reason.
- An action whose result requires no operator follow-up → operator
  is notified anyway. "Nothing for you to do" is information, not
  silence.
- `set-account-mode` always notifies with the target `account=<id>=<mode>`
  prepended to the reason so the operator can verify intent at a
  glance — see notify_run.sh.
- `fix-data-dir` always notifies on completion; the wrapper's
  post-state log lists the canonical heartbeat freshness +
  `/api/health` probe outcome so the operator can confirm the
  alignment took without opening the run page.

**Notification surface (implemented):**

1. **Telegram via `@claude_ict_comms_bot`.** The workflow's final
   step SSHs to the VM and invokes
   `scripts/ops/notify_run.sh <action> <exit_code> <run_url> <reason:b64>`,
   which queues a JSON payload in `runtime_logs/pending_claude_pings/`.
   `ict-claude-bridge.service` drains the queue within ~5 s and
   posts a one-message summary to the operator chat. No new GitHub
   secret was added — the Telegram bot token + chat ID stay on the
   VM where they already lived (`/etc/ict-trader/claude.env`).
2. **Workflow run page** on GitHub, linked from the Telegram
   message via `run_url`.
3. **30-day workflow artifact** with the full pre/post bundle.
4. **Repo-side audit record** at
   `runtime_logs/operator_actions/<ts>-<action>.json`, picked up by
   the next `ict-git-sync` cycle and visible via the diag relay.

**Telegram message format** (rendered verbatim from `notify_run.sh`):

```
[ops] <action>: <result>
reason: <operator-typed reason>     ← only if non-empty
run: <github actions run url>
tier: <1 or 2>
```

**Priority routing** (mapped from action + exit code in
`notify_run.sh`, fed to `send_ping.py --priority`):

| Action | Exit | Priority |
|---|---|---|
| Tier 1 (`status-check`, `pull-latest-logs`) | 0 (ok) | `low` |
| Tier 1 | non-zero | `high` |
| `pull-and-deploy` | 0 (ok) | `normal` |
| `pull-and-deploy` | 3 (deferred — vm-runner active) | `normal` |
| `pull-and-deploy` | other | `urgent` |
| `restart-bot-service` | 0 (ok) | `normal` |
| `restart-bot-service` | 3 (deferred — vm-runner active) | `normal` |
| `restart-bot-service` | other | `urgent` |
| `reboot-vm` | 0 / 255 (scheduled, SSH dropped) | `high` |
| `reboot-vm` | other | `urgent` |
| `enable-closed-flat-invariant` | 0 (ok) | `normal` |
| `enable-closed-flat-invariant` | 3 (deferred — vm-runner active) | `normal` |
| `enable-closed-flat-invariant` | other | `urgent` |
| `disable-closed-flat-invariant` | 0 (ok) | `normal` |
| `disable-closed-flat-invariant` | 3 (deferred — vm-runner active) | `normal` |
| `disable-closed-flat-invariant` | other | `urgent` |
| `enable-m5-consumer` | 0 (ok) | `normal` |
| `enable-m5-consumer` | 3 (deferred — vm-runner active) | `normal` |
| `enable-m5-consumer` | other | `urgent` |
| `disable-m5-consumer` | 0 (ok) | `normal` |
| `disable-m5-consumer` | 3 (deferred — vm-runner active) | `normal` |
| `disable-m5-consumer` | other | `urgent` |
| `backfill-pnl-nulls` | 0 (ok / noop) | `normal` |
| `backfill-pnl-nulls` | other | `urgent` |
| `backfill-orphan-pnl` | 0 (ok / noop) | `normal` |
| `backfill-orphan-pnl` | other | `urgent` |
| `set-account-mode` | 0 (ok) | `normal` |
| `set-account-mode` | 3 (deferred — vm-runner active) | `normal` |
| `set-account-mode` | other | `urgent` |
| `fix-data-dir` | 0 (ok) | `normal` |
| `fix-data-dir` | 3 (deferred — vm-runner active) | `normal` |
| `fix-data-dir` | other | `urgent` |

**Failure-of-notification semantics:** the notify step uses
`continue-on-error: true`. A failed ping never flips a successful
action to failed. The artifact + run-log + repo-side audit record
remain the canonical trail; Telegram is the proactive layer on top.

**Tier-1 noise note:** every Tier-1 run notifies today, by design.
If a daily auto-driven `status-check` cron starts to bury signal,
the followup is a state-change-only filter (e.g. only ping when the
result diverges from the last queued ping for the same action),
**not** dropping the always-notify principle. File it as a
follow-up doc PR if it ever becomes a problem.

---

## 6. Verification matrix

| Action | Pre-check | Action | Post-check | Failure behaviour |
|---|---|---|---|---|
| `status-check` | none | `systemctl is-active` for canonical units + heartbeat age + audit tail | wrapper exits 0 if all canonical units active, 1 otherwise | exit 1 = at least one unit not `active`; investigate before any restart |
| `pull-latest-logs` | none | dump journalctl + signal_audit + status.json | wrapper exits 0 if all readable | exit 1 = log paths missing → investigate diag relay first |
| `pull-and-deploy` | capture pre-deploy `git rev-parse HEAD` + unit `is-active` | invoke `scripts/deploy_pull_restart.sh` (fetch + hard-reset + dep install + restart trader & telegram bot) | poll `is-active` until "active" or 60 s timeout; dump 30 journal lines; record HEAD diff in audit | exit 3 → vm-runner active, deferred. exit 1 → deploy or restart failed; HEAD may be advanced even if restart didn't complete — see `audit-bundle.json` for the head transition |
| `restart-bot-service` | capture pre-state via `is-active` + `status` | `systemctl restart ict-trader-live.service` | poll `is-active` until "active" or 30 s timeout; dump 30 journal lines | exit 1 → unit failed to come back; ping operator with journal tail |
| `reboot-vm` | dump uptime + canonical unit states + 10 journal lines | `shutdown -r +1` | workflow polls SSH for ≤ 5 min; post-fetch `/api/diag/status` | SSH not back in 5 min → manual recovery required (Oracle Cloud Console) |
| `enable-closed-flat-invariant` | snapshot current `CLOSED_FLAT_INVARIANT_ENABLED` line in `.env` + unit `is-active` | atomic write to `.env` setting `CLOSED_FLAT_INVARIANT_ENABLED=true`; `systemctl restart ict-trader-live.service` | grep `.env` for the post-edit value; poll `is-active` until "active" or 30 s timeout; dump 30 journal lines | exit 3 → vm-runner active, deferred. exit 1 → env-file verification mismatch or unit failed to come back; rollback via `disable-closed-flat-invariant` |
| `disable-closed-flat-invariant` | snapshot current `CLOSED_FLAT_INVARIANT_ENABLED` line in `.env` + unit `is-active` | atomic strip of the env line + its comment header from `.env`; `systemctl restart ict-trader-live.service` | confirm `.env` no longer contains the key; poll `is-active` until "active" or 30 s timeout; dump 30 journal lines | exit 3 → vm-runner active, deferred. exit 1 → env-file still contains the key or unit failed to come back; investigate before re-enabling |
| `enable-m5-consumer` | snapshot current `M5_CONSUMER_ENABLED` line in `.env` + `ict-telegram-bot.service` `is-active` | atomic write to `.env` setting `M5_CONSUMER_ENABLED=1`; `systemctl restart ict-telegram-bot.service` | grep `.env` for the post-edit value; poll `is-active` until "active" or 30 s timeout; dump 30 journal lines | exit 3 → vm-runner active, deferred. exit 1 → env-file verification mismatch or unit failed to come back; rollback via `disable-m5-consumer` |
| `disable-m5-consumer` | snapshot current `M5_CONSUMER_ENABLED` line in `.env` + `ict-telegram-bot.service` `is-active` | atomic write to `.env` setting `M5_CONSUMER_ENABLED=0`; `systemctl restart ict-telegram-bot.service` | confirm `.env` value is `0`; poll `is-active` until "active" or 30 s timeout; dump 30 journal lines | exit 3 → vm-runner active, deferred. exit 1 → unit failed to come back; investigate before re-enabling |
| `backfill-pnl-nulls` | count rows in `trade_journal.db::trades` matching `status='closed' AND pnl IS NULL AND <complete inputs>` | `python3 scripts/ops/backfill_pnl_nulls.py --apply` — computes realised PnL via the canonical `src.runtime.local_pnl` helpers (`compute_realized_pnl` + `compute_pnl_percent`, multiplier-aware through `contract_value_usd_for` — the SAME maths as the live `order_monitor._sweep_local_pnl_for_unpriced` sweep, so the one-shot and the sweep never disagree; PR #4017 — previously a raw `(exit−entry)×size` that undercounted IBKR futures by their `contract_value_usd`). Prefers `notes.bybit_closed_pnl` (net-of-fees) when present. Writes pnl + pnl_percent | re-count candidate rows (should be 0 unless degenerate inputs were skipped); helper's own stdout lists every touched row id | exit 0 + post_count=0 → clean. exit 0 + post_count>0 → some rows skipped for degenerate inputs (unknown direction, zero notional); helper output names them. exit 1 → script failed; no service touched, no rollback needed |
| `backfill-orphan-pnl` | count rows in `trade_journal.db::trades` matching `status='orphaned' AND exit_reason='stuck_strategy_watchdog' AND exit_price IS NULL AND COALESCE(is_backtest,0)=0` | `python3 scripts/ops/backfill_orphan_pnl.py --apply` (depends on `account_closed_pnl_for_trade` from PR #1299) — looks up each orphan's real close fill on Bybit V5 `/v5/position/closed-pnl`, then writes `status='closed'` + `exit_price` + `pnl` + `pnl_percent` + `exit_reason='backfill_closed_pnl_recovery'` + audit notes. No service touched | re-count candidate rows; helper's stdout lists every touched row id plus a "skipped" section naming any rows where Bybit had no matching record (typically because the 7-day window expired) | exit 0 + post_count=0 → clean. exit 0 + post_count>0 → unrecoverable orphans remain; helper output names them, manual cleanup needed. exit 1 → script failed; no service touched, no rollback needed |
| `set-account-mode` | read pre-edit `mode:` value for `<ACCOUNT_ID>` from `config/accounts.yaml`; defer if `claude-vm-runner@*.service` active | targeted single-line regex edit of `config/accounts.yaml` setting `mode: <MODE>` for `<ACCOUNT_ID>`; `systemctl restart ict-trader-live.service` (clears in-memory `_DRY_RUN_OVERRIDES`) | verify post-edit `mode:` matches; poll `is-active` until "active" or 30 s timeout; dump 30 journal lines; probe `runtime_logs/runtime_status.json` `live[<ACCOUNT_ID>]` for the dashboard projection | exit 3 → vm-runner active, deferred. exit 1 → invalid input (account or mode), YAML edit didn't stick, or unit failed to come back; YAML edit is in-place so if the restart fails the file is already mutated — inspect `runtime_logs/operator_actions/*.json` for the pre/post values |
| `fix-data-dir` | snapshot `.env` `DATA_DIR=` / `TRADE_JOURNAL_DB=` lines + per-unit `is-active` state + file inventories at both candidate roots (split path under `<repo>/data/` and canonical `/data/bot-data/`); defer if `claude-vm-runner@*.service` active | stop ict-trader-live + ict-web-api + ict-claude-bridge + ict-telegram-bot; back up `.env` to `.env.bak`; atomic tmp+rename strip of `DATA_DIR=` / `TRADE_JOURNAL_DB=` lines; verify canonical mount writable; `rsync -a` `<repo>/data/{runtime_logs,runtime_state,artifacts,data}/` → `/data/bot-data/<same>/`; rename `<repo>/data` → `<repo>/data.MIGRATED-<utc-ts>` (preserved for forensics); `systemctl daemon-reload`; start all four units in dependency order | poll each unit's `is-active` until "active" or 30 s timeout, dump 30 journal lines per unit; verify canonical heartbeat freshness `mtime < 180 s`; probe `http://127.0.0.1:8001/api/health` for 200 OK | exit 3 → vm-runner active, deferred. exit 1 → env-strip verification failed, canonical mount missing, rsync failed, or a unit didn't return to active. `.env.bak` is the rollback (one-time restore: `cp .env.bak .env && systemctl restart <units>`); the migrated split-path is intact under the `MIGRATED-<ts>` suffix |

The `restart-bot-service`, `pull-and-deploy`, `set-account-mode`,
and `fix-data-dir` wrappers all **defer** if any
`claude-vm-runner@*.service` unit is currently active, mirroring
the guard in `scripts/deploy_pull_restart.sh` — exit 3, no
restart / deploy / edit attempted. Re-dispatch the action a few
minutes later when the `/vm` invocation has finished.

`pull-and-deploy` runs the wrapper's vm-runner check **before** the
git fetch/reset, so a deferred run leaves the worktree exactly as it
was — no half-deployed state where HEAD has advanced but services
still run the old code.

---

## 7. Operator ping format (Tier 2)

Short, decision-oriented. Paste into the issue or Telegram thread
when requesting approval for a Tier-2 action.

```
Action requested: restart-bot-service
Why needed: <one sentence — what symptom triggered this>
Risk if not done: <one sentence — what breaks if we hold>
Expected impact: <one sentence — what changes when this runs>
Verification plan: <one line — what artifact / diag call confirms success>
[Approve] [Hold]
```

For `pull-and-deploy` add a fifth line so the operator knows what's
landing on the VM:

```
HEAD currently on VM: <pre-deploy SHA — get from /api/diag/status if you have it>
HEAD will land:       <origin/main SHA + one-line PR title>
```

For `reboot-vm` add a fifth line:

```
Lower-blast-radius alternatives tried: <list, e.g. "restart-bot-service x1, no recovery">
```

For `set-account-mode` add a fifth line so the target is explicit:

```
Target: account=<ACCOUNT_ID> mode=<live|dry_run> (prev: <pre-mode-from-yaml>)
```

For `fix-data-dir` add a fifth line summarising the misalignment:

```
Current .env DATA_DIR: <value, or 'unset'>; canonical (systemd drop-in): /data/bot-data
```

### 7.1 Issue-driven dispatch — body format

Once the operator has acked the action, Claude opens an issue with
label `system-action`. Body must contain (any line order):

```
action: <one of the allowlist names>
reason: <one line, free text — captured in the audit bundle and the transparency notify ping>
```

For `set-account-mode`, two additional lines are required:

```
account: <ACCOUNT_ID as keyed in config/accounts.yaml, e.g. bybit_2>
mode: <live|dry_run>
```

For `fix-data-dir`, no additional lines are needed — the wrapper
is fully parameter-free (its target is always the systemd-declared
canonical path).

The `Resolve action + reason` step in `system-actions.yml` parses
the lines case-insensitively from the first match. Tier-2 actions
**must** include a non-empty `reason`; the workflow rejects
empty-reason Tier-2 dispatches with exit 1 in the validation step.
For `set-account-mode`, the same step also enforces non-empty
`account:` + `mode:`, validates `mode` is `live` or `dry_run`, and
gates `account` on `[A-Za-z0-9_-]+`.

The issue title is informational only — recommended form:

```
[system-action] <action> — <one-line reason>
```

The workflow comments back on the issue with the run URL + wrapper
exit code + truncated action output, then closes the issue
(`completed` on success, `not_planned` on failure).

Recommended path for Claude (web sandbox):

```
mcp__github__issue_write(method='create',
    title='[system-action] pull-and-deploy — <reason>',
    labels=['system-action'],
    body='action: pull-and-deploy\nreason: <reason>')

# set-account-mode variant:
mcp__github__issue_write(method='create',
    title='[system-action] set-account-mode — flip bybit_2 to live',
    labels=['system-action'],
    body='action: set-account-mode\naccount: bybit_2\nmode: live\nreason: <reason>')

# fix-data-dir variant:
mcp__github__issue_write(method='create',
    title='[system-action] fix-data-dir — strip stale .env override',
    labels=['system-action'],
    body='action: fix-data-dir\nreason: <reason>')
```

Then poll the issue's comments for the github-actions[bot] reply.

---

## 8. Runner architecture (control-plane choice)

The workflow runs on `runs-on: ubuntu-latest` (GitHub-hosted) and
SSHs to the VM. This is **deliberate**.

**Why not self-hosted runner on the VM?**

- A self-hosted runner sharing the VM would orchestrate its own
  reboot. The runner process dies as the VM goes down; the workflow
  step that called `shutdown` returns nonzero; the post-reboot
  reconnect step is on a runner that may not be available again
  until well after the workflow times out. Recovery is ambiguous.
- The control-plane / data-plane separation keeps the question
  "did the workflow succeed?" answerable independently of "is the
  VM healthy?". For `reboot-vm` and `restart-bot-service` that
  separation is the whole point.

**Why not GitHub Actions matrix or Codespaces?**

- Overkill for a single-target, single-action workflow.
- Costs more in minutes than the SSH path.

**Why fixed-form SSH instead of `appleboy/ssh-action`?**

- Smaller dependency surface to audit. The diag-relay workflow set
  the precedent and it has been reliable; this workflow follows the
  same shape so reviewers don't need to re-evaluate.

---

## 9. Required GitHub repo configuration

All already in place except the optional reboot sudoers entry.

### Secrets (Settings → Secrets and variables → Actions → Secrets)

| Name | Used by | Required? |
|---|---|---|
| `VM_SSH_KEY` | this workflow + `vm-diag-snapshot` | yes |
| `DIAG_READ_TOKEN` | pre/post `/api/diag/status` verification | yes (else verification skipped) |

### Variables (Settings → Secrets and variables → Actions → Variables)

| Name | Default | Override when |
|---|---|---|
| `VM_SSH_HOST` | `141.145.193.91` | VM moved |
| `VM_SSH_USER` | `ubuntu` | VM user changed |

---

## 10. VM sudoers setup (one-time, manual)

`restart-bot-service` works today: `ubuntu` already has
`NOPASSWD: /bin/systemctl` from the existing deploy flow.
`set-account-mode` and `fix-data-dir` reuse the same sudoers entry
for their post-edit restarts.

`reboot-vm` requires one additional sudoers entry. Edit
`/etc/sudoers.d/ict-system-actions` (create if missing) on the VM,
mode `0440`, owner `root:root`, contents:

```
# system-actions reboot path — see docs/claude/system-actions.md § 10
ubuntu ALL=(ALL) NOPASSWD: /sbin/shutdown -r *
```

Validate with `sudo -n /sbin/shutdown -r --help` as `ubuntu`. Until
this entry exists, `reboot-vm` will exit 1 with a clear error — it
will not silently do nothing.

---

## 11. What this surface deliberately is *not*

- Not a general remote-shell. There is no command-string input.
- Not a code-deploy path. `git fetch` + `systemctl restart` is the
  job of the existing `ict-git-sync.timer` + `deploy_pull_restart.sh`
  flow. Don't conflate the two — the next sprint that wants to
  trigger a deploy from a workflow should write a *separate*
  workflow with its own gates.
- Not a strategy or risk-config pathway. Anything that mutates
  trading behaviour goes through a PR, period — with the named
  exceptions of `set-account-mode` for the `mode:` field of
  `config/accounts.yaml` (§ 2.1) and `fix-data-dir` for the
  `DATA_DIR=` / `TRADE_JOURNAL_DB=` overrides in `.env` (§ 2.2).
- Not a replacement for the Telegram `/vm` dispatcher. That path
  remains the way the operator triggers freeform agentic VM work.
  Operator-actions is the **inverse**: a PM-side session triggering
  *only* a fixed action.

---

## 11.5 Data-hygiene ops (S-PERSIST-CANON, 2026-05-23)

Two persistence ops introduced with the canonical-store work. Neither
needs a new allowlist entry today:

- **Trainer-store ingest** — the federated sidecar `trainer_store.db` is
  rebuilt **lazily on read** by the web-API (mtime-gated, see
  `src/units/db/trainer_store.py`), so no operator action or timer is
  required for the Data Explorer to stay fresh. A manual/cron rebuild is
  available autonomously via `python -m src.units.db.trainer_store` on the
  live VM (e.g. through the trainer-VM diag relay's SSH-to-live path) if a
  push-time ingest is ever preferred.

- **One-time stray-journal cleanup (operator-approved)** — the live VM
  carries two stale duplicate journals created by the old CWD-relative
  fallback (now eliminated): `/home/ubuntu/ict-trading-bot/trade_journal.db`
  and `/home/ubuntu/ict-trading-bot/src/bot/trade_journal.db`. They are
  **not** read by any service (every consumer resolves
  `TRADE_JOURNAL_DB=/data/bot-data/trade_journal.db`). After the
  canonical-resolver change is deployed (so nothing recreates them), Claude
  removes them via the diag relay (`rm` of those two exact paths) — a
  destructive op, so it runs only after explicit operator approval in chat.
  Do **not** touch `/data/bot-data/trade_journal.db` (the canonical DB).

## 12. Cross-references

- `docs/CLAUDE-RULES-CANONICAL.md` — Prime Directive: live is the
  only default; `set-account-mode` is the explicit, named, audited
  path for any transition off live.
- `docs/claude/vm-operator-mode.md` § 9 — PM-side read-only diag
  contract (the bridge that **predates** this one and shares the
  same SSH wiring).
- `docs/claude/diag-relay.md` — full operator + session flow for
  the read-only relay; shape mirrors the system-actions flow on
  the request side.
- `docs/claude/operating-protocol.md` § 4 — merge-authority tiers
  (the *PR* tiers; this doc is the *dispatch* tiers, distinct).
- `scripts/deploy_pull_restart.sh` — canonical deploy flow; the
  `claude-vm-runner` defer guard there is mirrored here.
- `.github/workflows/system-actions.yml` — the workflow itself.
- `scripts/ops/*.sh` — wrapper scripts (one per action).
- `tests/ops/` — workflow + script validation.

