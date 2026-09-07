# Cleanup policy

> **Doc status:** `unknown` · category `instruction` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

## Usually safe to delete

- `*.bak`, `*.save`, `*.tmp`, editor swap files.
- `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`.
- notebook checkpoints.
- generated logs and runtime output.

## Never delete without review

- Strategy code.
- Exchange integration code.
- Telegram command code.
- Database migrations or schema files.
- Sample data used by tests.
- Model files unless there is a Hugging Face/Drive replacement.
- Anything containing credentials: sanitize first, rotate keys, then remove.

## Pre-delete checklist

```bash
git status -sb
git grep -n "filename_or_symbol"
PYTHONPATH=. pytest --collect-only -q tests
```
