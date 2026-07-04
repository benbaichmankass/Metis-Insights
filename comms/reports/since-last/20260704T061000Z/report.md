# System report — since-last

- Generated: 2026-07-04T06:10:00+00:00
- Window: 2026-07-02T06:17:00+00:00 → 2026-07-04T06:10:00+00:00
- Roll-up grade: caution

First prop WINNER banked: Breakout ETH long closed at TP for +297.12 net (~2.2R) with the manual-bridge report loop working end to end. Claude decision-grade coverage RESTORED to 100% (161 backfilled; 2,727/2,727). IB gateway was dark most of 07-03 (recovered 17:01Z; benign-diag verdict, definitive check at Monday's CME open). Real-money: no closed trades; one D-grade XRP long (~$33) opened on bybit_2. Paper: -531.87 realised, all intent_reduce churn under the #5101 soak watch. alpaca_live confirmed structurally size-0 on its $150 balance — operator accepted as-is. xauusd_trend_1h disabled (silent-enabled debris).

## P&L by class
- **real**: window — (prior —, —)
- **paper**: window — (prior —, —)
- **prop**: window — (prior —, —)

## Operator priorities
-. Overwrite the seeded prop balance with terminal truth — After the +297 ETH win, send `bal <balance> <equity> [realized_today]` on the prop bot (or the dashboard Prop form). The first-ever prop_account_status row was seeded from screenshot-derived numbers (balance ~5,215.27) — good enough to arm the rule-distance guard, but the terminal's actual figures should replace it (latest row wins).
-. Monday 07-06 CME open: confirm MES/MGC/MHG trade data flows — The definitive IB-gateway close-out after the 07-03 wedge+recovery: evals for the IB symbols should fetch and fire at the session open. If still dark, escalate the root cause (BL-20260527-003).
-. Repoint the Claude cloud-env DIAG_BASE_URL — It still targets the terminated x86 micro (158.178.210.252), so every live read this session went through the slow GitHub-issue relay. In the Claude Code cloud environment settings, set DIAG_BASE_URL=http://141.145.193.91:8001 (takes effect on the next session).

## Review coverage
- Strategy promotion: No promote/demote gate crossings this window. xauusd_trend_1h explicitly DISABLED (operator, 2026-07-04) — silent-enabled debris on the shelved oanda_practice (zero audit events while loaded); validated XAU edge preserved for re-enable. The M7 per-strategy review-packet matrix was not regenerated (abbreviated review: /health-review + grading backfill + operator follow-ups); next full /system-review runs generate-strategy-review-packets.
- ML training health: Trainer SSH reachability RESTORED after the 07-02 outage (BL-20260702-002 resolved). fc-conditioned head promoted candidate->shadow 07-03 with live soak verified accruing.
- Soak `btc-regime-15m-lgbm-fc-pcv-v1 (shadow, fc-conditioned)`: accruing — promoted 07-03; first predictions non-degenerate (0.51-0.95) with populated fc_* features (verified via shadow_stats + feature_row, relays #5489-#5490)
- Soak `exit-ladder / allocator / conviction soaks`: not re-checked — unavailable: abbreviated review — next /system-review tails runtime_logs/{exit_ladder_soak,allocator_soak,conviction_*}.jsonl
- Soak `intent_reduce churn fix (#5101)`: accruing (adverse signal) — 4 in-window orphaned parent packages + -531.87 paper reduce-leg PnL — the churn is still visible; verify item BL-20260629-INTENT-REDUCE-CHURN-VERIFY stays open
- 🚩 IB gateway dark most of 07-03 (mandatory reachability flag; recovered + verdict recorded)
- 🚩 trainer service exit-15 run
- 🚩 D-grade real-money XRP entry (conf 0.29)
- 🚩 prop rule-distance panel had ZERO snapshots ever (now seeded)

## Monitoring (soaking / awaiting decision)
- `BL-20260623-002` [health · verify] 2026-07-06 CME open: MES/MGC/MHG evals fetch+fire => IB gateway definitively healthy; else escalate root cause (BL-20260527-003).
- `BL-20260703-PROP-STATUS-EMPTY` [health · verify] prop status-request ping first live firing on the next open prop position (shipped PR #5521); operator to overwrite the derived balance seed with terminal `bal` numbers.
- `BL-20260629-INTENT-REDUCE-CHURN-VERIFY` [performance · soak] intent_reduce churn (-531.87 paper this window, orphaned parent packages) — confirm fix #5101 converges.

_report_id RPT-20260704-061000-since-last_