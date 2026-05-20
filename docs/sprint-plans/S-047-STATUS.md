# S-047 Branch Status Audit

**Audit date:** 2026-05-20  
**Sprint:** S-JANITOR-BRANCH-CLEANUP (Sprint 7)  
**Scope:** All `claude/S-047-*`, `claude/S-049-*`, and closely related spot-margin `claude/` branches. Includes post-sprint follow-ups through the spot-margin sunset (2026-05-10).

---

## Summary

S-047 (Bybit Spot Margin Enablement) completed on 2026-05-07. All tasks T0–T5 were merged to `main` by the operator on the same day. Three follow-up sprints (S-049, S-053, S-055) and two docs PRs (T6/T7) also merged by 2026-05-10.

**The spot-margin feature was subsequently sunset on 2026-05-10 (PR #792).** `bybit_2` was migrated from `market_type: spot-margin` to linear perps. PR #792 deleted ~1,357 lines of spot-margin source code across 5 files. All S-047 and follow-up branches are therefore squash-merge orphans — their commits live on `main`, and the spot-margin code they introduced was later removed.

One post-S-047 PR remains open: **PR #1026** (remove circuit breaker + linear perps margin fix). It is not a spot-margin branch but addresses a Prime Directive violation introduced by PR #741 (which was a direct response to 170131 errors that S-047 was trying to fix).

The only S-047 deliverable still active on `main` is the **VWAP monitor close logic** from T4 (PR #469) — the four close paths (TP/SL/VWAP-cross/time-decay) added to `vwap.py::monitor()`.

---

## S-047 Core Task Branches

| Branch | Task | PR | Merged | Code still on main? |
|---|---|---|---|---|
| `claude/S-047-T0-margin-enable-notebook-xBvbM` | T0 — verification notebook | #452 | ✅ 2026-05-07 | Docs/notebook only |
| `claude/S-047-T0-plan-no-gates-correction` | T0 — planning docs | #453 | ✅ 2026-05-07 | Docs only |
| `claude/S-047-T0-close-checkpoint` | T0 — checkpoint | #454 | ✅ 2026-05-07 | Session handoff docs |
| `claude/S-047-margin-agnostic-correction` | T0 — § 4.4 corrective | #455 | ✅ 2026-05-07 | Docs only |
| `claude/accounts-yaml-spot-margin-uCbil` | T1 — routing config | #456 | ✅ 2026-05-07 | ❌ Deleted by PR #792 |
| `claude/S-047-T2-risk-spot-margin-sizing-MOY0f` | T2 — risk sizing kernel | #459 | ✅ 2026-05-07 | ❌ Deleted by PR #792 |
| `claude/bybit-spot-margin-routing-tZMjN` | T3 — exec `isLeverage=1` | #464 | ✅ 2026-05-07 | ❌ Deleted by PR #792 |
| *(vwap-monitor-close-logic branch)* | T4 — VWAP close paths | #469 | ✅ 2026-05-07 | ✅ Still active |
| `claude/S-047-T5-reconciler-spot-margin` | T5 — position synthesis | #477 | ✅ 2026-05-07 | ❌ Deleted by PR #792 |

---

## S-049 and Post-Sprint Follow-ups

| Branch | Sprint | PR | Merged | Code still on main? |
|---|---|---|---|---|
| `claude/S-049-spot-margin-sizer-correctness` | S-049 — buy-side buffer + borrow capacity | #473 | ✅ 2026-05-07 | ❌ Deleted by PR #792 |
| *(s053 branch)* | S-053 — sizing net equity fix | #498 | ✅ 2026-05-08 | ❌ Deleted by PR #792 |
| `claude/spot-margin-borrow-reconciler-fdvTx` | S-055 — post-close repay + orphan reconciler | #528 | ✅ 2026-05-08 | ❌ Deleted by PR #792 |
| `claude/s047-t6-runbook` | T6 — runbook docs | #686 | ✅ 2026-05-10 | ✅ Docs still on main |
| `claude/s047-t7-sprint-close` | T7 — sprint close docs | #688 | ✅ 2026-05-10 | ✅ Docs still on main |
| `claude/fix-bybit-170131-retry-loop` | Circuit breaker (PR #741) | #741 | ✅ 2026-05-10 | ✅ Still on main |
| *(spot-margin-sunset branch)* | Delete dead code | #792 | ✅ 2026-05-10 | N/A — the deletion itself |

### Additional related spot-margin branches (unverified merge status)

The following branches are related to the spot-margin work but were not enumerated in S-047's task list. Based on the PR timeline, they are likely merged or abandoned as part of the same sprint cluster:

- `claude/spot-margin-ltv-fallback` — LTV fallback for spot-margin sizing
- `claude/fix-bybit-spot-margin-60JXp` — likely a spot-margin execution fix

These branches contain no open PRs and the underlying code was deleted by PR #792. No action required.

---

## Checkpoint and Ping Branches (Orphan Session Records)

All of these branches contain only session-handoff commits (checkpoint docs, operator ping PRs). The actual code they tracked is in `main`. Safe to delete at Ben's discretion; no open PRs attached.

**Checkpoint branches:**
- `claude/cp-2026-05-07-s047-t1-close` — T1 close checkpoint
- `claude/cp-2026-05-07-s047-t2-close` — T2 close checkpoint
- `claude/cp-2026-05-07-s047-t3-close` — T3 close checkpoint
- `claude/cp-2026-05-07-s047-t4-close` — T4 close checkpoint
- `claude/cp-2026-05-07-s047-t5-close` — T5 close checkpoint
- `claude/cp-2026-05-07-s049-close` — S-049 close checkpoint

**Ping branches (operator notification records):**
- `claude/ping-S-047-plan`
- `claude/ping-S-047-T1`
- `claude/ping-S-047-T2`
- `claude/ping-S-047-T3`
- `claude/ping-S-047-T3-complete`
- `claude/ping-S-047-T4-start`
- `claude/ping-S-047-T4`
- `claude/ping-S-047-T5-start`
- `claude/ping-S-047-T5`
- `claude/ping-S-047-T6-start`
- `claude/ping-S-049-start`
- `claude/ping-S-049`

---

## Open Post-S-047 PR: #1026 — Needs Ben's Attention

**PR #1026** (`claude/no-auto-dry-flip-and-margin-cap`, created 2026-05-12) is open as a draft. Not an S-047 branch, but directly related.

**What it does:**

1. **Removes the circuit breaker** added by PR #741 (`_EXCHANGE_REJECTION_PAUSE_THRESHOLD = 3`). The circuit breaker auto-flips an account to `dry_run` after 3 consecutive `exchange_rejected` results. This violates the Prime Directive: "The system never switches itself off. No auto-flip, no breaker that toggles mode, no 'safety' default that goes dry on boot."

2. **Fixes the position-sizer margin bug** for linear perps: replaces the `_MARGIN_SAFETY_BUFFER = 0.9` workaround (90% of `wallet_balance × leverage`) with a fetch of `availableToWithdraw` from the Bybit UNIFIED API — the exact free collateral available for new-position IM.

**Status:** NOT superseded. The circuit breaker from PR #741 is still live on `main` and still violates the Prime Directive. The margin fix is relevant for linear perps (bybit_2's current mode). The PR has no review comments from Ben.

**Recommendation:** Do not close this PR. Ben should review when ready — both changes are Tier-3 (touch live coordinator and risk paths). The circuit breaker removal is particularly time-sensitive as it's an active Prime Directive violation.

---

## Conclusion

- **All S-047 task branches (T0–T5) and related follow-ups (S-049, S-053, S-055) are merged.** Branches are squash-merge orphans.
- **No open S-047 PRs to close.** The sprint completed cleanly.
- **Spot-margin code was deleted from `main` in PR #792 (2026-05-10).** The only S-047 code still live is the VWAP monitor close logic (T4).
- **PR #1026 is the one pending action item** — a Prime Directive violation (circuit breaker on `coordinator.py`) that needs Ben's explicit approval to remove.
