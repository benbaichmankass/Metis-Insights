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

## North Star (operator vision, 2026-05-23)

The end state is **not** "make vwap work." It is a **portfolio of 3–5
complementary strategies, each with its own durable, fee-survivable
edge**, running on **both currencies (BTCUSDT and MES)**, coordinated by
a **decider layer that picks the best trade** when strategies compete.

Principles for getting there:
- **Edge-first.** A strategy earns a roster slot only if it shows a
  real net-of-fee edge in backtest (before exit/fee tuning). Tuning
  improves an edged strategy; it cannot manufacture edge (S4-B proved
  this for vwap).
- **Deep-dive every existing strategy** (vwap, turtle_soup, ict_scalp)
  on **both** symbols — backtest hard, tweak, and see what, if anything,
  makes each effective. Low live-trade counts are fine; backtesting is
  the instrument.
- **Be creative about NEW strategies.** Within the existing architecture
  (`StrategyInterface` → `SignalPackage` → `OrderPackage` → intent
  multiplexer), the *signal logic and trade-package construction can be
  anything* that produces an edge — momentum, breakout, carry,
  mean-reversion variants, regime-conditioned, ML-scored, multi-leg, etc.
  Aim for **complementary** edges (different market conditions) so the
  portfolio is smoother than any single strategy.
- **The decider layer** is the existing intent multiplexer / coordinator
  (priority → timestamp → name today). The vision is a smarter decider
  that chooses the best trade by edge/confidence/regime-fit — the M11
  advisory-ML hooks + regime models are the natural inputs. Designing
  this is part of the program.
- **Complementarity > individual maximization.** 3–5 strategies whose
  edges fire in different regimes beat one over-tuned strategy.
- **Models participate in backtesting (operator directive 2026-05-23).**
  The trained registry models (trade-outcome winrate, regime
  classifiers, setup-quality, etc.) must be evaluated *inside* the
  backtest, not only shadow-logged live. Build a **model-in-the-loop
  backtest**: at each signal, score the strategy's shadow feature-row
  (reuse `_build_shadow_feature_row` + `ml.shadow.factory`/`Predictor`)
  and test the model as an **entry gate** (take only trades scored above
  a threshold) and as a **decider input** (rank competing signals by
  model score). Measure net-of-fee edge with vs without the model gate —
  this both tests whether models add edge and validates the decider
  offline before any live promotion (which stays the operator-gated
  shadow→advisory step).

Everything below serves this North Star. The near-term sprints establish
which current strategies have edge (S4-B-3, S5), then the program moves
to creative new-strategy design + the decider (S6+). Going live with any
strategy (add/retire/replace/promote) remains Tier-3, operator-gated.

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

### Live-vs-repo verification flag — RESOLVED (S2/S3, 2026-05-23)

`src/units/strategies/vwap.py:224` sets `SL_STD_MULT_DEFAULT = 0.3`.
S2 confirmed **0.3 is live** (live VM SHA == main HEAD; empirical R:R
3.48 on bybit_2 = 1.0/0.3). The operator confirmed 2026-05-23 that 0.3
was proven + approved; the in-code "must approve before deploy" note
(line 223) and the stale R:R worked-example (`vwap.py:200-208`, was
`0.5σ → 1:2`) were **fixed in S3** to match the live field. No value
changed. Closed.

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

### S2 — Full strategy + symbol performance audit — Tier 1 — ✅ DONE 2026-05-23
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
- **Result:** report at `docs/audits/strategy-loss-drivers-2026-05-23.md`
  (evidence: live relays #1779/#1780/#1781). SL flag resolved — **0.3 is
  live** (R:R 3.48), with a Tier-3 governance flag surfaced for the
  operator. Dominant loss driver: **vwap overtrading → fee drag (418% of
  gross)**; thin +$11/7d gross edge buried by fees; 74% of exits are
  `reconciler_filled` stop-runs (17.9% WR); long-side bias (longs 79% of
  loss). turtle_soup/ict_scalp/MES flagged **low-N** (S2-B follow-up).
  Log: `docs/sprint-logs/S-STRAT-IMPROVE-S2-2026-05-23.md`.

### S3 — Exit-mechanism diagnosis (technical-first) — Tier 1 — ✅ DONE 2026-05-23
- **Why inserted:** operator directive 2026-05-23 — if the
  `reconciler_filled` exit dominance is a *bug* (designed exits not
  firing, everything reverting to exchange closes), fix it BEFORE
  tuning strategies.
- **Goal:** empirically classify the `reconciler_filled` closes as
  native-bracket fires (working-as-designed) vs anomalous (bug).
- **Tool:** `monitor-miss-analysis` action (#1782).
- **Result — no bug.** Of 125 reconciler closes: 36 TP_hit + 84 SL_hit
  + only 5 between_TP_SL → **96% are native Bybit SL/TP bracket fires**
  (`execute.py` submits stopLoss/takeProfit per entry; the exchange
  closes between 60s ticks; the reconciler records it). Losses are
  genuine strategy losses (stop hit 84× vs TP 36×). No faster monitor
  tape needed. One exit-geometry enhancement deferred to S5.
- **Exit criteria met:** technical-first question answered; program
  proceeds to strategy improvement.

### S4-A — Backtest net-of-fee instrumentation — Tier 1 — ✅ DONE 2026-05-23
- **Why:** S2 proved the strategy is gross-positive / net-negative
  (fees 418% of gross), so the gross-R backtest output is misleading.
- **Done:** `run_backtest_vwap.py` now reports per-trade `net_pnl_r`
  (gross − round-trip fee) + aggregate `net_total_r` (+ long/short),
  `net_win_rate_pct`, `total_fee_r`, `mean_trades_per_window`,
  `net_positive_windows`, per-regime net — via a `--fee-bps-roundtrip`
  arg (default 7.5). Additive; `--fee-bps-roundtrip 0` reproduces gross.
  102 tests pass. Local single-window preview confirmed fees dwarf gross
  and selectivity reduces drag (low-confidence, not regime-diverse). Log:
  `docs/sprint-logs/S-STRAT-IMPROVE-S4-A-2026-05-23.md`.
- **Blocker for S4-B:** the `vwap-backtest-sweep` relay runs from the
  VM's `main` checkout, so the net-of-fee output appears there only after
  this branch merges to `main` (or S4-B runs via `trainer-vm-diag` with a
  branch checkout). Operator decision point.

### S4-B — Net-of-fee selectivity sweeps — Tier 1 — ✅ DONE 2026-05-23
- **Ran:** threshold sweep (#1784, 8×14d/365d) + param sweep entry×SL
  (#1785, 12 configs × 3×14d/365d), net-of-fee.
- **Verdict: vwap has NO inherent edge — tuning cannot fix it.** 0/8 and
  0/36 windows net-positive; best config −41.7R/14d. Selectivity +
  fee-efficiency reduce the bleed but never reach positive; gross is
  ~flat-to-negative over a regime-diverse year. Full evidence + caveats:
  `docs/audits/vwap-viability-verdict-2026-05-23.md`. Confirms the
  operator's intuition ("not robust even in theory"). Log:
  `docs/sprint-logs/S-STRAT-IMPROVE-S4-B-2026-05-23.md`.

> **PROGRAM PIVOT (operator-directed 2026-05-23):** from *tuning vwap* to
> **edge-first** — establish which (if any) strategy has a durable,
> fee-survivable inherent edge, and what a robust base strategy looks
> like, before proposing any live direction. The sprints below are
> re-scoped accordingly.

### S4-B-3 — vwap HTF/regime edge filter — Tier 1 — NEXT (last vwap lever)
- **Goal:** the one untested vwap lever — does an HTF/regime trend
  filter create *gross* edge (regime-robust, not a static short-bias)?
  Run `vwap-backtest-sweep bt_mode: compare` net-of-fee. Even a positive
  result must clear the fee hurdle. If it doesn't lift gross, vwap is
  done as a standalone edge.

### S5 — Cross-strategy inherent-edge audit (both currencies + models) — Tier 1
- **Goal:** the full strategy-improvement treatment for **every** current
  strategy on **both** symbols. Do `turtle_soup` and `ict_scalp` (and
  vwap, for completeness) have a durable net-of-fee edge — on **BTCUSDT
  AND MES**, regime-split, long/short? Backtest hard + tweak each.
- **Prerequisites / sub-tasks:**
  - ict_scalp backtest instrumented (net-of-fee, 3604b86); **turtle_soup
    harness built** (`scripts/backtest_turtle_soup.py`, net-of-fee +
    long/short, single-TP1 exit to isolate setup edge; needs fresh
    365-day 15m data to produce a real read).
  - **MES backtesting** needs MES data (IB delayed CME bars) + the
    backtests parameterized off `config/instruments.yaml` (tick size,
    fee schedule — CME/IB fees differ from Bybit's 7.5 bps) instead of
    hardcoded BTCUSDT/Bybit.
  - **Model-in-the-loop** (operator directive): wire registry models as
    entry gate + decider input into the harness; report net-of-fee with
    vs without the model gate.
  - Run on the trainer VM (uncapped) — needs `git pull` + a pandas venv
    (1-core, slow but no 15-min cap).
- **Deliverable:** per-strategy × per-symbol inherent-edge table (gross +
  net, long/short, by-regime, model-gated vs raw), evidence-cited.
- **Read + regime confirmation (2026-05-23, BTCUSDT, net-of-fee, 2023/24/25):**
  **ict_scalp has a DURABLE gross edge** (positive every year, +29..+46R
  gross; net +2.1/−18.9/+4.2 — 2024 net-negative is fees/over-trading,
  not edge) → **keeper**. **turtle_soup did NOT hold** (gross negative in
  2023 & 2024; the +11.4R in 2025 was a regime artifact) → rework/
  exit-investigate. vwap: no edge. **Only 1 of 3 has durable edge →
  creative new-strategy workstream is central.** Audit:
  `docs/audits/strategy-inherent-edge-2026-05-23.md`.
  Remaining: harness speedup (per-bar too slow on 1-core for full 3yr),
  fee-efficiency sweeps for ict_scalp, turtle_soup exit-rescue, MES,
  model-in-loop, **new-strategy R&D**.

### S6 — Strategy-edge assessment & recommendation — Tier 1 (Tier 3 to ship)
- **Goal:** synthesize S4-B/S5 into a recommendation: which current
  strategy (if any) has a fee-survivable edge; what a robust base
  strategy looks like (edge before tuning); whether to keep/tune/retire/
  replace each. Package any live recommendation (incl. retiring vwap or
  cutting its frequency) as a **Tier-3 draft PR + approval request** —
  retiring/replacing a live strategy stops at the operator gate.
- **Regime constraint (operator directive 2026-05-23):** the live
  long/short gap reflects a **down-market regime**, not a permanent
  edge. Do **NOT** introduce a static short-bias / long-suppression.
  Any direction handling must be **regime-robust** (symmetric
  counter-trend gating, HTF-aware) and validated across **up AND down**
  market windows.
- **Method:** reuse the 24-window framework (`vwap-backtest-sweep`,
  key `bt_mode:`) and trainer-VM sweeps; measure **net-of-fee**
  expectancy + trade-count reduction; require n≥3 windows positive on
  both legs (long AND short) before proposing anything.
- **Deliverable:** ranked, backtested selectivity proposals. Any live
  change is a **Tier-3 draft PR + approval request** — never merged in
  this sprint.

> **Superseded by the edge-first pivot (2026-05-23).** The original
> S5 (SL/TP & exit-geometry research), S6 (validate winners across
> symbols), and S7 (package & rollout) assumed a tunable-but-edged
> strategy. S4-B showed vwap has no inherent edge, so exit-geometry
> tuning is moot until an edged strategy exists. Those scopes are folded
> forward: exit-geometry research applies **per strategy that S5 finds
> has an edge**; cross-symbol/regime validation + the Tier-3 packaging &
> staged rollout (draft PR → approval → `pull-and-deploy` →
> `restart-bot-service` → verify live → soak) become the back half of
> **S6** once a recommendation exists.

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

## Handoff (updated 2026-05-23 — edge-first pivot)

S0–S4-B done. **Headline: vwap has no inherent edge** (S4-B verdict:
0/8 + 0/36 windows net-positive across selectivity + entry×SL fee
sweeps). Program pivoted from tuning-vwap to **edge-first**: S4-B-3
(last vwap lever — HTF/regime edge filter), then **S5** (does
turtle_soup / ict_scalp have a durable net-of-fee edge on fresh
365-day data — ict_scalp instrumented; turtle_soup needs a harness;
run on the 1-core trainer VM, uncapped), then **S6** (strategy-edge
assessment + recommendation: keep/tune/retire/replace, Tier-3 to ship).
Trainer prerequisite: `git pull` + a pandas venv. No live change without
operator sign-off (retiring/replacing a live strategy is Tier-3).

---

## Superseded handoff (pre-pivot, kept for history)

S0 (architecture), S1 (comms path), S2 (performance audit), and S3
(exit-mechanism diagnosis, technical-first) are done. The evidence base
— `docs/audits/strategy-loss-drivers-2026-05-23.md` — names **vwap
overtrading → fee drag** as the dominant, real-money loss driver. S3
**cleared the technical-first check**: the `reconciler_filled` exits are
96% native-bracket fires, working as designed — losses are genuine
strategy losses, not a bug. The SL_STD_MULT governance flag is
**resolved** (operator confirmed 0.3 approved/live 2026-05-23; stale
comments fixed in `vwap.py`).

**Next sprint: S4** — selectivity / rule-tightening backtests
(long/short split, session gating, regime-robust counter-trend gate),
measured **net-of-fee**, because cutting trade count is the highest-ROI,
lowest-risk lever against fee drag. **Hard constraint (operator):** no
static short-bias — direction handling must be regime-robust and
validated across up AND down windows. One item still carries forward:
**S2-B** (journal-based per-strategy pull for low-N
turtle_soup/ict_scalp/MES). All analysis sprints are autonomous
(Tier 1); every live change is Tier 3 and stops at the approval gate.
