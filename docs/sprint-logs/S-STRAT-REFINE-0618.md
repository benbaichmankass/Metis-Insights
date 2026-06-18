# Sprint Log: S-STRAT-REFINE-0618

## Date Range
2026-06-18 (continues S-RECOMB-SWEEP; operator-directed "move on both").

## Objective
Enact the two Tier-3 strategy refinements the Direction-2 follow-up surfaced:
(a) demote the net-negative `mgc_trend_1h`; (b) apply the OOP-validated ADX≥25
entry gate to the pullback alt cells — both PAPER-only, bybit_2 untouched.

## Tier
Tier-3 (`config/strategies.yaml` + a live-unit code change). Draft PR #3962 —
merge pending explicit operator approval.

## Work Completed
- **mgc_trend_1h live→shadow** (`config/strategies.yaml`, `ib_paper`) — carries the
  `# shadow-guard: allow` marker (operator-approved); wiring test updated.
- **ADX≥25 gate on 5 pullback alt cells** (eth/sol/xrp/ada/avax `_pullback_2h`,
  `bybit_1` PAPER): ported the `scripts/backtest_pullback.py` Wilder-ADX gate
  **verbatim** into `src/units/strategies/htf_pullback_trend_2h.py` (OFF by
  default; new `adx_min`/`adx_max`/`adx_period` params). Set `adx_min: 25` on the
  5 cells. New `tests/test_htf_pullback_adx_filter.py`.

## Validation Performed
- **Real-money safety:** the BTC `htf_pullback_trend_2h` cell (live on bybit_2)
  has NO `adx_min` → unit change is behaviour-preserving there (verified via yaml
  load assertion). Only the 5 paper alt cells are gated.
- ADX gate unit tests pass (off=behaviour-preserving; high adx_min rejects with an
  ADX reason; low adx_min admits + stamps `meta.adx`; unit `_adx` == harness `_adx`
  bar-for-bar). ruff clean. YAML parses.
- mgc demote: `check_dry_run_in_diff.py` clean after the allow-marker;
  `test_mgc_trend_1h_wiring.py` updated to assert `shadow`.

## Evidence
docs/research/recombination-sweep-2026-06-18.md (sweep + OOP holdout + mgc real-1h).

## Risks and Follow-Ups
- **ETH is NOT promoted to bybit_2.** Correlated ~0.7–0.9 to BTC, no real-money
  track record, `account_compat_matrix` not run (BTC/ROSTER-centric, PB-012). This
  paper soak builds the track record a future real-money case would need.
- Trend-side recombination live_ready cells still need their own OOP holdout
  before any proposal.

## Wrap-Up Check
- [x] Real-money path unchanged (verified).
- [x] Tier-3 changes proposed via draft PR #3962, not merged without approval.
- [x] Tests + ruff + YAML validated locally.
- [x] Recorded in ROADMAP + this sprint log.

## Addendum — eth_pullback_2h promoted to bybit_2 REAL MONEY (operator-directed, PR #3963)
Same day, the operator reversed the bybit_2 hold and directed putting eth_pullback_2h
live on **bybit_2 (real money)** as a deliberate live test ("bybit_2 is a test account;
I want to see how ETH performs there") — explicitly accepting the override of the
paper-first / account_compat gate. Change: add `eth_pullback_2h` to `bybit_2.strategies`
+ `ETHUSDT` to `bybit_2.symbols` (config/accounts.yaml); same ADX≥25-gated config as
bybit_1. Wiring test updated (now asserts present on both accounts).
**Caveats (operator eyes-open):** ETH ~0.7-0.9 BTC-correlated (correlated real-money
exposure, not diversification); no prior real-money track record; account_compat_matrix
not run (BTC/ROSTER-centric). Governed by bybit_2 caps (1% risk, 5% daily-loss, 3x).
ROLLBACK: remove from bybit_2.strategies. #3962 (mgc demote + bybit_1 ADX gate) merged;
#3963 (bybit_2 real-money) pending operator merge.
