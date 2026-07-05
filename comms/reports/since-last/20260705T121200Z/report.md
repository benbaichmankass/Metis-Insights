# System report — since-last

- Generated: 2026-07-05T12:12:00+00:00
- Window: 2026-07-04T06:10:00+00:00 → 2026-07-05T12:12:00+00:00
- Roll-up grade: caution

Quiet, healthy weekend window with one real-money win and the prop account's first ETH fill of the cycle. Real money: 1 closed trade, ADAUSDT long +$9.31 (broker-truth, +2.03R, graded C) on bybit_2; two real positions open (ETH 0.07, BTC 0.002). Prop: the operator filled the 08:00Z eth_pullback ticket (1.9 ETH @1767.71, SL 1732/TP 1945.9) — logged as prop_fills 16, linked, monitored; $147.54 daily-loss cushion intact. Paper (bybit_1 demo) gave back -$1.7k, dominated by one AVAX 4h stop-out (-$2,276) against the ADA pump it also caught (+$1,483). Pipeline plumbing, DB integrity and alerting are clean; earlier suspicion of a prop ticket-emit bug was CORRECTED (donchian *_prop legs are shadow by design; the live ETH legs verified end-to-end). Watch items: ib_paper positions-read null over the weekend (0 IB positions at risk; re-probe at CME reopen), the trainer's 06:14Z OOM-kill mid-cycle, and prop re-ticketing while a position is open. Two fixes shipped this window: buy/long direction-synonym matcher (PR #5592) and the backlog drain (PR #5582).

## P&L by class
- **real**: window +$9.31 (prior —, flat)
- **paper**: window $-1,706.13 (prior —, down)
- **prop**: window +$0.00 (prior +$297.12, flat)

## Operator priorities
-.  — 
-.  — 
-.  — 
-.  — 
-.  — 

## Review coverage
- Strategy promotion: All strategies HOLD this window. Live ETH pullback prop family verified working end-to-end (first fill placed). donchian *_prop stays shadow by design pending the prop EV/survival gate. trend_donchian_avax_4h logged the window's big paper loss (n=1, no action). MES 15m ML heads remain promotion candidates blocked on the live_agreement gate (operator decision). fc heads soaking, not yet at gate volume.
- ML training health: Cycle ran and trained the fleet; a later stage OOM-killed on the 6GB box — bounded fix queued (MB-20260705-TRAINER-OOM).
- Soak `shadow regime fleet (btc/eth/sol/mes heads)`: accruing — all active heads scored within 15min of pull; btc-regime-15m-lgbm-v2 ADVISORY 831 preds (live vol-gate input)
- Soak `fc forecast heads (btc/eth 15m)`: accruing — btc fc-pcv 199 preds since 07-03; eth fc-pcv 74 preds since 07-04 17:44 (newly promoted)
- Soak `conviction-meta-v1`: accruing — 4533 preds, last 11:56Z
- Soak `exit-ladder (ExitPlan P3)`: accruing — rows for every prop ticket incl. 10:05/10:08Z; prop legs show single-target parity (0 differing rungs)
- Soak `allocator (M18 P0c, ev_net_r)`: accruing — ETHUSDT 2-candidate ticks through 11:14Z, mostly agree, regret <=0.016
- Soak `prop-mission-policy + trade-outcome baselines`: stalled — last_seen 06-30 — verify deliberate unwire vs stall at next /ml-review
- 🚩 trainer OOM-kill mid-cycle 06:14Z (MB-20260705-TRAINER-OOM) — fleet trained OK first; nightly timer still armed
- 🚩 ib_paper positions-read null over the weekend (balance path OK, 0 IB positions at risk) — standalone re-probe armed 22:10Z (BL-20260705-IBPAPER-POSITIONS-NULL-WEEKEND)
- 🚩 DASHBOARD_API_TOKEN unset — Tier-2 write endpoints unauthenticated (operator deferred to 'later'; standing reminder BL-20260705-DASHBOARD-API-TOKEN-UNSET)
- 🚩 prop emits fresh tickets while a position is open (10:05/10:08Z vs the 08:06 fill) — operator decision queued (BL-20260705-PROP-RETICKET-WHILE-OPEN)
- 🚩 prop-mission/trade-outcome baseline soaks silent since 06-30 — verify deliberate vs stalled

## Monitoring (soaking / awaiting decision)
- `BL-20260705-IBPAPER-POSITIONS-NULL-WEEKEND` [health · verify] ib_paper positions-read null while balance ok (weekend) (next: 22:10Z self check-in post-CME-reopen)
- `PB-20260625-001` [performance · awaiting-data] real swap charge on the open prop ETH position vs the 0.033%/day model (next: first daily swap event on the open position)
- `MB-20260705-TRAINER-OOM` [ml · verify] trainer OOM-kill mid-cycle (next: next nightly cycle 2026-07-06T00:57Z completes end-to-end)
- `BL-20260705-PROP-RETICKET-WHILE-OPEN` [performance · awaiting-decision] suppress vs keep re-tickets while prop position open (next: operator go)
- `fc-soak` [ml · soaking] btc/eth 15m fc heads accruing toward shadow->advisory gate (199/74 preds) (next: gate volume per fc-graduation program)

_report_id RPT-20260705-121200-since-last_