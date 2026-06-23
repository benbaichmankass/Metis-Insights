# System report — since-last

- Generated: 2026-06-23T15:45:33+00:00
- Window: 2026-06-23T13:36:10+00:00 → 2026-06-23T15:45:33+00:00
- Roll-up grade: healthy

Window 13:36Z->15:45Z was a fix-and-verify window, not a trading one: NO trades closed. The orphan-labeling, reconciler-close sl/tp classification, and ping silent-loss fixes were merged AND deployed to the live VM, and the orphan fix was VERIFIED on live data (a post-deploy dry/shadow package came back 'rejected', not 'orphaned'). System healthy: heartbeat live, 36 strategies, deployed current. Grading is current (2498 graded). The prior report's CAUTION (orphaning + reconciler mislabeling) is now RESOLVED.

## P&L by class
- **real**: window +$0.00 (prior $-1.64, flat)
- **paper**: window +$0.00 (prior +$110.08, flat)
- **prop**: window — (prior —, flat)

## Operator priorities
1. Optional: add GRADING_PAT secret for unattended grading auto-merge — The grade-order-packages bridge opens a grades PR each run; without a GRADING_PAT (fine-grained PAT, contents+PR write) it can't auto-merge (GITHUB_TOKEN PRs get no CI), so a session adopts the branch. One secret makes grading fully hands-off.
2. Residual orphan class: executed-then-exchange-flat-without-a-clean-fill — The dry/shadow orphan share is fixed; BL-20260601-001's remaining scope (executed positions that read flat with no recoverable fill/PnL) is the reconciler/closed-pnl-recovery domain — a separate follow-up.
3. Add /api/bot/ml/status (+strategies, prop) to the vm-diag relay allowlist — These read-only endpoints aren't on the relay's path allowlist, so ML registry detail can't be pulled over the issue relay. A one-line allowlist add restores full report coverage.

_report_id RPT-20260623-154533-since-last_