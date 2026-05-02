# Sprint Roadmap

**Project**: ICT Trading Bot
**Current Phase**: Hardening — wrapping the live system in production-grade risk management
**Last Updated**: 2026-05-02

---

## Structure

The roadmap is organized in three levels:

- **Milestone (M)**: A coherent theme or capability (e.g., "Production Hardening", "Web App")
- **Sprint (S)**: One Claude Code session worth of work (~2–6 hours of focused execution)
- **Checkpoint (C)**: An individually verifiable unit inside a sprint (test passes, file produced, behavior demonstrated)

Sprints are referenced as `M{milestone}.S{sprint}` (e.g., `M1.S2`). Checkpoints as `M{milestone}.S{sprint}.C{checkpoint}`.

Every checkpoint must be independently verifiable — a checkpoint is "done" when its acceptance criterion is observably true (test passes, file present, log line emitted, etc.).

---

## Milestone Map

| Milestone | Theme | Status | Classification |
|-----------|-------|--------|---------------|
| **M0** | Autonomous Workflow Setup | ✅ complete | `auto-claude` |
| **M1** | Production Hardening (Pre-Kickoff) | 🔄 active | `auto-claude` |
| **M2** | Communication & Safety Layer | ⏳ pending | `auto-claude` |
| **M3** | Repo Hygiene & Janitor Mode | ⏳ pending (concurrent) | `auto-claude` |
| **M4** | Web App | ⏳ pending | mixed |
| **M5** | Strategy Operations | ⏳ pending | mixed |

---

## M0 — Autonomous Workflow Setup ✅

**Goal**: Foundational protocol files so every subsequent sprint can operate autonomously.

### M0.S0 — Workflow Bootstrap ✅
- C1: `CLAUDE.md` master protocol
- C2: `docs/sprint-roadmap.md` (this file)
- C3: `comms/` directory + JSON schemas + `sprint_state.json`
- C4: `.github/workflows/ci.yml`
- C5: `docs/SPRINT_HARDENING_PROMPT.md` (next sprint's task spec)

---

## M1 — Production Hardening (Pre-Kickoff) 🔄

**Goal**: Stabilize the live system **before** building any new infrastructure on top of it. Three known issues have surfaced that must be resolved before Sprint M2.S1 begins:

1. **Live/dry-run mode confusion** — default is supposed to be live, but errors keep surfacing about mode. System must default to live, and if live trading is disabled, Ben must be pinged immediately on bot startup.
2. **VWAP order execution failure** — VWAP strategy generates signals but Bybit account isn't executing orders. This is a production bug, not a future feature.
3. **Architectural drift** — modules have hidden coupling, transparency gaps, and inconsistent error reporting.

**Spec**: `docs/SPRINT_HARDENING_PROMPT.md`

### M1.S1 — Infrastructure Audit & Stabilization (`auto-claude`)

**Tier**: Investigation is Tier 1. Any fix touching `src/runtime/orders.py` or `src/runtime/pipeline.py` is Tier 2 and pings Ben.

**Checkpoints:**

- **C1 — Live/Dry-Run Config Audit**
  - Find every place the live/dry-run flag is read
  - Confirm default is `live` everywhere
  - Add startup validation: if dry-run mode is on, log a loud warning AND write `comms/pending_input.json` with `type: "mode_alert"` so Ben gets a Telegram ping
  - Test: bot started in dry-run mode produces ping; bot started in live mode does not

- **C2 — VWAP Order Execution Debug**
  - Trace VWAP signal → order package → Bybit submission path
  - Identify where the disconnect is (signal not reaching `orders.py`? Order rejected silently? Strategy not registered?)
  - Document root cause in `docs/vwap-debug-findings.md`
  - Apply fix (Tier 2 ping required if it touches `orders.py` or `pipeline.py`)
  - Test: VWAP signal in staging produces an order submission attempt logged with full request/response

- **C3 — Modular Independence Audit**
  - Map import graph of `src/runtime/`, `strategies/`, `src/core/`, `src/exchange/`
  - Identify circular imports and hidden coupling
  - Document findings in `docs/architecture-audit.md` — no fixes yet, just inventory
  - Each cross-module dependency must be either: (a) justified, or (b) flagged for refactor in M3

- **C4 — Transparency Layer**
  - Audit current logging — every order attempt, signal generation, mode switch, and risk-cap check must produce a structured log line
  - Add missing log lines (Tier 1 in `notify.py`/`signal_writer.py`; Tier 2 in `orders.py`)
  - Add `/diagnose` Telegram command stub (full implementation in M2.S1) that prints last 20 structured events
  - Test: dry-run cycle produces a complete event trace covering signal → decision → order attempt → result

- **C5 — Stability Smoke Test**
  - 4-hour live monitoring run (no order changes) confirming:
    - No unexpected mode switches
    - No signal-without-order-attempt events
    - No silent exceptions in logs
  - Result documented in `docs/m1-stabilization-report.md`

**Acceptance**: All 5 checkpoints green. Bot has been running for 4 hours stable. VWAP fix verified by signal → order trace.

---

## M2 — Communication & Safety Layer ⏳

**Goal**: Operationalize the autonomous ping loop and add the risk caps that make live trading safe.

### M2.S1 — Comms Infrastructure (`auto-claude`)

**Tier**: All Tier 1. Self-merge once tests pass.

**Checkpoints:**
- **C1**: `comms/` polling task in `telegram_query_bot.py` (30s cycle)
- **C2**: Inline-button handler writes `input_response.json` + git push
- **C3**: `/test [strategy]` command writes `comms/test_request.json`
- **C4**: `/new-session [sprint_id]` command updates `sprint_state.json`
- **C5**: `/diagnose` command (full implementation, builds on M1.S1.C4 stub)
- **C6**: Unit tests for all handlers
- **C7**: PM sprint start ping handler (sends session link at scheduled time)
- **C8**: Sprint completion ping handler

**Spec**: `docs/SPRINT_M2_S1_PROMPT.md` (created at end of M1)

### M2.S2 — Risk Caps + Kill Switch (`auto-claude`)

**Tier**: Tier 2 ping required (touches `orders.py`).

**Checkpoints:**
- **C1**: `MAX_POSITION_USD`, `MAX_DAILY_LOSS_USD`, `MAX_OPEN_POSITIONS` in `config.py`
- **C2**: Hard-raise enforcement in `src/runtime/orders.py` (not soft warning)
- **C3**: `/halt` and `/resume` Telegram commands
- **C4**: `/status` enhanced with current exposure vs caps
- **C5**: Unit tests proving cap refusal
- **C6**: Dry-run smoke test
- **C7**: Tier 2 ping with merge/hold buttons → wait for response → merge

---

## M3 — Repo Hygiene & Janitor Mode ⏳ (concurrent)

**Goal**: Kill duplicate files, dead services, orphan configs. M3 runs in **parallel** with other milestones once M2.S1 is live.

### M3.S1 — Janitor Mode Bootstrap (`auto-claude`)

**Tier**: All Tier 1. Each fix is a separate small PR.

**Checkpoints:**
- **C1**: Resolve `src/backtester.py` vs `src/backtest/` duplicate
- **C2**: Resolve `telegramquerybot.py` vs `telegram_query_bot.py` duplicate
- **C3**: Delete `.bak` files after confirming originals are canonical
- **C4**: Audit `deploy/` for stale systemd units
- **C5**: Audit `scripts/` for callerless scripts
- **C6**: Resolve `src/strategies_manager.py` (stub or implement)
- **C7**: Add missing `__init__.py` files
- **C8**: Address refactor items flagged in M1.S1.C3 architecture audit
- **C9**: `/janitor` Telegram command — Ben can trigger janitor pass on demand

---

## M4 — Web App ⏳

**Goal**: Visual dashboard for P&L, positions, signals, and bot status. Now broken into design → backend → UI to surface design decisions before code is written.

### M4.S1 — Design & Architecture (`pm-sprint`)

**Tier**: No code changes. Design doc only.

**Checkpoints:**
- **C1**: Wireframes for dashboard, positions view, P&L view, signal log, strategy comparison
- **C2**: API contract spec (endpoints, payloads, auth model) in `docs/webapp-api-spec.md`
- **C3**: Tech stack decision (FastAPI vs Flask, React vs HTMX vs vanilla) documented
- **C4**: Data model — what views/tables are needed beyond the existing trade journal
- **C5**: Hosting & deployment plan (same VM? separate? Docker compose?)
- **C6**: PM review session — Ben approves design before M4.S2 starts

### M4.S2 — Backend Core (`auto-claude`)

**Tier**: All Tier 1 (new service, no live trading path).

**Checkpoints:**
- **C1**: FastAPI/Flask scaffold in `src/webapp/`
- **C2**: Endpoints from API spec implemented
- **C3**: Auth middleware
- **C4**: Health check endpoint
- **C5**: Docker Compose service definition
- **C6**: Endpoint tests

### M4.S3 — UI (`auto-claude`)

**Tier**: All Tier 1.

**Checkpoints:**
- **C1**: Dashboard view (per wireframe)
- **C2**: Positions table with live updates
- **C3**: P&L chart (daily + cumulative)
- **C4**: Signal log
- **C5**: Bot status indicator (running/halted/dry-run)
- **C6**: Strategy comparison view
- **C7**: Built and served from backend

---

## M5 — Strategy Operations ⏳

**Goal**: Repeatable strategy testing and approval pipeline.

### M5.S1 — Strategy Testing Pipeline (`auto-claude`)

**Tier**: Test runner is Tier 1. Live promotion is Tier 2 (with evidence) or Tier 3 (parameter changes).

**Checkpoints:**
- **C1**: Async test runner reading `comms/test_request.json`
- **C2**: Backtest output → `comms/test_results.json`
- **C3**: 2-cycle staging gate before any live promotion
- **C4**: Per-strategy position sizing
- **C5**: Multi-strategy test coverage

### M5.S2 — ICT Strategy Review (`pm-sprint`)

**Tier**: All Tier 3 — no merges without explicit approval.

**Checkpoints:**
- **C1**: Pre-load FVG/OB validation results (50+ trades)
- **C2**: Pre-load dry-run vs live benchmark
- **C3**: Pre-load risk cap headroom report
- **C4**: Pre-load parameter sensitivity analysis
- **C5**: PM session — Ben decides: promote, extend dry-run, or kill

---

## Concurrency & Sequencing Rules

1. **M1 must complete before M2.S1** — comms infrastructure depends on a stable bot.
2. **M2.S2 must complete before M5** — no strategy goes live without risk caps.
3. **M3 runs concurrently** with M2/M4/M5 once M2.S1 is live (Janitor needs the ping system).
4. **M4.S1 is a `pm-sprint`** — Ben must approve design before M4.S2/S3 build it.
5. **M5.S2 is a `pm-sprint`** — final live-promotion gate.

---

## Current Pointer

See `comms/sprint_state.json` — currently `M1.S1` (Infrastructure Audit & Stabilization).
