# Session workflow

## Start

1. Read `CLAUDE.md`.
2. Read `docs/claude/checkpoints/CHECKPOINT_LOG.md` — the **latest entry** is
   your starting point. Read `docs/claude/checkpoint-workflow.md` for the rules.
3. Read only the routed docs needed for the task.
4. Check `git status -sb`.
5. Identify whether the task is local, Colab, Hugging Face, Oracle VM, or GitHub.
6. State what will not be run, especially training, backtests, live trading, or deployment.
7. Confirm: **one task per session**, **PR-sized**, **stop if limits are near**.

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

**Append a checkpoint entry** to `docs/claude/checkpoints/CHECKPOINT_LOG.md`
using `HANDOFF_TEMPLATE.md`. Required fields: Completed, Files changed,
Tests run, Remaining, Next checkpoint.

Send the Telegram session ping (and sprint ping if the whole sprint is done):

```bash
PYTHONPATH=. python scripts/notify_session.py session \
  --checkpoint "CP-YYYY-MM-DD-NN" \
  --summary "<one-line summary>"
```

If `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` are not set, the script logs a
warning and exits 0 — record `Telegram sent: no (no creds)` in the log entry.

## Commit/push block

Only push when explicitly asked:

```bash
git status -sb
git add -A
git commit -m "type(scope): concise message"
git push origin main
```
