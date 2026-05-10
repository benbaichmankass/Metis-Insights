# ICT Bot — Master Instructions
> **For:** Perplexity Space AI assistant (Tech Lead)
> **Owner:** Ben Baichman-Kass
> **Last updated:** 2026-04-28 (CP-16)
> **Update policy:** After every completed task, assess whether any lessons learned should be appended to the [Lessons Learned](#lessons-learned) section at the bottom of this document.

---

## 1. Project Overview

**ICT Trading Bot** is an automated cryptocurrency trading system that runs on an Oracle Cloud VM and executes trades on Bybit mainnet. The bot implements multiple trading strategies (ICT/FVG, VWAP, breakout confirmation) and is controlled via a Telegram interface.

- **GitHub repo:** [benbaichmankass/ict-trading-bot](https://github.com/benbaichmankass/ict-trading-bot) (former owner: `the-lizardking` — preserved in older sprint records)
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
| Git username | `benbaichmankass` (was `the-lizardking`) |
| Git email | `ben.baichmankass@gmail.com` |
| GitHub repo | `https://github.com/benbaichmankass/ict-trading-bot` |

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
| `ict-trader-live.service` | Main live trading loop | ✅ Running |
| `ict-telegram-bot.service` | Telegram command interface | ✅ Running |
| `ict-git-sync.service` + `.timer` | Auto-pull and restart from GitHub | ✅ Running |

### VM is a read-only mirror of `origin/main`

The VM **must never carry local commits** that aren't on GitHub. The deploy
script (`scripts/deploy_pull_restart.sh`) uses `git fetch && git reset --hard
origin/main`, which means any uncommitted changes or local-only commits on
the VM will be wiped on the next sync (every 5 minutes via
`ict-git-sync.timer`).

This is enforced for two reasons:
1. **Drift detection.** If something is different between the VM and `main`,
   it must show up in `git log origin/main` — not as a hidden VM-only commit.
2. **Reproducibility.** Anything reproducible must come from a GitHub commit.
   If a fix only exists on the VM, it does not exist.

**Workflow rule:** Never `git commit` on the VM. Never `git push` from the
VM. All changes go through a PR → merge to `main` → auto-sync. The deploy
script also restarts services unconditionally, so a manual `git reset --hard`
on the VM is correctly picked up by the running Python processes.

**Auto-deploy:** Merging a PR to `main` auto-deploys to the VM within ~5
minutes via the git-sync timer. No manual restart needed.

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
- ❌ Do NOT commit on the VM — it is a read-only mirror of `origin/main` (see §6)
- ❌ Do NOT introduce **paper trading** in any form. There is no paper trader, no paper-trading mode, no `MODE=PAPER`, no `live|paper` toggles. We build, test, and deploy live on small accounts. Nothing in the repo, the bot, the docs, or any user-facing surface should suggest paper trading is even a possibility. When in doubt, remove the paper reference.
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
- ~~Do not blindly remove `paper` references; inspect the actual repo state first.~~ **Superseded 2026-04-28 (CP-16):** Paper trading is being fully excised from the repo. See §9 guardrail. Remove paper references on sight.
- Small, single-concern PRs are far easier for Claude to execute accurately than multi-concern PRs. Break anything that touches >3 files into separate PRs.

### 2026-04-28 — ICT strategy port (M7)
- `OB_BODY_THRESHOLD=1.5` was too strict (0 detections). The correct working range is `0.5–0.8`. Always validate OB detection produces non-zero events before committing a threshold.
- Gemini-in-Colab is effective for generating the research notebook scaffold but needs explicit prompting to parameterize thresholds — otherwise it hardcodes values.
- 13 trades on a single symbol is not enough for live promotion. 50+ trades across multiple symbols/timeframes is the minimum bar.

### General workflow lessons
- Auto-deploy timer (`ict-git-sync.timer`) must be verified live before starting any sprint that depends on hands-off deploys. Don't assume it's running.
- ~~When the VM is 38+ commits behind, a simple `git pull` may not be enough~~ **Superseded 2026-04-28 (CP-16):** The deploy script now uses `git fetch && git reset --hard origin/main` and restarts services unconditionally, which is robust against VM drift. The VM is a read-only mirror; any divergence is automatically corrected on the next 5-minute sync.
- Always tail `bot.log` after a deploy to confirm signals are firing before declaring a milestone done.

### 2026-04-28 — CP-16: Excise paper trading; harden VM auto-sync
- The bot's `live|paper` slash-command targets were a legacy of dual trader instances we no longer run. Excising paper trading is a multi-PR sprint (CP-16 → CP-19): bot → env-rendering scripts → src/ runtime mode branches → docs + config templates.
- The deploy script's `if "Already up to date": exit 0` early-return was a foot-gun: any time the VM was *manually* resynced (e.g. after a `git reset`), services were left running stale code because the script skipped restart. Always restart services unconditionally; gate only the expensive steps (pip install) on actual HEAD movement.
- Whenever a fix is only present on the VM and not on GitHub, treat that as a bug in the deploy/sync model, not a feature. The fix is to make the VM cleanly resyncable, not to preserve VM-only state.

### 2026-04-28 — CP-17/18/19: Paper-trading excision complete
- **CP-17** (PR #58, merged): removed `paper`, `colab`, `oracle_paper`, and `vwap_btcusd_dry_run` profiles from `scripts/render_env_from_master.py`; deleted `scripts/check_env_paper.py`; flipped `.env.example` to live-only defaults.
- **CP-18** (PR #59, merged): tightened `MODE` whitelist to `(LIVE, BACKTEST)` in `src/runtime/validation.py` (paper rejected outright); removed `.env.paper` auto-load fallback; renamed DRY_RUN order status `"simulated"` → `"dry_run"`; purged paper-trading vocabulary from `src/bot/telegram_query_bot.py` and `src/exchange/bybit_connector.py`.
- **CP-19** (this PR): scrubbed paper references from `docs/`, `config/master-secrets.template.yaml` (dropped `profiles.paper/colab/oracle_paper/vwap_btcusd_dry_run` and `risk.paper`), and `DEPLOYMENT_LIVE_TRADING.md`; added ARCHIVED banners to legacy planning docs (`claude_code_work_plan.md`, `claude_project_setup_guide.md`, `docs/sprint-plans/sprint-plan-2026-04-27.md`) preserving them as historical record.
- **End state:** the only `paper`/`PAPER` references remaining in the repo are (a) the explanatory comment in `src/runtime/validation.py` ("paper-trading is intentionally not a supported mode"), (b) similar warnings in operational docs telling readers paper is *not* supported, (c) banners on archived historical docs, and (d) the checkpoint log itself. The bot, the runtime, the env-rendering pipeline, the secrets template, and the deployment docs are paper-free.
- **DRY_RUN survives** as a short-window safety toggle (logged as `"dry_run"` status when an order is intercepted). It is NOT paper trading — it just means "use real live keys but don't submit the order". The interlock is `DRY_RUN=false` requires `ALLOW_LIVE_TRADING=true`.
