▶️ START — MI-57: OPEN-PRS settled/in-flight split + post-merge reconciler

Session: `session_01T8iWSepqAuBU7sgbs8GPHu` (manager-spawned)
Branch: `claude/openprs-settled-reconciler`
Tier-1 (tooling + CI). No order path, no config, no live-trading surface.

Posted through the `board-post` relay: `add_issue_comment` returned
`403 Resource not accessible by integration` on this issue, which is the
documented write-scope boundary in CLAUDE.md § "The OTHER TWO relays".

**Defect being fixed.** `open_prs[]` conflates two populations under one key —
an in-flight claim (legitimately graded against the live open list) and the
durable operator decision record (history, which is *more* load-bearing after
the merge than before it). Pruning a "stale row" therefore DELETES the
decision: #10746's conditional Tier-2 approval (`bybit_1` demo only, NOT
fleet-wide, real-money `bybit_2` accepted as exposed) is exactly the thing a
successor must not lose. Combined with the second root cause — a commit cannot
record its own merge — this is the non-terminating regress observed live on
#10775, which merged (`d08cac48`) about ninety seconds after a branch recorded
a row for it.

**Files I will touch:**
- `docs/claude/work/OPEN-PRS.json` — split into `open_prs[]` (graded against
  the live list) and `settled_prs[]` (NEVER compared to it); `schema_version`
  bump plus migration of the existing
  `recently_merged_pruned_from_this_record` rows; `_doc` rewritten, since it
  currently asserts "goes stale the moment a PR merges" and "MI-43 is building
  the check" and both are about to be false (field beats comment).
- `scripts/ops/open_pr_record.py` — `grade_completeness` grades ONLY
  `open_prs[]`; new finding for a `settled_prs[]` row with
  `terminal: closed_unmerged` and no `disposition`; a stale row whose
  `last_reconciled_sha` lags main reports "the reconciler has not run", kept
  distinct from `stale_row`.
- `scripts/ops/reconcile_open_prs.py` — NEW. Post-merge reconciler. MOVES
  rows, never deletes.
- `.github/workflows/reconcile-open-prs.yml` — NEW. `push: branches: [main]`
  with a `concurrency:` group.
- `scripts/ci/check_collapsed_states.py` — register the reconciler's
  `reconciled` / `no_change` / `could_not_look` contract.
- `tests/ops/test_reconcile_open_prs.py` and the `open_pr_record` tests —
  the regress reproduced FIRST, then the new shape shown not to exhibit it.

**Two things I am deliberately NOT doing**, both because the existing
docstrings already argue against them:
1. Not exempting the in-flight PR from the completeness check. That exemption
   is byte-indistinguishable from the real failure the check exists to catch
   (a PR nobody recorded), and a check that cannot tell those apart is worse
   than the transient row.
2. Not having the reconciler stub a row for an unrecorded open PR. Only a
   session knows the owner, intent and operator decision; a stub would
   manufacture completeness nobody established. An open PR with no row must
   keep failing.

⚠️ **One deviation from the brief, flagged up front rather than discovered in
review.** The brief specifies that the reconciler commits to `main` with a
rebase-retry. `main` is branch-protected and REJECTS a direct workflow push
(GH006 — `BL-20260706-GPU-BURST-LEDGER-PUSH-RACE`, and the shared action's own
header says so), so the reconciler lands through
`.github/actions/commit-to-main` instead.

Residual, stated rather than hidden: that route opens a short-lived
`automation/**` PR which itself carries no row, so `open_prs` reads
`unrecorded` for roughly the 15 minutes it is open. Unlike today's regress this
**terminates** — the reconciler commits only when a row actually moved, so the
run triggered by its own merge grades `no_change` and opens nothing. It is a
bounded, one-hop residual replacing an unbounded loop, not a closure. I will
report it to the manager rather than widen scope on my own.

Will open as a DRAFT and report back. Not merging it myself.
