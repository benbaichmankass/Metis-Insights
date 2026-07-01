# System report — since-last

- Generated: 2026-07-01T05:24:00+00:00
- Window: 2026-06-30T06:16:11+00:00 → 2026-07-01T05:24:00+00:00
- Roll-up grade: caution

~23h window, system core green (services up, mem 12%, deploy current, trainer cycled rc:0, shadow soaks accruing). But the real-money book is idle: alpaca_live rejects every order 'unauthorized'+undercapitalized and ib_paper futures aren't executing (IB gateway likely wedged). 0 real-money closed trades.

## P&L by class
- **real**: window +$0.00 (prior +$0.00, flat)
- **paper**: window $-134.68 (prior —, down)
- **prop**: window — (prior —, flat)

## Operator priorities
1. alpaca_live (real money) cannot trade — LIVE Alpaca key unauthorized + undercapitalized — 5 real-money attempts in window all rejected: TLT/SLV 'Alpaca rejected order: unauthorized' + QQQ risk_refused sized_qty=0 balance=$150. Operator: verify ALPACA_API_KEY_ID_LIVE/SECRET are the funded LIVE-account keys (not paper), fund >= $2k, re-run sync-vm-secrets + test-alpaca-creds. Tracked BL-20260627-ALPACA-LIVE-API-UNAUTH.
2. IB gateway session likely wedged — ib_paper MGC/MES futures not executing — ib_paper MGC (mgc_trend_1h) orders repeatedly 'dry_run_no_order_placed' through the window; the exchange_positions diag probe itself hung ~18min (consistent with an IB timeout). Investigate the gateway VM session; vm-ib-gateway-recover if wedged.
3. DIAG_BASE_URL still points at the terminated micro (this session's fix) — Session env var = http://158.178.210.252:8001 (dead). Repo sweep fixed (PR #5252, notebook template). Operator: set DIAG_BASE_URL=http://141.145.193.91:8001 in the web-env config (note: raw IP:port egress is proxy-dropped, so the relay stays the working path).
4. Orphan artifacts: paper AVAX (-$134) + prop SOL (BUG-049) — Reconciler self-heal is working (close-on-disappear) but produced a paper AVAX orphan and a never-executed prop SOL package. No real money. Watch INTENT-REDUCE-CHURN-VERIFY (BL-20260629).

## Review coverage
- Strategy promotion: No strategy is at a clean PROMOTE gate this window (0 real-money closes to add evidence). Two demote/kill candidates carried from the performance backlog (ict_scalp_5m degrade, fvg_range_15m bleed). M7 matrix over-fires DEMOTE at low n (PB-20260630-002) — weigh before acting.
- ML training health: 1 trainer cycle in-window (00:51-01:27, rc:0, publish OK) — healthy and cycling. Gaps: 2 MES manifests skipped empty_dataset; cross-asset ETH head trained on dead xa cols; f1=0 baseline correctly retired shadow→candidate.
- Soak `shadow regime heads (BTC/MES/ETH/SOL)`: accruing — 34 model×stage rows in shadow_predictions, last_seen 05:04 today; BTC-15m advisory live; ETH/SOL 5m+15m accruing since 06-28.
- Soak `conviction-meta-v1 (Design-B sizing)`: accruing — 3255 preds, mean 0.78 — P4 real-money-sizing gate not yet met/actioned.
- Soak `A vol-verdict enforce (BTC)`: accruing — advisory 15m head driving the gate live; firm read pending as enforce sample grows (MB-20260627-001).
- Soak `exit-ladder + allocator runtime soaks`: accruing — unavailable: log_file relay for exit_ladder_soak/allocator_soak did not return this run (queue backlog); shadow-ML soaks confirmed accruing as the proxy.
- 🚩 REAL-MONEY DOWN: alpaca_live rejects every order ('unauthorized') + undercapitalized ($150) — the real-money book cannot trade on Alpaca (BL-20260627, operator hand-off).
- 🚩 IB GATEWAY DEGRADED: ib_paper MGC/MES orders 'dry_run_no_order_placed'; the exchange_positions probe hung ~18min — futures execution likely blind.
- 🚩 DIAG_BASE_URL points at the terminated micro (fixed in-repo PR #5252; env-var repoint is the operator hand-off).
- 🚩 Reconciler orphan artifacts (paper AVAX -$134, prop SOL BUG-049) — self-heal working but churn persists (verify BL-20260629).

## Monitoring (soaking / awaiting decision)
- `BL-20260627-ALPACA-LIVE-API-UNAUTH` [health · awaiting-decision] alpaca_live real-money key unauthorized + undercapitalized; blocks all alpaca_live go-live. (next: operator verifies LIVE key + funds; test-alpaca-creds api_ok:true)
- `MB-20260627-001` [ml · awaiting-data] A vol-verdict live soak (BTC 15m advisory driving the gate) — first read was due ~06-30. (next: firm read ~2026-07 as enforce sample grows)
- `MB-20260616-CONVICTION-P4-SIZING` [ml · soaking] conviction-meta-v1 shadow soaking (3255 preds) before graduating to real-money sizing. (next: soak volume + operator go)
- `BL-20260628-XA-TRAINING-ZERO` [ml · awaiting-data] ETH 1h cross-asset head trained on all-zero xa_* cols — may be learning dead features. (next: alt dataset rebuild carries live xa_* values)
- `BL-20260629-INTENT-REDUCE-CHURN-VERIFY` [health · verify] confirm the intent-reduce partial-close fix stops the open→reconciler-close churn (AVAX/SOL orphans still appearing). (next: no new reconciler orphans over a soak window)

_report_id RPT-20260701-052400-since-last_