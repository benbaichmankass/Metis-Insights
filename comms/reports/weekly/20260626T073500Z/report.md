# System report — weekly

- Generated: 2026-06-26T07:35:00Z
- Window: 2026-06-19T07:28:41Z → 2026-06-26T07:35:00Z
- Roll-up grade: caution

Trader healthy and ticking; ML training cycle ran clean today (00:28-01:16Z, rc=0) with shadow soaks accruing live across 29 model/stage heads (2 advisory). The one real flag: alpaca_live is STILL api_ok=false / balance null AFTER the 2026-06-26 key rotation (alpaca_paper is fine), so live ETF signals keep refusing zero_balance. Real-money 7d is -$8.82 over 7 trades (tiny ~$95 bybit_2 book; ict_scalp_5m -$7.38 the bleeder). The paper book's -$12,070 headline is ARTIFACT-INFLATED — the new bucketing tool scores 0/48 records gradeable in the recent window (the big trend_donchian -$7.4k / mhg_pullback -$5.2k 'losses' are orphan/reduce/MHG-flap artifacts, not genuine round-trips).

## P&L by class
- **real**: window $-8.82 (prior —, down)
- **paper**: window $-12,070.07 (prior —, down)
- **prop**: window — (prior —, —)

## Operator priorities
-. alpaca_live still down after key rotation (api_ok=false, balance null) — Confirm the new ALPACA_API_KEY_ID/SECRET reached the VM .env (sync-vm-secrets) and ict-web-api + trader were restarted to pick them up; then re-check /api/bot/accounts/balances shows api_ok=true. Owned by the key-rotation session. Until then live ETF signals refuse zero_balance.
-. Paper performance is artifact-inflated — apply the bucket-A filter before judging strategies — The -$12k paper headline is dominated by intent_reduce/orphan/MHG-flap artifacts (bucketing tool: 0/48 gradeable). Merge PR #4660 and run the performance-review pre-filter so per-strategy win-rate/expectancy is computed over genuine round-trips only. trend_donchian -$7.4k and mhg_pullback -$5.2k are flap artifacts, not strategy losses.
-. ict_scalp_5m is the worst REAL-money strategy (7d -$7.38, 40% win) — On the live ~$95 bybit_2 book ict_scalp_5m drove essentially all of the -$8.82 7d loss. Small absolute dollars but the only genuine real-money bleeder. Candidate for a TUNE/DEMOTE review once a fuller window accrues.

## Review coverage
- Strategy promotion: No strategy is promotion-ready this window. Paper strategy PnL is NOT a valid promotion signal right now because it is artifact-inflated (0/48 gradeable) — promotion/demotion calls must wait for the bucket-A-filtered scorecard (PR #4660). Real-money sample is tiny (7 trades). ict_scalp_5m flagged for a tune/watch.
- ML training health: ict-trainer.service ran a clean full cycle 2026-06-26 00:28->01:16Z (overall_rc=0, calibrators_ok, publish_post_ok). ~30 manifests trained OK; 3 MES manifests skipped 'empty_dataset' (mes-execution-quality / mes-setup-quality / mes-trade-outcome-winrate — known MES closed-trade sparsity). No failing/stuck cycle.
- Soak `shadow predictions (29 model/stage heads)`: accruing — all last_seen current (07:2xZ); 2 advisory heads order-influencing; new heads (conviction-meta-v1, eth cross-asset) started 06-23/06-24 and accruing
- Soak `btc regime baselines`: watch — score_mean ~0.97 (saturated-looking); MB-20260623-001 degeneracy item marked fixed via #4602 — confirm yz/lgbm-v2 variants stay healthier
- Soak `conviction (sizing/arbitration)`: accruing — conviction-meta-v1 1340 recs since 06-23; observe-only, not order-influencing
- 🚩 alpaca_live STILL api_ok=false / balance null AFTER the 2026-06-26 key rotation — live ETF signals refusing zero_balance; needs key propagation + web-api restart (owned by key-rotation session).
- 🚩 Paper-book PnL (-$12k/7d) is artifact-inflated (0/48 gradeable) — do NOT read raw per-strategy paper PnL as strategy edge until the bucket-A filter is applied.

## Monitoring (soaking / awaiting decision)
- `PB-20260626-ARTIFACT-BUCKETS` [performance · verify] Apply the A/B/C bucketing pre-filter in a live performance-review run. (next: next performance-review / PR #4660 merge)
- `BL-20260626-ALPACA-WHOLEUNIT-LATENT` [health · awaiting-data] Confirm whole-share sizing takes 1 share (not refuse) once alpaca_live is funded with working keys. (next: alpaca_live api_ok=true + funded)

_report_id RPT-20260626-073500-weekly_