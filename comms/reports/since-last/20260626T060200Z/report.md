# System report — since-last

- Generated: 2026-06-26T06:02:00+00:00
- Window: 2026-06-25T05:40:00+00:00 → 2026-06-26T06:02:00+00:00
- Roll-up grade: caution

~24h window. HEALTHY infra: heartbeat up, services up, VM relaxed, clean restart 05:27Z deploying advisory regime-head fix (#4602). REAL-MONEY 0 closes — 9 ETF pullback signals (incl. GLD shorts ADX46-51 conf .80-.97) ALL rejected no_fill (target_qty=0): alpaca_live can't fill. Paper +$197.93/7 (artifact-heavy). ML clean: cycle ran, advisory bug fixed.

## P&L by class
- **real**: window +$0.00 (prior +$0.00, flat)
- **paper**: window +$197.93 (prior +$19.00, up)
- **prop**: window +$0.00 (prior +$0.00, flat)

## Operator priorities
1. Real-money execution blocked: alpaca_live can't fill (creds/dry_run) — All 9 real-money ETF pullback signals rejected no_fill_all_accounts (target_qty=0). Operator switching keys; then I propagate via sync-vm-secrets, verify api_ok=true, and flip alpaca_live live via set-account-mode. Do NOT flip before keys verify.
2. Investigate target_qty=0 sizing across paper AND real-money — Every in-window package (incl. the paper sol close) shows aggregated_target_qty=0. Real -> rejects; paper -> 'closed' via reconciler artifact. Confirm whether real-money is purely alpaca creds or a broader RiskManager sizing-to-zero symptom.
3. Verify advisory regime-head fix in the live decision log — Advisory fix (#4602) deployed 05:27Z. Verify next advisory_decisions.jsonl rows carry excluded_regime populated and no degenerate ~0.98 quorum constant.
4. Treat paper KPIs as artifact-heavy, not a clean strategy read — Paper +$197.93/7 closes is reconciler/netting-artifact-dominated (sol +$141 closed reconciler_filled at target_qty=0; rest xrp_pullback churn). Don't read it as edge.

## Review coverage
- Strategy promotion: All strategies HOLD. No new closed real-money trades to update gates; the ETF pullback cells are signal-healthy but execution-blocked (alpaca_live), which is infra not a strategy-quality demotion. No promote/demote/kill triggered this window.
- ML training health: 1 training cycle ran clean overnight (00:24->01:16Z, rc:0); dataset builds OK; no stuck cycle. Advisory regime-head degeneracy fixed + deployed (#4602).
- Soak `shadow regime heads (17)`: accruing — all sound via replay pre-gate RG3; the advisory bug polluting their track record is fixed (#4602)
- Soak `advisory downsize (post-fix)`: accruing — regime heads now excluded from quorum; verifying excluded_regime populates in advisory_decisions.jsonl
- Soak `replay pre-gate nightly`: accruing — nightly workflow merged this session; awaiting first scheduled run
- Soak `conviction + exit-ladder soaks`: accruing — observe-only; no graduation gate met (PB-20260617-002 exit-ladder not yet accrued)
- 🚩 REAL-MONEY EXECUTION BLOCKED: all 9 real-money ETF pullback signals rejected no_fill_all_accounts (target_qty=0) — alpaca_live can't fill (creds/dry_run). Quality signals (GLD shorts ADX 46-51, conf .80-.97) lost.
- 🚩 target_qty=0 also on the paper sol package (still 'closed' via reconciler artifact +$141) — sizing-to-zero + reconciler-artifact pattern spans both funding classes.
- 🚩 Paper KPIs are reconciler-artifact-heavy — not a clean strategy read this window.

## Monitoring (soaking / awaiting decision)
- `MB-20260625-ADVISORY-VERIFY` [ml · verify] Advisory regime-head exclusion fix (#4602) deployed 05:27Z; confirm live decision log shows excluded_regime populated. (next: advisory_decisions.jsonl rows with excluded_regime non-empty)
- `RG-NIGHTLY` [ml · soaking] Replay pre-gate nightly workflow merged this session; awaiting first scheduled run + committed report. (next: first nightly replay-pregate report committed)
- `BL-20260625-ALPACA-KEYS` [performance · awaiting-decision] alpaca_live real-money fills blocked (no_fill/target_qty=0); operator switching keys. (next: operator rotates keys -> sync-vm-secrets -> api_ok=true)
- `MB-20260601-002` [ml · awaiting-data] regime-classifier-baseline-v0 collapses (f1_volatile=0) — degenerate baseline, shadow-only; awaiting a retrain experiment. (next: retrain manifest + replay-pregate pass)

_report_id RPT-20260626-060200-since-last_