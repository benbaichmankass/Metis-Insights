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
| `stop-bot-service` | 2 | `scripts/ops/stop_bot.sh` | **STOP the trader and hold it stopped** — the half `restart-bot-service` could not give you. Added 2026-08-26 because a repair needing the trader ABSENT for a bounded window had NO dispatch path: a stranded IB protective group owned by the trader's own execution clientId 497 can only be cancelled by connecting AS 497, and while the trader holds it IBKR **REFUSES the duplicate (Error 326) rather than evicting**, so the cancel simply cannot land. Racing a restart window across two ~90 s Actions runs is not a control mechanism. ⚠️ **REFUSES unless `pause-autoheal` has already run** (exit 4): the liveness watchdog issues `systemctl restart` on a stale heartbeat, so a stop taken without it is SILENTLY UNDONE minutes later — and the operator sees a stop that "did not work" rather than one that was reverted, which are different failures. The gate reads the timer's **is-active**, not just is-enabled, because a disabled-but-still-running timer is the one that would undo the stop. (`Restart=always` on the unit is NOT the same hazard — systemd does not restart after an explicit stop.) ⚠️ **A stopped trader evaluates NO exits** — no monitor tick, no reconciler, no naked-autoprotect re-arm. What still protects an open position is its BROKER-SIDE resting bracket (IB GTC OCA legs, Bybit position/partial SL-TP), which lives on the venue and keeps working while the process is down; that is the entire safety argument for a bounded stop, which is why the script **reports the open positions it is leaving in that state** and records them in the audit rather than letting the window be taken on an unstated book. A journal it cannot read reports `unread`, never "no open positions". Defers on an active `claude-vm-runner@*` like its restart sibling. Stop-only — touches no strategy param, no account mode, no order. |
| `start-bot-service` | 2 | `scripts/ops/start_bot.sh` | The symmetric companion that CLOSES a `stop-bot-service` window. Deliberately a **separate** action rather than a timeout inside the stop: a stop whose restart is bundled into it cannot be held open for the work the stop was taken for. Reconciles systemd units before starting (same reason `restart_bot.sh` does — a merged unit-file change can sit on disk un-applied when git-sync has already advanced HEAD). ⚠️ **WARNS, and does not refuse, when the liveness watchdog is still paused**: while paused the genuine dead-man switch is OFF, so a trader that dies after this start gets no alert and no auto-restart. It warns rather than blocks because `resume-autoheal` is its own action and the operator may legitimately sequence it after confirming the trader is heartbeating — but "started" and "protected" are different states and the output says which one you have. Start-only. |
| `reboot-vm` | 2 (last resort) | `scripts/ops/reboot_vm.sh` | full host |
| `enable-closed-flat-invariant` | 2 | `scripts/ops/enable_closed_flat_invariant.sh` | `.env` (`CLOSED_FLAT_INVARIANT_ENABLED=true`) + restart `ict-trader-live.service` |
| `disable-closed-flat-invariant` | 2 | `scripts/ops/disable_closed_flat_invariant.sh` | `.env` (remove `CLOSED_FLAT_INVARIANT_ENABLED`) + restart `ict-trader-live.service` |
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
| `backfill-exit-labels` | 2 | `scripts/ops/backfill_exit_labels_action.sh` | `UPDATE trades SET exit_reason` on rows that were PRICED after they were closed. `_close_trade_from_order_status`'s no-record fallback hard-codes `reconciler_filled` and leaves `exit_price` NULL — correctly, since at that moment there is nothing to classify against — and the sweeps that later supply the price used to leave the label frozen (#10151 fixed the Bybit-truth sweep; its sibling commit fixed the anchored-price one). This backfills history. **The price's provenance gates the label:** MEASURED → `price_vs_pkg_bracket`, ESTIMATED → `price_vs_pkg_bracket_est_price`, FABRICATED/UNVERIFIED → **REFUSED** (`refused_unmeasured_price`), because `local_markprice` is the market at SWEEP time, hours after the exit, and comparing it to the bracket manufactures an sl/tp verdict out of unrelated price action. The refusal is STAMPED, not skipped — the absence of `exit_reason_source` is the 100% signature that made this class readable. Reduce legs excluded (their bracket can be inverted). Touches **no** monetary field; records the prior label under `notes.pre_backfill_exit_reason`, so it is reversible per row. Wrapper runs `--self-test` (12 planted controls) as a PRECONDITION, then a dry-run plan, then `--apply`. Idempotent (a stamped row is skipped). No service touched. Measured 2026-08-23: 497 eligible, 191 relabelled, 105 refused. Added by the 2026-08-23 full-system audit. |
| `backfill-closed-at` | 2 | `scripts/ops/backfill_closed_at_action.sh` | `UPDATE trades SET closed_at=?` for historical `status='closed' AND closed_at IS NULL` rows (non-backtest), deriving the value from the same chain the read path uses (linked `order_packages.updated_at` via EITHER `op.linked_trade_id` or `trades.order_package_id`, else `notes.closed_at`; never fabricated). The `closed_at` column (added 2026-06-16, P1-B) is the single source of truth for a trade's close timestamp — every close path now stamps it going forward, so this one-shot repair makes old rows match the new write-path and the read path stops deriving on the fly. Runs with `--also-account-class` (operator widest-scope directive 2026-06-17), so the SAME audited pass also closes any remaining `account_class` gap (delegates to `backfill_account_class.py`). Wrapper runs a DRY-RUN preview (counts scanned/fillable/left-NULL + a sample) then `--apply`; idempotent (`AND closed_at IS NULL` guard); defensively ensures the column exists first. No service touched. Added 2026-06-17 dashboard-truth Phase P1-E (wraps `scripts/ops/backfill_closed_at.py`). |
| `backfill-broker-truth-costs` | 2 | `scripts/ops/backfill_broker_truth_costs_action.sh` | FIFO-attribute **broker-truth** round-trip fees onto cleanly-attributable closed trades (Slice B / B2, MB-20260629-ALLOC-COSTCAP). Joins `trade_journal.db::trades` to the exchange-fills store (`runtime_state/exchange_fills.sqlite`) by the `trades.broker_order_id` join key (B0) — entry leg exact, exit leg FIFO-paired — and writes `fee_taker_usd` + `fee_maker_usd` + `cost_source='broker'` ONLY for **clean** attributions (both legs matched, unambiguous, USD fees). Ambiguous (netted) / entry-only / non-USD trades keep their `estimate`. Overwrites an `estimate` with `broker` but **never** an existing `broker` row; **NEVER** touches `pnl`, `funding_paid_usd`, the order path, or any live state. Does NOT populate funding (that needs the B1 funding puller). **PREREQ:** `pull-exchange-fills` (populate the store) + `backfill-broker-order-id` (populate the key) first. Wrapper runs a DRY-RUN coverage report (clean/ambiguous/entry-only/…) then `--apply`; no service touched. Added 2026-07-17 (wraps `scripts/ops/backfill_broker_truth_costs.py` + `src/runtime/broker_cost_attribution.py`). |
| `backfill-fabricated-exits` | 2 | `scripts/ops/backfill_fabricated_exits_action.sh` | Re-derive **FABRICATED** exit prices from the broker fills the system has been storing all along (`BL-20260730-BROKER-TRUTH-COLLECTED-NEVER-READ`). Matches each fabricated / unverified closed row against `exchange_fills` on account+symbol+side+window via the same `exit_from_fills` the LIVE close path uses, so a repaired row is reached by exactly the route a fresh close takes. **TWO TIERS, kept apart:** own fills → `exit_price_source='exchange_fill'` (**MEASURED**); mirror account (`bybit_portfolio`←`bybit_2`, `alpaca_portfolio`←`alpaca_live`) → `'mirror_account_fill'` (**ESTIMATED**, only with `allow_mirror: 1`) — a sibling account's execution is an inference, not a measurement of this order, and qty is never copied. **DRY-RUN BY DEFAULT** (runs both tiers so the mirror-only count is the delta between two printed blocks); `apply: 1` makes it a Tier-2 money-DB write and needs an operator OK. Only FABRICATED/UNVERIFIED rows are candidates, so it can only improve provenance and never overwrites a measured row; each write records `notes.backfill` with the prior source + run id (auditable, reversible); unresolvable rows are LEFT ALONE, never guessed. `pnl` is NOT recomputed here — the monitor's local sweep re-derives it from the corrected exit on its next tick. Touches no order path and restarts no service. **2026-08-08:** the unresolved residual is now broken out **by refusal STAGE and by account** (`no_fill_in_window` / `fills_present_but_qty_unreconciled` / `direction_or_symbol_unmappable` / `account_has_no_fills_stored` / `coverage_unknown_store_unreadable`), with the covered-account set measured from the store itself. `exit_from_fills` returns a bare `None` from four different refusals and a single label over all of them names a cause no code path tested — the distinction is load-bearing, since `fills_present_but_qty_unreconciled` is a **deliberate** netting refusal (one exchange position backs N journal rows) and is NOT closable by pulling deeper. Added 2026-07-31. |
| `backfill-broker-order-id` | 2 | `scripts/ops/backfill_broker_order_id_action.sh` | `UPDATE trades SET broker_order_id=? WHERE broker_order_id IS NULL` (value = `json_extract(notes,'$.trade_id')`) in `trade_journal.db` (Slice B / B0, MB-20260629-ALLOC-COSTCAP). The broker's entry orderId has always ridden inside `notes.trade_id`; the new `broker_order_id` column (`database._migrate_add_broker_order_id`) promotes it to a first-class, indexed join key so the Slice-B broker-truth cost sweep can tie a trade to its `exchange_fills` rows EXACTLY (`exchange_fills.order_id` = Bybit orderId) instead of a fuzzy `(account,symbol,side,qty,time-window)` heuristic. Forward rows get it at open; this one-shot fills the historical book. **Observability-only** — writes only `broker_order_id`; NEVER touches `pnl`, cost columns, the order path, or any live-trading state. Idempotent + non-destructive (fills only NULL rows). Writes NO cost (the fee/funding sweep that consumes this key is a separate follow-up). Wrapper runs a DRY-RUN preview (candidate / would-write / skipped-no-id counts) then `--apply`; no service touched. Added 2026-07-17 (wraps `scripts/ops/backfill_broker_order_id.py`). |
| `backfill-trade-costs` | 2 | `scripts/ops/backfill_trade_cost_estimates_action.sh` | `UPDATE trades SET fee_taker_usd=?, cost_source='estimate' WHERE status='closed' AND COALESCE(is_backtest,0)=0 AND cost_source IS NULL AND fee_taker_usd IS NULL` in `trade_journal.db` (MB-20260629-ALLOC-COSTCAP). The live close path stamps the fixed-model round-trip cost estimate on every close (`database._record_trade_cost_estimate`, M18 P0a), but that writer only went live recently — trades that closed before it have no cost at all (as of 2026-07-17: 86/798 costed, 712 uncosted). This one-shot applies the SAME pure estimator (`src.runtime.trade_costs.estimate_roundtrip_fee_usd` over each row's `entry_price`/`position_size`/`contract_value_usd`) to every uncosted closed non-backtest row, so a net-R label over the historical book has a consistent modelled cost. **Observability-only** — writes only `fee_taker_usd` + `cost_source`; NEVER touches `pnl`, the order path, or any live-trading state. Idempotent + non-destructive (skips any row already carrying a cost, so it never overwrites broker truth / a prior estimate). Does NOT populate `funding_paid_usd` / `fee_maker_usd` (those need the broker-truth writer, a separate follow-up). Wrapper runs a DRY-RUN preview (candidate / would-write / skipped-uncomputable counts) then `--apply`; no service touched. Added 2026-07-17 (wraps `scripts/ops/backfill_trade_cost_estimates.py`). |
| `migrate-closed-at-iso` | 2 | `scripts/ops/migrate_closed_at_to_iso_action.sh` | Normalises existing `trades.closed_at` **epoch-ms** rows (and `notes.closed_at`) to ISO-8601 (`BL-20260620-RECONCILER-CLOSEDAT-MS`). The reconciler-filled close path historically wrote Bybit's `updatedTime`/`execTime` as a raw epoch-ms string (e.g. `"1782128223798"`) into the ISO column; the writer was fixed (PR #4168) and the read endpoints guard it (PR #4162), and this one-shot rewrites the already-persisted ms rows so the column is uniformly ISO. **Distinct from `backfill-closed-at`** which fills `closed_at IS NULL` rows — this converts the OPPOSITE case (populated as ms). Wrapper runs a DRY-RUN preview (counts scanned / ms→ISO + a sample) then `--apply`; idempotent (only all-digit ≥12-char values are touched, so a re-run is a no-op); no service touched. Added 2026-06-22 (wraps `scripts/ops/migrate_closed_at_to_iso.py`). |
| `pull-exchange-fills` | 2 | `scripts/ops/pull_exchange_fills_action.sh` | Pulls Bybit fills for **every live Bybit account** over a `days:`-selectable window (**default 7**; deeper windows are WALKED — see below) (`--all-bybit-accounts`: `bybit_1` / `bybit_2` / `bybit_portfolio`, each with its own `BYBIT_API_KEY_*` creds and its own host — the two `demo: true` accounts route to `api-demo.bybit.com` via `src/runtime/bybit_ccxt.py`) into the exchange-fills store `runtime_state/exchange_fills.sqlite` via `scripts/pull_exchange_fills.py` — feeds the exchange-truth P&L surface `/api/bot/pnl/exchange`. Read-only on the exchange side (`fetch_my_trades`); touches NO service and NO `trade_journal.db` table; idempotent (store PK `exec_id` drops duplicates, over-sampling windows are safe). Added 2026-07-13 (`BL-20260713-EXCHANGE-FILLS-STORE-EMPTY`): the S-067 puller had never been wired to a timer or action and pulled the spot category only, so the store stayed empty while bybit_2 traded linear perps daily. Now on a daily timer (`ict-exchange-fills-pull.timer`) as well as on-demand — run it before any orphan/PnL reconciliation pass that wants exchange-truth fills. **2026-08-07 (BL-20260807-BYBIT-DEMO-FILLS-NEVER-PULLED):** the 2026-07-29 multi-account rollout enumerated the two demo accounts but dialled them on mainnet, so both returned `retCode 10003 "API key is invalid"` on every nightly run while the summary printed `ran=3/3 total_inserted=0` and systemd reported **success** — `bybit_1`, the account carrying the largest losses in the book, had ZERO exchange-truth coverage and `/api/bot/pnl/exchange` served that absence as clean zeros. Demo routing is fixed and a reachable-but-failing account now exits **1**, so read the per-account `ok=/failed=/skipped=` summary rather than trusting the exit status alone. **2026-08-08 (BL-20260808-FILLS-WINDOW-TOO-SHORT-TO-REPAIR-HISTORY):** the window is now settable via a `days:` body line, and a deeper window is **walked in ≤7-day chunks** (`MAX_RANGE_DAYS`) rather than asked for in one call — Bybit caps the query RANGE at 7 days while RETAINING 2 years, so a single `--days 90` call returned the 7-day slice `[now-90d, now-83d]` and reported `candidates=0` on all three accounts. Caught by monotonicity: a 90-day window cannot hold fewer fills than the 7 days nested inside it (63/3/13 at 7 days vs 0/0/0 at 90). Two things to know before reading a deep pull's numbers: it costs `ceil(days/7)` requests **per account** (`days: 365` is 53 calls, not 1), and each chunk is one `fetch_my_trades` at `PAGE_LIMIT=200` with **no intra-chunk pagination**, so a chunk returning exactly 200 is PAGE-CAPPED and logs a loud warning naming the window — that count is a floor, not a total. |
| `pull-exchange-funding` | 2 | `scripts/ops/pull_exchange_funding_action.sh` | Pulls Bybit **perp FUNDING** for **every live Bybit account** over a `days:`-selectable window (**default 30**; was hardcoded to 30 with no override until 2026-08-09) (demo accounts routed to `api-demo.bybit.com`; same fix + exit-code contract as `pull-exchange-fills`) into the exchange-funding store (`runtime_state/exchange_fills.sqlite :: exchange_funding`) via `scripts/pull_exchange_funding.py` — the sibling of `pull-exchange-fills`. Perp funding is NOT in the execution/fills list (it's a separate `fetch_funding_history` / transaction-log stream), so the broker-truth cost sweep needs its own pull to attribute `funding_paid_usd` onto cleanly-attributable closed trades (Slice B / B1, MB-20260629-ALLOC-COSTCAP). Read-only on the exchange side; touches NO service and NO `trade_journal.db` table; idempotent (store keys on `funding_id`, so overlapping windows are safe). **PREREQ for the funding half of `backfill-broker-truth-costs`** — run it (alongside `pull-exchange-fills`) before the cost sweep. **READ THE SERVED SPAN, NOT THE ROW COUNT.** Each run now logs `requested Nd ending <ts> — SERVED span <oldest> .. <newest> (N rows)`, and WARNs when the newest row falls far short of the window end. That line exists because a row count cannot distinguish "no funding accrued" from "the venue never queried the recent period": Bybit V5 caps the queryable RANGE (while retaining 2 years) and MOVES a too-wide window to the OLD end rather than widening it — measured on the execution endpoint 2026-08-08, where a 90-day request returned a 7-day slice (BL-20260808-FILLS-WINDOW-TOO-SHORT-TO-REPAIR-HISTORY). Whether the same cap applies to the funding/transaction-log endpoint is **UNVERIFIED** (BL-20260808-FUNDING-PULLER-SAME-RANGE-CAP-EXPOSURE); if it does, the 30-day default has been serving `[now-30d, now-23d]` and missing the most RECENT three weeks. The warning is deliberately **cause-neutral** — it names both readings and picks neither, since the puller cannot know whether positions were open. To settle it, run `days: 7` and `days: 30` and compare: a wider window cannot legitimately hold FEWER rows than a narrower one nested inside it (the monotonicity argument that caught the fills bug). Added 2026-07-17 (wraps `scripts/pull_exchange_funding.py` + `src/runtime/exchange_funding_puller.py`); `days:` + served-span logging 2026-08-09. |
| `net-r-regrade` | 1 | `scripts/ops/net_r_regrade_action.sh` | **Read-only** M24 P2 net-R re-grade scorecard (`scripts/research/net_r_regrade.py`). Opens `trade_journal.db` strictly `mode=ro` and recomputes the per-strategy / per-`(strategy,symbol)` aggregates on TRUE net-of-cost R (`gross_pnl` minus the Slice-B broker/estimate fee + funding columns, over the SL-distance risk denominator), printing the markdown scorecard — coverage buckets (broker / estimate / uncosted / r_uncomputable), Σgross_R vs Σnet_R + cost-drag, and the **sign-flip flag** (gross-positive but net-negative after real costs → a Tier-3 review candidate). NO DB write, NO order path, NO service, NO config change — sign-flips are flagged for the operator, not enacted. Design: `docs/research/M24-net-r-cost-aware-DESIGN.md` (P2). Added 2026-07-17. |
| `backfill-shadow-predictions` | 2 | `scripts/ops/backfill_shadow_predictions_action.sh` | Replays every historical trade in `trade_journal.db` against every `target_deployment_stage=shadow` model and writes `runtime_logs/shadow_predictions_backfill.jsonl` (the `ml backfill-shadow-predictions` CLI; writer truncates each run). **Observational only** — no trade-journal mutation, no service restart, no exchange calls. Read by `/api/bot/trades/scores` (`backfill_kind`) so the dashboard shows shadow decisions for the full live history. Registry root + output path resolve through the same Python the live shadow factory uses, so no path drift. Added 2026-05-21 alongside the shadow auto-wire fix (#1630). |
| `pull-mes-ibkr-history` | 2 | `scripts/ops/pull_mes_ibkr_history.sh` | Paced IBKR historical pull for MES, run ON the live VM (shares the one IB gateway on a DISTINCT clientId 450, `pause_s=20`, `use_rth=false`, ~365d of 5m+15m → `/data/bot-data/ibkr_datasets/market_raw/MES/...`, synced to the trainer for the regime models — MB-20260528-002). **Secondary by construction:** the wrapper **detaches** (returns immediately; the ~20-30 min paced run survives), re-execs under `nice -n 19 ionice -c3`, and **aborts if the live trader heartbeat is stale (>10 min)** so it never adds gateway load during a live-trading incident. No trade-journal mutation, no service restart, no order-path touch. Monitor via `diag log_file?name=ibkr_mes_pull`. Best run in the CME maintenance break / weekend. Added 2026-05-28. |
| `pull-mes-ibkr-history-daily` | 2 | `scripts/ops/pull_mes_ibkr_history.sh` | Same wrapper + the same live-gateway guards as `pull-mes-ibkr-history`, but baked to a **DAILY multi-year** pull (`MES_TIMEFRAMES=1d`, `MES_HIST_START=2019-05-06` ≈ MES inception, `MES_MAX_CONTRACTS=28` to stitch the quarterly expiries back to 2019, `DATASET_VERSION=v003` — must be `vNNN`, digits only, per `metadata.py`) → `/data/bot-data/ibkr_datasets/market_raw/MES/1d/v003/data.jsonl`. The `ibkr_offvm` adapter stitches dated MES expiries for depth. Added 2026-06-01 to validate `mes_trend_long_1d` (the execution:shadow daily long-only diversifier) on **native MES** rather than the SPX-CFD proxy before any shadow→live. |
| `pull-ibkr-history` | 2 | `scripts/ops/pull_mes_ibkr_history.sh` | **Generalized symbol-parameterized** sibling of `pull-mes-ibkr-history` — same wrapper, same live-gateway guards (detach, `nice -n 19 ionice -c3`, live-first heartbeat abort, distinct clientId 450, `pause_s=20`, single-instance lock), but the `symbol:` / `timeframes:` / `hist_start:` / `dataset_version:` / `max_contracts:` come from the issue body so the **metals sleeve (MGC/MHG)** can be backfilled the same way MES is → `/data/bot-data/ibkr_datasets/market_raw/<SYMBOL>/<tf>/<version>/data.jsonl`. `symbol` is allowlisted to the IB futures roots `_build_contract` maps (`MES`/`MGC`/`MHG`); `timeframes` to `1m 5m 15m 30m 1h 4h 1d`; `hist_start` must be `YYYY-MM-DD`, `dataset_version` `vNNN`, `max_contracts` an int. Blank params fall back to the wrapper defaults. No trade-journal mutation, no service restart, no order-path touch. Monitor via `diag log_file?name=ibkr_mes_pull`. Added 2026-07-07 to backtest the metals sleeve (`mgc_trend_1h` / `mgc_pullback_1d` / `mhg_pullback_1d`) on native IBKR history before any shadow→live. |
| `set-account-mode` | 2 | `scripts/ops/set_account_mode.sh` | in-place edit of `config/accounts.yaml` `mode:` for the named account + restart `ict-trader-live.service`. Added 2026-05-12 in response to the silent-flip incident (see § 2.1). |
| ~~`enable-mes` / `disable-mes`~~ | — | *removed 2026-05-22* | **Deleted — these were a forbidden second gate.** The `MULTI_SYMBOL_ENABLED` env they flipped no longer exists; the symbol set is derived from `config/accounts.yaml` (`_resolve_tick_symbols` unions every configured account's `symbols`). Per the "one switch per account" rule, the only way to gate MES is the account's `mode:` (via `set-account-mode` on `ib_paper` → stops execution, signals still log) or removing its `strategies` / `symbols` in a PR. |
| `fix-data-dir` | 2 | `scripts/ops/fix_data_dir.sh` | strips `DATA_DIR=` / `TRADE_JOURNAL_DB=` overrides from `.env` (backup retained), rsyncs `/home/ubuntu/ict-trading-bot/data/{runtime_logs,runtime_state,artifacts,data}/` → `/data/bot-data/<same>/` to align with the systemd drop-in's canonical mount, renames the legacy split path with a `MIGRATED-<ts>` suffix, then restarts every canonical unit. Added 2026-05-12 in response to the path-bifurcation incident (see § 2.2). |
| `get-env` | 1 | `scripts/ops/get_env_action.sh` | **READ-ONLY: the missing READ half of `set-env`.** `set-env` could WRITE an env var on the live VM and nothing could READ one back — a Tier-3 order-path setting whose scope can be written from a session but never read is the write-without-a-reader asymmetry `provenance-consumer-guard` exists to catch, one level up at the ops surface (`BL-20260810-CONVICTION-SIZING-APPLY-LIVE-VS-DOC`; the concrete cost was `CONVICTION_SIZING_ACCOUNTS`, where an EMPTY value means *every* account including real money and no surface could establish the live value). Reports **both** sources side by side, per the same doctrine `bybit-bracket-audit` uses for `BYBIT_TPSL_MODE`: the running process's `/proc/<MainPID>/environ` (**authoritative** — what the process actually holds) and the unit's systemd-declared `EnvironmentFiles` (what the *next* restart picks up), asked of systemd rather than hardcoded. A disagreement is reported as **`pending_restart`** — the `.env` was edited and the service never re-read it — and is only asserted when BOTH sides were readable (`undetermined` otherwise; an unreadable side is never evidence of agreement). Each side reports one of four states that are **never collapsed**: `set` / `set_empty` (present but EMPTY — for an allowlist var this is the WIDEST setting, not the absence of one) / `unset` (we looked, it is not there) / `unreadable` (we could NOT look). Params: `env_key:` (required — a name from the fixed `ALLOWED_KEYS` in `scripts/ops/get_env.py`, or `ALL`; **no freeform key input by design**, adding a key is a reviewed one-line edit), `service:` (optional, default `ict-trader-live`; allowlisted units only). **This action's stdout is commented back onto a PUBLIC issue, so the binding rule for `ALLOWED_KEYS` is that a key belongs there only if its value is safe to publish** — and as belt-and-braces any secret-NAMED key (`TOKEN`/`SECRET`/`KEY`/`PASSWORD`/…) is served presence + a sha256 fingerprint, never its value, which still answers "is `DASHBOARD_API_TOKEN` set?" (`BL-20260705-DASHBOARD-API-TOKEN-UNSET`) without leaking it. No socket, no write, no restart; the audit JSON records the key + outcome, never a value. `python3 scripts/ops/get_env.py --self-test` proves the four states stay distinguishable and is shown to FAIL on a planted collapse. Added 2026-08-10. **2026-08-21: `ACCOUNT_DOWN_ALERT_SKIP` + `SILENT_REFUSAL_SKIP` added to `ALLOWED_KEYS`.** They are not cadence knobs — they DISABLE a named account's alarm, so their live value is the difference between *"nothing is wrong"* and *"the only thing watching this was switched off"*. A skip writable by `set-env` and readable by nothing is `BL-20260813-ENV-VARS-SHIP-WITHOUT-A-READ-SURFACE` in its worst form, because the failure it hides is silence. Added when `alpaca_live` (real money, 127 of 127 orders refused for zero balance) was deliberately silenced by operator decision — defensible, but undiscoverable by a future review without this. Values are account-id CSVs and carry no secret. **2026-08-25: `IB_MD_CLIENT_ID` added to `ALLOWED_KEYS`.** It pins the clientId the **web-api's** IB market-data socket uses so it cannot collide with the trader's own (`ib_paper` exec 497 / md 498). It is here because the collision is invisible and the fallback is silent: `market_data._ib_connection_identity` resolves `settings → env → exec_client_id + 1`, so a caller passing no settings — `local_pnl.last_mark_price` passes `{}` — lands on **498**, the trader's live socket, the moment this var is unset. **Nothing in the repo provisions it**: the only thing supplying it is a hardcoded `"600"` inside `routers/candles.py::_settings()`, which protects that one caller and no other — which is why `/api/bot/candles` returns real MGC/MHG/MES bars while the uPnL mark-price fallback returns `unavailable` on the same symbols in the same process (measured 2026-08-25, all three `ib_paper` legs). A clientId integer is safe to publish, and without this key the question *"is the reservation actually set?"* has no answer at all. |
| `send-ping` | 1 | `scripts/ops/send_ping_action.sh` | **No mutation, no restart.** Enqueues one immediate Telegram message via `scripts/send_ping.py` (`target=claude` default → @claude_ict_comms_bot; the bridge drains within ~5 s). This is the autonomous "Claude wants to say something NOW" path — far faster than the ≤5-min `pending-pings.jsonl` git-relay. Params: `message:` (required), `priority:` (low\|normal\|high\|urgent, default normal), `target:` (claude\|trader, default claude). The transparency notify is skipped for it (the action IS the message). Added 2026-05-24. |
| `send-prop-test-ping` | 1 | `scripts/ops/send_prop_test_ping_action.sh` | **No mutation, nothing journaled, no exchange socket.** Fires ONE synthetic, clearly-labelled TEST prop ticket through the REAL prop-account ping path — `scripts/prop/send_test_ping.py` → `src.prop.breakout_executor.emit_prop_ticket` → `emit_prop_signal` (typed FCM push + the prop Telegram bot). Exercises the Breakout prop "trade flow" up to and including the notification (ruleset resolution → per-account leg + sizing → ticket render → fan-out) without touching the execute path, so no order package is written. Params: `symbol:` (optional, default `SOLUSDT`), `strategy:` (optional, default `trend_donchian_sol`). Safe to run repeatedly. Added 2026-06-17 to verify the prop ping end-to-end (operator sees the ticket land in Telegram + Android). |
| `set-env` | 2 | `scripts/ops/set_env.sh` | Idempotent single-key upsert into the VM `.env` (preserves all other lines/comments) + restart the named `service:` so systemd re-reads its `EnvironmentFile`. The autonomous "Claude owns + configures the VM env" path. Params: `env_key:` (required, `^[A-Z][A-Z0-9_]*$`), `env_value:` (omit for secret-backed keys — see below), `service:` (allowlisted unit, or `none` to skip restart), **`env_file:`** (optional — `shared` (default, the repo `.env`) | `web-api` (`/etc/ict-trader/web-api.env`)). ⚠️ **`env_file` exists because this action could choose which SERVICE to restart but not which FILE to write** (2026-08-25, `BL-20260825-SET-ENV-CANNOT-TARGET-A-SERVICE-SCOPED-ENV-FILE`), which is exactly the wrong shape for a key that must DIFFER between two services sharing a file — and `ict-web-api.service` shares the repo `.env` with `ict-trader-live.service` **by design** (it loads `/etc/ict-trader/web-api.env` first, then the repo `.env`, so operator overrides stay aligned between writer and reader). The worked example is `IB_MD_CLIENT_ID`: nothing puts it in a settings dict except `routers/candles.py` (web-api only), so the TRADER reads it from the environment and today falls through to `exec_client_id + 1` = **498**. Writing `600` to the SHARED file moves the trader's market-data socket 498 → 600, colliding with the web-api's own 600 across two processes — **IB error 326**, starving the MES/MGC/MHG candles the reservation exists to protect. A shared-file write would have been WORSE than doing nothing. **Targets are SYMBOLIC NAMES, never paths** — the issue body is untrusted input and accepting a path would make this an arbitrary-file writer on the live VM; adding a target is a reviewed one-line edit, the same doctrine `get_env.py::ALLOWED_KEYS` uses for the read half. ⚠️ **An unknown target is a HARD ERROR, never a fallback to `shared`** — a typo that silently wrote the shared file would reintroduce the very collision the parameter prevents, and would report success doing it (pinned by `tests/test_set_env_sh.py`, which is shown to FAIL on the pre-change script). A non-`shared` target is root-owned, so the read/write hop through `sudo` and the write lands via `tee` (writing THROUGH the existing inode, preserving owner + mode — `mv` or a redirect would replace them); a missing root-owned file is **refused, not created**, since guessing the mode of a file that may hold credentials is not this action's job. The audit JSON records the target NAME (never a value, unchanged). **Values are never logged or recorded in the audit JSON.** Secret-backed keys (e.g. `TELEGRAM_CLAUDE_BOT_TOKEN`) take their value from the matching `secrets.<KEY>` GitHub Actions secret when `env_value` is blank, so the secret never transits the (public) issue body or run log. Added 2026-05-24. |
| `pause-autoheal` | 2 | `scripts/ops/pause_autoheal.sh` | `systemctl disable --now ict-liveness-watchdog.timer` — pauses the per-minute liveness watchdog (stale-heartbeat alert **and** auto-restart of `ict-trader-live.service`). Added 2026-06-05 for the restart-loop incident: when the trader's first pipeline tick runs longer than the autoheal window (e.g. a logged-out IB Gateway making every MES fetch time out, inflating the tick past ~3 min), the watchdog restarts the trader before it can complete a tick + write a heartbeat, so the heartbeat stays permanently stale and the autoheal fires forever (self-perpetuating loop). Pausing lets the running instance finish its slow first tick, write a heartbeat, and stabilise. **Pauses the dead-man switch** — resume promptly. Idempotent; does not touch `ict-trader-live.service` or any config. |
| `resume-autoheal` | 2 | `scripts/ops/resume_autoheal.sh` | `systemctl enable --now ict-liveness-watchdog.timer` — symmetric undo of `pause-autoheal`; restores the dead-man switch + autoheal. Run once the trader is confirmed heartbeating (no boot-grace applies, so a still-stale heartbeat would autoheal on the next streak). Idempotent. |
| `sync-clock` | 2 | `scripts/ops/sync_clock.sh` | Diagnose + correct live-VM clock drift. Added 2026-06-05 after the VM clock was found ~6.5 s behind (pybit `ErrCode 10002`, exceeds Bybit `recv_window`) and NTP wasn't disciplining it even post-reboot. Reads `timedatectl status` + the NTP daemon's source/offset (`chronyc tracking/sources` or `timedatectl timesync-status`; no sudo), then `systemctl enable --now` + `restart`s the time daemon (chrony / systemd-timesyncd) to force a fresh sync. **Limited to `systemctl`** (the only NOPASSWD sudo), so it cannot `date -s`/`chronyc makestep`; if the daemon's sources are unreachable (offset unchanged, `NTPSynchronized!=yes`), NTP egress (UDP 123) is likely blocked at the OCI security list — the one external step. No trade-path/config impact. |
| `flatten-ib-position` | 2 | `scripts/ops/flatten_ib_position_action.sh` | One-shot guarded flatten of a single IB exchange position. Body params: `account: <id>` + `symbol: <SYM>` (both required) + optional `apply: true` (default **dry-run**). Reads the LIVE exchange position via the bot's read path, and on `apply: true` places the close through the unified `close_open_position` (IB: cancel the resting protective bracket/OCA legs, then an opposing reduce market order **clamped to the live qty** so it can never flip), using a process-unique OPS clientId (9900-range) distinct from the trader's execution id (496/497) + the read range (9000–9899). Then re-reads to verify flat. The journal row is left for the trader's reconciler to close-on-disappear. Built 2026-06-19 for the BL-20260618-RECONCILE-DUP residual (the stranded `ib_paper` −232 MGC short the IBKR-futures reduce path couldn't self-clean). DRY-RUN previews without touching the broker; only `apply: true` mutates. Wraps `scripts/ops/flatten_ib_position.py`. |
| `cancel-ib-order` | 2 | `scripts/ops/cancel_ib_order_action.sh` | Cancel ONE resting IB order by id. Body params: `account: <id>` (required) + exactly one of `order: <orderId>` / `perm: <permId>` + optional `apply: true` (default **dry-run**). **NOT a flatten — it places nothing.** Optional `force_protective: true` / `force_client_id: true` waive the two refusals below — **added 2026-08-17**, because the wrapper previously passed neither flag, so the action could not cancel the one class of order it exists for: a stranded PROTECTIVE, TRADER-OWNED stop trips both guards at once, which is exactly what `BL-20260816-NO-PER-ORDER-IB-CANCEL` was. Live-reconfirmed 2026-08-17 on a duplicate MES stop (perm 166865400, clientId 597) that returned `action: refused` with both blockers and no reachable way forward. **The guards are unchanged and still default to refusing**; the keys are independent (they answer different questions — strip-this-exit vs connect-as-a-trader-band-id), a typo'd value is rejected rather than read as off, and the wrapper echoes which refusal was waived on its own line so the run log answers "was this forced?" without the reader knowing the default. An escape hatch that is documented but not wired is not a guard — it is pressure to reach for `flatten-ib-position`, which PLACES an order. Added 2026-08-16 for `BL-20260816-NO-PER-ORDER-IB-CANCEL`: a stranded `ib_paper`/MGC `MKT SELL 105` was unreachable because the only two options were `flatten-ib-position` (which PLACES another market order) and `reqGlobalCancel` (which strips every protective stop on the account). **Mechanism, per the TWS API:** an order is bound to its submitting clientId — `cancelOrder` "can only be used to cancel an order that was placed originally by a client with the same client ID" — so the script reads account-wide (`reqAllOpenOrders`) to find the order AND its owning `clientId`, then connects AS that clientId to cancel. The Master API client ID does **not** grant this (it is documented only for receiving order-status callbacks), so configuring one is not the fix. **Two default refusals**, each with its own explicit override: a **protective** order (OCA-grouped, or a stop/trailing type) needs `--force-protective` because cancelling it strips a live position's exit; an order owned by a clientId **below 9000** (the trader's execution band) needs `--force-client-id` because connecting as that id would evict the trader's live IB session. Lookup is three-state and never collapsed — `could_not_look` (the read failed; **not** evidence the order is absent) / `not_found` (a confirmed clean read) / `found` — and the post-cancel verification re-reads account-wide, so an accepted-but-unverified cancel reports `cancelled_unconfirmed` rather than success. Wraps `scripts/ops/cancel_ib_order.py`. |
| `attach-ib-target` | 2 | `scripts/ops/attach_ib_target_action.sh` | Attach the **declared** take-profit to a TARGET-NAKED IB position. Body params: `account: <id>` + `symbol: <SYM>` (both required) + optional `apply: true` (default **dry-run**). Added 2026-08-16 for `BL-20260816-COVERAGE-IS-ONE-SIDED`, where BOTH live `ib_paper` positions held a stop and no target and **zero limit orders existed account-wide** — `protection_coverage` graded a stop and a take-profit as interchangeable, so nothing ever alerted. **Places the target INTO the stop's existing OCA group and cancels nothing.** That is the whole mechanism: IBKR cancels the stop when the target fills (`ocaType=1`), so the stop stays armed throughout and cannot survive onto a flat book. Using `place_protective` instead would mint a NEW group, pre-cancel on a name that does not match, leave the original stop resting, and — once the new target filled and cancelled only its own sibling — leave that stop able to fill into a REVERSE position. **The target price is read from the journal (`trades.take_profit_1`), never supplied by the caller**, so a repair cannot fat-finger a level; a trade declaring no target is reported and left alone (whether every strategy SHOULD declare one is Tier-3, not a repair). **Four refusals, each a real hazard on this account:** a resting non-protective order on the symbol (a stray `MKT` can fill after the target flattens the position and open a reverse — clear it with `cancel-ib-order` first); a target already resting; more than one stop OCA group (MES has two — joining one leaves the other unlinked); stop qty != journal `position_size`. Read states are three-way — `could_not_look` is never reported as "no target exists" — and an accepted place whose verification read fails reports `placed_unconfirmed`. **Connects on a process-unique OPS clientId (9900-range), distinct from the trader's execution ids (496/497/498) and the 9000–9899 read band** — the same rule its `flatten-ib-position` sibling states above, and ⚠️ **THIS ROW DID NOT SAY IT UNTIL 2026-08-22, WHICH IS HOW THE DEFECT SHIPPED** (`BL-20260822-ATTACH-IB-TARGET-USES-TRADER-CLIENTID`). `_attach` called `ib_client_for(cfg, readonly=False)`, resolving the account's configured **execution** id; IBKR refuses a duplicate clientId rather than evicting, so with the trader up — always — the apply died on `Error 326` and tripped a 120 s breaker inside the ops process. Measured live on `ib_paper`/MES, issue #10139. ⚠️ **THE DRY RUN NEVER BUILDS A CLIENT**, so it reported `state: ready` with all four refusals passed while the apply could not connect — a repair action that reads as available right up to the moment it is needed, and the reason a dry-run `ready` on THIS action is not evidence the apply will work. **Third defect found on this one action by the third session to look:** #9920 died `exit 127` (git-sync lag), #9922 on an `ImportError` for a symbol that never existed, each fixed in turn and each hiding the next — see `docs/sprint-logs/S-SYSREV-TRADE-MECHANICS-2026-08-18.md` § 5, whose "found and fixed" was true of the defect it found. Wraps `scripts/ops/attach_ib_target.py`. |
| `flatten-bybit-position` | 2 | `scripts/ops/flatten_bybit_position_action.sh` | One-shot guarded flatten of a single **Bybit** exchange position — the Bybit sibling of `flatten-ib-position`. Body params: `account: <id>` + `symbol: <SYM>` (both required) + optional `apply: true` (default **dry-run**). Reads the LIVE exchange position via the bot's read path (`account_open_positions`), and on `apply: true` places the close through the unified `close_open_position` (Bybit: a **reduce-only** opposing market order sized to the live qty — `reduceOnly=True` means it can only shrink to flat, never flip; no resting bracket to cancel because Bybit SL/TP are position-attached and clear with the position). Then re-reads to verify flat. The journal row is left for the trader's reconciler to close-on-disappear. The python script guards `exchange==bybit`, so a non-Bybit account is refused. **Authenticates with whatever Bybit key is currently in the VM `.env`** (via `load_runtime_secrets`) — so before a different-account key rotation it closes on the OLD account that still holds the position; run it BEFORE `rotate-account-keys`. DRY-RUN previews without touching the broker; only `apply: true` mutates. Wraps `scripts/ops/flatten_bybit_position.py`. |
| `flatten-alpaca-position` | 2 | `scripts/ops/flatten_alpaca_position_action.sh` | One-shot guarded flatten of a single **Alpaca** exchange position — the Alpaca sibling of `flatten-ib-position` / `flatten-bybit-position` (Alpaca was the only real-money venue without one). Body params: `account: <id>` + `symbol: <SYM>` (both required) + optional `apply: true` (default **dry-run**). Reads the LIVE exchange position via the bot's read path (`account_open_positions`), and on `apply: true` places the close through the unified `close_open_position` (Alpaca: `AlpacaClient.close` — the native, **qty-available-gated** flatten `DELETE /v2/positions/{symbol}`). This is the fix for `BL-20260708-ALPACA-CLOSE-QTY-AVAILABLE`: an Alpaca long's resting protective **bracket** (stop/TP sell legs) reserves the shares as `held_for_orders`, so `qty_available` is 0 and a naive sell — including the operator's own in-app sell — is rejected *"insufficient qty available for order (requested: N, available: 0)"*; `AlpacaClient.close` **cancels the reserving bracket first, waits for `qty_available` to release, then market-closes** the whole position. Then re-reads to verify flat. The journal row is left for the trader's reconciler to close-on-disappear. The python script guards `exchange==alpaca`. **Market hours:** the close is a MARKET order — Alpaca rejects it outside RTH (13:30–20:00 UTC), so run `apply: true` during regular hours; dry-run is safe any time. Works regardless of the account's `mode:` (ops path, not the trader pipeline), so it flattens a `dry_run`-shelved account. Authenticates with the account's own Alpaca key in the VM `.env` (via `load_runtime_secrets`). DRY-RUN previews without touching the broker; only `apply: true` mutates. Wraps `scripts/ops/flatten_alpaca_position.py`. |
| `close-stranded-journal-row` | 2 | `scripts/ops/close_stranded_journal_row_action.sh` | Close a **stranded open journal row** whose broker position is already flat — the JOURNAL-side companion to `flatten-alpaca-position` (which flattens the BROKER side). Body params: `account: <id>` + `symbol: <SYM>` (both required) + optional `apply: true` (default **dry-run**) + optional `exit_price: <num>` (the flatten fill price, for local-compute pnl). Fixes the shelved-account gap: when an Alpaca account is `mode: dry_run`, `account_open_positions` gates it to `None` (clients.py:1319-1323) so the reverse reconciler SKIPS it and never closes-on-disappear — a position flattened out-of-band leaves its `trades` row `status='open'` forever, still showing on `/api/bot/positions` (the "open" list). This makes a **mode-agnostic** broker read (`flatten_alpaca_position._live_position`) and, **only if the broker reads FLAT**, marks the matching open row(s) closed with a local-compute realised pnl + an audit stamp (`exit_reason='operator_flatten_reconciled'`, `pnl_source='local_compute'`). Hard safety gate: a still-open position → `refused_position_open`; an unreadable account → `abort_unreadable`; neither writes. Never flips account mode, never touches the broker (no order path), never touches backtest rows; idempotent (WHERE re-checks `status='open'`). Alpaca-only in v1. DRY-RUN previews the rows it would close; only `apply: true` writes. Wraps `scripts/ops/close_stranded_journal_row.py`. |
| `reconcile-orphan-history` | 2 | `scripts/ops/reconcile_orphan_history_action.sh` | Historical orphan-flap reconciliation (orphan-flap hardening #5). Optional body param `apply: true` (default **dry-run**). Collapses the phantom flap duplicates a position left behind (the classic case: one MGC/MHG position flapping into N phantom `adopted_orphan` closed trades, each with a fabricated PnL — the −$20,127 incident) so each physical position is ONE row: keeps the canonical (the live OPEN row if any, else earliest), reconciles it to its originating order package when recoverable (else flags it `unreconciled` — the honest red-flag terminal state), and **void-flags** the phantom duplicates `reconcile_status='superseded'` (preserved for audit, excluded from analytics — never deleted). Clustering is conservative (time-gapped per `(account,symbol,direction)`; a duplicate linking a *distinct* real package is never collapsed; an OPEN row is never void-flagged). DRY-RUN prints the full per-cluster KEEP/VOID plan without writing; `apply: true` writes after a timestamped DB backup. Pure journal hygiene — never closes/opens an exchange position. For broker-API accounts (bybit) run `backfill-orphan-pnl` FIRST to recover the real exit/PnL on the canonical row. Wraps `scripts/ops/reconcile_orphan_history.py`. |
| `supersede-options-adoption-artifacts` | 2 | `scripts/ops/supersede_options_adoption_artifacts_action.sh` | One-shot journal hygiene for the 2026-06-27 options-account orphan-adoption incident. Optional body param `apply: true` (default **dry-run**). Before the #4858 + #4867 fixes, the reverse reconciler adopted `alpaca_options_paper` `us_option` legs as equity `adopted_orphan` trades and the local-PnL sweep priced them with the equity formula (`local_markprice` × qty × `contract_value_usd=1.0`), fabricating phantom paper PnL (the −$845 figure). Those code paths are fixed, so no NEW artifacts are produced; this cleans up the **historical** rows that still carry the fabricated PnL by void-flagging them `reconcile_status='superseded'` (excluded from analytics). Precise predicate — **paper only** (`is_demo=1`), `setup_type='adopted_orphan'`, `account_id='alpaca_options_paper'`, `status='closed'`, notes carrying the `pnl_source=local_compute` marker, not already superseded; optional `ids:` allowlist narrows further. **Real-money rows are categorically excluded.** DRY-RUN lists the matched rows + their fabricated PnL without writing; `apply: true` writes after a timestamped DB backup. Idempotent; never closes/opens an exchange position, never deletes a row. Wraps `scripts/ops/supersede_options_adoption_artifacts.py`. |
| `supersede-reset-orphan-artifacts` | 2 | `scripts/ops/supersede_reset_orphan_artifacts_action.sh` | One-shot journal hygiene for the 2026-07-07 **alpaca_paper external reset** (`BL-20260707-ALPACA-RESET`). Optional body params `apply: true` (default **dry-run**) + `ids: <csv>` (allowlist). The paper account was reset externally (Alpaca re-seeded a default ETF portfolio the bot never opened); the reverse reconciler adopted the unfamiliar positions as **bare** `adopted_orphan` trades (`strategy_name='orphan_adopt'`, NULL `order_package_id`) and the local-PnL sweep priced them with the equity formula, fabricating phantom PnL (the 1360-share SLV short adopted **twice** as trades 3265+3266 at −693.6 each = −1387.2). The live-path fix (PR #5951 reset-detection) stops NEW strategy-attributed reset artifacts; this void-flags the **historical** bare phantoms `reconcile_status='superseded'` (excluded from analytics). Precise predicate — **paper only** (`is_demo=1`), `setup_type='adopted_orphan'`, **`strategy_name='orphan_adopt'` + `order_package_id IS NULL`** (bare, NOT a genuinely-reattached orphan — those keep their real strategy + package + `reconcile_status='reconciled'` and are categorically excluded, e.g. trade 3250), `status='closed'`, `pnl_source=local_compute` marker, not already superseded, default `account_id='alpaca_paper'`; `ids:` narrows further. **Real-money rows are categorically excluded.** DRY-RUN lists matched rows + fabricated PnL; `apply: true` writes after a timestamped DB backup. Idempotent; never closes/opens an exchange position, never deletes a row. Wraps `scripts/ops/supersede_reset_orphan_artifacts.py`. |
| `supersede-intent-reduce-phantom-pnl` | 2 | `scripts/ops/supersede_intent_reduce_phantom_pnl_action.sh` | One-shot journal hygiene for the **INTENT-REDUCE phantom-PnL** rows (`BL-20260711`; PR #6926 follow-up). Optional body params `apply: true` (default **dry-run**) + `ids: <csv>` (allowlist) + `equal_only: true` (restrict to the ironclad `entry==exit` rows). An `intent_reduce` leg is bookkeeping, not a trade — `apply_intent_reduce_partial_close` leaves its `pnl` NULL by design and the read path (`exclude_reduce_leg_predicate`) excludes reduce legs. Before PR #6926 the reconciler write-back + the mark-to-market sweep booked a non-NULL pnl onto the leg; on a netting account the qty-matched `closed_pnl` is the **parent position's** realized close, so it was attributed onto the reduce leg with an `entry==exit` signature — a fabricated win/loss (the `trend_donchian` demo rows 2604/2607/2610 at +$561/+620/+898). PR #6926 stops NEW phantoms at the source (reduce-leg pnl stays NULL; the sweep skips reduce legs); this void-flags the **historical** rows `reconcile_status='superseded'`. Precise predicate — a **reduce leg** (`setup_type='intent_reduce'` OR notes `intent_reduce:true`), `status='closed'`, `pnl IS NOT NULL`, not already superseded; `equal_only:` narrows to `entry==exit`, `ids:` to an allowlist. **Account-agnostic** (the phantom hits real-money `bybit_2` too) — DRY-RUN reports real-money vs paper rows separately so a human eyeballs the real-money ones before writing. Pure journal hygiene: void-flags ONLY the bookkeeping reduce leg (never the parent close that carries the real pnl), never closes/opens an exchange position, never deletes a row. `apply: true` writes after a timestamped DB backup; idempotent. Wraps `scripts/ops/supersede_intent_reduce_phantom_pnl.py`. |
| `fix-prop-mislinked-close` | 2 | `scripts/ops/prop_fix_mislinked_close_action.sh` | One-shot prop-journal hygiene for the 2026-07-06 mis-linked ETH prop close (`BL-20260706-PROP-CLOSE-MISLINK`). Optional body param `apply: true` (default **dry-run**). Before PR #5744, a prop CLOSE with no explicit `ticket_id` linked to the *newest* open-status ticket — a never-placed `emitted` **signal** rather than the `filled` **position**: the 2026-07-06 ETH close (`prop_fills` id 17) hit the emitted ticket `prop-manual-849ece101a3c` instead of the filled position ticket `prop-manual-5bc393741ec4`, marking a phantom closed and leaving the real position open. #5744 stops recurrence; this repairs the rows already written with the clean end state (no artifacts): (1) relink `prop_fills` id 17 to `…5bc393741ec4`, (2) that ticket `filled`→`closed`, (3) the phantom `…849ece101a3c` `closed`→`expired`. **Guarded + idempotent** — each op fires only when its expected current value holds, so re-running after apply is a clean no-op. Touches only `prop_fills` / `prop_tickets` (the prop journal is isolated from real-money/paper KPIs); never a `trades` row, never an exchange position. DRY-RUN prints the 3-op plan without writing; `apply: true` writes after a timestamped DB backup. Wraps `scripts/ops/prop_fix_mislinked_close.py`. |
| `repair-prop-fill-direction` | 2 | `scripts/ops/repair_prop_fill_direction_action.sh` | Prop-journal hygiene for fills admitted with **no direction** (`BL-20260820-PROP-FILL-DIRECTION-ADMISSION-GAP`). Optional body params `account: <id>` (omit = every account) and `apply: true` (default **dry-run**). `prop_monitor_pulse._position_key` identifies a position by `(account_id, symbol, canonical_direction)`, but `prop_report.ingest_report` — the single chokepoint every report-back passes through — validates only `account_id` and `symbol` and lets `direction` through unvalidated. A fill admitted without one therefore keys as `akd:<acct>|<SYM>|` while its own close, reported with a direction, keys as `…|long`: the close is invisible to `find_open_prop_positions` and the row reads **OPEN for ever**. Measured 2026-08-20 over the full 32-row population — `prop_fills` id 30 (`breakout_1`/SOLUSDT/83.0, ticket `prop-manual-5e30b930`) against its own closes ids 31/32, both `direction='long'`. Two consequences: the monitor pulse pings hourly about a closed trade, and `prop_sl_tp_alert`'s `_sl_crossed('')` falls through to `return False`, so an undirected position **can never fire an SL/TP alert**. This repairs the DATA: it fills the missing field from the linked ticket — an authority that already recorded it — through the **same canonical mapper** the pulse keys on, so a ticket reading `buy` stores `long` rather than re-splitting the key the way `BL-20260708-PROP-PULSE-DIRECTION-ALIAS` did. It writes **no synthetic close fill** (that would swap a phantom-open artifact for a phantom-close one and fire a spurious `prop_closed` notification). **A predicate, not a row id** — unlike its three predecessors in this class, so a fourth vocabulary mismatch needs no fifth script. Three outcomes, never collapsed: `resolvable` / `ticket_blank` (we looked, the ticket has none) / `no_ticket` (nothing to look at); an unresolvable row is reported and **skipped, never guessed**, and the tool exits non-zero so a row that still reads open is not booked as success. **Guarded + idempotent** — every UPDATE fires only while `direction` is still empty, so a re-run after apply is a clean no-op. Touches `prop_fills` only; never a `trades` row, never an order, never an exchange position. The **structural** fix (admission covering identity, plus an explicit reconciliation state) is the backlog row's resolution criteria and is NOT this action. Wraps `scripts/ops/repair_prop_fill_direction.py`. |
| `repair-malformed-notes` | 2 | `scripts/ops/repair_malformed_notes_action.sh` | One-shot repair of legacy malformed-JSON blobs in `trade_journal.db` (`BL-20260618-CLOSEDFLAT-MALFORMED-JSON` / `BL-20260709-MALFORMED-NOTES-LEGACY-REPAIR`). Optional body param `apply: true` (default **dry-run**). Before the write-side moved to `dump_capped` (RISK-1 Task 2, PR #6037), `json.dumps(payload)[:N]` char-slicing could persist **invalid JSON** into `trades.notes` and `order_packages.{signal_logic,meta}`. The write-path is now fixed (INV-6 `recent=0`), but a legacy backlog of `json_valid(col)=0` rows remains (INV-6 `total`); this rewrites each into a valid, length-bounded envelope that salvages the intact load-bearing keys (`closed_at`/`closed_by`/…) and preserves the raw original under `_original_truncated`. **Idempotent by construction** — a repaired row has `json_valid=1`, so a re-run never re-touches it. DRY-RUN prints the per-column count + a sample; `apply: true` writes. Touches only the three JSON columns; never closes/opens a position, never deletes a row, no service restart. Wraps `scripts/ops/repair_malformed_notes.py`. |
| `repair-netted-rows` | 2 | `scripts/ops/repair_netted_rows_action.sh` | One-shot honest-null repair of the 8 Jun-2026 netted-position misattribution rows (`BL-20260720-ICTSCALP-PASTSTOP-EXITS` + `BL-20260720-PAPER-PNL-CROSSWRITE`). Several journal trades shared one netted Bybit position; each position-level bracket fire flattened everything but closed only the newest row, and the phantom-open siblings were later mis-resolved with **other trades' closed-pnl records** or resolution-time mark prices — their stored `pnl`/`exit_price` are not measurements of those trades. Nulls `pnl`/`pnl_percent`/`exit_price` with full provenance preserved under `notes.netted_repair` and stamps `exit_reason='netted_misattributed'`. Optional body param `apply: true` (default **dry-run**). **Signature-verified + idempotent** — refuses any row whose current values no longer match the expected corrupt signature. Validated 2026-07-20 on the trainer's synced copy (apply 8/8 on a throwaway copy, re-run 0/8 — issue #7125). Touches only the 8 hard-coded ids; no service restart. Wraps `scripts/ops/repair_netted_misattributed_rows.py`. |
| `reconcile-netting-phantom-rows` | 2 | `scripts/ops/reconcile_netting_phantom_rows_action.sh` | W1 reconciliation (`BL-20260731-W1-JOURNAL-EXCHANGE-DIVERGENCE-MAP`, operator-approved 2026-08-01): closes the 4 bybit_1 **netting phantom-open** ict_scalp rows (4179 BTC 1.543, 4255 ETH 106.76, 4220+4243 SOL 1844.6+936.1) whose exchange share no longer exists — the same-moment check (vm-diag #8218 vs trainer-diag #8227, 2026-08-01T06:40Z) shows the remaining pairs-sleeve rows alone match the exchange EXACTLY, so after this batch journal == exchange per symbol with no orphan re-adoption. `status='closed'` + `reconcile_status='superseded'` + `exit_reason='netting_phantom_reconciled'`; `pnl`/`exit_price` stay **NULL (UNMEASURED)** — the real closes happened inside position-level exits at unknown moments, never priced from a mark. Full provenance under `notes.netting_phantom_reconcile`. Optional body param `apply: true` (default **dry-run**). **Signature-verified + idempotent** — refuses any row whose live values no longer match the pinned signature. Does NOT touch the BNBUSDT pairs-row surplus (root-cause item) or any row outside the 4 ids; no service restart. Wraps `scripts/ops/reconcile_netting_phantom_rows.py`. |
| `reconcile-netting-rows` | 2 | `scripts/ops/reconcile_netting_rows_action.sh` | **GENERAL same-moment netting partial-close reconcile** (`BL-20260801-NETTING-PARTIAL-CLOSE-ROWS-NEVER-REDUCED`, option **(c)+(b)**, operator-approved 2026-08-02) — the generalization of `reconcile-netting-phantom-rows` from a signature-pinned one-shot to a cadence-safe, on-demand job for the whole class. Under Bybit one-way netting several journal `trades` rows share ONE exchange position; a **partial** (non-flat) shrink is attributed to at most one row and the siblings survive at full size (the cascade only fires on FULL flat), inflating the journal and suppressing netting-guard re-entries — the live `journal_qty_divergent` sweep DETECTS every instance per tick but remediates nothing. **Two steps on the VM:** (1) `netting_reconcile_snapshot.py` reads the OPEN non-pairs journal groups + the LIVE per-account exchange positions (`account_open_positions` — the same read `/api/diag/exchange_positions` uses) + the Bybit resting protective-leg ids, and writes the engine's same-moment input JSON (`{account/symbol/direction → {size, resting_legs}}`); (2) `reconcile_netting_rows.py` closes the SURPLUS open rows so each group's open sum matches the broker's netted size — `status='closed'` + `reconcile_status='superseded'` + `exit_reason='netting_partial_reconciled'`, `pnl`/`exit_price` left **NULL (UNMEASURED)** (the real closes happened inside position-level exits at unknown moments — never mark-priced), full provenance under `notes.netting_partial_reconcile`, plus the (b) precision layer (a surplus row whose tracked SL/TP leg has FIRED is closed before the oldest-first fallback). **Fail-safe:** an account that could-not-read is OMITTED from the snapshot → the engine SKIPS its groups (never close on an unconfirmed broker read); pairs-sleeve rows excluded; never closes more than the surplus (a straddling row is KEPT). Optional body param `apply: true` (default **dry-run**). Idempotent — a re-run finds the surplus gone; no service restart. Wraps `scripts/ops/reconcile_netting_rows.py` (+ `netting_reconcile_snapshot.py`; engine + 7 tests in PR #8401). |
| `mark-netted-duplicate-pnl` | 2 | `scripts/ops/mark_netted_duplicate_pnl_action.sh` | **Mark journal rows carrying a DUPLICATED netted broker PnL** (`BL-20260806-DUPLICATE-PNL-NETTED-SIBLING-ROWS`, operator-approved 2026-08-06). Under one-way netting the broker returns ONE closed-pnl record for the whole netted position; historical rows written before the proration fix carry that record's FULL magnitude on EVERY sibling **and** a broker `exit_price_source`, so they classify **MEASURED** and flow into the fidelity calibration set, every R metric, the ML label builders and the `totalPnlMeasured` promotion gate. This stamps `notes.exit_price_source = 'netted_duplicate_unattributed'` (a FABRICATED source, so `pnl_is_trustworthy` refuses the row), preserving the original under `notes.pre_remediation_exit_price_source`. **It never rewrites `pnl`** — there is no defensible per-row value (the magnitude belongs to the netted POSITION; splitting it after the close with no per-row fill to anchor to would be the proration assumption dressed as a correction), and zeroing would silently change historical aggregates rather than disqualifying them. **ALWAYS STATE THE POPULATION:** selection is a `(account_id, symbol, ROUND(pnl,2))` cluster with 2+ closed non-backtest rows, SUSPECT only when quantities differ by more than `--qty-spread` (1.5x) AND `|pnl|` clears `--min-abs-pnl` ($1.00) — without the spread filter the raw count called 236/408 real-money rows clustered; with it, **31 `bybit_1` rows / $24,272.18** and **79 `bybit_2` rows / $45.52** (the latter an upper bound, mostly still false positives at scalp size). Biased toward UNDER-marking on purpose: marking a correct row costs real information. Optional body params `apply: true` (default **dry-run**, which opens the DB `mode=ro` so a selection bug cannot write) and **`account:`** (review scope, not a safety control — the body key is `account:`, NOT `account_id:`; the parser only matches `^account:` and a wrong key is silently ignored, so the run falls back to ALL accounts. Verified the hard way on #8541). Idempotent; no service restart; touches no order path. Wraps `scripts/ops/mark_netted_duplicate_pnl.py` (+ 11 tests). |
| `validate-partial-tpsl` | 2 | `scripts/ops/validate_partial_tpsl_action.sh` | Venue validation for **`BYBIT_TPSL_MODE=partial`** (qty-scoped brackets — Fix 2 of `BL-20260720-ICTSCALP-PASTSTOP-EXITS`). **Hard-locked to the demo account `bybit_1`** (the helper refuses any non-paper/non-demo account). Places two tiny netted orders with Partial tpsl on an ISOLATED symbol no strategy trades (LTCUSDT; a flat-at-start guard aborts if the symbol is contaminated — the first run, #7145, rode BTCUSDT on top of the demo strategies' live position), verifies BOTH bracket pairs coexist on the venue (under the default Full mode the second order replaces the first's bracket — the Jun-2026 incident mechanism), amends one SL qty-scoped, then cleans up (cancels stop orders + reduce-only closes). A PASS verdict is the evidence gate for the Tier-3 `BYBIT_TPSL_MODE=partial` env flip (via `set-env`) on the live VM. Wraps `scripts/ops/validate_partial_tpsl.py`. |
| `validate-bybit-naked-rearm` | 2 | `scripts/ops/validate_bybit_naked_rearm_action.sh` | Venue validation for the **Bybit broker-naked re-arm sweep** (`BL-20260729-BYBIT-NAKED-POSITION-BLINDSPOT`). **Hard-locked to the demo account `bybit_1`.** On an ISOLATED symbol no strategy trades (LTCUSDT; flat-at-start guarded), opens a tiny **naked** position (Market, no SL/TP), verifies the detection reads it as UNPROTECTED, re-arms a **Full-mode `set_trading_stop`** (the exact call `order_monitor._attempt_naked_autoprotect`'s bybit branch makes), verifies it now reads PROTECTED, then cleans up (cancel stops + clear position stop + reduce-only close). A PASS verdict is the evidence gate for merging the real-money Bybit naked-rearm fix (PR #7874). Wraps `scripts/ops/validate_bybit_naked_rearm.py`. |
| `cancel-stale-tpsl-legs` | 2 | `scripts/ops/cancel_stale_tpsl_legs_action.sh` | Stopgap cleanup for **`BL-20260721-BYBIT2-XRP-TPSL-LEGCAP`**: under `BYBIT_TPSL_MODE=partial`, Bybit's `set_trading_stop(tpslMode=Partial)` is documented as add-only (never amends an existing partial leg in place — Full mode's the one that amends), so every trailing-stop tick adds a new leg with nothing cancelling the old one, until Bybit's 20-combined-leg-per-symbol cap (ErrCode 110061) silently blocks further SL amends. Body params: `account: <id>` + `symbol: <SYM>` (both required) + optional `apply: true` (default **dry-run**). Lists the symbol's live conditional (StopOrder) orders and selects by **OWNERSHIP** via the pure `src/runtime/stale_leg_decision.py::decide_stale_legs`: a leg is cancelled because the journal row that owns it (`trades.sl_order_id` / `tp_order_id`) is **CLOSED**; every leg owned by an **OPEN** row is kept; a leg no row claims **refuses the whole run** rather than guessing; and a cancel that would leave stop coverage below the position refuses too (`would_undercover`). ⚠️ **THIS ROW USED TO SAY it "keeps the MOST RECENTLY created leg (the strategy's current intent)"** — that was the rule until 2026-08-26 and it was **catastrophic on a netted symbol**. Measured on `bybit_1`/ETHUSDT: position 5.59 across two open rows (4921 qty 1.18 + 4903 qty 4.41), and the NEWEST leg was **0.19, owned by CLOSED trade 5003** — newest-wins would have kept that and cancelled the live 1.18, taking a 167%-over-covered position to **3.4% covered**. Age is not ownership: a leg is old because its trade has been open a long time, which is the *opposite* of stale. Do not restore the old wording — it describes a rule the code no longer has and would tell an operator to expect the wrong plan (`BL-20260826-CANCEL-STALE-TPSL-LEGS-WOULD-KEEP-A-CLOSED-TRADES-LEG-AND-CANCEL-THE-LIVE-ONE`). Refuses to run if the live position is flat (out of scope) or if zero SL legs are found while the position is live (the position may already be naked — needs a human look, not a cancel). On `apply: true`, cancels the stale legs and re-reads to confirm the post-state; if the re-read shows zero SL legs remaining it reports `cancel_left_naked` (critical) rather than declaring success. This was a STOPGAP for the legs already stranded before the structural fix shipped — it does not itself stop new legs from accumulating. **The structural fix has since shipped** (PR #7321, 2026-07-21): `execute_pkg` now captures each trade's Bybit leg id at entry (`trades.sl_order_id`/`.tp_order_id`) and `modify_open_order` amends that specific leg in place via `/v5/order/amend` instead of re-adding via `set_trading_stop`, so a trade opened after the fix deployed never accumulates duplicate legs. This action remains useful for (a) legs from trades opened before the fix deployed (no tracked id, still fall back to the legacy add-a-leg path — see `backfill-tpsl-leg-ids` below for the retroactive-tag fix) and (b) any other stranding this class of bug could still produce. Wraps `scripts/ops/cancel_stale_tpsl_legs.py`. |
| `backfill-tpsl-leg-ids` | 2 | `scripts/ops/backfill_tpsl_leg_ids_action.sh` | Structural-fix completion for **`BL-20260721-BYBIT2-XRP-TPSL-LEGCAP`**, addressing item (a) above: PR #7321's entry-time leg capture has no way to retroactively tag a trade that was already open when it deployed, so those rows keep `sl_order_id`/`tp_order_id` NULL forever and silently keep re-accumulating duplicate legs on the legacy add-a-leg path — live-confirmed 2026-07-21 on `bybit_2`/XRPUSDT, which kept adding legs for hours after the fix shipped because its position pre-dated the deploy. Body params: `account: <id>` + `symbol: <SYM>` (both required) + optional `apply: true` (default **dry-run**). Reads the symbol's current live conditional (StopOrder) legs; refuses if there is more than one live SL leg or more than one live TP leg (ambiguous — run `cancel-stale-tpsl-legs` first to collapse to one of each) or if more than one open, non-backtest, untracked trade row matches `(account, symbol)` (ambiguous which trade owns the single leg pair). On a clean single match, writes the found leg id(s) onto only the currently-NULL column(s) of that one trade row — never overwrites an already-populated id. Read-only against the broker (`get_open_orders` only); the only DB write is the two new columns on one row. **Live-verified 2026-07-21**: applied to `bybit_2`/XRPUSDT (trade 3577) — the tagged leg held unchanged (same order id) past its next scheduled trailing-stop tick, confirming amend-in-place is active. Wraps `scripts/ops/backfill_tpsl_leg_ids.py`. |
| `purge-cloudflared` | 2 | `scripts/ops/purge_cloudflared.sh` | Purge the retired `ict-cloudflared-tunnel.service` from the live VM. The Cloudflare tunnel was retired in the React→Streamlit dashboard pivot and removed from the repo in #3233 — but `install_systemd_units.sh` is install-only, so an already-installed unit kept running; once the operator disconnected the Cloudflare account (2026-06-10) it just retries a dead tunnel (harmless to trading — nothing routes through it — but pointless churn on the 2-core box). Runs `systemctl disable --now` + removes the unit file(s) + token drop-in + `daemon-reload` + `reset-failed`. **Fully idempotent** — if the unit was never installed every step is a no-op and it exits 0 with a "nothing to purge" report, so it's safe to run blind. Touches only `ict-cloudflared-tunnel.service`; the live stack + config are untouched. |
| `purge-vm-runner` | 2 | `scripts/ops/purge_vm_runner.sh` | Remove the dead **claude-vm-runner** subsystem from the live VM — including its **passwordless-root sudoers grant** — while PRESERVING the `ufw` grant that shares the same file. `/etc/sudoers.d/claude-vm-runner` grants `ubuntu ALL=(root) NOPASSWD: /usr/local/bin/claude-vm-dispatch`; that wrapper's only caller (the Telegram `/vm` + `/vm_write` surface) was deleted in **PR #1933, 2026-05-25**, and the 2026-08-13 full-system audit found the grant still installed three months later with **zero call sites in `src/`** (`BL-20260813-VM-RUNNER-ZOMBIE-SUDOERS-ROOT-GRANT`). **THE ORDERING IS THE SAFETY PROPERTY:** the obvious `rm` would also drop the `ufw` grant, which the system-actions / vm-net-fix workflows use to reopen TCP/8001 after a reboot loses the firewall rules (#537/#542/#545) — and it would break that auto-recovery SILENTLY, surfacing only at the next outage. So the script **installs `deploy/ict-ufw.sudoers` FIRST**, proves `sudo -n -l /usr/sbin/ufw` still resolves, and only then removes the old file, the wrapper and the template unit. If the replacement fails `visudo -c` or the grant does not resolve, it **aborts having changed nothing** (or leaves the old file in place) — a partially-applied sudoers change on the money VM is not an acceptable intermediate state. It also **refuses to run** if a `claude-vm-runner@*.service` instance is somehow ACTIVE: nothing in the repo can start one, so that would contradict the purge's premise and is a finding, not something to purge underneath. Fully idempotent; asserts its own post-state (all three artifacts absent, the new file present, `ufw` still resolving) and exits non-zero if any check fails. Operator-approved 2026-08-13. |
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
- `bybit-bracket-audit` — **READ-ONLY broker-truth audit of Bybit protective
  bracket COVERAGE**, plus a definitive three-source read of the effective
  `BYBIT_TPSL_MODE`. Answers the two questions nothing else could:
  (a) *what value is the RUNNING trader actually using?* — printed from the
  `.env` file, the systemd unit's `Environment=`/`EnvironmentFiles=`, AND
  `/proc/<MainPID>/environ` (a `set-env` writes the FILE while the running
  process keeps whatever it started with, so those can legitimately disagree
  and **only the process environ is the truth**); and (b) *is every open Bybit
  trade actually protected right now?* — per account+symbol it reports the
  venue position row (`size`/`stopLoss`/`tpslMode`), every resting conditional
  leg, and crucially the **SL-covered qty vs position size**
  (`coverage_pct` / `uncovered_qty` → `PROTECTED` / `PARTIALLY_NAKED` /
  `NAKED`), plus a per-trade join showing whether each journal row's tracked
  `sl_order_id` leg is still alive at the broker. This measures the *quantity*
  dimension that `order_monitor._bybit_position_protection` treats as a mere
  boolean (`any()` resting SL leg ⇒ "protected"), which is how a netted
  position with partly-missing per-trade legs read as protected while being
  only partially covered. Places/amends/cancels **nothing**, writes no DB row —
  safe to run against real money at any time. Optional issue-body fields:
  `account: <bybit_id>` (default all), `symbol: <SYM>` (default every symbol
  with an open journal row). Scripts: `scripts/ops/bybit_bracket_audit.py` +
  `scripts/ops/bybit_bracket_audit_action.sh`.
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
- `backfill-pnl-nulls`
- `backfill-orphan-pnl`
- `backfill-closed-null-pnl`
- `mark-reconciler-incomplete`
- `backfill-monitor-closed-pnl`
- `revert-backfill-monitor-closed-pnl`
- `rebuild-pnl-from-bybit`
- `pull-exchange-fills`
- `backfill-shadow-predictions`
- `backfill-account-class`
- `backfill-exit-labels`
- `backfill-closed-at`
- `backfill-trade-costs`
- `backfill-broker-order-id`
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
- `cancel-ib-order`
- `attach-ib-target`
- `flatten-bybit-position`
- `flatten-alpaca-position`
- `close-stranded-journal-row`
- `reconcile-orphan-history`
- `supersede-options-adoption-artifacts`
- `supersede-reset-orphan-artifacts`
- `supersede-intent-reduce-phantom-pnl`
- `fix-prop-mislinked-close`
- `repair-prop-fill-direction`
- `reset-daily-risk-state`
- `repair-malformed-notes`
- `repair-netted-rows`
- `reconcile-netting-phantom-rows`
- `reconcile-netting-rows`
- `mark-netted-duplicate-pnl`
- `validate-partial-tpsl`
- `validate-bybit-naked-rearm`
- `cancel-stale-tpsl-legs`
- `backfill-tpsl-leg-ids`

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

