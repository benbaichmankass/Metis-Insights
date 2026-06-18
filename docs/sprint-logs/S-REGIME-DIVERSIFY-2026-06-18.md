# Sprint Log: S-REGIME-DIVERSIFY-2026-06-18

## Date Range
2026-06-18 (single session).

## Objective
Reconcile the strategy/ML direction onto a realistic, overall-PnL-positive
path: (1) investigate regime-conditional strategy weighting ("predict when a
strategy is good") under the variation-matrix + holdout discipline; (2) bank
the diversification win if weighting doesn't beat it; (3) stand up a live
paper-book watch; plus two operator asks — confirm the prop-account no-trades
cause, and assess OANDA gold readiness.

## Tier
Tier-1 (research tooling, docs, skill) for the bulk; one Tier-2 fix carried on
the branch (reconciler re-adopt guard, draft PR #3953); no Tier-3 config/live
change made (OANDA correctly left OFF).

## Starting Context
Prior sessions had expanded the strategy book for paper decision-frequency (the
10-cell alt book on `bybit_1`) and parked an idea: a regime layer that predicts
when a strategy will be good. The original `btc-regime` classifier predates the
variation-matrix discipline that caught repeated overfits, so the operator
asked that any regime work be tested as a FULL matrix of variations + holdout.

## Repo State Checked
`config/accounts.yaml` (account→strategy routing, modes), `config/strategies.yaml`
(cell enablement/execution), ROADMAP.md (regime-router PERF-20260601-006/007),
`docs/research/m15-phase0-results-2026-06-10.md` + `docs/runbooks/oanda-integration.md`
(OANDA/gold history), `health-/performance-review-backlog.json` (BL-20260611-007).

## Files and Systems Inspected
- Trainer VM via `vm-driver` (cached 10-cell regime map at `results/m15_regime_map`).
- Live VM via the `vm-diag-request` issue relay: `/api/diag/audit_query`
  (prop signals), `/api/diag/journal?table=order_packages`, `/api/bot/config`,
  `/api/bot/candles?symbol=XAUUSD`.
- Prop notify path: `src/prop/breakout_notify.py`, `breakout_executor.py`,
  `src/units/accounts/execute.py` (emit_prop_ticket wiring).

## Work Completed
- **Regime-weighting investigation (Tier-1):** `scripts/ops/regime_performance_map.py`
  (Step-1 ADX×vol edge map), `scripts/ops/regime_weight_overlay.py` (Step-2
  train/holdout weight MATRIX + a `--group cell|family` knob),
  `scripts/ops/portfolio_robustness.py`. Ran on the trainer over the 10 cells.
  **Finding: regime weighting does NOT beat diversification OOS** — naive
  per-cell overfits (degrade 0.30–0.76); family-level collapses the overfit
  (degrade 0.08–0.31) but is net-R-neutral / risk-adjusted-better only.
- **Diversification banked:** the un-weighted 10-cell book is robustly +OOS
  (+409.8R / Sharpe 4.03; all 5 holdout cutoffs +, leave-one-cell-out +, both
  families independently +, fee-robust to +0.13R/trade, bootstrap P(+)=0.984).
  One blemish: 2026-YTD flat in backtest.
- **Paper-book tracker:** `config/research/diversified_paper_book.yaml` (10-cell
  cohort), `scripts/ops/paper_book_tracker.py` + `docs/research/paper-book-tracker.jsonl`,
  wired into `/performance-review` (new step + section + output field) to watch
  the live paper trajectory for decay-vs-noise.
- **Reconciler re-adopt flap guard (Tier-2):** `RECONCILER_READOPT_GUARD_SECONDS`
  in `src/runtime/order_monitor.py` + tests (BL-20260618-RECONCILE-DUP); draft PR #3953.
- **doc-freshness skill** gained a decision-landing completeness check
  (roadmap + sprint log + backlog), addressing "stuff flows through the cracks".

## Validation Performed
- All new tools: `ast.parse` + `ruff check` clean; smoke-tested on synthetic +
  the real trainer data (vm-driver `automation/results/{regime-family,portfolio-robust}.txt`).
- Reconciler: 24 new + 145 existing reverse-reconciler tests pass.
- Cohort↔wiring consistency: all 10 cells `enabled`+`execution:live` on `bybit_1`,
  cohort yaml ⊆ live routing.
- Prop: `/api/diag/audit_query?strategy=trend_donchian_sol` = 107 `*_eval`
  events since 2026-06-01, all `side: none`.
- OANDA: `/api/bot/candles?symbol=XAUUSD` returns live bars (auth OK), but
  config + BL-20260611-007 confirm order placement is venue-blocked.

## Documentation Updated
- `docs/research/regime-map-step1-results-2026-06-18.md` (Step 1/2/3 results).
- `docs/research/regime-conditional-strategy-weighting-DESIGN.md` (brought onto branch).
- ROADMAP.md: PERF-20260601-007-REGIME-WEIGHTING (ON HOLD) + S-DIVERSIFY-BANK rows + header.
- `.claude/skills/performance-review/SKILL.md` + response template (paper-book tracker).
- `.claude/skills/doc-freshness/SKILL.md` (decision-landing completeness step).

## Contradictions or Drift Found
- Roadmap regime link was dangling on-branch (design doc on its own PR branch) —
  fixed by cherry-picking the design doc onto this branch.
- Session env `DIAG_BASE_URL` still points at the terminated micro
  `158.178.210.252` (egress firewalled anyway) — environment config, not repo;
  noted for the operator (use the issue relay).

## Risks and Follow-Ups
- **Prop no-trades = signal drought, not a notify bug.** The path
  (execute.py → emit_prop_ticket → emit_prop_signal → FCM+Telegram) is wired;
  `trend_donchian_sol` has evaluated 107× since 06-01, all non-actionable.
  Optional belt-and-suspenders: fire a test `prop_signal` to confirm delivery.
- **OANDA stays OFF (correct).** OANDA US can't trade XAU_USD (BL-20260611-007);
  the only strong OANDA cell is gold (already covered live via IBKR MGC +
  Alpaca GLD); the tradeable OANDA-US FX cells (EUR/GBP) fail the robustness bar.
  Forward path: validate an OANDA-US-tradeable instrument to the gate, else
  leave OANDA retired.
- **2026-YTD flat** on the banked book — the paper-book tracker is the watch.

## Deferred Items
- Regime classifier (`btc-regime-*`) re-validation under the matrix discipline +
  family-level reductive soft-weight as a drawdown-reducer — **ON HOLD** for a
  dedicated session (PERF-20260601-007).
- Cross-asset diversification (futures/equities/gold robustness pass) — the next
  bankable expansion (Direction 1; new-session prompt drafted).
- Reconciler fix #3953 — Tier-2 merge + deploy pending operator OK.

## Next Recommended Sprint
Cross-asset diversification robustness pass (Direction 1): run the non-crypto
paper books (ib_paper futures, alpaca_paper equities) through
`portfolio_robustness.py` + build the combined cross-asset book; in parallel,
the recombination sweep (Direction 2). Regime classifier stays on hold.

## Wrap-Up Check
- [x] Tools validated (ruff + smoke + real-data runs).
- [x] Material decisions recorded in ROADMAP + this sprint log + backlogs.
- [x] No Tier-3 config/live change made without approval (OANDA left OFF).
- [x] `/doc-freshness` run at session close.
