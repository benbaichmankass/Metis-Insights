# Cleanup report

Generated as part of Claude Code setup.

## Completed

- **M4b** (sprint 2026-04-28): `src/bot/telegramquerybot.py` (no-underscore duplicate) deleted in
  PR #31. Verified in M4b audit: `src/bot/telegram_query_bot.py` (canonical) is the live Telegram
  bot process — invoked by `run_telegram_bot.sh`, `scripts/start.sh`, monitored by `check_bots.sh`,
  and imported by `tests/test_kill_switch.py`. Do **not** delete the canonical file.
  Note: `deploy/ict-telegram-bot.service` contains a stale `ExecStart` path (`-m src.telegram_bot`);
  the real entrypoint is `run_telegram_bot.sh`.
- Remove tracked editor/backup junk when present:
  - `test_bybit_keys.py.save`
  - `src/bot/telegram_query_bot.py.bak_20260409_191144`
- `bybit_config.py`: rewrote as a clean env-based shim exposing only
  `BYBIT_TESTNET_API_KEY` and `BYBIT_TESTNET_API_SECRET`. Removed
  misplaced Telegram vars (nothing imported them from here). Fixed a
  pre-existing SyntaxError caused by escaped-quote docstring (`\"\"\"` →
  `"""`).

## Security cleanup

- `test_bybit_keys.py`: replaced with `tests/test_bybit_env.py` — a proper pytest smoke test
  that skips gracefully when env vars are absent. Top-level script deleted. No hardcoded keys
  were present in the original file; the escaping bug (`\"\"\"`) was also resolved.

## Completed (continued)

- **S-004 M1** (2026-04-29): `deploy/ict-telegram-bot.service` ExecStart corrected from
  `src.telegram_bot` → `src.bot.telegram_query_bot` (PR #97).
- **S-004 M2** (2026-04-29): Deleted 3 self-declared archived docs — `claude_code_work_plan.md`,
  `claude_project_setup_guide.md`, `THREAD1_CHANGELOG.md`. No code or test references to any of
  them. (PR #98)

## Completed (continued)

- **S-004 M3** (2026-04-29): HF loaders wired in `strategies/breakout_confirmation.py` and
  `ml/src/test_breakout_strategy.py`; `scripts/hf_upload_large_files.py` created (PR #99).
- **S-004 M4** (2026-04-29): Large files removed after confirmed HF upload (PR #100):
  - `data/bybit_btcusdt_1m.csv` → `bentzbk/ict-trading-bot-btcusdt-1m` dataset
  - `ml/data/raw/btcusdt_1m.csv` → `bentzbk/ict-trading-bot-btcusdt-1m` dataset
  - `ml/models/local/btc_breakout_confirmation_v1.joblib` → `bentzbk/ict-trading-bot-rf-breakout-v1` model

## HF migration backlog (do not delete without HF upload first)

| File | Size | Used by | Action |
|---|---|---|---|
| ~~`data/bybit_btcusdt_1m.csv`~~ | ~~2.4 MB~~ | `download_bybit_history.py`, `run_comparison_backtest.py` | ✅ Removed (M4) |
| ~~`ml/data/raw/btcusdt_1m.csv`~~ | ~~3.4 MB~~ | `ml/src/test_breakout_strategy.py` | ✅ Removed (M4) |
| ~~`ml/models/local/btc_breakout_confirmation_v1.joblib`~~ | ~~1.5 MB~~ | `strategies/breakout_confirmation.py` | ✅ Removed (M4) |

**Do not migrate:** `data/btc_1m_sample.csv` — test fixture used by 4 tests, must stay in repo.
**Do not migrate:** `data/backtest_candles.csv` — default backtest data loaded by `src/backtest/run_backtest.py`.

## Candidate migrations (remaining)

- Old top-level docs in `docs/` (`architecture.md`, `bot.md`, `deployment.md`, `news_layer.md`,
  `hf_claude_patch.md`) reviewed and left in place — actively maintained or still referenced.

## Completed (continued)

- **CP-2026-05-02-08** (2026-05-02, S-XXX G6): trimmed `src/runtime/signal_notifications.py` to its live surface. The module had grown into a grab-bag of legacy notification helpers that no live code path imports anymore — superseded by `src/runtime/hourly_report.py` (S-022 PR2) for summaries and by `src/bot/telegram_query_bot.py` (S-023 multi-account renderers) for everything else. Functions removed (verified zero non-self callers across `src/`, `scripts/`, `tests/`, `notebooks/`):
  - `msg_bi_daily(stats)` — explicit-removal hard error stub kept since CP-2026-05-02-01; the prompt for this sprint asked whether it could be deleted entirely. Yes — no callers remain in the live tree, and the legacy "Bi-daily summary" string is forbidden by `should_send_summary` already.
  - `msg_started`, `msg_stopped`, `msg_trade_open`, `msg_trade_close` — string formatters never called from any live path; the bot uses different on-trade and on-startup notification strings now (see `src/runtime/notify.py` and the trader's startup logging).
  - `plot_signal_summary`, `plot_trade_chart`, `_plot_base` — matplotlib chart helpers wired for an old per-trade chart attachment that's been replaced by HTML chart artefacts (`ict_complete_chart.html` etc.) at trade-write time.
  - `summarize_trades`, `load_db` — unused stat utilities.
  - `import matplotlib.pyplot as plt` — no longer imported at all. Several test files used to carry "matplotlib is a transitive dep of signal_notifications" comments — those scaffolds can be loosened in a follow-up sprint, but I haven't touched them in this PR.
- Survives: `fetch_df`, `get_last_signals`, `format_signals`, `ensure_signals_table`, `insert_signal` — these are the four entry points consumed by `src/bot/telegram_query_bot.py` (signals view) and `src/runtime/signal_writer.py` (DB insert).
- Inventory verified `python scripts/repo_inventory.py`: no junk candidates, no `*_old.py` / `*_bak.py` / `*.save` / `*.orig` files in the tree. All 8 `.service` files in `deploy/` are referenced by `scripts/install_systemd_units.sh`, `scripts/deploy_pull_restart.sh`, `scripts/vm_bootstrap.sh`, or `scripts/daily_heartbeat.py` — none are dead. The 8 notebooks under `notebooks/` (including `ict_multi_symbol_backtest.ipynb`) are operator/setup tooling, not retired training notebooks; `notebooks/training/` does not exist.
- `.env.example` siblings: only `.env.example` itself is tracked. It's used by `README.md` (developer onboarding) and `tests/test_s006_ict_risk_config.py`. It does **not** match `_ENV_DISCOVERY_RESERVED` filtering at runtime — the runtime filter uses the `.env.<account_id>` shape, and `_ENV_DISCOVERY_RESERVED` already excludes `example`. Nothing to remove here.
- Fixed an unrelated regression introduced by CP-2026-05-02-05 (G3, #267): `tests/test_telegram_surface_cleanup.py::test_botcommand_registry_includes_vm_commands` did a literal-string match for `BotCommand("vm",` which the G3 `BotCommandSpec` refactor broke. Test now accepts either `BotCommand("vm", ...)` or `BotCommandSpec("vm", ...)`. The invariant being asserted (vm and vm_write surface in the operator menu) is unchanged.

## Process
