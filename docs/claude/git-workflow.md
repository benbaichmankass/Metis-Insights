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

## Tracking files inside an excluded glob (BUG-011 / BUG-012)

The repo-wide `*.html` exclusion exists to ignore generated coverage
reports / output dumps. Vendored web templates under `web/templates/`
need an explicit recursive whitelist to stay tracked:

```
# .gitignore
*.html
!web/templates/*.html
!web/templates/**/*.html       # recursive — fragments/, partials/, etc.
```

The recursive form (`**/*.html`) is **required** for any subdirectory
(e.g. `web/templates/fragments/`). Without it, the bare `*.html` rule
re-excludes nested files and `git add` silently skips them — confirmed
twice in the S-014 web-client work (PR #192 lost top-level templates,
PR #195 lost the `fragments/` subdir).

When adding a new tracked-asset tree under an excluded glob, ALWAYS:

1. Add both `!path/*.ext` and `!path/**/*.ext` patterns.
2. Run `git check-ignore -v <path>` to confirm the file is NOT ignored.
3. Run `git add path/ && git status` and confirm the expected files
   show as `new file:`.
