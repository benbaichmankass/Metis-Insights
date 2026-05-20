# Sprint Log: S-JANITOR-BRANCH-CLEANUP

**Sprint:** 7 (S-JANITOR-BRANCH-CLEANUP)  
**Date:** 2026-05-20  
**Type:** auto-claude (docs + GitHub housekeeping)  
**PR:** n/a — docs committed directly to `claude/setup-coding-session-qCToW`

---

## Objective

Audit and document the S-047 spot-margin branch cluster. Determine merge status of each branch and associated PR. Close any clearly superseded open PRs (with a comment explaining why). Do not delete branches.

---

## Deliverables

1. `docs/sprint-plans/S-047-STATUS.md` — written. For each S-047/S-049 branch: merged / pending / superseded; brief rationale. Includes post-sprint follow-ups through spot-margin sunset.
2. No PRs closed — all S-047-associated PRs were already merged or are not superseded. PR #1026 explicitly left open (not superseded; contains Prime Directive fix).

---

## Key Findings

### S-047 Sprint (2026-05-07) — All tasks merged

S-047 was the Bybit Spot Margin Enablement sprint. It completed in a single session on 2026-05-07 with the operator merging all five code tasks:

| Task | Branch | PR | Merged |
|---|---|---|---|
| T1 routing config | `claude/accounts-yaml-spot-margin-uCbil` | #456 | ✅ 2026-05-07 |
| T2 risk sizing | `claude/S-047-T2-risk-spot-margin-sizing-MOY0f` | #459 | ✅ 2026-05-07 |
| T3 exec routing | `claude/bybit-spot-margin-routing-tZMjN` | #464 | ✅ 2026-05-07 |
| T4 VWAP close | *(vwap-monitor-close-logic branch)* | #469 | ✅ 2026-05-07 |
| T5 reconciler | `claude/S-047-T5-reconciler-spot-margin` | #477 | ✅ 2026-05-07 |

Follow-ups S-049 (PR #473), S-053 (PR #498), S-055 (PR #528) also merged by 2026-05-08.

### Spot-margin sunset (2026-05-10)

PR #792 deleted ~1,357 lines of spot-margin source code. `bybit_2` was migrated from `market_type: spot-margin` to linear perps. The only S-047 code still active on `main` is the VWAP monitor close logic (T4 / PR #469): four close paths (TP-cross, SL-cross, VWAP-cross, time-decay).

### Circuit breaker (PR #741 vs. PR #1026)

PR #741 (merged 2026-05-10) added `_EXCHANGE_REJECTION_PAUSE_THRESHOLD = 3` to `coordinator.py` in response to bybit_2's 170131 rejection storm. The circuit breaker auto-flips an account to `dry_run` on 3 consecutive exchange rejections — a Prime Directive violation.

PR #1026 (open draft, 2026-05-12) proposes to remove the circuit breaker and replace it with a proper fix: fetch `availableToWithdraw` from Bybit UNIFIED API as the sizing ceiling for linear perps. This PR is NOT superseded and should be reviewed by Ben.

### Branch cluster summary

- **25 S-047/S-049 branches identified** (task branches + checkpoint + ping + related)
- **All task branches are squash-merge orphans** — commits live on `main`; no open PRs
- **No branches to close** (Sprint 7's "close superseded PRs" deliverable is a no-op)
- **1 open related PR** (PR #1026) — leave open; flag for Ben

---

## Definition of Done Assessment

- [x] `docs/sprint-plans/S-047-STATUS.md` written and accurate — each branch determination based on PR merge status (not guessed)
- [x] No clearly-superseded open S-047 PRs exist to close
- [x] PR #1026 explicitly documented with recommendation (do not close)
- [x] Sprint log written

---

## Follow-ups Generated

None new. PR #1026 is pre-existing work that Ben is already aware of.
