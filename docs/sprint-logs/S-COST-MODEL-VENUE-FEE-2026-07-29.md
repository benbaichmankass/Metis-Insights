# Sprint Log: S-COST-MODEL-VENUE-FEE-2026-07-29

## Date Range
2026-07-29

## Objective
Resolve the open M24 Tier-3 queue item **C** — the `spy_pullback_1h/SPY` net-R
**sign-flip** (gross +1.456R → net −0.457R), surfaced by the M24 net-R re-grade
and held (2026-07-28 roadmap reconcile) as a "9-trade estimate-cost flip, likely
fee over-charge, paper-only; do not demote on the estimate." Determine whether
the sign-flip is a real cost or a fee-model artifact, and fix the root cause.

## Tier
Tier-1 (observability / cost-attribution correctness). No live order-routing
change: the close-path cost estimate is observe-only, and the allocator EV
scorer that shares the fee constant is parked/observe-only (M18 P2+ is
backtest-gated).

## Starting Context
The M24 re-grade found `spy_pullback_1h` the ONLY gross-positive but
net-negative cell, at +0.21 R/trade of "cost-drag" under the ESTIMATE cost
model. The note flagged +0.21 R/trade as high for an equity and suspected the
estimate over-charges — but prior sessions left it as a HOLD pending broker-truth.

## Repo State Checked
- Branch reset to `origin/main` (d534515, incl. the just-merged rec #5 tooling).
- Board (#6927): posted the rec #5 START covering research scope; this cost-model
  fix is the continuation ("keep going") under the same session.

## Files and Systems Inspected
- `src/runtime/net_r_label.py` — net_R = (gross − fees)/risk; trusts the cost columns.
- `src/runtime/broker_cost_attribution.py` — broker-truth (Bybit fills) attribution; SPY has no broker-truth row → falls to `estimate`.
- `src/runtime/trade_costs.py::estimate_roundtrip_fee_usd` — the Slice-A estimator; `DEFAULT_FEE_BPS_ROUNDTRIP = 7.5` (from `src/runtime/allocator_ev.py`).
- `src/units/db/database.py::_record_trade_cost_estimate` (close-path writer) — passed the default 7.5 bps to **every** venue.
- `config/instruments.yaml` — SPY = `exchange: alpaca, category: spot`; all 14 `(alpaca, spot)` roster rows are US ETFs/equities.
- `src/core/profile_loader.py::contract_value_usd_for` — the existing per-symbol close-path resolver to mirror.

## Work Completed
Root cause: the close-path estimate charged the flat **7.5-bps crypto-perp
default** to SPY — an Alpaca **commission-free** US equity/ETF (only
sub-basis-point SEC/TAF regulatory fees on the sell leg). 7.5 bps on SPY is a
~25× over-charge; the +0.21 R/trade "cost" is that phantom fee, and the
−0.457R net was entirely it. The +1.456R gross is the honest read.

Fix (surgical, venue-aware):
- Added `profile_loader.roundtrip_fee_bps_for(symbol)` — a sibling of
  `contract_value_usd_for`, resolving 0 bps for commission-free venues
  (`(alpaca, spot)`, keyed on the VENUE not the underlying asset_class so all 14
  ETFs incl. GLD/SLV/TLT resolve to 0) and `None` (= use the estimator default)
  for crypto / futures / fx. Unknown symbol → `None` (conservative). Pure, cached.
- Wired it into `database._record_trade_cost_estimate` (pass the resolved bps;
  `None` → the estimator keeps `DEFAULT_FEE_BPS_ROUNDTRIP`).
- `trade_costs.py` stays the owner of the 7.5 default (no constant duplication,
  no core→runtime import inversion).

Disposition: `spy_pullback_1h` is **not** a net-negative cell — no demote; it
stays live as a mildly-positive equity pullback.

## Validation Performed
- `pytest tests/test_trade_costs.py` → 16 passed (added `TestVenueAwareFeeBps` +
  a close-writer test that a SPY trade stamps `fee_taker_usd == 0.0`,
  `cost_source == 'estimate'`; BTC/futures paths unchanged).
- `pytest tests/test_net_r_label.py tests/test_net_r_regrade.py
  tests/test_broker_cost_attribution.py` → 25 passed (no regression).
- `ruff check` clean; import sanity (no circular `database`↔`profile_loader`).
- Manual: `roundtrip_fee_bps_for` → 0.0 for all 14 alpaca-spot rows, `None` for
  BTCUSDT/MES/MGC/EURUSD/unknown; composed close-path fee $0 for SPY, unchanged
  ($0.825 crypto / $4.125 MES) elsewhere.

## Documentation Updated
- `docs/research/M24-net-r-regrade-findings-2026-07-17.md` — sign-flip marked
  RESOLVED (fee-model artifact).
- `src/runtime/trade_costs.py` docstring — venue-aware split + allocator follow-up.
- `docs/claude/performance-review-backlog.json` — filed `PB-20260729-ALLOCATOR-VENUE-FEE`.
- `ROADMAP.md` — recent-sessions entry.

## Contradictions or Drift Found
The M24 estimate cost model contradicted the real Alpaca fee schedule
(commission-free equities charged a crypto-perp fee). Fixed at the source.

## Risks and Follow-Ups
- **`PB-20260729-ALLOCATOR-VENUE-FEE`** — `allocator_ev.candidate_ev_score` still
  uses the flat 7.5 bps; harmless while the allocator is parked, but it should
  adopt `roundtrip_fee_bps_for` before it graduates so decision-time and logged
  cost agree for equities.
- Pre-fix historical `estimate` rows for equities are stale-high (observe-only).
  A one-off re-stamp (`UPDATE trades SET fee_taker_usd = corrected` for
  `cost_source='estimate'` equity rows) is a **Tier-2 DB writeback** — deferred;
  new closes are correct.

## Deferred Items
- The equity `estimate`-row re-stamp (Tier-2, optional observability cleanup).
- The allocator EV-scorer venue-fee parity (`PB-20260729-ALLOCATOR-VENUE-FEE`).

## Next Recommended Sprint
Continue the roadmap forward-plan (A2–A5 buildable Tier-1 items from
`S-ROADMAP-RECONCILE-2026-07-28`), or the allocator venue-fee parity when the
M18 allocator next moves toward graduation.
