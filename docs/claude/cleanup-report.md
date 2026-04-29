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

## HF migration backlog (do not delete without HF upload first)

| File | Size | Used by | Action |
|---|---|---|---|
| `data/bybit_btcusdt_1m.csv` | 2.4 MB | `download_bybit_history.py`, `run_comparison_backtest.py` | Upload to bentzbk HF dataset, update path refs, then `git rm` |
| `ml/data/raw/btcusdt_1m.csv` | 3.4 MB | `ml/src/test_breakout_strategy.py` | Upload to HF dataset, update path ref, then `git rm` |
| `ml/models/local/btc_breakout_confirmation_v1.joblib` | 1.5 MB | `strategies/breakout_confirmation.py` (live load) | Upload to HF model repo, update loader to `hf_hub_download`, then `git rm` |

**Do not migrate:** `data/btc_1m_sample.csv` — test fixture used by 4 tests, must stay in repo.
**Do not migrate:** `data/backtest_candles.csv` — default backtest data loaded by `src/backtest/run_backtest.py`.

## Candidate migrations (remaining)

- Old top-level docs in `docs/` (`architecture.md`, `bot.md`, `deployment.md`, `news_layer.md`,
  `hf_claude_patch.md`) reviewed and left in place — actively maintained or still referenced.

## Process

Run:

```bash
python scripts/repo_inventory.py
python scripts/secret_scan.py
```

Process one row at a time in separate Claude sessions.
