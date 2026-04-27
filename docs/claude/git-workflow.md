# Git workflow

## Rules

- Never push unless explicitly asked.
- Show `git status -sb` before staging.
- Keep commits scoped.
- Do not include `.env`, local settings, data dumps, or model artifacts.
- Run `python scripts/secret_scan.py` before commit.

## Push command

```bash
git push origin main
```

Use branches for risky refactors:

```bash
git checkout -b chore/claude-docs
git push -u origin chore/claude-docs
```
