# System report — since-last

- Generated: 2026-07-02T06:17:00+00:00
- Window: 2026-07-01T06:10:00+00:00 → 2026-07-02T06:17:00+00:00
- Roll-up grade: caution

Very quiet ~24h window: real-money flat (0 closed trades), one paper TLT loss (-$176.77, reconciler-estimated close, graded C). Two live incidents surfaced + driven: ict-trainer.service OOM-killed mid-cycle (fixed-forward pending, next run tomorrow) and the recurring ~06:00Z IB gateway wedge (ib_paper down, recovery dispatched, gateway login restored, trader-side reconnect still confirming). alpaca_live remains structurally unable to size real orders on its $150 balance, plus one contradicting 'unauthorized' rejection from the same day as the prior report's fix claim.

## P&L by class
- **real**: window +$0.00 (prior —, flat)
- **paper**: window $-176.77 (prior —, down)
- **prop**: window — (prior —, flat)

## Operator priorities
1. ict-trainer.service OOM-killed mid-cycle; no retry until tomorrow 00:10Z — 68-manifest daily cycle killed ~36min in at 02:50:17Z; VM memory is healthy now so this reads as a peak spike, not a shortage — add a memory cap/checkpointing so a bad cycle can't strand a full day.
2. alpaca_live cannot size any real order at its current ~$150 balance — Every QQQ/GLD/IWM-class signal risk_refused with sized_qty=0 this window; the 'test account can finally trade' fix from 2026-06-30 has not produced a single real fill.
3. alpaca_live 'unauthorized' order rejection on 2026-07-01, same day as the prior report's auth-fix claim — SLV order EXCHANGE_REJECTED 'unauthorized' at 14:26Z RTH; current broker_account_status reads ACTIVE/unblocked, so likely a stale-cred edge case around the fix window — keep watching for an actual FILL to confirm.
4. Recurring ~06:00Z IB gateway wedge (BL-20260623-002) — ib_paper unreachable again this morning; recovery dispatched and gateway login succeeded, but root cause (IBC nightly auto-restart unreliable) still not fixed at the source.
5. Two advisory-stage BTC regime models stopped scoring 7-9 days ago — btc-regime-1h-lgbm-yz-v1 (stale since 06-23) and btc-regime-5m-lgbm-yz-v1 (stale since 06-25) — not the head driving the live vol-gate, but worth confirming they're not silently dead.

## Review coverage
- Strategy promotion: No new promote/demote gate crossings identified this window (extremely quiet trading — 1 closed trade total). turtle_soup was de-routed from bybit_1 in a prior session (commit 8106255, net-negative at every stop) — already actioned, not a new finding. Full per-strategy M7 packet re-derivation was not run this session (relay budget prioritized to live-incident verification).
- ML training health: Dataset builds (market_features/market_raw/MES) completed cleanly; the training cycle itself was interrupted by an OOM kill partway through. Trainer VM memory is healthy now (5.2Gi available), consistent with a transient peak-memory spike rather than a sustained shortage.
- Soak `btc-regime-15m-lgbm-v2 (advisory, live vol-gate)`: accruing — count=502, scoring live through capture time — this is the head actually driving REGIME_ML_VERDICT_MODE=use
- Soak `btc-regime-1h-lgbm-yz-v1 (advisory)`: stalled — no new shadow_predictions row since 2026-06-23T06:53Z (9 days)
- Soak `btc-regime-5m-lgbm-yz-v1 (advisory)`: stalled — no new shadow_predictions row since 2026-06-25T06:22Z (7 days)
- Soak `A vol-verdict enforce decision (MB-20260627-001)`: gate_met — Already enforced live 2026-06-28 per CLAUDE.md — closed this session as resolved (was tracked as still-pending)
- Soak `conviction-meta-v1 / setup-quality / execution-quality shadow family`: accruing — scoring through 2026-06-30 to 2026-07-01 depending on model; gaps track order-package activity for their wired strategies
- 🚩 ict-trainer.service OOM-killed mid-cycle 2026-07-02T02:50:17Z — next scheduled retry not until 2026-07-03T00:10Z (a full day gap)
- 🚩 ib_paper (declared-live account) read unreachable at 2026-07-02T06:05Z — MANDATORY reachability flag; recovery dispatched this session (vm-ib-gateway-recover), gateway login succeeded 06:07:01Z, trader-side reconnect not yet independently reconfirmed as of 06:16Z
- 🚩 alpaca_live real-money account rejected an order as 'unauthorized' on 2026-07-01T14:26Z (RTH), the same day the prior report claimed the auth fix was verified — contradicts that claim; broker_account_status currently reads ACTIVE/unblocked

## Monitoring (soaking / awaiting decision)
- `BL-20260627-ALPACA-LIVE-API-UNAUTH` [health · verify] Waiting for a confirmed alpaca_live FILL during RTH to prove the sync-vm-secrets auth fix holds; latest evidence is a contradicting unauthorized rejection from the same day as the fix. (next: next real-money alpaca_live order attempt during RTH)
- `BL-20260623-002` [health · verify] Recurring ~06:00Z IB gateway wedge; watching whether the reactive watchdog catches it before a manual recover is needed. (next: next daily 06:00Z window)
- `btc-regime-{1h,5m}-lgbm-yz-v1 stale advisory soak` [ml · verify] Both stopped producing shadow_predictions 7-9 days ago while at advisory stage; confirm intentional vs silently dead. (next: next /ml-review)
- `MB-20260627-002` [ml · awaiting-data] Multi-symbol A (ETH/MES regime-head gating) blocked on RG4 trust criteria maturing. (next: RG4 fleet scorecard re-run)

_report_id RPT-20260702-061700-since-last_