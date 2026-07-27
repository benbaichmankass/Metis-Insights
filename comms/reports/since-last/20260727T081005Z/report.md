# System report — since-last

- Generated: 2026-07-27T08:10:05Z
- Window: 2026-07-26T09:32:00+00:00 → 2026-07-27T08:10:05Z
- Roll-up grade: caution

CAUTION (quiet, plumbing-healthy since-last window; nothing new at money-risk). System restarted cleanly 07:44Z onto 52d51dfe (L3 paper-eval merge); services/DB/accounts all healthy, all live accounts reachable, prop reconcile clean. Real money bled small in a chop regime - 1 in-window close (eth_pullback_2h -$3.75, SL, graded D 'should_skip' @ conf 0.21); 7d real -$17.58, PF 0.12, 1W/7L, all SL-appropriate. Paper soak large & isolated (-$4.1k/24h, big paper notionals). ML lifecycle healthy: 2 trainer cycles/24h completing rc=0, 116 builds OK, shadow soaks accruing. Live flags (none money-risk): 3 IB 'no candle data' ALERT banners (MGC/MHG - draft PR #7701 in flight to reclassify severity); heartbeat 'paused' label is a post-restart transient (trader confirmed ticking); prop account-status 7d stale; MES base stale keeps 4 mes-* datasets empty + mes-regime-1d audit-quarantined; promotion-readiness OOM still blocks automated promote packets. Grading ran (18 rows, B9/C8/D1). Backlogs worked to zero untriaged (H:13 P:1 M:2 re-validated).

## P&L by class
- **real**: window $-3.75 (prior —, down)
- **paper**: window $-4,141.60 (prior —, down)
- **prop**: window +$0.00 (prior +$0.00, flat)

## Operator priorities
1. Review/merge draft PR #7701 (IB no-candle-data alert reclassification) — 3 IB 'no candle data' RuntimeError banners (MGC/MHG) are paging on every gateway cold-flow flap. PR #7701 reclassifies the transient no-data RuntimeError ERROR->WARN (still banner/diag-visible, stops paging); persistent IB outages remain paged by account_reachability_alert. Zero trading-behavior change, 46+13 tests pass. Held for operator review.
2. Execute the protected-main autonomous-commit bypass (operator-approved 07-26) — BL-20260723-WORKFLOW-COMMIT-TO-PROTECTED-MAIN: operator granted an Actions-bot bypass 07-26 to push the data paths (gpu-burst spend ledger + FRED valuation snapshots) to protected main. Awaiting execution (needs a branch-protection setting OR a PAT secret value). Blocks the gpu-ledger persistence AND the M28 valuation-snapshot producer.
3. Governance call: add layer-guard to REQUIRED_CONTEXTS — BL-20260726-LAYER-GUARD-NOT-REQUIRED: the M0a/M0b layering guard enforces its 6 contracts on its own CI job but is NOT a required branch-protection check, so a PR can merge past a broken-layering advisory. Operator decision (could newly-block merges).
4. ruff 0.16 adoption cleanup (operator-decided 07-26) — BL-20260723-RUFF-016: operator decided 07-26 to bump to ruff 0.16 and clear the ~8228 pre-existing residuals (6558 autofixable). Awaiting the cleanup PR(s); the interim <0.16 pin holds CI green meanwhile.
5. MES base staleness keeps blinding the MES ML fleet — BL-20260626-MES-BASE-STALE + BL-20260726-MES-IBKR-PULL: the trainer MES market_raw base is stale, so 4 mes-* datasets build 0 rows and mes-regime-1d-lgbm-v2 is audit-quarantined (dead feature). The MES-IBKR-pull per-timeframe-retry fix (PR #7635) is landed/monitoring; verify a clean pull refreshes the base.

## Review coverage
- Strategy promotion: Mostly HOLD this window. No strategy met a promote gate; the only demotion (sol-regime fc-pcv-v1) already executed 07-26. Automated promotion-readiness packets remain blocked by the oos_edge OOM (weekly-flagged) - promotion decisions are running manually off drift + gate-checks. Next scheduled ML gate: fc-pcv-v2 advisory swap ~07-28.
- ML training health: Healthy & progressing. Last cycle rc=0 (cycle_end 05:34Z), DB sync live->trainer OK (00:39Z), trainer VM relaxed (mem 238MB used, load 0.14). The trainer-cycle-termination concern (BL-20260717) is NOT reproducing - cycles complete.
- Soak `Shadow regime heads (btc/eth/sol 5m variants) accruing ~5300`: accruing — Shadow regime heads (btc/eth/sol 5m variants) accruing ~5300-5700 preds each, last_seen 07:54Z - actively scoring, no stall.
- Soak `conviction-meta-v1 + setup-quality heads 5263 preds each, la`: accruing — conviction-meta-v1 + setup-quality heads 5263 preds each, last_seen 05:57Z - accruing.
- Soak `fc-geometry soak 146 rows, exit-ladder soak 267 rows`: growing — fc-geometry soak 146 rows, exit-ladder soak 267 rows - growing steadily.
- Soak `fc-pcv v2 advisory-candidate soak maturing toward the ~07-28`: maturing — fc-pcv v2 advisory-candidate soak maturing toward the ~07-28 gate-check.
- Soak `No soak stalled; none met-but-unactioned (fc-pcv-v2 is the n`: maturing — No soak stalled; none met-but-unactioned (fc-pcv-v2 is the next due, still maturing).
- 🚩 3 IB 'no candle data' ALERT banners active (MGC/MHG, IBKR) - known cold-flow flap; draft PR #7701 in flight to reclassify severity. Not a money-loss risk; IB clients confirmed connected/not-wedged.
- 🚩 Real-money 7d PF 0.12 (1W/7L, -7.58) - chop-regime entry-edge whipsaw; the one in-window close graded D (should_skip, conf 0.21). Small dollars, SL-appropriate. Watch; addressed by ongoing regime/vol-gate work.
- 🚩 Heartbeat 'paused' label - post-restart transient (services restarted 07:44:30Z, trader confirmed ticking tick_age 75s). Verify it clears next cycle.
- 🚩 MES base stale -> 4 mes-* datasets empty + mes-regime-1d audit-quarantined; MES ML fleet blinded (BL-20260626).
- 🚩 Trainer promotion-readiness oos_edge OOM still blocks automated readiness packets (weekly-flagged; promote decisions running manually).
- 🚩 Prop account-status 7d stale (last 07-20); reconcile clean, no open prop position - minor.

## Monitoring (soaking / awaiting decision)
- `BL-20260717-TRAINER-CYCLE-TERM-AT-START` [health · verify] Trainer cycles now completing (2/24h, rc=0) - the ~90s-termination symptom is NOT reproducing. Resolve after one more clean window. (next: next daily trainer cycle (00:03Z))
- `PB-20260620-001` [performance · verify] 6 intraday ETF cells confirmed producing live paper fills (alpaca_paper 6 open +35/1h). Resolve after one more window of sustained fills. (next: next since-last review)
- `MB-20260721-FCPCV-V2-SOAK` [ml · awaiting-data] btc/sol-regime-15m fc-pcv-v2 fresh-data siblings soaking; gate-check + swap for the frozen v1 advisory heads when mature. (next: ~2026-07-28 (soak maturity))
- `REGIME_ML_VERDICT-SOL-ADVISORY` [ml · awaiting-data] SOL has NO advisory head after the 07-26 drift-demote of fc-pcv-v1; v2 sibling to restore it ~07-28, then SOL trend_vol cell authoring unblocks. (next: ~2026-07-28)
- `BL-20260726-MES-IBKR-PULL-SERVICE-FAILED` [health · verify] Per-timeframe-retry fix landed (PR #7635); verify a clean pull refreshes the stale MES base (4 mes-* datasets still empty this cycle). (next: next trainer cycle)
- `PROP-STATUS-STALE` [health · verify] Prop account-status snapshot 7d stale (last 07-20); no open prop position. Watch for a fresh balance report on next prop activity. (next: next prop fill/status report)

_report_id RPT-20260727-081005-since-last_