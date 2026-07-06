# System report — weekly

- Generated: 2026-07-06T15:32:00+00:00
- Window: 2026-06-29T15:32:00+00:00 → 2026-07-06T15:32:00+00:00
- Roll-up grade: caution

First-ever WEEKLY-window /system-review (no prior weekly report to diff against). The trader restarted recently (bot_uptime_s=1474 at snapshot time, git_sha 9cc460bf matching the latest merged commit) and is heartbeating normally. All probeable real-money/paper accounts (bybit_1, bybit_2, alpaca_paper, alpaca_options_paper, alpaca_live, oanda_practice) read reachable; ib_paper is declared live but its broker-side position probe returned null at snapshot time -- flagged, not yet confirmed as a sustained outage (see flags). The relay allowlist was missing exit-ladder/allocator/fc-geometry soak endpoints and the pnl/history alias -- fixed in this branch, but since the vm-diag-snapshot workflow runs off main, those three soaks could not be read this session (fix needs to merge first). Shadow-model fleet: 37 (model_id, stage) combinations logging, one already at advisory (btc-regime-15m-lgbm-v2) showing stable/no-drift scores -- healthy, hold. The highest-volume shadow candidate (btc-regime-5m-lgbm-v2, 14.2k observations) shows SIGNIFICANT KS/moderate PSI score drift week-over-week -- do not promote until the drift is investigated. Conviction-sizing (Design-B) soak is accruing normally in annotate-only mode. Full order-package grading and per-trade dossiers could not be completed this session -- the /api/diag/journal trades pull truncates at 55KB per relay message vs a 650KB payload, so the grader script never received the full closed-trade set. ML training-cycle health from the trainer VM could not be read (sqlite3 CLI absent, and the python3 sqlite3 fallback hit 'unable to open database file' -- a path/permissions issue on the trainer VM, not yet root-caused).

## P&L by class
- **real**: window — (prior —, flat)
- **paper**: window — (prior —, flat)
- **prop**: window — (prior —, flat)

## Operator priorities
1. Merge this PR (relay allowlist fix) — vm-diag-snapshot allowlist was missing exit-ladder/allocator/fc-geometry soak + pnl/history; fixed locally, needs merge to take effect for future soak reads.
2. Investigate btc-regime-5m-lgbm-v2 score drift before promotion — 14.2k-observation shadow candidate shows significant KS/moderate PSI drift week-over-week -- do not promote on volume alone.
3. Confirm ib_paper reachability — Declared-live account read positions=null at probe time; could not cross-check the reachability latch state this session.
4. Fix the diag-relay trades-pull truncation blocking weekly grading — /api/diag/journal?table=trades truncates at 55KB vs a 650KB payload -- no closed trades were graded this week as a result.

## Review coverage
- Strategy promotion: Not freshly assessed with per-strategy M7 review-packet pulls this session (time spent on the ML soak/promotion mandate + account-reachability + grading gaps per the operator's explicit ask). No KILL/DEMOTE signal surfaced in what WAS pulled (heartbeat healthy, no strategy-silence visible in the truncated audit tail). All HOLD by default; deferred to next review with explicit per-strategy pulls.
- ML training health: Partial: journalctl tail shows clean manifest_ok/manifest_audit_flagged rows through 2026-07-05 02:30Z and a successful live-DB pull 2026-07-06T04:00Z (420MB journal, 788871 audit lines). Could not confirm the 2026-07-05 OOM-containment fix (MemoryHigh/MemoryMax) survived a full nightly cycle post-fix -- next timer fire ~00:57Z is the check.
- Soak `shadow_models (btc-regime-15m-lgbm-v2, advisory)`: accruing — n=722 current-window/951 lifetime; drift minor/no_change -- healthy, hold at advisory.
- Soak `shadow_models (btc-regime-5m-lgbm-v2, shadow)`: accruing — n=1986 current-window/14202 lifetime; drift significant/moderate -- accruing volume but do not promote yet.
- Soak `conviction_sizing (Design-B, annotate-only)`: accruing — 142KB log, oldest record 2026-06-18, still logging would_be_size decisions.
- Soak `exit_ladder`: stalled — Not a real soak stall -- relay-allowlist gap blocked the read this session (fix committed, needs merge).
- Soak `allocator`: stalled — Same relay-allowlist gap as exit_ladder.
- Soak `fc_geometry`: stalled — Same relay-allowlist gap as exit_ladder.
- 🚩 ib_paper is a declared-live account (status.live.ib_paper=true) whose broker-side exchange_positions probe returned null at snapshot time (2026-07-06T15:33Z) -- could not cross-check against the account-reachability latch state (runtime_logs/account_reachability_alert_state.json) this session, so this is raised as an open flag, not a confirmed sustained DOWN.
- 🚩 btc-regime-5m-lgbm-v2 (shadow, highest observation count of any non-advisory model) shows significant score-distribution drift week-over-week -- do not let volume alone drive a promotion decision here.
- 🚩 The vm-diag-snapshot relay allowlist gap (exit-ladder/allocator/fc-geometry soak + pnl/history) blocked three of the mandatory soak reads this session -- fixed in this branch, needs merge to take effect.

## Monitoring (soaking / awaiting decision)
- `btc-regime-5m-lgbm-v2-drift` [ml · soaking] Significant KS/moderate PSI score drift week-over-week on the highest-volume shadow candidate; re-check drift verdict next review before considering promotion. (next: next /system-review or /ml-review drift re-pull)
- `ib_paper-reachability` [health · awaiting-data] exchange_positions probe returned null for ib_paper at snapshot time; could not confirm against the reachability-alert latch state this session. (next: next diag pull of runtime_logs/account_reachability_alert_state.json)
- `conviction-sizing-soak` [ml · soaking] Design-B conviction-sizing soak (annotate-only) accruing since 2026-06-18, 142KB log, no gate-check due yet per CLAUDE.md (symmetric apply already failed the backtest gate; reductive apply not yet evaluated). (next: next backtest A/B evidence run for reductive conviction sizing)

_report_id RPT-20260706-153200-weekly_