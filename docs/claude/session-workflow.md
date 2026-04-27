# Session workflow

## Start

1. Read `CLAUDE.md`.
2. Read only the routed docs needed for the task.
3. Check `git status -sb`.
4. Identify whether the task is local, Colab, Hugging Face, Oracle VM, or GitHub.
5. State what will not be run, especially training, backtests, live trading, or deployment.

## Middle

- Make the smallest useful change.
- Prefer one focused script/notebook over many manual commands.
- Keep non-technical instructions copy-ready.
- Never print `.env` contents or API keys.

## End

Run lightweight checks:

```bash
python scripts/repo_inventory.py
python scripts/secret_scan.py
PYTHONPATH=. pytest --collect-only -q tests
```

Then update the relevant `docs/claude/*.md` memory file.

## Commit/push block

Only push when explicitly asked:

```bash
git status -sb
git add -A
git commit -m "type(scope): concise message"
git push origin main
```
