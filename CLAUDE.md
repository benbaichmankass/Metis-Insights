# CLAUDE.md

Lean router for Claude Code sessions in the ICT Trading Bot repo.

## First rule

Do not load every project document by default. Start with this file, identify the task type, then read only the focused docs listed below.

## Resume rule (read before anything else)

1. Read `docs/claude/checkpoints/CHECKPOINT_LOG.md` — the **most recent entry**
   tells you exactly where to resume.
2. Read `docs/claude/checkpoint-workflow.md` for the rules.
3. Only then read the task-specific docs below.

Do **not** start from the top of the sprint plan. Always resume from the
latest checkpoint. Work **one task per session**, keep changes **PR-sized**,
stop and hand off if usage/context/timeout limits are near, and continue the
sprint even if a previous PR has not been merged yet.

At the **end of every session**, append an entry to the checkpoint log using
`docs/claude/checkpoints/HANDOFF_TEMPLATE.md`. The entry must contain:
1. Completed   2. Files changed   3. Tests run   4. Remaining   5. Next checkpoint.
Then send the Telegram session ping via `scripts/notify_session.py session`
(and the sprint ping via `... sprint` only if the whole sprint is done).

## Task routing

| Task | Read |
|---|---|
| Any session | `docs/claude/checkpoints/CHECKPOINT_LOG.md`, `docs/claude/checkpoint-workflow.md`, `docs/claude/INDEX.md` |
| End-of-session handoff | `docs/claude/checkpoint-workflow.md`, `docs/claude/checkpoints/HANDOFF_TEMPLATE.md` |
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
- For tests and notebooks, never pull market data from Binance or other
  key-gated exchanges. Use hand-crafted DataFrames, repo fixtures, or open
  keyless sources (Bybit public, Coinbase public, Kraken public,
  CryptoCompare, yfinance, or our HF datasets). See
  `docs/claude/testing-policy.md` → “Test data sources”.

## Merging Rules (MANDATORY)

- For ALL sprint PRs: Create PR → run tests/lint → if green → **SELF-MERGE immediately via GitHub MCP tools**.
- No waiting for manual approval.
- ONLY flag for PM review (do not self-merge):
  1. New secrets/API key handling.
  2. Changes to live trading logic (`src/runtime/orders.py`).
  3. VM deployment scripts (`deploy/`).
- After self-merge, post `/sprintlet_status PR#X merged` to Telegram bot.

## Default verification

Run lightweight checks only:

```bash
python scripts/repo_inventory.py
python scripts/secret_scan.py
PYTHONPATH=. pytest --collect-only -q tests
```

If tests need optional dependencies, explain the missing dependency and do not install broad packages without approval.
