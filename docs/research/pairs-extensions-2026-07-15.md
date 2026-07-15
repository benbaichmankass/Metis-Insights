# M22 wave-2 — pairs-sleeve extensions (D5 / D6)

The D2 market-neutral cointegration pairs sleeve shipped live-on-paper
(4 pairs on `bybit_1`; `docs/sprint-logs/S-M22-D2-PAIRS-SLEEVE-2026-07-15.md`).
Because the edge is **statistical, not symbol-specific**, the two highest-EV
follow-ons are to (D5) find MORE robust pairs across the liquid-perp universe,
and (D6) sharpen the engine that trades them. Both are Tier-1 research
(propose-only); any add-to-sleeve / engine change is a separate Tier-3
`config/pairs.yaml` / `pairs_engine.py` proposal.

## D5 — cointegration universe-scan  (tool built + self-tested)

`scripts/research/pairs_universe_scan.py` scans every candidate pair from a
directory of candle CSVs and ranks them by the operator's capital-efficiency
metric (`net_r_per_pos_day`). It **composes the already-validated pieces** —
`backtest_pairs.run_backtest` (the parity-verified engine) +
`cointegration_stability.analyze` — and adds a statistically-sound
cointegration gate:

- **Engle-Granger step 1** — one *fixed* full-sample cointegrating vector (OLS
  of logA on logB). (The gate must NOT use the engine's rolling beta: a rolling
  regression re-fits every bar and makes *any* pair's residual spuriously
  stationary, so it can't discriminate cointegration — a bug caught + fixed in
  the self-test.)
- **Engle-Granger step 2** — the Dickey-Fuller t-stat (`_adf_tstat`) on the
  residual. In-sample gate: `adf_tstat <= -2.86`.
- **Cointegration PERSISTENCE (the key false-positive filter)** — apply the
  *same* full-sample vector to the OOS slice and ADF that residual too
  (`oos_adf_tstat <= -2.86`). A genuine pair stays stationary under one vector
  OOS; a spurious in-sample fit breaks (ADF rises toward 0). Two independent
  random walks can hit ADF −4.3 in-sample (a tail draw) but their OOS ADF
  reverts to ≈ −2.3 — the self-test proves the gate rejects them on exactly
  this basis.

The `oos_robust` flag = cointegrated in-sample AND persists OOS AND OOS-net-of-
fee-positive (expectancy > 0, ≥ `min_trades`) AND rolling-HL stable AND
half-life in a sane band. The driver emits a ranked JSON + markdown and a
**low-leg-overlap shortlist** (`recommend_add`, greedy cap on per-symbol
appearances so the sleeve diversifies rather than going all-BTC).

**Self-test** (`--self-test`, no data files): a synthetic cointegrated triple —
B = A + a small stationary OU spread, C independent — must rank A/B #1 + robust
and reject A/C, B/C. PASS.

**Next (trainer run):** point `--data-dir` at the trainer's 1h candle store,
`--oos-start 2025-01-01`, over the full liquid-perp universe; the robust,
not-already-live shortlist becomes a Tier-3 `config/pairs.yaml` expansion
proposal (with the mandatory per-account compat check). **Findings appended
below as they land.**

### D5 findings

_(pending the trainer scan — this section is filled when the run returns.)_

## D6 — engine upgrades: OU-optimal bands + Kalman hedge-ratio  (planned)

The live engine uses fixed `entry_z=2 / exit_z=0.5` and a rolling-window β. Two
textbook stat-arb upgrades, to A/B vs that baseline on the 4 live pairs
(Tier-1 research; wire only if OOS improves, Tier-3):

1. **OU-optimal entry/exit bands** — fit the Ornstein-Uhlenbeck mean-reversion
   speed per pair and derive the cost-aware optimal entry/exit band instead of
   fixed z-thresholds.
2. **Kalman-filter adaptive β** — track the hedge-ratio drift continuously (the
   `hedge_beta_drift` the scan already reports is the symptom this addresses),
   which should tighten the spread stationarity and cut the tail drawdowns.

## Guardrails

All Tier-1 / propose-only / trainer-VM. Nothing here touches the live order
path. The live 4-pair sleeve is unaffected until an explicit Tier-3
`config/pairs.yaml` (add pairs) or `pairs_engine.py` (band/β change) PR is
operator-approved.
