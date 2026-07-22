# System report — since-last

- Generated: 2026-07-22T18:46:51+00:00
- Window: 2026-07-20T08:15:00+00:00 → 2026-07-22T18:46:51+00:00
- Roll-up grade: caution

Quiet, plumbing-healthy 58h window (all services up, all 7 live broker accounts reachable, no breach) but three real signals: the diversified paper book's decay is now confirmed genuine (-$16.4K swing since 07-07, 2 cells 3-review-confirmed for demotion), 2 of 3 live-influencing ML heads show a known frozen-dataset symptom pending a pre-approved 07-28 swap, and a chronic >3wk-old infra bug plus a 58h-stale prop snapshot need operator follow-through. All three sub-review backlogs fully drained (79/79, 55/55, 41/41).

## P&L by class
- **real**: window $-4.51 (prior +$0.00, flat)
- **paper**: window $-20,967.38 (prior —, unavailable: prior-window trades/closed pull (since=2026-07-17T21:55, limit=500) returned fetch_failed — the same relay query-timeout bug performance-review's anomalies[] flagged (reproduced independently this session))
- **prop**: window +$0.00 (prior $-96.94, up)

## Operator priorities
1. Prioritize root-cause fix for the >3-week-old /dev/null OCI-agent clobbering bug — BL-20260629-DEVNULL-OCI-SOURCE-KILL open since 2026-06-29, hit a 3rd surface on 07-21. Self-healing but un-root-caused; flagged per the operator's alarm-fatigue directive — this is itself now a P1.
2. Review 2 Tier-3 DEMOTE_SHADOW proposals: avax_pullback_2h, sol_pullback_2h — 3rd consecutive review confirms both as persistent laggards in the diversified paper book (-$3976.67/14.3%WR and -$2646.02/0%WR respectively). n is thin per-cell; recommend a 4-6wk shadow re-eval, not a permanent kill.
3. Get a fresh breakout_1 prop account-status snapshot — Last snapshot is 58h+ stale against only a $125.61 cushion to the static-DD floor. The bot's own PROP_STATUS_REQUEST nudge should be re-asking; no fresh balance has landed.
4. No action needed yet on the 2 fc-pcv advisory heads (BTC, SOL) showing frozen-dataset symptoms — stage-guard proposes demote on both, but evidence is thin (BTC AUC=0.25 on n=4; SOL drift=significant, no attribution yet). Root cause already tracked (MB-20260720-FCPCV-RETRAIN-NOOP); pre-approved fresh-data v2 swap due ~2026-07-28. Hold, watch closely.
5. Two bot-side API findings routed to health-review backlog (not yet triaged there) — trades_closed.py collapses sl_cross/tp_cross/exit_head/stale_stop into a generic 'other' closeReason (data loss); GET /api/bot/trades/closed?limit>=500 reliably times out (reproduced 3x+ this session, incl. independently by this master session). Both need a health-review look before a code fix.

## Review coverage
- Strategy promotion: All other strategies HOLD this window (no M7 packet pulled independently by the master session — strategy_promotion status is the performance-review's own read, not re-derived here). Two Tier-3 demote proposals pending operator approval; everything else stands pat.
- ML training health: 8 training cycles ran since 2026-07-20T08:15Z, 0 manifest failures, 0 manifest-quarantine trips. Registry 92 models (61 candidate/27 shadow/3 advisory/1 research_only), matching the live-VM mirror exactly.
- Soak `btc-regime-15m-lgbm-fc-pcv-v1 (advisory)`: accruing — 189 predictions; live AUC read thin (n=4) and inverted — watching, not yet actionable.
- Soak `sol-regime-15m-lgbm-fc-pcv-v1 (advisory)`: accruing — 183 predictions; drift verdict=significant, no attribution rows yet.
- Soak `btc/sol-regime-15m-lgbm-fc-pcv-v2 (shadow, fresh-data siblings)`: accruing — 148/144 predictions in ~1.5 days, on pace for the ~07-28 swap decision.
- Soak `conviction-meta-v1 (shadow)`: stalled — n_eval stuck at 69 for weeks (design bar >=150); live score variance widened >20x this session — gate_met: false, and not accruing meaningfully toward it.
- Soak `eth-regime-15m-lgbm-xasset-v1 (shadow)`: accruing — 522 predictions in ~2 days since the 2026-07-20 restart post-dead-feature-fix; healthy re-soak.
- Soak `diversified paper-book cohort (10-cell, bybit_1)`: gate_met (decay confirmed) — decay_flag fired for the first time this session on the FIRST real snapshot in 2+ weeks (a tracker bug silently blocked every prior attempt) — -$16,381.51 swing since 07-07. 2 cells (avax/sol_pullback_2h) now 3-review-confirmed laggards — see tier3_proposals_pending.
- 🚩 The /dev/null OCI-agent clobbering bug is now >3 weeks open with no root-cause fix, hitting a 3rd surface on 07-21 — per the operator's 2026-07-19 alarm-fatigue directive, THIS is now flagged as its own priority item, not just re-noted.
- 🚩 The diversified paper-book decay_flag fired for the first time (this session's first real tracker snapshot in 2+ weeks, after fixing a silent tooling bug) — a genuine -$16,381.51 swing since 07-07, not noise.
- 🚩 2 of 3 live-influencing (advisory) ML heads show real symptoms of a known frozen-dataset condition — not yet actionable (thin n / drift-only) but both are order-influencing and being watched closely pending the pre-approved ~2026-07-28 swap.
- 🚩 breakout_1's prop account-status snapshot is 58h+ stale against a thin $125.61 cushion to its drawdown floor.
- 🚩 A real, reproducible relay/API query-timeout bug (GET /api/bot/trades/closed at limit>=500) was independently confirmed a 4th time by this master-review session's own report-data-gathering pull — recommend a health-review follow-up on the underlying query.

## Monitoring (soaking / awaiting decision)
- `MB-20260721-FCPCV-V2-SOAK` [ml · soaking] Fresh-data v2 siblings (BTC + SOL fc-pcv) accruing on schedule (~148/144 predictions in 1.5 days); swap decision due once soak matures. (next: ~2026-07-28 decision point)
- `MB-20260616-CONVICTION-P4-SIZING` [ml · stalled] conviction-meta-v1 stuck at n_eval=69 (design bar >=150) for weeks; live score variance widened >20x this session. Blocking both P4-sizing and P5-fusion. (next: n_eval >= 150 with non-degenerate purged-CV macro_f1)
- `SRQ-20260618-002` [performance · awaiting-decision] avax_pullback_2h / sol_pullback_2h DEMOTE_SHADOW proposals escalated this run after a 3rd consecutive confirming review. (next: operator go/no-go)
- `SRQ-20260715-REALMONEY-ALLOC-BENCHMARK` [performance · awaiting-data] alpaca_live ETF sleeve does not clear the real-money allocation bar this window ($-based read only, no R-normalization yet). Recommend a longer, R-normalized re-run before the next verdict. (next: next /performance-review with a risk-normalized pull)
- `BL-20260629-DEVNULL-OCI-SOURCE-KILL` [health · awaiting-decision] Chronic /dev/null clobbering, self-healing but un-root-caused for >3 weeks; escalated as operator_priorities[1] this run. (next: a dedicated VM-ops root-cause session)

_report_id RPT-20260722-184651-since-last_