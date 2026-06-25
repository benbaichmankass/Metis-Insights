# System report — since-last

- Generated: 2026-06-25T05:40:00+00:00
- Window: 2026-06-24T05:23:22+00:00 → 2026-06-25T05:40:00+00:00
- Roll-up grade: caution

Window 06-24 05:23Z->06-25 05:40Z (~24h). System healthy: heartbeat running, all services/timers up, VM relaxed (cpu0/mem12/disk26), 36 strategies evaluating, clean restart ~05:11Z (git 6f1f9401). REAL MONEY IDLE: 0 closed real trades in 24h. Paper +$19 net but pure artifact (crypto reconciler closes +$2597 minus MHG adopted_orphan phantom -$2577). LOUD ML FLAG: the sole advisory model btc-regime-5m-lgbm-yz-v1 is degenerate live (stage-guard DEMOTE; brier_lift -0.277, auc 0.40). Recurring: order packages size to target_qty=0 -> orphan/reject; MHG -2530 phantom (06-24) is the LAST PRE-FIX flap — orphan-flap hardening #1-#5 now ALL MERGED (#4453/#4464/#4465/#4468/#4469/#4481); fixes holding (no new flap post-06-24 13:49); residual rows await the operator-gated reconcile-orphan-history cleanup.

## P&L by class
- **real**: window +$0.00 (prior —, flat)
- **paper**: window +$19.19 (prior —, flat)
- **prop**: window — (prior —, flat)

## Operator priorities
1. DEMOTE degenerate advisory btc-regime-5m-lgbm-yz-v1 (advisory->shadow) — Only order-influencing model; worse than baseline live (brier_lift -0.277, auc 0.40). Soft-off demote reverts to known-good zero-advisory conviction. (MB-20260625-001)
2. Fix aggregated_target_qty=0 emit-then-orphan (BUG-049) — ada/mgc/spy/slv/qqq decisions size to 0 qty then orphan/reject this window. Refuse-with-cause instead of emit-then-orphan. (BL-20260601-001)
3. Run reconcile-orphan-history dry-run to clean the MHG/MGC phantom rows (orphan-flap queue COMPLETE) — Orphan-flap hardening #1-#5 ALL MERGED (#4453/#4464/#4465/#4468/#4469/#4481). The MHG -$2530.41 closes (06-24 11:41-13:49) PREDATE the #1 fix (#4464, 15:28) — pre-fix flap, not a regression; no new flaps post-deploy. Residual phantom rows are cleaned by #4481's reconcile-orphan-history (dry-run -> operator-gated apply).
4. Confirm real-money idle is intended (0 closes in 24h) — bybit_2 real-money had no closed trades in the window; verify this is market-driven quiet, not a silent execution gap.
5. MES quality manifests skip empty_dataset every cycle — mes-execution-quality / mes-setup-quality / mes-trade-outcome-winrate never train — data-blocked (needs intraday MES history). (BL-20260526-002)

## Review coverage
- Strategy promotion: No model ready to promote (stage-guard promote=[]). One demote: the sole advisory yz head. Trading strategies: no new M7 review packets pulled this window (relay budget) and no strategy met a promote/kill gate from the live data — effectively all HOLD.
- ML training health: 1 training cycle since last review (cycle_end rc=0 06-25 01:08Z); datasets build_end rc=0 06-25 00:59Z. 3 MES quality manifests skip empty_dataset every cycle (data-blocked).
- Soak `shadow models (24 at shadow)`: accruing — conviction-meta-v1 + execution-quality scored on this window's order packages; predictions logging
- Soak `advisory yz head (btc-regime-5m-lgbm-yz-v1)`: gate_met — stage-guard DEMOTE gate met (negative brier_lift) and UNACTIONED -> flagged as operator priority #1
- Soak `cross-asset shadow (eth-regime-1h-lgbm-xasset-v1)`: accruing — at shadow; trained this cycle (manifest_ok 06-25 01:07)
- Soak `exit-ladder soak`: accruing — unavailable: exit_ladder_soak log not pulled this session (relay budget) — last reports showed it accruing
- 🚩 DEGENERATE ADVISORY: btc-regime-5m-lgbm-yz-v1 (only order-influencing model) is worse than baseline live (brier_lift -0.277, auc 0.40); demote gate met + unactioned.
- 🚩 MHG/ib_paper phantom flap: the -2530.41 close (06-24 11:41-13:49) was the LAST pre-fix flap; orphan-flap hardening #1-#5 now all merged and holding (no new flap post-06-24 13:49). Residual rows await the #4481 reconcile-orphan-history cleanup (operator-gated apply).
- 🚩 aggregated_target_qty=0 -> orphan/reject cluster (ada/mgc/spy/slv/qqq) still active (BUG-049/BL-20260601-001).
- 🚩 REAL-MONEY IDLE: 0 closed real trades in 24h — confirm market-driven, not a silent execution gap.
- 🚩 3 MES quality manifests skip empty_dataset every trainer cycle (data-blocked).

_report_id RPT-20260625-054000-since-last_