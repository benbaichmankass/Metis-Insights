# System report — since-last

- Generated: 2026-07-08T07:47:00+00:00
- Window: 2026-07-06T15:32:00+00:00 → 2026-07-08T07:47:00+00:00
- Roll-up grade: investigate

Core money-trader healthy and trading (Bybit real + Alpaca real reachable, heartbeat live). Two infra failures found: (1) the IB ib_paper execution client wedged on account_warmup_timeout since the 07:08 restart — MES/MGC/MHG dark — REMEDIATED live this session via vm-ib-gateway-recover; (2) the trainer VM is SSH-unreachable (down/hung post-OOM), so ML training is offline and sweeps are 29d stale — needs an operator OCI reboot. Real-money is roughly flat in $ over 30d but -12.2R, with ict_scalp_5m (-11.4R) the clear drag. Since-last real window: 2 trades, both losses (-$5.20).

## P&L by class
- **real**: window $-5.20 (prior +$10.27, down)
- **paper**: window +$362.83 (prior +$246.00, up)
- **prop**: window +$0.00 (prior $-71.66, up)

## Operator priorities
1. Trainer VM down — reboot/re-provision (OCI console) — 158.178.209.121 SSH-unreachable (banner timeout) x2; ML training offline, sweeps 29d stale; probable hung box post the 07-05 OOM-kill. SSH-dead blocks the relay, so needs an OCI-console reboot. Live inference unaffected. (MB-20260705-TRAINER-OOM escalated.)
2. IB exec wedge remediated — verify breaker cleared + harden warm-up — ib_paper exec client wedged on account_warmup_timeout (9 fails, no OK since 07:08). Drove vm-ib-gateway-recover live (login OK). Verify MES/MGC/MHG resumed; consider self-heal on N consecutive warmup_timeouts so it doesn't need a manual gateway restart (BL-20260708-IB-WARMUP-WEDGE-RECUR).
3. Review ict_scalp_5m (+ htf_pullback_trend_2h) for demote/tune — Real-money 30d: ict_scalp_5m -11.35R (9 trades, wins small/losses big), htf_pullback_trend_2h -2.14R (0% WR/3). Dominates the -12.2R book drag; ML setup-quality heads also score ict_scalp low. Tier-3 strategy change — operator decision. (PB-20260630-ICTSCALP-DEGRADE.)
4. Clean up reconciler/orphan artifacts polluting paper + prop — SLV orphan_adopt duplicate -693.6 closes + an 8-trade 14:15:56Z mass-reconcile batch skew paper metrics; 2 prop packages (sol_prop/eth_prop) mis-marked 'orphaned' by the package watchdog. Bucket/exclude artifacts (PB-20260626-ARTIFACT-BUCKETS, BL-20260705-SHADOW-PKG-ORPHAN-STATUS).
5. Live-VM health checks git_drift + accounts_api failing — 5/7 health checks ok. git_drift (worktree vs main) + accounts_api (counts down/shelved accounts — worsened by the IB exec-down). Reconcile git state; fix the shelved-account overcount (BL-20260705-HEALTHCHECK-SHELVED-ACCOUNTS).

## Review coverage
- Strategy promotion: Mostly HOLD. ict_scalp_5m + htf_pullback_trend_2h are demote/tune candidates on 30d R-metrics. Formal M7 review packets were NOT re-generated this run (packet generation is trainer-adjacent tooling and the trainer VM is down); stance is from live /performance R-metrics.
- ML training health: unavailable: trainer VM SSH-unreachable — no registry/cycle/dataset-build verification. Live shadow inference healthy (heads accruing, advisory scoring). Escalated MB-20260705-TRAINER-OOM to operator.
- Soak `shadow regime heads (btc/eth/sol 5m/15m)`: accruing — 190-218 obs each, last_seen ~07:33-07:36Z; advisory btc-regime-15m-lgbm-v2 scoring live.
- Soak `fc-geometry soak`: accruing — 6 records; fc coverage low (1/6 fc_present) — placed SL/TP + decision-time fc snapshot per opening order.
- Soak `exit-ladder soak`: accruing — 20 records, differs_from_single_target=false throughout (n_rungs 0).
- Soak `allocator soak (M18)`: accruing — 20 records; disagreements+regret logged (GLD 1h-long vs 1d-short, SOL); observe-only, routing unchanged.
- Soak `training-side dataset builds`: stalled — BLOCKED — trainer VM down; no new dataset/training work landing.
- 🚩 IB ib_paper execution client wedged (breaker_open, account_warmup_timeout, 9 fails, no OK since 07:08 restart) — MES/MGC/MHG dark. REMEDIATED live via vm-ib-gateway-recover.
- 🚩 Trainer VM 158.178.209.121 DOWN/unreachable (SSH banner timeout x2) — ML training offline, sweeps 29d stale; needs operator OCI reboot.
- 🚩 Real-money 30d negative in R: -12.24R (expectancyR -0.64), ict_scalp_5m -11.35R the dominant drag + htf_pullback_trend_2h -2.14R.
- 🚩 Paper/prop data-quality: SLV orphan_adopt duplicate -693.6 closes, 8-trade 14:15:56Z mass-reconcile batch, 2 prop packages mis-marked 'orphaned'.
- 🚩 Live-VM health checks git_drift + accounts_api FAILING (5/7 ok).
- 🚩 /api/pnl/history and /api/bot/shadow/drift returned fetch_failed this run (minor observability gap).

## Monitoring (soaking / awaiting decision)
- `MB-20260705-FC-ADVISORY-READINESS` [ml · soaking] fc BTC+ETH 15m heads at shadow soaking toward the fc->advisory Tier-3 gate. (next: soak-days + score-sanity criteria met)
- `MB-20260628-REGIME-SOAK-READINESS` [ml · awaiting-data] ETH/SOL 15m regime heads soak->advisory readiness. (next: RG4 re-check on fixed data)
- `PB-20260617-002` [performance · awaiting-decision] Graduate the ExitPlan ladder to the real exit (P4) — soak shows ladder==single-target so far. (next: backtest gate + operator go)
- `M18-allocator` [performance · awaiting-decision] Allocator soak accruing (regret logged, disagrees on GLD/SOL); P2 parked pending a proven P_win input. (next: operator go / P_win ranker)
- `PB-20260628-001` [performance · awaiting-data] Confirm small-ticket real-money orders actually place + fill. (next: next real-money fills)
- `BL-20260624-MHG-CLOSE-CONFIRM-VERIFY` [health · verify] Verify IB close-confirm/close-retry-cooldown holds — blocked until IB stably back after this recover. (next: next review with IB healthy)

_report_id RPT-20260708-074700-since-last_