# CLAUDE.md

Lean router for Claude Code sessions in the ICT Trading Bot repo.

## First rule

Do not load every project document by default. Start with this file, identify the task type, then read only the focused docs listed below.

## Task routing

| Task | Read |
|---|---|
| Any session | `docs/claude/INDEX.md`, then the smallest relevant subset |
| Bug fix / regression | `docs/claude/session-workflow.md`, `docs/claude/debug-memory.md`, `docs/claude/testing-policy.md` |
| Repo cleanup | `docs/claude/cleanup-policy.md`, `docs/claude/cleanup-report.md` |
| ML model work | `docs/claude/ml-training-policy.md`, `docs/claude/external-delegation.md` |
| Colab work | `docs/claude/colab-workflows.md` |
| Hugging Face work | `docs/claude/huggingface-workflows.md` |
| Deployment / Oracle VM | `docs/claude/deployment-ops.md`, `docs/claude/security-secrets.md` |
| Git / PR / push | `docs/claude/git-workflow.md`, `docs/claude/security-secrets.md` |
| Architecture lookup | `docs/claude/repo-map.md` |

## Always do

- Keep changes small and reversible.
- Prefer scripts/notebooks that let Colab, Hugging Face, or the VM do heavy work.
- Do not run training, full backtests, live trading, or deployment unless explicitly asked.
- Do not print secrets.
- Update the relevant `docs/claude/*.md` file after discovering a recurring bug, cleanup rule, or workflow improvement.

## Default verification

Run lightweight checks only:

```bash
python scripts/repo_inventory.py
python scripts/secret_scan.py
PYTHONPATH=. pytest --collect-only -q tests
```

If tests need optional dependencies, explain the missing dependency and do not install broad packages without approval.
