# System report — weekly

- Generated: 2026-07-16T06:12:00+00:00
- Window: 2026-07-09T06:07:48+00:00 → 2026-07-16T06:12:00+00:00
- Roll-up grade: caution

Net-positive real-money week (+$5.57, PF 1.34) carried by the ETH book against small alt bleeders; core services healthy and grading is fully current. Watch items: a transient (benign) IB MES probe backoff, a recurring sub-min-qty BTCUSDT refusal on bybit_2, a trainer manifest (5m-flow head) repeatedly OOMing under real memory pressure, and no ML promotions - the regime heads have soaked their time but are blocked on the live_agreement gate.

## P&L by class
- **real**: window +$5.57 (prior —, up)
- **paper**: window $-7,551.97 (prior —, down)
- **prop**: window — (prior —, flat)

## Operator priorities
1. ict_scalp_5m real-money R-collapse - decide TUNE vs DEMOTE — 30d expectancyR -1.26 (66.7% win but tiny wins, full-R losses). Adverse realized R:R. Needs an exit/target review or a shadow demotion. PB-20260630-ICTSCALP-DEGRADE.
2. Trainer memory: 5m-flow manifest OOM + box at swap ceiling — btc-regime-5m-lgbm-flow-v1 killed (137->143 after ~19h); trainer 186MB free. Fix: stream-aggregate the shadow-log read (attribution.py list(records)) + systemd MemoryMax + per-manifest subprocess. BL-20260715 / MB-20260709.
3. bybit_2 sub-minimum-qty BTCUSDT refusals (recurring alert noise) — qty 0.00037 < 0.001 lot floor on the $307 account - correct refusal but ERROR-level alert spam. Floor to minOrderQty w/ affordability re-check, or skip without an ERROR outcome. BL-20260716-BYBIT2-SUBMIN-QTY.
4. Regime heads soaked but promotion structurally blocked — btc/eth/sol 5m heads have 9d/2400+ preds, healthy spreads, but shadow->advisory needs the live_agreement (trade-win AUC) gate, which they can't clear. Decide whether to revisit the gate design. MB-20260626-003.
5. Standing security-hardening reminders (owner-side) — GHAS secret-scanning still disabled on the public repos; hardware-2FA on the owner account is the single linchpin for every issue-driven workflow. BL-20260628-SEC-HARDENING-FOLLOWUPS.

## Review coverage
- Strategy promotion: No strategy or model is ready to PROMOTE. The btc/eth/sol regime heads have soaked their 7-day time (9d, 2400+ preds, healthy spreads) but shadow->advisory is structurally blocked on the live_agreement gate (MB-20260626-003). Demote/kill candidates: ict_scalp_5m (R-collapse) and ada_pullback_2h (persistent negative). Real-money bright spots holding HOLD: eth_pullback_2h, trend_donchian_eth_4h, trend_donchian.
- ML training health: Training IS progressing - full cycles ran 2026-07-11 and 2026-07-15 (76 manifests, head e82ac858), datasets built OK (market_raw 215,862 rows, rc:0). One manifest (5m-flow VPIN head) repeatedly OOMs and one TCN family empty-dataset-skips; both tracked. Trainer under memory/disk pressure (186MB free, disk 87%).
- Soak `btc-regime-5m-lgbm-v2 / -yz-v1 (shadow)`: gate_met — 9 days, 2487 preds, healthy score spread (0.51-0.9999). Soak-TIME met; promotion blocked on live_agreement gate (not a soak stall).
- Soak `eth-regime-5m-lgbm-v1 / sol-regime-5m-lgbm-v1 (shadow)`: accruing — ~2312-2469 preds, healthy spreads; still accruing toward RG4 readiness (MB-20260628).
- Soak `conviction-meta-v1 (shadow)`: accruing — 1993 preds but a very narrow band (0.8168-0.8188) - near-constant; watch for degeneracy.
- Soak `baseline v0 heads (execution/setup-quality)`: accruing — Constant outputs (-0.20 / -0.057) - expected for v0 baselines, not a stall.
- Soak `ExitPlan ladder / fc-geometry soaks`: accruing — Observe-only soaks accruing toward their backtest-gated graduations (PB-20260617-002, MB-20260705).
- 🚩 btc-regime-5m-lgbm-flow-v1 manifest repeatedly OOM/terminated (exit 137->143) - the 5m VPIN head is both DATA-BLOCKED and the trainer's memory offender.
- 🚩 Trainer VM under memory/disk pressure (186MB RAM free of 5.9GB, disk 87%) - chronic OOM/wedge risk on its own cycle.
- 🚩 ict_scalp_5m real-money R-collapse confirmed a 2nd window (expectancyR -1.26) - needs a TUNE/DEMOTE decision.
- 🚩 Recurring sub-minimum-qty BTCUSDT pre-flight refusals on real-money bybit_2 (ERROR-level alert noise; correct refusal).
- 🚩 Regime heads have met soak-time but shadow->advisory is structurally blocked - no ML promotions are possible under the current gate.
- 🚩 GHAS secret-scanning remains disabled on the public repos (standing owner-side hardening reminder).

## Monitoring (soaking / awaiting decision)
- `MB-20260626-003` [ml · awaiting-decision] Regime-head promotion blocked on the live_agreement gate; heads have soaked their time but can't clear it. (next: operator decision on gate design)
- `BL-20260715-TRAINER-CYCLE-MEM-SATURATION` [health · awaiting-data] Trainer's own cycle drives the 6GB box into deep swap; 5m-flow manifest OOMs. Fix is a footprint/serialization change (Tier-2). (next: trainer-infra code session lands stream-aggregate + MemoryMax)
- `PB-20260630-ICTSCALP-DEGRADE` [performance · awaiting-decision] ict_scalp_5m real-money R-collapse confirmed 2nd window; awaiting TUNE/DEMOTE decision. (next: operator TUNE vs DEMOTE call)
- `MB-20260613-002` [ml · awaiting-data] VPIN/order-flow head (5m-flow) DATA-BLOCKED awaiting forward L2 capture; currently also the OOM offender. (next: L2 capture path built (MB-20260604-002))
- `PB-20260617-002` [performance · soaking] ExitPlan ladder soak accruing toward the P4 graduation gate. (next: soak volume sufficient for backtest gate)

_report_id RPT-20260716-061200-weekly_