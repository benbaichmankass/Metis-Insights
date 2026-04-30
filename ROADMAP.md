# ICT Trading Bot — Product Roadmap

> **Last Updated:** 2026-04-30 (S-011, S-012 complete; mobile-app phases retired; replaced by **Secure Web Dashboard** track starting at S-013)
> **Maintained by:** PM (Ben) + Tech Lead (Perplexity)
> **Sprint prompt files:** `docs/sprints/sprint-NNN-prompt.md`

---

## Core Principles

1. **Lean solutions** — smallest change that delivers real value; no over-engineering.
2. **Stability first** — never build features on a shaky foundation. Hardening sprints precede feature sprints.
3. **Profitability focus** — every sprint should move the needle on live trading performance or operational safety.

---

## Workflow

- Roadmap items are discussed between PM and Tech Lead and broken into **sprints**.
- Before a sprint starts, a sprint prompt file is created at `docs/sprints/sprint-NNN-prompt.md`.
- Claude Code executes the sprint autonomously, merges PRs independently, and posts a checkpoint summary.
- After each sprint, we review, discuss, and update this file to reflect progress and re-prioritise.

---

## Roadmap Overview

### Phase 0 — Foundation & Workflow
**Goal:** Establish clean process before accelerating feature work.

| Sprint | Title | Status |
|--------|-------|--------|
| S-000 | Repo hygiene, CLAUDE.md hardening, checkpoint system | ✅ Done |

---

### Phase 1 — Core Stability
**Goal:** Make the live system robust, observable, and maintainable before scaling.

| Sprint | Title | Status |
|--------|-------|--------|
| S-001 | **Telegram Bot Hardening** — decouple bot from hardcoded config, make it dynamically reflect the live system state | ✅ Done |
| S-002 | **System Observability** — structured logging, error alerting, runtime health metrics pipeline | ✅ Done |
| S-003 | **Test Coverage & CI Hardening** — expand test suite, enforce linting/type checking in CI | ✅ Done |

---

### Phase 2 — Model Pipeline
**Goal:** Build a robust, repeatable process for training, evaluating, and iterating on models.

| Sprint | Title | Status |
|--------|-------|--------|
| S-004 | **Automated Training & Backtesting Pipeline** — scheduled Colab/HF jobs for periodic retraining, standardised metrics output | ✅ Done |
| S-005 | **Master Model / Strategy Monitor** — periodic task that reviews all strategy performance, flags underperformers, generates structured improvement report | ✅ Done |
| S-006 | **Model Registry & Versioning** — track model versions, associate them with strategy configs, enable rollback | ✅ Done |

---

### Phase 3 — Prop Trading Layer
**Goal:** Enable trading on funded/prop accounts safely with isolated risk management per account.

| Sprint | Title | Status |
|--------|-------|--------|
| S-007 | **Prop Account Manager** — upload API key, associate with a strategy, isolated execution layer | ✅ Done |
| S-008 | **Coordinator Architecture & Full Unit Rewire** — Translator/Coordinator pattern, unit rewire (strategies, accounts, dashboards, trading school), Telegram bot rewired, 178 tests across 9 PRs (#120–#128) | ✅ Done |
| S-009 | **Deferred Wiring: Colab Backtest + App Config** — `trigger_backtest()` Colab wiring, App unit config operations (carried over from S-008) | ✅ Done |
| S-010 | **Per-Account Risk Engine** — `TradingAccount`, `RiskManager`, `Integrator`, multi-account execution, Telegram risk commands, 62 tests (PRs #135–#139) | ✅ Done |
| — | **Prop Account Model** — lightweight breach-avoidance model per account (probability scoring, position adjustment) | 📋 Backlog — Deferred until prop accounts ready |

---

### Phase 3.5 — Text Milestones (Web UIs)
**Goal:** PM-iterable visibility into backtests and strategy config — no mobile app required.

| Sprint | Title | Status |
|--------|-------|--------|
| S-011 | **Backtesting UI + Strategy Config UI** — Streamlit dashboards for historical results / equity curve / strategy comparison; `/accounts` dry/live toggle; `/reload_strats` Telegram command | ✅ Done |
| S-012 | **Production Wiring Audit & Full Live Activation** — strategy roster reduced to `turtle_soup + vwap`; one strategy dir / one registry / one entrypoint; phantom services removed; live-mode hard guard; risk caps proven by tests | ✅ Done |

---

### Phase 4 — Secure Web Dashboard (replaces former Mobile App track)
**Goal:** A single responsive **website** (mobile + desktop) that gives the PM read-only visibility into the live bot and a small set of operational controls. Auth must be locked to the PM's Google account, with a Telegram-mediated whitelist flow for any other account that ever attempts a login. After first login on a device, the device stays logged in but each fresh login (or 30-min inactivity timeout) requires a passkey (WebAuthn). No native mobile app — the website is mobile-first responsive.

> **Why a website, not an app:** zero app-store overhead, instant updates, identical codebase for mobile and desktop, simpler auth (browser-native passkeys + Google OAuth via NextAuth.js), reuses the existing Oracle VM. The mobile-app sprints from the previous roadmap (former S-013/S-014/S-015) are retired in favour of this track.

#### Auth & session model (non-negotiable)

1. **Google OAuth (only sign-in method).** NextAuth.js with the Google provider. The allowlist is exactly one Google email — the PM's. Stored server-side; never exposed to the client.
2. **Whitelist alert flow.** If any account that is **not** on the allowlist attempts to sign in, the server (a) refuses the session, (b) sends a Telegram message to the PM with the requesting email + device fingerprint + IP/country, and (c) presents inline `Approve` / `Deny` buttons. Approve → email is added to the allowlist (via the existing Telegram bot's callback handler) and the requester can retry. Deny → email is added to a deny list and the requester gets a generic "request denied" page. All decisions are logged.
3. **Device-persistent sessions.** Once a device successfully completes Google OAuth + passkey, a long-lived `device_id` cookie keeps that device "trusted". The trusted-device record is stored server-side keyed by `device_id` and `user_id`.
4. **Passkey (WebAuthn) re-auth.** Required:
   - on first login from any device (passkey enrolment),
   - on every fresh login on a trusted device,
   - after **30 minutes of inactivity** on the site (idle timeout — JS heartbeat to `/api/heartbeat`).
   Passkey credentials live server-side via the WebAuthn `simplewebauthn` library; private keys never leave the device.
5. **Read-only by default.** Every endpoint that mutates state (kill-switch, dry-run toggle, strategy reload) is gated behind a fresh-passkey assertion (i.e. passkey was used in the last 5 minutes), in addition to the session.

#### Sprint sequence

| Sprint | Title | Status |
|--------|-------|--------|
| S-013 | **Sprint 8 — Website UI with Secure Auth (foundations)** — read-only FastAPI endpoints, Next.js + Tailwind responsive scaffold, NextAuth.js Google OAuth restricted to the PM's email, Telegram whitelist alert flow, WebAuthn passkey enrolment + re-auth, 30-min idle timeout, device-trust cookie, staging deploy on VM port 3001, then prod deploy on port 3000 behind Nginx + Let's Encrypt | 🔜 Next |
| S-014 | **Web Dashboard V1 (data + read views)** — connect frontend to the S-013 read-only API, render PnL curve, open positions, recent signals, system status, active strategies; recharts/plotly; mobile-first layout polished on iOS Safari + Android Chrome + desktop | 📋 Backlog |
| S-015 | **Web Dashboard V2 (operational controls)** — kill-switch UI, `/accounts` dry/live toggle from the web, `/reload_strats` from the web, audit log view; every mutating action requires fresh-passkey assertion | 📋 Backlog |
| S-016 | **Secure API Key Management (Web)** — add/rotate exchange API keys through the website into the existing SOPS-encrypted master-secrets workflow; client-side never sees plaintext keys; eliminates manual master-secrets edits | 📋 Backlog |

---

## S-008 Sprint Record

**Completed:** 2026-04-29 | **Checkpoint:** `CP-2026-04-29-58` in `CHECKPOINT_LOG.md`
**PRs merged:** #120–#128 (9 PRs) | **Tests added:** 178

| Unit | Key File | Tests |
|------|----------|-------|
| Coordinator (TRANSLATOR) | `src/core/coordinator.py` | — |
| Strategies | `src/units/strategies/{ict,vwap,breakout_confirmation,killzone}.py` | 27 |
| Accounts | `src/units/accounts/{risk,execute}.py` | 23 |
| Dashboards | `src/units/dashboards/{alerts,stats}.py` | 25 |
| Telegram Bot rewired | `src/bot/telegram_query_bot.py` | 19 |
| Trading School | `src/units/trading_school/validator.py` | 23 |
| Workflows + Docs | `docs/workflows/`, `docs/architecture.md` | — |
| Integration Tests | `tests/test_coordinator_flow.py` | 25 |

**Deferred to S-009:**
- `trigger_backtest()` Colab wiring
- App unit config operations

---

## Items Under Consideration (Not Yet Scheduled)

These are suggested additions for discussion — they are not committed sprints yet:

- **Exchange Failover / Multi-Exchange Support** — add resilience by supporting a secondary exchange in case Bybit has issues.
- **Notification Centre** — structured trade, error, and performance notifications beyond Telegram (browser push from the web dashboard).
- **Audit Log / Trade Journal** — persistent, queryable record of all trade decisions with reasoning for review.
- **Paper Trading Mode** — ability to run any strategy in simulated mode against live data without real orders, useful for validating new models.
- **Deployment Automation** — CI/CD pipeline for deploying approved code to the Oracle VM automatically after sprint merges.
- **Web Push Notifications** — browser-side push to the PM (PWA / Web Push API) as a Telegram fallback for critical alerts.

---

## Sprint File Naming Convention

Sprint prompt files live in `docs/sprints/` and follow this pattern:

```
docs/sprints/sprint-NNN-prompt.md
```

Example: `docs/sprints/sprint-001-prompt.md`

Each file contains:
- Sprint goal and scope
- Ordered task list with acceptance criteria
- Files Claude is permitted to modify
- Merge and handoff instructions

---

## Status Key

| Symbol | Meaning |
|--------|---------| 
| ✅ Done | Sprint completed and merged |
| 🔜 Next | Planned as the immediate next sprint |
| 🔄 In Progress | Currently being executed by Claude Code |
| 📋 Backlog | Defined but not yet started |
| 💬 Discussion | Idea raised, not yet broken into tasks |
