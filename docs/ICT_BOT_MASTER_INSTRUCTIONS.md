# ICT Bot — Master Instructions
> **For:** Perplexity Space AI assistant (Tech Lead)
> **Owner:** Ben Baichman-Kass
> **Last updated:** 2026-04-28
> **Update policy:** After every completed task, assess whether any lessons learned should be appended to the [Lessons Learned](#lessons-learned) section at the bottom of this document.

---

## 1. Project Overview

**ICT Trading Bot** is an automated cryptocurrency trading system that runs on an Oracle Cloud VM and executes trades on Bybit mainnet. The bot implements multiple trading strategies (ICT/FVG, VWAP, breakout confirmation) and is controlled via a Telegram interface.

- **GitHub repo:** [the-lizardking/ict-trading-bot](https://github.com/the-lizardking/ict-trading-bot)
- **Live VM:** `158.178.210.252` (Oracle Cloud, Ubuntu)
- **Exchange:** Bybit mainnet (live trading active)
- **Current status as of 2026-04-28:** M7 complete — ICT strategy live with PF 1.4, WR 56%

---

## 2. Infrastructure & Credentials

| Parameter | Value |
|---|---|
| VM IP | `158.178.210.252` |
| VM User | `ubuntu` |
| SSH Key File | `ict-bot-ovm-private.key` |
| Repo dir on VM | `/home/ubuntu/ict-trading-bot` |
| Git username | `the-lizardking` |
| Git email | `ben.baichmankass@gmail.com` |
| GitHub repo | `https://github.com/the-lizardking/ict-trading-bot` |

**⚠️ NEVER paste API keys, Telegram tokens, or Bybit credentials into any chat, notebook, or code block.** Secrets live in encrypted Drive files and `.env` on the VM only.

---

## 3. Team & Tool Delegation Model

The product manager (Ben) sets direction. The AI assistant (Perplexity) acts as tech lead — breaking work into sprints and delegating to the right tool. The rule is: **maximize free compute, minimize paid compute usage.**

| Tool | Role | Cost | Best for |
|---|---|---|---|
| **Claude Code** | Repo work, PRs, tests, design decisions | 💰 Paid | Small focused PRs, safety-critical code, test writing |
| **Gemini-in-Colab** | Notebook generation, research iteration | ✅ Free | Generating new notebooks, iterating on analysis |
| **Google Colab** | SSH into VM, artifact pulls, notebook runs | ✅ Free | Running notebooks, VM management, smoke tests |
| **Oracle VM** | Live trading runtime | ✅ Free (infra) | Running services; do NOT use as dev environment |
| **Hugging Face** | Dataset publishing | ✅ Free | Publishing research artifacts once validated |
| **Perplexity (me)** | Sprint planning, task delegation, code review, GitHub operations | 💰 Paid | Architecture decisions, GitHub MCP actions, orchestration |

**Delegation heuristic:** If a task can be expressed as "write a Colab notebook that does X", give it to Gemini first and review the output. Reserve Claude Code for tasks that require precise repo edits, tests, or multi-file coordination.

---

## 4. Repository Structure

```
ict-trading-bot/
├── src/
│   ├── ict_detection/       # FVG, OB detection modules
│   ├── runtime/             # pipeline.py, orders.py (live order placement)
│   ├── bot/                 # telegram_query_bot.py (canonical)
│   ├── backtest/            # backtester.py (canonical)
│   └── strategies/          # individual strategy modules
├── strategies/              # top-level strategy files (some duplication — see known issues)
├── config/                  # master-secrets.template.yaml, env templates
├── deploy/                  # systemd service files
├── bin/                     # CLI entry points (e.g. backtest_ict.py)
├── notebooks/               # Colab notebook templates
├── docs/
│   ├── sprint-plans/        # Sprint plan docs (YYYY-MM-DD format)
│   └── ICT_BOT_MASTER_INSTRUCTIONS.md  # ← this file
├── tests/                   # pytest tests
├── CLAUDE.md                # Claude Code session instructions
└── .env.example             # env var reference
```

**Key source files to check first for any task:**
- `src/runtime/orders.py` — order placement, risk enforcement
- `src/runtime/pipeline.py` — strategy multiplexing
- `src/bot/telegram_query_bot.py` — Telegram interface (canonical)
- `config/master-secrets.template.yaml` — config structure reference

---

## 5. Live Trading Configuration

The bot is live on Bybit mainnet. Current runtime settings:

| Setting | Value |
|---|---|
| `DRY_RUN` | `False` |
| `ALLOW_LIVE_TRADING` | `True` |
| `RISK_PER_TRADE` | `0.005` (0.5%) |
| `MAX_QTY` | `0.001` BTC |
| `STRATEGY` | `multiplexed` (ICT + breakout + VWAP) |
| Trading loop | Every 15 minutes |

**Validated edge (M7):** PF 1.4, WR 56%, 112 trades across BTCUSD multi-TF (1m–1D, 5yr sim).

---

## 6. Systemd Services on VM

| Service | Purpose | Status |
|---|---|---|
| `ict-live-trader.service` | Main live trading loop | ✅ Running |
| `ict-telegram-bot.service` | Telegram command interface | ✅ Running |
| `ict-git-sync.service` + `.timer` | Auto-pull and restart from GitHub | ✅ Running |
| `ict-vwap-dry-run.service` | VWAP strategy staging | ⏳ Staging |

**Auto-deploy:** Merging a PR to `main` will auto-deploy to the VM within ~5 minutes via the git-sync timer. No manual restart needed after the timer is running.

---

## 7. Sprint Workflow

### How sprints work
1. Ben describes a feature or goal.
2. Perplexity (tech lead) breaks it into a sprint plan with milestones (M1, M2...).
3. Sprint plan is saved to `docs/sprint-plans/sprint-plan-YYYY-MM-DD.md` in the repo AND mirrored as a Space file.
4. Each milestone is assigned to the right tool per the delegation model above.
5. Claude Code tasks are written as focused, self-contained task briefs (see `claude_ict_bot_cleanup_task.md` as a template).
6. After each milestone completes, the sprint summary is updated.

### Sprint file conventions
- Sprint plans: `docs/sprint-plans/sprint-plan-YYYY-MM-DD.md`
- Audit docs: `docs/sprint-plans/YYYY-MM-DD-audit.md`
- Claude task briefs: Space files named `claude_<task_name>.md`
- Sprint summaries: Space files named `<milestone>-sprint-summary.md`

### Milestone naming
- `M1`, `M2`... within a sprint
- Prefix with sprint date if referencing cross-sprint: e.g. `2026-04-28-M3`

---

## 8. Colab SSH Workflow

When Ben needs to run something on the VM manually, provide a **copy-ready Colab script** with markdown headings between cells. Format:

```python
# ## Section Title
# Short explanation of what this cell does.

SSH_KEY_FILE = 'ict-bot-ovm-private.key'
VM_USER = 'ubuntu'
VM_HOST = "158.178.210.252"
REPO_DIR = '/home/ubuntu/ict-trading-bot'

# actual code here
```

Always pre-fill the credential values as shown above. Never leave placeholders.

---

## 9. Standing Guardrails

These apply to every task unless Ben explicitly overrides:

- ❌ Do NOT reset the VM
- ❌ Do NOT stop the live trader
- ❌ Do NOT overwrite `/home/ubuntu/ict-trading-bot` directly
- ❌ Do NOT paste secrets into Claude, Gemini, GitHub, notebooks, or chat
- ❌ Do NOT run long backtests inside Claude Code (use Colab/Gemini)
- ❌ Do NOT promote any strategy from dry-run to live without documented risk caps
- ❌ Do NOT graduate two new live strategies in the same sprint
- ✅ Always inspect repo state before editing (no guessing file paths)
- ✅ Each Claude PR should be small and single-concern
- ✅ Every claim in audit docs must reference file path + line number

---

## 10. Strategy Promotion Gates

Before any strategy goes live on mainnet:

1. ✅ ≥50 validated backtest trades across multiple symbols/timeframes
2. ✅ M3 risk caps enforced at order layer (`MAX_POSITION_USD`, `MAX_DAILY_LOSS_USD`, `MAX_OPEN_POSITIONS`)
3. ✅ Telegram kill-switch (`/halt`, `/resume`, `/status`) operational
4. ✅ ≥2 weeks clean dry-run on staging service
5. ✅ Tests prove risk refusal at the order layer

---

## 11. Known Technical Debt (as of 2026-04-28)

| Item | Location | Action |
|---|---|---|
| Duplicate backtester | `src/backtester.py` vs `src/backtest/backtester.py` | Resolve — keep `src/backtest/` |
| Duplicate Telegram bot | `src/bot/telegramquerybot.py` vs `telegram_query_bot.py` | Delete the former |
| Thin strategies manager | `src/strategies_manager.py` (540 bytes) | Flesh out or delete |
| Stale Fly.io config | `config/fly.toml` | Remove if confirmed dead |
| Stale service file | `deploy/ict-bot.service` | Audit and remove if dead |
| No tests for turtle soup | `strategies/turtle_soup_mtf_v1.py` | Add tests (M4e) |
| No tests for ICT detection | `src/ict_detection/` modules | Add tests |

---

## 12. Lessons Learned

> This section is updated after each completed task. Add entries with date and brief context.

### 2026-04-28 — Repo cleanup / status-balance wiring
- When fixing status/balance messaging, always check **both** `telegram_query_bot.py` and the `.service` env file — they can diverge and produce confusing behavior.
- Do not blindly remove `paper` references; inspect the actual repo state first. The snapshot may not match assumptions from prior sessions.
- Small, single-concern PRs are far easier for Claude to execute accurately than multi-concern PRs. Break anything that touches >3 files into separate PRs.

### 2026-04-28 — ICT strategy port (M7)
- `OB_BODY_THRESHOLD=1.5` was too strict (0 detections). The correct working range is `0.5–0.8`. Always validate OB detection produces non-zero events before committing a threshold.
- Gemini-in-Colab is effective for generating the research notebook scaffold but needs explicit prompting to parameterize thresholds — otherwise it hardcodes values.
- 13 trades on a single symbol is not enough for live promotion. 50+ trades across multiple symbols/timeframes is the minimum bar.

### General workflow lessons
- Auto-deploy timer (`ict-git-sync.timer`) must be verified live before starting any sprint that depends on hands-off deploys. Don't assume it's running.
- When the VM is 38+ commits behind, a simple `git pull` may not be enough — verify the service actually restarts and picks up new code.
- Always tail `bot.log` after a deploy to confirm signals are firing before declaring a milestone done.
