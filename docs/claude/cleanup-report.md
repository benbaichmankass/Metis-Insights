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

## Candidate migrations

- Large CSV files should move to Hugging Face datasets or Drive if not required as tiny fixtures.
- Local model artifacts should move to Hugging Face model repos.
- Old top-level docs superseded by `docs/claude/` can be archived after review.

## Process

Run:

```bash
python scripts/repo_inventory.py
python scripts/secret_scan.py
```

Process one row at a time in separate Claude sessions.
