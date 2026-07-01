# System report — since-last

- Generated: 2026-07-01T06:10:00+00:00
- Window: 2026-06-30T06:16:11+00:00 → 2026-07-01T06:10:00+00:00
- Roll-up grade: healthy

~24h window. Core green + a real-money WIN: bybit_2 BTCUSDT short +$2.86 via TP (graded B). Main flag this session was alpaca_live rejecting every order 'unauthorized' — DIAGNOSED (VM cached stale creds; keys valid, routing correct, risk fine) and FIXED (sync-vm-secrets + restart, verified authenticates). Remaining: paper reconciler churn (no real money); DIAG_BASE_URL env repoint (operator).

## P&L by class
- **real**: window +$2.86 (prior +$0.00, up)
- **paper**: window $-134.68 (prior —, down)
- **prop**: window — (prior —, flat)

## Operator priorities
1. alpaca_live real-money — FIXED this session; confirm a fill at next US RTH — Root cause was VM-side stale cached creds (Actions keys pass the live creds-test; accounts.yaml has alpaca_env:live; risk mgmt sizes correctly). Fixed via sync-vm-secrets + restart-bot-service + vm-web-api-recover; verified live auth (positions:[]). Confirm an ETF order actually places at RTH ~13:30 UTC.
2. DIAG_BASE_URL env var still points at the terminated micro — Repo fix in PR #5252 (notebook). Operator: set DIAG_BASE_URL=http://141.145.193.91:8001 in the web-env config (raw IP:port egress is proxy-dropped, so the relay stays the working path).
3. Paper reconciler-churn (bybit_1 BTC -$177/-$200, AVAX -$134) — Reconciler force-closes on PAPER positions (reconciler_filled / orphan). Self-heal working; no real money. Verify the intent-reduce partial-close fix stops the churn (BL-20260629).

## Review coverage
- Strategy promotion: 1 real-money close this window (BTC TP win). ict_scalp_5m degrade flag is tempered by the win; fvg_range_15m remains a kill candidate. No clean PROMOTE this window.
- ML training health: 1 trainer cycle in-window (rc:0, publish OK) — healthy. Two MES manifests skipped empty_dataset; cross-asset ETH head on dead xa cols; f1=0 baseline retired shadow→candidate.
- Soak `shadow regime heads (BTC/MES/ETH/SOL)`: accruing — 34 model×stage rows, last_seen 05:04; BTC-15m advisory live; ETH/SOL 5m+15m since 06-28.
- Soak `conviction-meta-v1`: accruing — 3255 preds — P4 real-money-sizing gate not yet actioned.
- Soak `A vol-verdict enforce (BTC)`: accruing — advisory 15m head drives the gate live; firm read pending (MB-20260627-001).
- Soak `exit-ladder + allocator runtime soaks`: accruing — unavailable: those log_file relays didn't return this run (queue backlog); shadow-ML soaks confirmed accruing as the proxy.
- 🚩 RESOLVED THIS SESSION: alpaca_live real-money rejected every order 'unauthorized' → root-caused to VM cached stale creds (keys valid, routing correct, risk fine) → FIXED via sync-vm-secrets + restart → verified authenticates. Confirm a fill at RTH.
- 🚩 RETRACTED (my first-pass errors): 'real-money idle' — actually a BTC TP win (+$2.86); 'IB gateway wedged' — mgc_trend_1h is execution:shadow by design.
- 🚩 OPEN: paper reconciler-churn (bybit_1 BTC -$177/-$200 pre-window, AVAX -$134) — no real money; verify BL-20260629.
- 🚩 OPEN: DIAG_BASE_URL env points at the terminated micro (repo fix PR #5252; env repoint = operator).

## Monitoring (soaking / awaiting decision)
- `alpaca_live-fix` [health · verify] alpaca_live auth fixed (sync+restart, verified authenticates). Confirm an actual ETF order PLACES + FILLS at US RTH. (next: next alpaca_live order at RTH ~13:30 UTC / next hourly api_ok snapshot)
- `MB-20260627-001` [ml · awaiting-data] A vol-verdict live enforce soak (BTC 15m advisory drives the gate). (next: firm read as enforce sample grows)
- `BL-20260629-INTENT-REDUCE-CHURN-VERIFY` [health · verify] confirm the intent-reduce fix stops the paper open→reconciler-close churn (AVAX/BTC paper orphans). (next: no new reconciler orphans over a soak window)

_report_id RPT-20260701-061000-since-last_