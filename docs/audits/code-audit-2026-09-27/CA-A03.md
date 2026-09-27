# CA-A03 — Code audit Wave A: execution core II (exits, positions, fills, invariants, protection, broker truth)

**Status: PARTIAL (first landing).** This PR carries the protection/over-cover/OCA/leg-coverage
findings. Findings for the exit, fills/PnL and broker-truth module groups come in the follow-up PR.

## Findings so far

| severity | id | disposition |
|---|---|---|
| high | AUD-20260927-CA-A03-over-cover-keeper-qty-unchecked | PI-20260927-ENGC5EUD-0001 |
| medium | …-over-cover-unreadable-qty-as-zero | same fix as above |
| medium | …-stray-oca-cancels-native-bracket-of-any-trade | filed |
| medium | …-reassert-attempt-budget-never-resets | filed (call site in order_monitor, CA-A01) |
| medium | …-package-leg-latch-never-pruned | filed (live: 21/25 latches stale) |
| — | …-tp-venue-cap-single-owner | verified-non-issue |

## Coverage (so far)
- Tests: all 109 in-scope test files ran, **1908 passed, 2 skipped**.
- Read fully: protection_price (162), protection_reassert (237), tp_venue_cap (107), over_cover_decision (426), stray_oca_groups (195), package_leg_coverage (379).
- Exercised against crafted input: over_cover_decision, stray_oca_groups, package_leg_coverage (temp DB).
- Checked against live state: stray_oca_soak (131 rows), package_leg_coverage latch (25) × trades journal.
- Live `/api/diag/ib_open_orders` read `could_not_look` for both IB accounts at 13:16Z, so resting-leg state could not be checked.
