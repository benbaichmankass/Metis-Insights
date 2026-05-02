# Sprint Roadmap

**Project**: ICT Trading Bot  
**Current Phase**: Hardening — wrapping the live system in production-grade risk management  
**Last Updated**: 2026-05-02

---

## Sprint Overview

| Sprint | Focus | Classification | Status | Depends On |
|--------|-------|---------------|--------|------------|
| S0 | Autonomous Workflow Setup | `auto-claude` | ✅ complete | — |
| S1 | Comms Infrastructure | `auto-claude` | 🔄 next | S0 |
| S2 | Risk Caps + Kill Switch | `auto-claude` | ⏳ pending | S1 |
| S3 | Repo Hygiene (Janitor) | `auto-claude` | ⏳ pending | — |
| S4 | Web App Core | `auto-claude` | ⏳ pending | S2 |
| S5 | Strategy Testing Pipeline | `auto-claude` | ⏳ pending | S1, S2 |
| S6 | Web App UI | `auto-claude` | ⏳ pending | S4 |
| S7 | ICT Strategy Review | `pm-sprint` | ⏳ pending | S2, S5 |

---

## Sprint Details

### S0 — Autonomous Workflow Setup (`auto-claude`) ✅

**Goal**: Establish the foundational files and protocols so every subsequent sprint can operate autonomously.

**Deliverables**:
- [x] `CLAUDE.md` — master autonomous protocol with 3-tier decision matrix
- [x] `docs/sprint-roadmap.md` — this file
- [x] `comms/` directory with schema files and `sprint_state.json`
- [x] `comms/schemas/` — JSON schema examples for all comms file types
- [x] `.github/workflows/ci.yml` — CI pipeline (tests + lint on every PR)
- [x] `docs/SPRINT_1_PROMPT.md` — detailed task spec for Sprint 1

**Tier**: All Tier 1. No live trading path touched.

---

### S1 — Comms Infrastructure (`auto-claude`) 🔄

**Goal**: Get the Telegram ping system live so all future sprints can use it. This unlocks the full autonomous loop.

**Deliverables**:
- [ ] `comms/` polling logic in the Telegram bot — detect `pending_input.json`, send formatted message with inline buttons
- [ ] Bot writes response back to `comms/input_response.json` on button press
- [ ] PM Sprint start ping handler — reads `sprint_state.json`, sends session link at scheduled time
- [ ] Sprint completion ping — announces next autonomous sprint starting
- [ ] `/test [strategy]` command stub — writes `comms/test_request.json`
- [ ] `/new-session [sprint_id]` command — triggers sprint handoff
- [ ] Unit tests for all comms handlers
- [ ] Docs update

**Tier**: Tier 1 throughout (comms infra, bot handlers, no live order path touched). Self-merge once tests pass.

**Spec**: `docs/SPRINT_1_PROMPT.md`

---

### S2 — Risk Caps + Kill Switch (`auto-claude`) ⏳

**Goal**: Close the highest-priority safety gap. The live bot currently has no hard position or loss limits.

**Deliverables**:
- [ ] Add `MAX_POSITION_USD`, `MAX_DAILY_LOSS_USD`, `MAX_OPEN_POSITIONS` to `config.py`
- [ ] Enforce in `src/runtime/orders.py` — hard `raise RiskCapExceeded`, not soft warning
- [ ] Add `/halt` and `/resume` Telegram commands (pause all order submission)
- [ ] Enhance `/status` to show current exposure vs caps
- [ ] Unit tests proving cap refusal (orders above limit must be rejected)
- [ ] Dry-run smoke test

**Tier**: Touches `src/runtime/orders.py` → **Tier 2 ping** after tests pass. Claude runs full test suite + dry-run, then pings Ben with merge/hold button.

**Depends on**: S1 (needs comms infrastructure for the Tier 2 ping)

---

### S3 — Repo Hygiene / Janitor Mode (`auto-claude`) ⏳

**Goal**: Kill duplicate files, dead services, and orphan configs. This sprint is also the template for recurring Janitor Mode runs.

**Known issues to resolve**:
- [ ] Resolve `src/backtester.py` vs `src/backtest/` — pick canonical, delete duplicate
- [ ] Resolve `src/bot/telegramquerybot.py` vs `src/bot/telegram_query_bot.py` — delete the orphan
- [ ] Delete `.bak` files after confirming originals are canonical
- [ ] Audit `deploy/` for stale systemd unit files
- [ ] Audit `scripts/` for scripts with no callers
- [ ] Resolve/flesh out `src/strategies_manager.py` — stub or implement
- [ ] Add missing `__init__.py` files that break imports
- [ ] Each fix in a **separate PR** — never bundle unrelated cleanups

**Tier**: All Tier 1. Pure cleanup, no live trading path. Self-merge all PRs.

**Note**: S3 can run in parallel with other sprints since it's all Tier 1 cleanup. Can be triggered by Ben via `/janitor` Telegram command once S1 is live.

---

### S4 — Web App Core (`auto-claude`) ⏳

**Goal**: Build the foundational backend for a visual dashboard — P&L tracking, open positions, bot status.

**Deliverables**:
- [ ] FastAPI (or Flask) backend in `src/webapp/`
- [ ] API endpoints: `/status`, `/positions`, `/pnl`, `/signals`
- [ ] Data layer connecting to trade journal DB
- [ ] Authentication (API key or simple token)
- [ ] Basic health check endpoint
- [ ] Docker Compose service definition
- [ ] Tests for all endpoints

**Tier**: All Tier 1. New service, no live trading path modified.

**Depends on**: S2 (risk caps and status data from orders.py)

---

### S5 — Strategy Testing Pipeline (`auto-claude`) ⏳

**Goal**: Repeatable, bot-triggered strategy testing. Ben types `/test turtle_soup_mtf_v1` and gets a full backtest report.

**Deliverables**:
- [ ] `/test [strategy]` command in Telegram bot — writes `comms/test_request.json`
- [ ] Async test runner: reads `test_request.json`, runs backtest, writes results to `comms/test_results.json`
- [ ] Bot sends results summary: win rate, R-multiple, max drawdown, sample size
- [ ] Dry-run staging mode: 2 cycles clean before Tier 2 ping for live promotion
- [ ] Per-strategy position sizing support
- [ ] Multi-strategy test coverage

**Tier**: Command handler and runner are Tier 1. Promoting to live is Tier 2 (with full test evidence) or Tier 3 (strategy parameter changes).

**Depends on**: S1 (comms), S2 (risk caps must be live before staging)

---

### S6 — Web App UI (`auto-claude`) ⏳

**Goal**: Visualization layer on top of the S4 backend.

**Deliverables**:
- [ ] React or plain HTML/JS frontend in `src/webapp/ui/`
- [ ] P&L chart (daily, cumulative)
- [ ] Open positions table with entry price, current price, unrealized P&L
- [ ] Signal log with timestamps
- [ ] Bot status indicator (running / halted / dry-run)
- [ ] Strategy performance comparison view
- [ ] Served as a static build from the FastAPI backend

**Tier**: All Tier 1. UI only, no trading logic.

**Depends on**: S4

---

### S7 — ICT Strategy Review (`pm-sprint`) ⏳

**Goal**: Ben reviews FVG/OB backtest results and decides on live promotion. Also reviews any strategy parameter optimizations from S5/S6 data.

**Claude pre-loads**:
- Multi-symbol FVG/OB validation results (50+ trades)
- Current dry-run stats vs live benchmark
- Risk cap headroom (current exposure vs MAX_POSITION_USD)
- Parameter sensitivity analysis from S5 backtest runs

**PM decisions required**:
- Promote ICT strategy to live, extend dry-run, or kill
- Approve/reject any parameter changes from S6 analysis

**Tier**: All changes resulting from this sprint are **Tier 3** — no merges without Ben's explicit approval in the session.

**Depends on**: S2 (risk caps live), S5 (strategy testing pipeline)

---

## Roadmap Notes

1. **S3 (Janitor)** can run concurrently with any other sprint since it's all Tier 1 cleanup PRs.
2. **S1 is the unlock sprint** — the comms infrastructure enables autonomous pings for all subsequent Tier 2/3 decisions.
3. **S2 must be live before any strategy goes live** — risk caps are non-negotiable.
4. New `pm-sprint` items can be inserted before S7 if Ben schedules calendar blocks.
