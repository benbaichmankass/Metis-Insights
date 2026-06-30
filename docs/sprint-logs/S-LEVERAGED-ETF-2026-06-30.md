# Sprint Log: S-LEVERAGED-ETF-2026-06-30

## Date Range
- Start: 2026-06-30
- End: 2026-06-30 (extends the M15 ETF-expansion arc; sibling of
  S-EXPANSION-ETF-BREADTH-2026-06-20)

## Objective
- Primary goal (operator directive): research whether **leveraged equity ETFs**
  (TQQQ and similar) are good additions to the tradeable set, do the
  backtesting, and bring recommendations — holding all Tier-3 decisions for the
  operator.
- Follow-on (operator-approved mid-session): **wire the two standouts (TQQQ 3x +
  QLD 2x) to `alpaca_paper` (paper money) to begin a soak**, log a soak-monitor
  follow-up, and run the `account_compat_matrix` gate.

## Tier
- Tier 1 (research memo + study spec + backlog) and Tier 3 (the two new
  `*_trend_long_1d` paper cells — operator-approved).
- Justification: the research/compat/backlog artifacts are Tier-1; the new
  strategy cells touch `config/strategies.yaml` / `config/accounts.yaml` /
  `config/instruments.yaml` + the signal-builder/intent registries (Tier-3),
  shipped **paper-only** on `alpaca_paper`. Real-money (`alpaca_live`) promotion
  is explicitly deferred (Tier-3, gated — see Risks/Follow-Ups).

## Starting Context
- Active roadmap item: M15 (Market & Platform Migration — Alpaca ETF sleeve).
- Prior sprint reference: S-EXPANSION-ETF-BREADTH-2026-06-20 (the unleveraged
  ETF-breadth book this extends) + S-M15-ALPACA-LIVE-2026-06-27/28.
- Known facts at start: the bot already trades unleveraged ETFs (SPY/QQQ/IWM/
  GLD/SLV/USO/TLT/IEF/GDX) on Alpaca via the validated `trend_donchian` /
  `htf_pullback_trend_2h` families; `alpaca_live` is live real-money at
  `risk_pct: 0.10` on a ~$150 balance; leveraged ETFs were NOT yet covered.

## Repo State Checked
- Branch/commit: `claude/leverage-equity-research-sfaz05` cut from `origin/main`.
- Canonical docs reviewed: `CLAUDE.md`, `docs/CLAUDE-RULES-CANONICAL.md`
  (tiers, two execution gates, instruction hierarchy), ROADMAP.md;
  skills `backtesting`, `new-strategy`, `doc-freshness`.
- Deployment state: no live-VM action taken; the paper cells deploy on merge to
  `main` via `ict-git-sync`.

## Files and Systems Inspected
- Harnesses: `scripts/backtest_trend.py`, `scripts/backtest_pullback.py`,
  `scripts/ops/m15_ws_b_fold_report.py`, `scripts/ops/classify_strategy_tier.py`,
  `scripts/ops/portfolio_robustness.py`, `scripts/prop/account_compat_matrix.py`,
  `scripts/ops/etf_account_compat.sh`.
- Wiring touch points (mirrored from the live `qqq_trend_long_1d` cell):
  `src/runtime/strategy_signal_builders.py` (builders + `monitor_unit` table),
  `src/runtime/intent_multiplexer.py`, `src/runtime/intents.py`,
  `config/strategies.yaml`, `config/accounts.yaml`, `config/instruments.yaml`,
  `config/strategy_descriptions.json`.
- Data: Yahoo chart JSON API (full daily history per symbol, 2006/2008/2010→2026).

## Work Completed
- **Research (Tier-1).** Backtested the validated daily `trend_donchian`
  long-only cell (donchian 30 / atr-stop 2.5 / trail 4.0) on the **actual
  leveraged-ETF price series** (so decay + ~0.75–1.0% expense + financing are
  already embedded) across the liquid universe, via the repo's own canonical
  gate (matched-window leveraged-vs-baseline → fee-stress 7.5/15bps → per-year →
  2019/2022 holdouts → 5-fold anchored WF tier classifier → portfolio
  robustness). Memo: `docs/research/leveraged-etf-research-2026-06-30.md`;
  trainer study spec: `config/research/studies/leveraged_etf_trend.yaml`.
  - **Finding:** TQQQ (3x Nasdaq) grades `paper_ready` and **beats the live QQQ
    cell** (+13.8R vs +10.4R OOS 2019–2026, 2x-fee headroom, lower R-drawdown);
    QLD (2x) similar (+12.7R). Leveraged S&P (UPRO/SSO) ≈ neutral vs SPY.
    **Reject:** leveraged small-cap (TNA), Dow (UDOW), semis (SOXL) and ALL
    inverse ETFs (SQQQ/SPXS/SOXS/TZA/SDOW) — decay destroys the edge on
    choppier underlyings; trend-following an inverse just gets chopped. Side
    finding: unleveraged **SMH** is the strongest single cell (+15.4R), untraded
    today. FANG+ (FNGU/FNGG) excluded (ETN credit risk / discontinuous history).
    Consistent with the literature (Gayed–Bilello; Avellaneda–Zhang).
- **Wiring (Tier-3, paper-only).** Added `tqqq_trend_long_1d` + `qld_trend_long_1d`
  (reuse `trend_donchian`) to `alpaca_paper` (`execution: live` → paper money):
  two signal builders + `monitor_unit=trend_donchian` tags, intent-multiplexer +
  `DEFAULT_PRIORITIES` registration, `strategies.yaml` cells, `instruments.yaml`
  TQQQ/QLD profiles, `alpaca_paper` routing + symbols, descriptions, and the 4
  roster-pin tests (43→45).
- **Compat gate.** Added both cells to `scripts/ops/etf_account_compat.sh` and
  ran `account_compat_matrix`: **both ROUTE on `alpaca_paper`** (survival 1.0,
  P(breach) ~0 at 1.5% risk); **both SKIP on `alpaca_live`** at its current 10%
  per-trade risk (TQQQ P(breach) 10.8%, QLD 20.3% > 10% cap) — the SKIP is driven
  by the risk setting, not the edge (at 1.5% P(breach) ≈ 0).

## Validation Performed
- 179 wiring/registry/intent tests green locally (incl. the
  `test_strategy_monitor_unit_resolution` drift guard once the `monitor_unit`
  tags were added — CI caught the initial omission, fixed in `93c5022`).
- `python scripts/ci/check_canonical_doc_coherence.py` → all 4 checks PASS.
- Config files parse (yaml + json); compat matrix executed end-to-end.

## Documentation Updated
- `docs/research/leveraged-etf-research-2026-06-30.md` (new — the memo).
- `config/research/studies/leveraged_etf_trend.yaml` (new — trainer study spec).
- `docs/claude/performance-review-backlog.json` — PB-20260630-002 (soak monitor +
  the explicit `promotion_prerequisite`: lower `alpaca_live.risk_pct` before any
  real-money route).
- ROADMAP.md — M15 row + Last-Updated header (this session).
- This sprint log.

## Contradictions or Drift Found
- None. Coherence checker passes; the new cells are described consistently with
  the existing ETF-cell docs; no execution-gate / tier / hierarchy / VM-topology
  / removed-gate / ML-ladder drift introduced.

## Risks and Follow-Ups
- **Real-money promotion is gated** (PB-20260630-002). Required before any
  `alpaca_live` route: (1) lower `alpaca_live.risk_pct` (currently 0.10) to a
  level where `account_compat_matrix` returns ROUTE for both cells (sweep
  0.02→0.05; this also moves the existing cells' breach math, so re-run the full
  `etf_account_compat.sh`); (2) ≥20–30 clean closed paper trades per cell that
  track the backtest; (3) operator approval.
- **Overnight-gap tail:** the idealized-stop backtest can't model a 3x ETF
  gapping through an overnight stop — the one risk genuinely worse on leverage.
  The paper soak is how it's observed before real money.
- **Concentration:** TQQQ/QLD are ~1.0 correlated with the live QQQ cell —
  capital efficiency on a small account, NOT diversification.

## Deferred Items
- UPRO/SSO (S&P leveraged) + SMH (unleveraged semis) candidates — not wired;
  operator's call (memo §6/§7).
- Optional trainer cross-check on longer parquet history + a 1h intraday TQQQ
  sweep (memo §5).

## Next Recommended Sprint
- Drain PB-20260630-002 once paper fills accrue: compare paper vs backtest, then
  (if the operator wants real money) propose the `alpaca_live.risk_pct` reduction
  + re-run compat + the promotion PR.

## Wrap-Up Check
- doc-freshness run this session: canonical set consistent; decision landed in
  ROADMAP + this sprint log + research doc + performance-review backlog.
- PR #5239 carries the research + wiring + compat + backlog; merges to `main`
  on green CI (starts the paper soak via auto-deploy).
