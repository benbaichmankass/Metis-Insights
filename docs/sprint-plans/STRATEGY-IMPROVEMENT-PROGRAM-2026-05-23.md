# Strategy Improvement Program — Milestone Plan (2026-05-23)

> **Status:** Active program plan. Created in **S-STRAT-IMPROVE-S0**.
> **Authority order:** `docs/CLAUDE-RULES-CANONICAL.md` →
> `docs/ARCHITECTURE-CANONICAL.md` → `ROADMAP.md` → this plan →
> the active sprint log under `docs/sprint-logs/`.
> **Maps onto existing roadmap:** M7 (Strategy review gate) + M8
> (Strategy tuning), plus the weekly **Strategy Improvement Review**
> recurring session (`ROADMAP.md` § Standing / Recurring Sessions).
> This plan does not fork the roadmap; it is the execution detail for
> those milestones.

---

## Mission

Improve strategy profitability while reducing bad trades, **without
breaking the live system**. Favor evidence over intuition. Cut bad
trades without cutting good ones. Every live-affecting change goes
through the decision tiers below and lands as a small, reviewable,
backtested PR.

This is a multi-sprint, multi-session program. Each sprint has a clean
start, a clear goal, and a clean handoff. Work does not get rushed into
one pass.

---

## Current state (verified 2026-05-23, S0)

### What is live

| Account | Exchange | Money | Mode | Strategies | Symbol |
|---|---|---|---|---|---|
| `bybit_1` | Bybit **demo** | paper | live | turtle_soup, vwap, ict_scalp_5m | BTCUSDT |
| `bybit_2` | Bybit | **real** | live | **vwap only** | BTCUSDT |
| `ib_paper` | IB paper | paper | live | turtle_soup, vwap, ict_scalp_5m | MES |
| `ib_live` | IB | real | dry_run (inert) | — | MES |
| `prop_velotrade_1` | Velotrade | — | dry_run (not wired) | — | — |

Three live strategies, two live symbols (BTCUSDT on Bybit, MES on IB
paper since 2026-05-22). The only real-money exposure is `bybit_2`
running **vwap only** on BTCUSDT linear perps at 3× leverage.

### The known problem (the program's first target)

The real-money `bybit_2` vwap account is losing:

- ~**18% win rate** over 7d (confirmed real, not an accounting
  artifact — `scripts/ops/strategy_performance_audit.py` docstring,
  issue #1432, 2026-05-18).
- Severe **long/short asymmetry**: ~10.9% long WR vs ~40.9% short WR
  (operator Telegram audit 2026-05-18; `S-VWAP-POLICY-INVESTIGATION-2026-05-19`).
- Regime-aware **policy gating did not fix it** — two properly-powered
  backtests (24 × 14d windows ≈ 11.5 months) landed at +0.13 R and
  +0.36 R, i.e. roughly flat. Policy tuning moves the needle ±5 R/yr,
  not the magnitude needed to explain the live long-side bleed.
- Conclusion from prior sprint: the problem is **structural strategy
  parameters / exit logic**, not the regime gate. The recommended next
  step was `S-VWAP-STRATEGY-PARAMS` — investigate `ENTRY_STD_THRESHOLD`,
  `SL_STD_MULT_DEFAULT`, the VWAP anchor/window, and the time-decay /
  `vwap_cross` exit conditions, with a **long-vs-short split** added to
  the backtest aggregate.

### Live-vs-repo verification flag (carry into S2)

`src/units/strategies/vwap.py:224` sets `SL_STD_MULT_DEFAULT = 0.3`
with an explicit `# TIER-3: Ben must approve before this value is
deployed to the live bot` note (line 223), justified by the
2026-05-19 sweep (#1569: ENTRY=1.0/SL=0.3 ranked #1, +4.88 mean R).
But the earlier S-TRAINER-BT-1 deploy (2026-05-17) confirmed the live
VM running `sl_std_mult: 0.5`. **It is unknown from the repo alone
whether the live VM currently runs 0.3 or 0.5.** The R:R worked-example
comment at `vwap.py:200-208` still cites `0.5σ → 1:2`, which is stale
vs the 0.3 field (actual R:R 3.33:1, per line 221). The whole vwap loss
analysis depends on knowing the *actual live* SL multiplier — so S2's
first action is to pull live state via the diag relay and reconcile.

---

## Canonical paths (the map this program operates on)

| Concern | Canonical path |
|---|---|
| Bot entrypoint | `src/main.py` → `src/runtime/pipeline.py` |
| Strategy modules | `src/units/strategies/{turtle_soup,vwap,ict_scalp}.py` |
| Strategy registry | `src/strategy_registry.py` |
| Strategy config (params) | `config/strategies.yaml` (**Tier 3**) |
| Account config (risk caps, mode) | `config/accounts.yaml` (**Tier 3**; `mode:` only via `set-account-mode`) |
| Instruments | `config/instruments.yaml` |
| Position sizing | `src/units/accounts/risk.py::position_size` (**Tier 3**) |
| Risk gating | `risk.py::evaluate/approve`, `prop_risk.py`, `src/runtime/risk_counters.py` |
| Order construction | `src/runtime/orders.py::safe_place_order` |
| Multi-strategy intent layer | `src/runtime/{intents,intent_multiplexer,positions}.py`, `strategy_signal_builders.py` |
| Backtest harness | `src/backtest/{backtester,run_backtest,run_backtest_vwap}.py`, `scripts/backtest_ict_scalp.py` |
| Deploy / sync | merge to `main` → `ict-git-sync.timer` (5 min) → services reload |
| Comms (Claude↔operator) | `comms/requests/`, `comms/schema/`, `src/bot/comms_handler.py`, `scripts/comms_ask.py` |

---

## Tooling already in place (reuse, do not reinvent)

The repo already ships the infrastructure this program needs. S0
confirmed each path exists.

| Need | Existing tool | How to run |
|---|---|---|
| Live per-strategy loss breakdown (WR, expectancy, R:R, exit-reason, hour, direction, fees, slippage) | `scripts/ops/strategy_performance_audit.py` | `strategy-performance-audit` operator action (`--account X --days N`) |
| Bybit ground-truth account audit | `scripts/ops/bybit_account_audit.py` | `bybit-account-audit` operator action |
| Closed-PnL inspection | `scripts/ops/inspect_closed_pnl.py` | `inspect-closed-pnl` operator action |
| VWAP param/policy backtest sweeps | `src/backtest/run_backtest_vwap.py` (`--compare`/`--threshold-sweep`/`--adaptive`) | `vwap-backtest-sweep` operator action (key `bt_mode:`, **not** `mode:`) |
| ICT scalp backtest | `scripts/backtest_ict_scalp.py` | local / trainer VM |
| Generic backtest sweep on trainer VM | `scripts/ops/run_backtest_sweep.sh` | `trainer-vm-diag` relay |
| Live VM read-only state | `/api/diag/*` | `vm-diag-snapshot` relay (label `vm-diag-request`) |
| Trainer VM arbitrary bash (backtests, datasets) | — | `trainer-vm-diag` relay (label `trainer-vm-diag-request`) |
| Deploy a merged change to live | `scripts/ops/pull_and_deploy.sh` | `pull-and-deploy` operator action (Tier-2 ack) |
| Restart trader after config change | `scripts/ops/restart_bot.sh` | `restart-bot-service` operator action (Tier-2 ack) |

All of these are autonomous-read or operator-acked per
`docs/claude/operator-actions.md`. **No operator toil, no manual SSH.**

---

## Decision tiers (how every change is gated)

Restated from `CLAUDE-RULES-CANONICAL.md` for this program's scope:

- **Tier 1 (autonomous):** backtests, analysis, audits, docs, tests,
  backtest-only tooling, the long/short split in the backtest
  aggregate, evidence artifacts. Most of this program's *analysis* is
  Tier 1.
- **Tier 2 (review-before-merge):** order-path plumbing, deploy/timer
  changes, anything touching runtime flow but not strategy/risk
  meaning. Dispatching an existing `pull-and-deploy` / `restart` is
  Tier-2 (single operator ack).
- **Tier 3 (explicit operator approval):** ANY change to
  `config/strategies.yaml` params, `config/accounts.yaml` risk caps,
  strategy entry/exit logic in `src/units/strategies/`, sizing in
  `risk.py`, signal thresholds, SL/TP behavior, or promotion. **Every
  recommended live change in this program is Tier 3** and ships as a
  draft PR + a structured approval request, never auto-merged.

---

## Communication flow (repo-driven, isolated from trading logic)

When a decision needs Ben:

1. Claude writes a structured request to `comms/requests/REQ-*.json`
   (schema: `comms/schema/request.schema.json`; helper:
   `scripts/comms_ask.py`).
2. The VM syncs (`ict-git-sync.timer`); the Telegram bot
   (`src/bot/comms_handler.py`) sends it.
3. Ben answers in Telegram; the bot writes the answer back and commits.
4. Claude resumes from the updated repo state.

For tightly-scoped Tier-3 approvals during an active session, an
in-chat ack is also sufficient (per the operator-actions contract). The
comms artifact path is for asynchronous, auditable decisions and for
packaging the final approval bundle (Sprint 6).

The comms system must stay isolated from `src/runtime/` and
`src/units/` (no trading code imports `src.comms`) — an architecture
invariant, not just a convention.

**Verified end-to-end in S1** (2026-05-23): 163 comms tests pass; a
schema-valid request round-trips pending → sent → answered. Concrete
command to author a Tier-3 approval request (the bot delivers it on its
next poll; the operator answers in Telegram and the answer is written
back to the artifact):

```bash
python scripts/comms_ask.py \
    --topic "Approve Tier-3 vwap SL change?" --slug vwapsl \
    --context "Backtest evidence: <link to S-STRAT-IMPROVE-S4 artifact>." \
    --question approve --type yes_no \
        --prompt "Deploy SL_STD_MULT=<x> to bybit_2 live?" \
    --expires-in 48h --commit
```

---

## Sprint roadmap

Sprint ids: `S-STRAT-IMPROVE-S0` … `S-STRAT-IMPROVE-S6`. Any sprint may
split into sub-sprints (`-A`, `-B`, …) if larger than expected. Each
sprint produces a sprint log under `docs/sprint-logs/`.

### S0 — Kickoff & architecture (this sprint) — Tier 1
- **Goal:** map the repo, confirm canonical paths, confirm live wiring,
  confirm the comms path, produce this plan. **Done when** the map is
  clear and the roadmap is actionable.
- **Deliverable:** this plan + `S-STRAT-IMPROVE-S0` sprint log.
- **Exit criteria:** ✅ canonical paths confirmed; ✅ tool inventory
  confirmed; ✅ live config mapped; ✅ known problem characterized; ✅
  drift findings recorded.

### S1 — Confirm the communication path — Tier 1 — ✅ DONE 2026-05-23
- **Goal:** verify the repo-driven Claude↔operator request/response
  flow end-to-end so later approval-gated sprints have a working
  channel. Confirm `comms_ask.py` produces a schema-valid request, the
  bot polls + sends, and the writeback round-trips. Add a small
  strategy-improvement request helper/template only if a gap exists.
- **Tier:** 1 (comms infra is isolated from trading logic).
- **Exit criteria:** a documented, verified request→answer round-trip
  (or a precise gap list if anything is broken).
- **Result:** 163 comms tests pass (126 core + 37 handler); a
  schema-valid request round-trips pending → sent → answered; isolation
  invariant holds; `GitPusher` is sandbox-safe (gated by
  `COMMS_PUSH_ENABLED=1`). No gaps — no new code needed. The Tier-3
  approval command is documented above. Log:
  `docs/sprint-logs/S-STRAT-IMPROVE-S1-2026-05-23.md`.

### S2 — Full strategy + symbol performance audit — Tier 1
- **Goal:** the evidence base. For **every strategy × every live
  symbol/account**, collect: win rate, expectancy, avg R, loss/win
  ratio, max drawdown, trade frequency, avg duration, exit mechanism in
  use, and where losses concentrate (by direction, hour, exit_reason,
  fees, slippage, regime).
- **First action (mandatory):** pull fresh live state via
  `vm-diag-snapshot` and reconcile the **SL_STD_MULT live-vs-repo flag**
  above before any analysis. Confirm the live VM SHA vs `main` HEAD.
- **Tools:** `strategy-performance-audit` action per account
  (bybit_1, bybit_2, ib_paper), `inspect-closed-pnl`,
  `bybit-account-audit`, `/api/diag/journal`. MES has only days of data
  (live 2026-05-22) — treat MES as low-N and call that out explicitly.
- **Deliverable:** a ranked **loss-driver report** (artifact under
  `experiments/` or `docs/audits/`) + per-strategy/per-symbol metrics
  table. **No code/config changes.**
- **Exit criteria:** every live strategy×symbol has a metrics row and a
  named primary loss driver, evidence-cited.

### S3 — Selectivity / rule-tightening experiments — Tier 1 analysis, Tier 3 to ship
- **Goal:** cut bad trades without cutting good ones. Backtest
  candidate filters: better confirmation, session gating, HTF
  alignment, momentum/volatility filters, symbol-specific filters.
  Add the **long-vs-short split** to the backtest aggregate (the
  explicit S-VWAP-POLICY-INVESTIGATION follow-up).
- **Method:** reuse the 24-window framework (`vwap-backtest-sweep`) and
  trainer-VM sweeps; require n≥3 windows positive on both legs before
  proposing anything.
- **Deliverable:** ranked, backtested selectivity proposals. Any live
  change is a **Tier-3 draft PR + approval request** — never merged in
  this sprint.

### S4 — SL/TP & exit-logic research — Tier 1 analysis, Tier 3 to ship
- **Goal:** experiment with multi-tier TPs, partial exits, break-even
  moves, dynamic/ATR-based trailing, adaptive exits. (turtle_soup
  already has TP1/TP2 + partial + trail; vwap is single-target +
  vwap_cross/time-decay; ict_scalp is single TP@1.5R — uneven exit
  sophistication is itself a finding to test.)
- **Deliverable:** backtested exit-logic variants with expected metric
  impact + tradeoff, per strategy. Tier-3 draft PRs for the winners.

### S5 — Validate winners on strongest & weakest symbols — Tier 1
- **Goal:** take the best S3/S4 candidates and validate on the best- and
  worst-performing symbols/accounts to check generalization (avoid
  overfit to BTCUSDT). Compare results explicitly.
- **Deliverable:** a generalization report; promote only changes that
  hold up across symbols.

### S6 — Package & prepare rollout — Tier 3
- **Goal:** bundle the validated winners into clean, small Tier-3 PRs,
  each with: what changed, why it should help, expected metric impact,
  tradeoff, and the approval request. Document the staged rollout
  (draft PR → operator approval → merge → `pull-and-deploy` →
  `restart-bot-service` → verify live via diag relay → confirm metrics
  improve over a soak window).
- **Deliverable:** approval-ready PR bundle + rollout runbook. **Stop at
  the approval gate.**

---

## Safety constraints (non-negotiable)

- Never change strategy logic, risk caps, sizing, thresholds, SL/TP, or
  promote dry→live without explicit Tier-3 operator approval.
- Never write `config/accounts.yaml` `mode:` outside `set-account-mode`.
- Never collapse multiple risky changes into one edit — one variable per
  PR so attribution is clean.
- Always pull and reconcile **live VM state** before analyzing — repo
  `main` may differ from what's running (see the SL_STD_MULT flag).
- Backtest before proposing; require both-leg positive evidence at n≥3.
- Keep the comms system isolated from trading runtime.
- Leave a clean handoff at each sprint boundary; split rather than rush.

---

## Handoff

S0 (architecture) and S1 (comms path) are done. **Next sprint: S2** —
the full strategy + symbol performance audit, where the real
evidence-gathering begins. S2 is the linchpin: everything downstream
depends on its loss-driver ranking. S2's first action is the live-state
pull (via `vm-diag-snapshot`) + the SL_STD_MULT reconciliation from S0.
All analysis sprints are autonomous (Tier 1); every live change is
Tier 3 and stops at the approval gate.
