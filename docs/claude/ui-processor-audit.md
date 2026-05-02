# UI processor audit — `src/bot/telegram_query_bot.py`

**Status:** audit-only deliverable. No code changes are made by the
audit; subsequent sprints migrate handlers in PR-sized chunks per the
priority order in § 5.

**Why this exists.** The operator flagged that the Telegram bot has
functionality hard-coded into itself instead of routing through
`src/ui/processor.py`. That makes it impossible to add a webapp UI
without forking logic. The processor (introduced in CP-2026-05-02-01)
is the agreed boundary: every UI surface (Telegram bot today, webapp
tomorrow) reads through `processor.*` and renders separately.

This audit enumerates every command handler in `telegram_query_bot.py`
and labels what needs to change before a second UI surface can be
added cleanly.

## How to read this doc

For each handler:

* **Reads:** what data it pulls — DB, env, file, subprocess, exchange,
  Coordinator. Each direct read is a "should be processor" candidate.
* **Renders:** how it formats the reply. If the renderer is pure (no
  I/O), it can be moved to `src/ui/renderers/telegram.py` without
  refactoring the read.
* **Migration class:** A / B / C — see § 5 below.

Categories:

* **A** — handler already calls a processor API or only delegates to a
  pure renderer. Zero migration risk; can be moved to a thin
  `processor → renderer` shape with no behaviour change.
* **B** — handler reads via `data_loaders` or the Coordinator. Requires
  a new processor API but is otherwise mechanical.
* **C** — handler runs subprocesses, mutates `/tmp/trader_halt.flag`,
  spawns Claude on the VM, or otherwise has effects beyond reading
  data. Each of these needs a design decision: should the processor
  expose a write API, or should the bot keep the responsibility because
  webapp parity isn't in scope?

## 1. Handlers already on / near the processor (Class A)

| Handler | What it does today | Notes |
|---|---|---|
| `cmd_help` / `cmd_start` | Renders top-level button menu + per-category drill-downs from `BOT_COMMAND_SPECS` | G3 ✅. Pure rendering. Move `render_help_*` to `src/ui/renderers/help.py` so the webapp can render the same hierarchy as accordion sections. |
| `cmd_set_keys` | Replies with the Colab notebook URL | Pure constant string. Move to a `processor.get_key_rotation_link()` if the URL ever differs per environment. Otherwise keep inline. |
| `cmd_price` | Hits Bybit public REST for BTC ticker | One external call, no DB. Webapp will want this too. Add `processor.get_btc_spot_price()`. |
| `cmd_backtest_ui` | Static instructions string | Pure constant. Either inline in the renderer or move to `processor.get_backtest_ui_help()`. |
| `cmd_webapp` | Builds an `InlineKeyboardButton` linking to the dashboard URL | Pure URL construction. Could stay in renderer once the URL comes from `processor.get_webapp_url()`. |
| `cmd_hourly` | Calls `build_hourly_report()` directly (line ~2380) | **B almost-A.** `processor.get_hourly_report()` already exists — this handler should call that instead of the runtime module. Quick win. |

## 2. Handlers reading DB / journals / runtime files / Coordinator directly (Class B)

These are the bulk of the migration work. Every one of them needs (a)
a processor API and (b) a thin handler that calls processor + renderer.

### 2.1 Trade journal & runtime DB

| Handler | Direct read | Proposed processor API |
|---|---|---|
| `cmd_status` | `fetch_today_pnl()`, `fetch_open_positions_count()` (both use `sqlite3.connect(DB_PATH)`) per account | `processor.get_runtime_status(account_id=None)` returning `[{account_id, exchange, trades_today, pnl_today, open_positions, halted}]`. Web dashboard renders the same fields. |
| `cmd_last5` | `dl.recent_trades_for(account, n=5)` per account | `processor.get_recent_trades(account_id=None, n=5)` returning a flat list of trade rows with `strategy` populated. Renderer (`_format_trade_row`) is already pure — move it to `src/ui/renderers/telegram_trades.py`. |
| `cmd_latest_backtest` | `dl.latest_backtests_per_model()` | `processor.get_latest_backtests()`. Renderer (`format_backtest_summary`) is pure — move to renderers module. |
| `cmd_download_journal` | Sends `trade_journal.db` as a Telegram document | Inherently a file-stream response. Not in scope for the webapp until a download endpoint is added. Mark as "Telegram-only, leave alone for now". |

### 2.2 Account / strategy / risk state (via Coordinator)

| Handler | Direct read | Proposed processor API |
|---|---|---|
| `cmd_balance` | `dl.list_accounts()` + `format_bybit_balance(account)` (which calls `dl.account_balance(account)`) | **`processor.get_account_balances()` already exists.** Migrate `cmd_balance` to consume it; the rendering helpers (`format_bybit_balance`, `format_binance_balance`) become consumers of the processor's row dicts. |
| `cmd_trades` | `dl.list_accounts()` + `format_*_positions` (calls `dl.account_open_positions(account)`) | `processor.get_open_positions(account_id=None)`. Rendering helpers move to `src/ui/renderers/telegram_positions.py`. |
| `cmd_strategies` | `coord.dashboard_stats()` + `dl.strategy_dashboard_data()` fallback | `processor.get_strategy_dashboard()` — single call, returns the union shape. Renderer (`format_strategies_dashboard`) is pure. |
| `cmd_alerts` | `coord.list_alerts(n=10)` | `processor.get_recent_alerts(n)`. |
| `cmd_accounts` (no args) | `coord.accounts_status()` | `processor.get_accounts_summary()` returning the dry/live + risk shape currently rendered inline. |
| `cmd_accounts_status` | `coord.accounts_status()` | Same processor API as above. Redundant with `cmd_accounts`; merge once the processor exists. |
| `cmd_risk_check` | `coord.accounts_status()` + `_render_risk_check_for_account` (G4 — already pure) | `processor.get_account_risk_state(account_id)` returning the single-account shape. Renderer is already shared between typed and button paths. |
| `cmd_signals` | `_read_audit_tail(SIGNAL_AUDIT_PATH, ...)` — reads `runtime_logs/signal_audit.jsonl` directly | **`processor.get_recent_signals(limit, strategy)` already exists.** Migrate `cmd_signals` to consume it; delete `_read_audit_tail` from the bot module. |

### 2.3 Logs (via systemd / data_loaders)

| Handler | Direct read | Proposed processor API |
|---|---|---|
| `cmd_log` (typed + callback) | `dl.recent_logs_for(svc, n=...)` | `processor.get_service_logs(service, n)`. Tail size is a UI concern; leave it as a parameter. |

## 3. Handlers with side effects on the VM (Class C — design decision needed)

These handlers do more than read. Each has a question attached;
flagging here so the next sprint resolves before migration.

| Handler | Side effect | Question for the operator |
|---|---|---|
| `cmd_halt` | `open(HALT_FLAG_PATH, "w")` + `coord.return_command("halt")` | Should the kill switch be a processor write API, or should the webapp call a separate `/api/halt` endpoint that the bot consumes too? Recommend: `processor.set_kill_switch(active: bool)` with a single source of truth file, both surfaces call this. |
| `cmd_resume` | `os.remove(HALT_FLAG_PATH)` + `coord.return_command("resume")` | Same as `cmd_halt`. |
| `cmd_toggle` | `toggle_service(svc, "start"/"stop")` (calls `sudo systemctl`) | systemctl actions need privilege. The bot has it via the polkit / sudoers setup in `deploy/`. The webapp won't. Recommend: keep this Telegram-only for now; mark in the handler. |
| `cmd_closeall` | `dl.close_all_bybit_positions_for_strategy(account, strategy)` | Real exchange-touching action. Should be `processor.close_positions(account_id, strategy=None)` with explicit confirm semantics. The G4 follow-up that adds a confirm-button flow can ride on this. |
| `cmd_smoke_test` | `coord.smoke_test_run(account_id, exchange_client_factory=...)` | Already gated by the autonomous-trading rule. `processor.run_smoke_test(account_id=None)` works; the factory injection is an internals concern. |
| `cmd_set_all_live` | Mutates per-account dry/live flags via Coordinator | **Sensitive.** This flips trading mode. Recommend: keep typed-only and operator-confirmed for now, mark with a per-PR ping per the live-mode invariant rule. |
| `cmd_accounts dry|live <name>` | `coord.set_account_dry_run(name, dry=...)` | Same sensitivity as `cmd_set_all_live`. Migration should add a confirm step before the flip; `processor.set_account_dry_run(name, dry, *, confirm_token)` sketch. |
| `cmd_reload_strats` | `coord.reload_strategy_config()` | Read-after-write. `processor.reload_strategy_config()` returning the new config snapshot. Webapp will want this. |
| `cmd_vm` / `cmd_vm_write` | Spawns a Claude session on the VM via `handle_vm_command` | Telegram-specific (operator confirmation flows in Telegram). Not webapp-relevant; leave alone. |
| `cmd_ping_test` | Writes a JSON file to the pending-pings inbox | Diagnostic. Could move to `processor.fire_test_ping()` if the webapp wants the same diagnostic; otherwise keep inline. |
| `cmd_health` | Iterates `dl.list_accounts()` + `get_service_status(unit)` + checks data file mtimes | Aggregator. `processor.get_health_snapshot()` returning `{services, data_freshness, accounts}`. Webapp will absolutely want this. |
| `cmd_vmstats` | Runs `uptime`, `free`, `df` via `run_shell_command` | VM-only, privileged. Telegram-only for now; mark in the handler. |
| `cmd_checkpoint` | Reads the top of `docs/claude/checkpoints/CHECKPOINT_LOG.md` from disk | Could be `processor.get_latest_checkpoint()` returning the parsed top entry. Webapp will want this for a "what is the bot working on" widget. |
| `cmd_sprintlet_status` / `cmd_sprintlet_complete` | Posts to Telegram + appends to pending-pings | Telegram-only. Notification mechanism, not a UI concern. Leave alone. |

## 4. Renderers in the bot module (move regardless of read-path migration)

These functions are pure (no I/O, no side effects) but live in
`src/bot/telegram_query_bot.py`. The webapp would have to import from
`src.bot.*` to render the same data — that's a layering violation.

Move to `src/ui/renderers/telegram_*.py` as a no-behaviour-change PR.
None of these affect read paths.

| Renderer | Where used | Suggested new home |
|---|---|---|
| `format_backtest_summary(latest)` | `cmd_latest_backtest` | `src/ui/renderers/telegram_backtest.py` |
| `format_bybit_balance(account)` / `format_binance_balance(account)` | `cmd_balance` | `src/ui/renderers/telegram_balance.py` |
| `format_bybit_positions(account)` / `format_binance_positions(account)` | `cmd_trades` | `src/ui/renderers/telegram_positions.py` |
| `_format_trade_row(row)` | `cmd_last5` | `src/ui/renderers/telegram_trades.py` |
| `_format_signal_row(rec)` | `cmd_signals` | `src/ui/renderers/telegram_signals.py` |
| `format_strategies_dashboard(stats, rows)` | `cmd_strategies` | `src/ui/renderers/telegram_strategies.py` |
| `_render_risk_check_for_account(statuses, name)` (G4) | `cmd_risk_check` + callback | `src/ui/renderers/telegram_risk.py` |
| `render_help_top()` / `render_help_category()` (G3) | `cmd_start` + callbacks | `src/ui/renderers/telegram_help.py` |

When these move, give each a sibling `webapp_*.py` (returns dict / HTML
fragment) so the webapp can plug in without re-reading.

## 5. Proposed migration order

Goal: each PR should be small, reversible, and leave the bot fully
operational. Suggested ordering:

1. **`cmd_hourly`** — already has the processor API. One-line change in
   the handler, plus a regression test asserting it goes through
   `processor.get_hourly_report()`. Risk: zero. Smallest possible PR.
2. **`cmd_balance` + `cmd_signals`** — both have processor APIs already
   (`get_account_balances`, `get_recent_signals`). Same pattern: handler
   becomes 5 lines, renderer moves to `src/ui/renderers/`. Risk: low.
3. **Renderer-only PR** — relocate `format_*` and `_format_*` helpers
   to `src/ui/renderers/telegram_*.py`. No read-path changes; bot
   imports the new module. Risk: import-only, easy to revert.
4. **`processor.get_runtime_status` + `cmd_status`** — first new
   processor API. Returns the per-account status block. Test asserts
   the bot's `cmd_status` consumes it.
5. **`processor.get_recent_trades` + `cmd_last5`** — DB read migration.
   The G1 plain-text fix (BUG-030, #265) ensures rendering doesn't
   regress.
6. **`processor.get_open_positions` + `cmd_trades`** — same shape as
   trades, follows once `get_recent_trades` lands.
7. **`processor.get_health_snapshot` + `cmd_health`** — aggregator,
   likely the highest-value webapp endpoint after balances.
8. **`processor.get_strategy_dashboard` + `cmd_strategies`** — wraps
   the Coordinator's `dashboard_stats`.
9. **`processor.get_recent_alerts` + `cmd_alerts`** — small, one
   Coordinator read.
10. **`processor.get_account_risk_state` + `cmd_risk_check`** — single
    account; renderer already pure (G4). Trivial.
11. **`processor.get_accounts_summary` + `cmd_accounts` (read path)** —
    the no-args listing. Mode-toggle (write path) deferred.
12. **`processor.get_service_logs` + `cmd_log`** — tail size + service
    name parameter. Same shape as `recent_logs_for`.
13. **Write-path APIs (Class C)** — kill switch, account dry/live
    toggle, close positions. Each requires the per-PR ping per
    CLAUDE.md § Live-mode invariant. Save for last; don't bundle.
14. **Telegram-only handlers** (`cmd_vm`, `cmd_vm_write`, `cmd_toggle`,
    `cmd_vmstats`, `cmd_sprintlet_*`, `cmd_set_keys`,
    `cmd_download_journal`) — annotate with `# Telegram-only` and
    leave as-is; webapp will not surface these.

After step 12, the read surface is fully on the processor. The webapp
can then be added as a second renderer set without forking any read
logic.

## 6. Anti-patterns to avoid during migration

* **Don't add a `processor.format_balance(account)`.** The processor
  returns data; renderers format. If a Telegram-specific format is
  needed, it belongs in `src/ui/renderers/telegram_*.py`, not in the
  processor.
* **Don't import `src.bot.*` from a webapp module.** That's the loop
  this audit is breaking. Renderers in `src/ui/renderers/` are fine to
  share if they're pure-string from a processor dict; renderers that
  reach back into bot internals are not.
* **Don't merge a "shared" renderer for two surfaces** (Telegram +
  webapp) until both surfaces actually exist. Premature
  generalisation. The pattern is one-renderer-per-surface, both fed by
  the same processor dict.
* **Don't bypass the Coordinator** by writing a processor API that
  reads units directly. The Coordinator is the source of truth for
  cross-unit state; the processor is a UI-facing facade over it.

## 7. What this audit deliberately does not change

* No code in `src/bot/telegram_query_bot.py`.
* No new processor APIs.
* No renderer relocations.
* No tests.

The next sprint executes step 1 first (lowest risk, smallest PR) and
re-evaluates the order based on what falls out.
