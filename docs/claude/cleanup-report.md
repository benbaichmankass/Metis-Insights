# Cleanup report

Generated as part of Claude Code setup.

## Completed

- Remove tracked editor/backup junk when present:
  - `test_bybit_keys.py.save`
  - `src/bot/telegram_query_bot.py.bak_20260409_191144`

## Security cleanup

- `bybit_config.py`: must not contain a Telegram token. Replace with env-based shim or delete after import check.
- `test_bybit_keys.py`: must not contain Bybit keys. Replace with env-based smoke helper or delete.

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
