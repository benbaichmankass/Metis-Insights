# System report — since-last

- Generated: 2026-07-30T04:52:00+00:00
- Window: 2026-07-28T09:12:00+00:00 → 2026-07-30T04:50:00+00:00
- Roll-up grade: caution

Quiet, plumbing-healthy ~44h window. All services up, all 7 live accounts reachable, IB connected, zero alert banners. Real money (bybit_2 ~$268) essentially flat: 24h -$4.38 (0/2), 7d -$17.33 but +12R; both open real positions SL/TP-protected. 16 closed trades graded (B4/C10/D2). ML cycles clean (2/24h rc=0, 104 dataset builds). Big $ swings are all paper. Bybit naked-position blindspot fix (#7874) confirmed live.

## P&L by class
- **real**: window $-4.38 (prior —, flat)
- **paper**: window +$3,513.40 (prior —, up)
- **prop**: window +$0.00 (prior +$0.00, flat)

## Operator priorities
1. M27 real-money graduation decision (SOL/XRP/AVAX/ETH scalps + ungated gold config) — SRQ-20260728 + PB-20260728-GOLD-GATE: paper-validated scalp winners await a Tier-3 real-money sizing decision; gold config should ship UNGATED (fitted gate over-filters, derive-window-fragile).
2. Fix promotion-readiness OOM (subprocess-per-model) — readiness output unconfirmed this pull — MB-20260719: sweep memory-thrashes the 6GB trainer (~5GB RSS, 0-byte outputs); readiness dir was empty in this pull. Ready-to-fix (bounded subprocess + MemoryMax), mirrors the training-cycle memfix. Trainer PR.
3. Venue-aware allocator EV fee (ready Tier-1 follow-up PR) — PB-20260729: allocator_ev.py charges a flat 7.5bps roundtrip regardless of venue — ~25x over-charge on commission-free equities. Parallel to the shipped #7930 cost-model fix; allocator is parked (soak-only) so no live impact yet, but it feeds the future graduation gate.
4. slv_trend_1h emitting degenerate should-skip entries — conf 0.06 and 0.15 signals, both graded D, 0/4 win -$629 (paper 24h). Confidence-floor / regime gate review warranted before any real-money exposure.
5. Close BL-20260729-BYBIT-NAKED via demo validation — Primary sweep landed live (#7874). Remaining gate: run validate-bybit-naked-rearm on bybit_1 demo to confirm the re-arm path, then close. Autonomous.

## Review coverage
- Strategy promotion: All live strategies HOLD. No M7 packet flipped to PROMOTE/KILL this window. Pending Tier-3: M27 scalp winners graduation (SRQ-20260728) + ungated gold config. sol-regime-15m-lgbm-fc-pcv-v2 soaking toward advisory (~08-01) to restore the SOL vol head.
- ML training health: Trainer healthy (load 0.02, mem fine). Last cycle 2026-07-30 01:52 rc=0. 8 skips = known dead-feature audit-enforcement (4) + empty-dataset MES coverage gaps (4). No OOM/quarantine this cycle. Promotion-readiness output NOT found in this pull (dir empty) — possible MB-20260719 OOM still producing 0-byte; verify next readiness fire.
- Soak `shadow regime heads (btc/eth/sol 5m)`: accruing — 6100–6500 preds each, last_seen current (04:43). Healthy.
- Soak `conviction-meta-v1`: accruing — 5854 preds, score_mean 0.806 varied. Matched-holdout AUC read pending (MB-20260727).
- Soak `execution-quality-baseline-v0`: degenerate — score constant -0.2017 (min==max) — flag; known-class degenerate baseline.
- Soak `fc-pcv v2 (BTC/SOL vol heads)`: soaking — fresh-data siblings soaking; gate re-check + swap for frozen v1 advisory ~2026-08-01 (MB-20260721).
- Soak `SOL advisory vol head`: awaiting-decision — sol-regime-15m-lgbm-fc-pcv-v1 demoted advisory→shadow 07-26 (KS drift); v2 replacement soaking. No live-order impact (no SOL trend_vol cell).
- Soak `promotion-readiness sweep`: stalled? — readiness output dir empty this pull — verify (MB-20260719 OOM).
- 🚩 slv_trend_1h emitting degenerate low-confidence (0.06/0.15) should-skip entries → 0/4 win, -$629 paper, 2× D-grade. Needs a confidence-floor/regime gate before real money.
- 🚩 Promotion-readiness output not present in the trainer pull (empty dir) — possible MB-20260719 OOM still producing 0-byte; verify at the next readiness fire.
- 🚩 prop breakout_1 account_status snapshot 10 days stale (no open prop position, so no live risk — the feed is just dark).
- 🚩 execution-quality-baseline-v0 shadow score constant (-0.2017 min==max) — degenerate soak (known-class).

## Monitoring (soaking / awaiting decision)
- `BL-20260729-ICTSCALP-ETH15M-STALE-FIRSTFIRE` [health · awaiting-data] stale12 exit lever shipped paper-only (#7863); verify P5 parity + P7 first-fire on the first close (next: first ict_scalp_eth_15m stale_stop close)
- `BL-20260729-IB-FLEX-FILLS` [health · awaiting-decision] ib_paper broker-truth fills need the IB Flex Web Service; design landed, secret slots minted empty (next: operator provides IB_FLEX_TOKEN/QUERY_ID)
- `SRQ-20260728-M27-WINNERS-SHIP` [performance · awaiting-decision] Tier-3 M27 real-money graduation of SOL/XRP/AVAX/ETH scalp winners (next: operator go)
- `PB-20260728-ICTSCALP-GOLD-GATE-CONFIG` [performance · awaiting-decision] ship ungated gold scalp config to real money (fitted gate fragile) (next: operator go)
- `PB-20260620-001` [performance · awaiting-data] verify 6 intraday ETF cells produce live paper fills (next: paper fills accrue)
- `MB-20260727-CONVICTION-AB-AUC` [ml · awaiting-data] matched-holdout AUC read (both conviction models on identical live rows) (next: enough shared live rows)
- `MB-20260728-ICTSCALP-EXIT-LEVERS` [ml · soaking] family-wide ict_scalp exit-lever sweeps (harness built, MGC swept honest_negative) (next: remaining legs swept on trainer)
- `MB-20260721-FCPCV-V2-SOAK` [ml · soaking] fc-pcv v2 fresh-data siblings soak → gate-check + swap for frozen v1 advisory (BTC/SOL vol heads) (next: ~2026-08-01 gate re-check)

_report_id RPT-20260730-045200-since-last_