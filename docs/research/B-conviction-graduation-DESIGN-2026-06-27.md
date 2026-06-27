# Design B — graduate the conviction lens (c_reg) from soak to live (2026-06-27)

Operator-approved (2026-06-27, all-three go). Tier-3 order-path program, staged.
This doc is the spec. Pairs with `A-regime-router-ml-vol-verdict-DESIGN-2026-06-27.md`.

## Current soak (cited)

- Conviction blend (`src/runtime/conviction.py:70`):
  `conviction = news_mult × (0.45·c_strat + 0.20·c_setup + 0.20·c_wr + 0.15·c_reg)`,
  weights renormalized over inputs **actually present** (`conviction.py:113-120`).
  Inputs from `conviction_inputs.classify_head` (`:38`): `c_setup`←setup-quality,
  `c_wr`←trade-outcome, `c_reg`←regime, `c_strat`←the strategy's own calibrated conf.
- Stamped observe-only at signal time (`strategy_signal_builders.py:313-334`) → flows
  to `order_packages.meta`, **never read back into the order**.
- Would-be size (`conviction_sizing.compute_conviction_sizing:74-175`):
  `desired = conviction × (per_trade_risk_budget 0.02 × basis / (risk_dist × contract_value))`,
  × free-margin throttle, capped by margin, floored to step / whole-contract.
  `annotate_conviction_sizing` logs to `conviction_sizing.jsonl` and **always returns
  qty unchanged** (`:230`). Call site `coordinator.py:1406-1416`, right after
  `apply_advisory_downsize` (`:1382`) + `apply_news_downsize` (`:1393`).
- Arbitration soak (`conviction_arbitration.py`) — observe-only, arbitrates on raw
  `confidence` not the calibrated blend; **out of scope for B** (separate Tier-3 PR).

## Two critical findings (must read)

1. **`c_reg` is effectively stubbed.** `_default_normalize` returns `None` for
   `c_reg` with no regime calibrator (`conviction_inputs.py:74-76`; confirmed by
   `tests/runtime/test_conviction_inputs.py:65-68`). The per-bar regime scorer never
   feeds the signal-time `captured` dict. So **today's live conviction is
   `c_strat`(+`c_setup`+`c_wr` when present), with `c_reg` almost always absent.**
   B's sizing code is forward-compatible (renormalization handles a missing input),
   so **B can ship without `c_reg`** — but the regime lens then has **zero** size
   influence until A (regime promotion) + a **regime-alignment calibrator** land.
   This is the explicit A↔B dependency.
2. **The `CONVICTION_SIZING_MODE` flag was previously operator-REJECTED** (2026-06-16,
   "stranding trap"; `unified-confidence-risk-DESIGN.md:389-401`, `runtime_flags.py:109-116`,
   `tests/runtime/test_conviction_sizing.py:154-174` asserts no such flag).
   **Reconciliation (operator-blessed 2026-06-27):** the rejection was about gating
   an *observe-only annotator*. The new flag gates a **new apply path** (a genuine
   reductive/symmetric influence), exactly the role `NEWS_INFLUENCE_MODE` plays (and
   which passes `env-gate-guard` as a tri-state `*_MODE`, not `*_ENABLED`). Keep the
   flagless annotator soak untouched; add a **separate** `apply_conviction_sizing`.
   The PR must cite this reconciliation; the asserting test moves to scope the
   *annotator* and the new apply-path gating tests are added.

## Design — `apply_conviction_sizing`

- Flag `CONVICTION_SIZING_MODE ∈ {off (default), annotate, apply}` via a new
  `runtime_flags._conviction_sizing_mode` mirroring `_news_influence_mode`
  (`:93-106`); `+_conviction_sizing_accounts` (allowlist, empty=all);
  `+_conviction_sizing_direction ∈ {reductive, symmetric}`.
  - `off` → unchanged (flagless annotator soak still runs).
  - `annotate` → compute the would-be **composed** size, stamp on the real package +
    log, return unchanged.
  - `apply` → replace `sized_qty` with the conviction-driven size, bounded.
- **Reductive vs symmetric — recommend SYMMETRIC, staged.** Reductive-only is
  redundant with advisory/news and defeats the design intent (confidence *scales*
  size within the 2% budget; the real-money fill-volume problem is unblocked by a
  size that can scale **up** off the 0.001 min lot). Symmetric is hard-bounded:
  conviction=1.0 reaches exactly the 2% risk budget, never more. Ship reductive-clamped
  first (`final = min(conv, sized_qty)`) on demo, lift to symmetric via
  `CONVICTION_SIZING_DIRECTION` after the demo soak + real-money OK.
- **Composition (Option A — recommended):** conviction produces the base; advisory +
  news downsizes still apply reductively on top:
  `base = RiskManager.position_size; base = apply_conviction_sizing(base,…);
   base = apply_advisory_downsize; base = apply_news_downsize; annotate(…)`.
  Advisory (ML bearishness) + news (event opposition) stay live reducers that can
  shrink even a high-conviction trade. (Reject Option B — letting conviction re-inflate
  a news-downsized trade.)
- **Guardrails:** floor = exchange min / whole-contract; cap = 2% budget × margin cap
  (already enforced). Conviction `< NO_TRADE_FLOOR` (default 0.0 inert) → journaled
  per-trade refusal (reuse `execute.log_rejection_to_journal`), not a gate. Fail-inert
  (any error/None → unchanged). **Daily-loss clamp:** because conviction can enlarge,
  clamp the effective risk fraction to `min(0.02, account.effective_risk_pct_after_daily_loss)`
  so a daily-loss-throttled account can't be re-inflated (an open number for the operator).

## `c_reg` enabler (the bridge to A — operator approved to build)

For `c_reg` to flow, build a **regime-alignment calibrator**: map a regime head's
class-probability vector + trade direction → `P(favorable regime | direction)`, fit
per regime model_id and shipped in the `calibrators.json` artifact via the trainer
mirror; and ensure the regime head scores the package at signal time so it lands in
`captured`. `_default_normalize` deliberately refuses to invent this from a single
scalar — so this is real trainer-side ML work, not a flag flip. With it + an advisory
regime head (A), `c_reg` auto-flows through `build_conviction_inputs` with **zero B
code change**.

## Phasing (P4/P5)
G0 keep flagless soak + add a per-strategy `rank_auc>0.5` readiness pass (conviction
may be a weak signal — `c_strat` alone barely discriminates). G1 `annotate` (compose +
stamp + log on real packages, no resize) — Tier-2 inert. G2 `apply`+`reductive`+demo
(`bybit_1`). G3 `apply`+`symmetric`+demo. G4 add a real account (P4, daily-loss clamp).
G5 `c_reg`+v2 (when A + the calibrator land; no B code change). Each G2+ is Tier-3
operator-gated + backtest-gated; soak logging preserved throughout.

## Test + backtest plan
Unit (new `tests/runtime/test_conviction_apply.py`; keep the annotator's no-gate tests,
scope them to the annotator): mode gating (off/annotate/apply), account allowlist,
sizing math (reuse `compute_conviction_sizing` assertions), direction sub-mode
(reductive=min, symmetric=can exceed up to budget), guardrails (below-floor refusal,
daily-loss clamp), composition order, fail-inert, env-gate-guard compliance (`*_MODE`
not `*_ENABLED`).

Backtest (`scripts/backtest_system.py`, Tier-1 research): add `--conviction-sizing`
A/B — replace flat `_risk_qty` with `conviction × budget` sizing (conviction ≈
calibrated `c_strat`, since heads aren't replayed offline — stated limitation).
Compare flat vs conviction-sized over the validated multiyear history: total return,
**maxDD%** (the live `max_dd_pct` guard this protects), Sharpe/Sortino, expectancy,
per-trade risk distribution. **Advance G2→G3→G4 only if** non-inferior return with
reduced maxDD (or improved Sharpe) AND the `rank_auc>0.5` readiness pass is green AND
the live `annotate` soak looks sane. Cross-check vs `conviction_sizing.jsonl`.

## Files (tiers)
Modify (Tier-1/2): `src/runtime/conviction_sizing.py` (**add** `apply_conviction_sizing`;
do **not** touch `annotate_`/`compute_`); `src/runtime/runtime_flags.py` (3 helpers);
`tests/runtime/test_conviction_sizing.py` (scope no-gate tests to annotator) + new
`tests/runtime/test_conviction_apply.py`; `scripts/backtest_system.py` (`--conviction-sizing`).
**The integration edit — `src/core/coordinator.py:1382-1416`** inserting
`apply_conviction_sizing` — is the live order-sizing path → **Tier-3, operator-approved
draft PR.** Docs: this file, `unified-confidence-risk-DESIGN.md` (record the P4 + the
flag reconciliation + the c_reg dependency), `CLAUDE.md` env table, ROADMAP/sprint.
**Not edited:** `src/units/accounts/risk.py` (Tier-3 in spirit — conviction stays a
post-sizing multiplier at the coordinator site), strategies.yaml/accounts.yaml/
risk_caps.yaml/orders.py/risk_counters.py/unit files.

## Honest caveats
`c_reg` stubbed (regime lens has no size influence until A + calibrator); the flag was
previously rejected (now reconciled, operator-blessed); conviction may be a weak signal
(rank_auc gate before apply); symmetric sizing is the intent but the dangerous half
(staged demo-first + daily-loss clamp); arbitration graduation is a separate PR.
