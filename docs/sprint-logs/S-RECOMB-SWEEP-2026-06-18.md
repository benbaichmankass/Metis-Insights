# Sprint Log: S-RECOMB-SWEEP-2026-06-18

## Date Range
2026-06-18 (single session; continues S-CROSS-ASSET-DIVERSIFY).

## Objective
Direction 2 of the strategy-expansion initiative: run the strategy-primitives
recombination sweep (`scripts/ops/recombination_sweep.py`) over the v1 pool to
mix existing validated primitives (symbol × family × ADX regime-filter × trail ×
selectivity) into candidate cells, gated on holdout robustness. Plus two
follow-ups from S-CROSS-ASSET-DIVERSIFY: (#3) validate `mgc_trend_1h`; (#2)
per-contract cost assessment for the non-crypto cells.

## Tier
Tier-1 (research tooling + docs; trainer-VM sweeps via vm-driver). No live-path
change. Any cell refinement = a future Tier-3 `config/strategies.yaml` proposal.

## Starting Context
S-CROSS-ASSET-DIVERSIFY (merged #3960) banked the cross-asset paper books and
named Direction 2 + the `mgc_trend_1h`/account_compat follow-ups as next.

## Work Completed
- **Direction-2 sweep (Tier-1):** 90 coherent tuples through harness×2 →
  k-fold → tier. Detached on the trainer via `vm-driver` (sweep ran ~5 min).
  Result: 9 live_ready / 67 paper_ready / 14 reject. Doc:
  `docs/research/recombination-sweep-2026-06-18.md`; raw
  `automation/results/direction2-collect2.txt`.
- **#3 `mgc_trend_1h` validation (Tier-1):** emit via XAUUSD_15m→1h **spot proxy**
  (live params donchian20/atr2.5/trail3.0) → `portfolio_robustness.py`.
- **#2 per-contract cost (analysis):** reasoned from `config/instruments.yaml`
  contract specs + the Direction-1 added-cost headroom.

## Validation Performed / Findings
- **Direction 2 — ADX × family interaction (the robust finding):** an ADX entry
  floor **helps pullback** (every live_ready pullback cell has an ADX gate; 0
  live_ready without) and **hurts trend at high ADX** (strong_trend_only → 8
  rejects, 0 live_ready). Reproduces regime-map Step-1's complementary profiles
  at the cell level. Top live_ready: `pullback_ETHUSDT_2h_adxmin25_trail5`
  +63.1R (2× +59.5) vs no-ADX baseline +59.0R. **Caveat:** 76/90 survivors are
  parameter variants of the same ~10 alt edges (multiple-comparisons, DESIGN §6)
  — the output is a handful of param refinements, not 76 strategies; no
  out-of-pool holdout yet.
- **#3 — honest NEGATIVE:** `mgc_trend_1h` on the spot proxy is net **−50.7R**
  (1269 trades, Sharpe −1.05, 6/8 years −, holdouts 2/5 −, bootstrap P(+)=0.13)
  — REJECT on every axis. Caveats: spot ≠ COMEX micro future (sessions, roll); a
  1h donchian-trend on gold is far more chop-exposed than the +56R daily
  `mgc_pullback_1d`. Paper-only (no live-money risk).
- **#2 — per-contract cost is negligible for the daily futures cells:** risk/contract
  ≈ 2–2.5×ATR × contract_value ≈ $300–600 (MES $5/pt, MGC $10/oz, MHG); a ~$2–4
  round-trip per-contract cost ≈ **0.005–0.013 R/trade** vs the futures book's
  measured **+0.43 R/trade** breakeven headroom → 30–80× margin. Fees do not
  threaten the futures edge. The formal `account_compat_matrix` is BTC/ROSTER-centric
  (`_PANDAS_TF` has no `1d`, hardcodes BTCUSDT) → needs a futures/daily extension
  for the rigorous Tier-3 gate (logged).

## Documentation Updated
- `docs/research/recombination-sweep-2026-06-18.md` (new).
- ROADMAP.md: S-RECOMB-SWEEP row + header.
- This sprint log; performance-review-backlog updates (PB-20260618-010 negative;
  new eth_pullback-ADX refinement + account_compat-extension items).

## Risks and Follow-Ups
- **mgc_trend_1h** is `execution: live` on ib_paper (paper) but net-negative on
  the proxy — Tier-3 candidate for demote-to-shadow/removal pending real MGC 1h
  data; operator decides (no config change made).
- **eth_pullback ADX≥25 refinement** — the highest-value paper refinement; needs
  an out-of-pool holdout before a Tier-3 strategies.yaml proposal.
- **account_compat_matrix** futures/daily extension — needed for the rigorous
  per-contract real-money gate.

## Next Recommended Sprint
Out-of-pool holdout on the top recombination live_ready cells (esp. eth_pullback
ADX≥25); a real MGC ≤1h data pull to settle mgc_trend_1h; account_compat_matrix
futures extension if a non-crypto real-money proposal is wanted.

## Wrap-Up Check
- [x] Sweeps validated on real trainer data (vm-driver logs committed).
- [x] Material decisions recorded in ROADMAP + this sprint log + backlog.
- [x] No Tier-3 config/live change made without approval.
- [x] `/doc-freshness` run at session close.
