# Full-system audit report

- Generated: 2026-08-13T17:30:00+00:00
- Window: 2026-08-13T00:00:00+00:00 → 2026-08-13T17:30:00+00:00
- Roll-up grade: attention

Combined /full-system-audit + /system-review. THE FINDING: on bybit_2 (REAL MONEY) Bybit returns every ACCOUNT-level margin aggregate as the empty string, so totalAvailableBalance was unusable and the margin pre-flight cap silently fell back to TOTAL EQUITY — counting initial margin already pledged to open positions as free. NINE venue refusals (ErrCode 110007) across 3 strategies and 2 symbols = 30% of that account's orders in the window, and LATENT not cleared. Found only by widening a filter from one strategy to the whole account; every prior read was structurally incapable of seeing it. FIXED Tier-3/Tier-2 with explicit operator approval (#9039): available margin now derives from the USDT COIN block (equity - totalPositionIM - totalOrderIM, all broker-reported), the equity fallback subtracts ESTIMATED pledged margin, and every rejection row is stamped with its basis. Validated four ways before shipping (0.05% against an independent journal reconstruction; 0.22% against the venue's own posIM; inside a contradiction bound off Bybit's own refusal; replays the rejection window to 0.008 BTC, the size the account was already filling at). STATUS: DEPLOYED (5bbb3416) BUT LIVE-UNVERIFIED — bybit_2's newest journal row predates the deploy by two days, so nothing carries a margin_basis stamp yet. ALSO SHIPPED: the AVAX venue-max clamp (filed headline DISPROVED), the VM-runner sudoers purge on a root-verified post-state, a 14-day test time bomb reddening main, and a pytest-run short-circuit that let a PR merge having executed ZERO tests. HONESTY: CI caught 8 failures the session missed, two serious — an UnboundLocalError on the exact failure path the new stamp exists to record, and a RE-CREATED halt vector that would refuse every trade on an account whose journal over-reports open notional (451x measured under netting). Root cause of the misses: a pytest -k filter that excluded every failing test. NOT DONE: the doc-stale backfills.


## Operator priorities
1. bybit_2 margin fix is DEPLOYED but LIVE-UNVERIFIED — one read closes it — No bybit_2 dispatch since 2026-08-11, so no row carries a margin_basis stamp. Query trades for bybit_2 for a row created after 2026-08-13T17:00Z and read notes.margin_basis: kind=coin_derived (basis ~226) CONFIRMS; equity_unadjusted/equity_minus_pledged/equity_pledged_implausible means the coin-block rung is INERT and the row REOPENS at high. Do NOT substitute a journalctl tail — a 400-line tail covers 43 SECONDS.
2. BL-20260701-BYBIT-AVAILABLE-FIELD item 3: the deprecated rung is dead code wearing a trap — availableToWithdraw is still the third rung of the ladder. Measured today it fires on NONE of the three Bybit books. Removing it is correct but is its own order-path change and was deliberately not smuggled in behind Part A's approval.
3. BL-20260813-FAMILY-RESOLVER-DRIFT-SCALP-NEVER-POOLED needs a pooling decision — Blocked on an operator call, not on capability.
4. Doc-stale backfills from the audit half remain unwritten — Velotrade doc, binance_connector.py, 54->55 strategy cells, ten undocumented API routes, Caddy docs + _CANONICAL_UNITS. Identified, not written.

## Review coverage
- Strategy promotion: NOT ASSESSED this session — it was an audit + real-money-defect session, not a promotion/ML review. Recorded explicitly so the omission is visible rather than read as 'nothing to report'.
- ML training health: NOT ASSESSED this session — it was an audit + real-money-defect session, not a promotion/ML review. Recorded explicitly so the omission is visible rather than read as 'nothing to report'.
- Soak `(none reviewed)`: not_assessed — No soak was assessed. Stated rather than omitted — an empty soak block and an unexamined one look identical otherwise.
- Execution capture: 9 of bybit_2's orders in the window never reached the exchange (ErrCode 110007) — an execution-capture gap by any reading. Root-caused and fixed; DEPLOYED but LIVE-UNVERIFIED. (dollars reconciled: None)
  - `ict_scalp_5m` [real_money]: round-trip —, giveback —R, hold —/—h → anomaly
  - `trend_donchian` [real_money]: round-trip —, giveback —R, hold —/—h → anomaly
  - `xrp_pullback_2h` [real_money]: round-trip —, giveback —R, hold —/—h → anomaly
  - 🔴 `ict_scalp_5m`:  (open 1 review(s), —)
  - 🔴 `trend_donchian`:  (open 1 review(s), —)
  - 🔴 `xrp_pullback_2h`:  (open 1 review(s), —)
- 🚩 REAL MONEY: 30% of bybit_2's orders in the window were venue-refused (9x ErrCode 110007) across ict_scalp_5m, trend_donchian and xrp_pullback_2h. Fixed and deployed, but LIVE-UNVERIFIED.
- 🚩 A halt vector was RE-CREATED in this session's own fix and caught by CI, not by the author — unconditional pledged-margin subtraction refuses every trade on an account whose journal over-reports open notional (451x measured under netting).
- 🚩 pytest-run was letting PRs merge having executed ZERO tests (third recurrence of the class); #8994 merged that way.
- 🚩 A test time bomb reddened main at a wall-clock instant — second in six days.

## Monitoring (soaking / awaiting decision)
- `bybit2-margin-basis-live-verification` [health · awaiting-evidence] Part A/B deployed (5bbb3416) but bybit_2 has not dispatched since 2026-08-11, so no row carries a margin_basis stamp. ONE READ closes it: a bybit_2 trades row created after 2026-08-13T17:00Z, then notes.margin_basis.kind. coin_derived (~226) confirms; equity_unadjusted/equity_minus_pledged/equity_pledged_implausible REOPENS at high. Do NOT substitute a journalctl tail — 400 lines covers 43 seconds. (next: next session / on the next bybit_2 dispatch)
- `deprecated-availableToWithdraw-rung` [health · awaiting-operator] Third rung of the available-margin ladder. Fires on NONE of the three Bybit books today — dead code wearing a trap. Removal is its own order-path change. (next: operator decision)
- `venue-max-clamp-operator-invisibility` [health · awaiting-work] BL-20260813-VENUE-MAX-CLAMP-IS-INVISIBLE-TO-EVERY-OPERATOR-SURFACE — filed by a CONCURRENT session against the clamp this session shipped. This session's own loose end. (next: next health-review)
- `audit-half-doc-backfills` [docs · awaiting-work] Velotrade doc, binance_connector.py, 54->55 strategy cells, ten undocumented API routes, Caddy docs + _CANONICAL_UNITS. Identified in the audit half, not written. (next: next audit/doc session)

_report_id AUDIT-20260813_