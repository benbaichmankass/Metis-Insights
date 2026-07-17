# System report — daily

- Generated: 2026-07-17T05:56:00+00:00
- Window: 2026-07-16T06:12:00+00:00 → 2026-07-17T05:56:00+00:00
- Roll-up grade: investigate

On REAL money yesterday (2026-07-16) was a -$5.54 day (3 closed, 1 win) — small, because bybit_2 is barely sized. The alarming red is on the PAPER book (the fleet at real size): -$1,485 (07-14), -$4,362 (07-15), then +$895 (07-16). The worst day was 07-15, not 07-16. Root cause of the bleed is structural, not a one-off: the losing trades are low-confidence '*_pullback_2h' longs entered because ADX read the tape as 'trending' — but ADX measures trend STRENGTH, not DIRECTION, so a hard sell-off looks identical to a rally. The regime router that is supposed to catch this covers only the 6 original BTC strategies; the ~38-strategy alt/equity/futures roster (every pullback_2h bleeder included) has no regime cells and trades permissive-ON. The one ML layer that flagged these setups as weak is shadow-only and ignored by design, and the trainer that would promote it is idle.

## P&L by class
- **real**: window $-10.25 (prior —, down)
- **paper**: window +$895.14 (prior $-4,361.53, up)
- **prop**: window — (prior —, flat)

## Operator priorities
1. Regime router governs only 6 of 44 live strategies — extend coverage or gate the pullback_2h family — config/regime_policy.yaml only names the original BTC set (trend_donchian, squeeze_breakout_4h, htf_pullback_trend_2h, fade_breakout_4h, fvg_range_15m, vwap). The 38 newer alt/equity/futures strategies — incl. every *_pullback_2h that bled this week — resolve to permissive-ON with zero regime protection. Confirmed live: only 1 hard-gate row fired across all of 2026-07-16. Author OFF/weight cells for the alt roster (backtest-gated) or demote the pullback_2h family to shadow.
2. ADX regime is direction-blind — add a trend-DIRECTION filter — detect_regime() classifies chop/transitional/trending purely on ADX-14 magnitude. A strong DOWNtrend has high ADX too, so long-only pullback-buyers fire 'buy the dip' into a sell-off. Every executed real loss this window was a pullback-long stopped out in an ADX-'trending' but falling market. Add a directional gate (e.g. midline slope / DI+ vs DI- sign) so long pullbacks are suppressed when the trend is down.
3. Re-enable the trainer training cycle — ict-trainer.service is inactive/disabled and 0 cycles ran in 24h. Dataset builds + DB sync work, but nothing promotes models — 1 advisory vs 28 shadow. The setup-quality models that would have down-weighted yesterday's setups are stranded at shadow. Re-enable the timer and unblock the shadow->advisory live_agreement gate (MB-20260626-003).
4. Demote ada_pullback_2h (review the whole *_pullback_2h family) — ada_pullback_2h is the biggest loser on both books; the pullback_2h family is low-edge (conf 0.30-0.56) and direction-blind. Demote ada to shadow now; put eth/xrp/avax_pullback_2h on notice pending a direction-aware entry fix.
5. Consider enabling FLIP_CONFIDENCE_THRESHOLD for genuine reversals — FLIP_POLICY=hold (correct for churn) refuses an opposing signal and rides the wrong-way position to its stop — exactly the failure mode in a real regime reversal. FLIP_CONFIDENCE_THRESHOLD (the override that lets a high-conviction opposing signal exit early) is 0.0 = disabled. Backtest a small positive threshold so a strong reversal can cut a losing hold.

## Review coverage
- Strategy promotion: Nothing ready to PROMOTE. Real-money bright spot HOLD: trend_donchian (only winner this window, +0.50). Primary structural issue is not a single strategy but that the regime router covers only 6 of 44 live strategies, so demote/kill is a stop-gap; the fix is regime coverage + a direction filter.
- ML training health: Dataset builds (68 ok/24h) and live->trainer DB sync (05:00) are healthy, but the training SERVICE is disabled and no cycles ran in 24h, so the shadow->advisory promotion pipeline is starved (1 advisory / 28 shadow / 53 candidate).
- Soak `regime heads (btc/eth/sol)`: soaked-but-blocked — met their time-in-shadow but shadow->advisory blocked on live_agreement gate (MB-20260626-003).
- Soak `setup-quality shadow models`: accruing-but-ignored — actively scoring live setups (negative on yesterday's losers) but observe-only; no path to influence while promotion is stalled.
- Soak `exit-ladder / conviction soaks`: unavailable: not pulled this run — observe-only logs; no live impact.
- 🚩 Regime router covers only ~6 of 44 live strategies; the alt/equity/futures roster trades with no regime protection (1 hard-gate row all day).
- 🚩 ADX regime classifier is direction-blind — long pullback-buyers fire in strong downtrends.
- 🚩 Trainer training cycles idle (service disabled, 0/24h); model promotion starved.
- 🚩 Paper fleet bled -$5.8k over 07-14/07-15 (the fleet-at-scale read); real money spared only by minimal sizing.
- 🚩 hold-policy rides wrong-way positions to stop; the reversal-exit override (FLIP_CONFIDENCE_THRESHOLD) is disabled.

## Monitoring (soaking / awaiting decision)
- `MB-20260626-003` [ml · awaiting-decision] Regime-head shadow->advisory blocked on the live_agreement gate; heads have soaked their time but can't clear it. Directly limits regime coverage. (next: operator decision on gate design)
- `TRAINER-IDLE-20260717` [ml · awaiting-decision] ict-trainer.service disabled; 0 cycles/24h. Re-enable to resume promotions. (next: operator re-enable)

_report_id RPT-20260717-055600-daily_