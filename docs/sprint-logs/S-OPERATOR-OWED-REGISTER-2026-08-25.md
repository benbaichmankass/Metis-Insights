# Sprint Log: S-OPERATOR-OWED-REGISTER-2026-08-25

## Date Range
- Start: 2026-08-25T19:16Z
- End: 2026-08-25T20:1xZ

## Objective

Fix the operator-owed hand-off as a **pipeline defect**, not a to-do list —
`BL-20260825-OPERATOR-OWED-ITEMS-HAVE-NO-REGISTER-NO-AGE-AND-NO-ESCALATION`.
Build the four parts its `resolution_criteria` specifies: a durable register
with an owner class and an age; escalation that is measured, not asserted;
every defaulted-to-human item automated or carrying a recorded reason; and
**(d)** a standing check that FAILS when an item is carried across N sessions
with no state change.

## Tier

**Tier 1** — the register, the grader, the guard, the tests, and the docs.
No live-VM dispatch was made. The MHG cancel is Tier-2 and was deliberately
**not** taken; the order path was not touched at all.

## Starting Context

Three sessions on 2026-08-25 — `01X2zMCh` (13:33Z), `qhpxyh` (14:27Z),
`018aKyS3` (16:56Z) — each closed by handing forward **the same four items**,
with zero state change on any of them: the `ib_paper` MHG 200% stop cover, the
`DIAG_READ_TOKEN` rotation, the `breakout_1` balance report, and a decision on
`daily_usd: 200`. A `grep` for `operator-owed` / `operator_pending` /
`awaiting_operator` across `src/`, `scripts/` and `docs/claude/*.md` returned
**zero**. Prose in a board comment was the entire mechanism.

The filing row deliberately proposed **no design**, on the grounds that the
shape is a real choice with different failure modes. Choosing it was this
session's first job.

## Repo State Checked

- Branch `claude/operator-owed-register-8lu2w9` off `main` at `f34a74d`.
- Board issue #6927 tail **proved**, not assumed: `get_comments perPage=30
  page=50` returned a **short page of 17** (newest `2026-08-25T18:49:19Z`) —
  a short page is the proof; a full page proves nothing
  (`BL-20260817-BOARD-TAIL-READ-CANNOT-ASSERT-IT-REACHED-THE-END`).
- `▶️ START` posted before the first change; `⚠️`/`✅` follow-ups posted as
  facts changed.

## Files and Systems Inspected

- `docs/CLAUDE-RULES-CANONICAL.md` §§ Backlog governance · Collapsed states ·
  Always state the population · Permission tiers · Generation discipline ·
  Multi-session coordination · Session-end reconciliation.
- `src/runtime/protection_reassert.py` + `src/runtime/protection_price.py` —
  the pattern the filing row named, and the price grader reused rather than
  re-derived.
- `src/runtime/order_monitor.py` over-cover detection; `docs/claude/system-actions.md`
  rows for `cancel-ib-order` / `attach-ib-target`.
- `scripts/ops/broker_bracket_reconcile.py` — its `protective_leg_side` and
  `load_tick_sizes` are reused; see "Contradictions" below.
- `scripts/ci/run_guards.py`, `check_collapsed_states.py`,
  `check_unwired_artifacts.py`, `scripts/ops/check_backlog_criteria.py` for
  guard shape and registration.

## Work Completed

**(a) The register** — `docs/claude/operator-owed-register.json`, seeded with
the five items currently owed. Owner class is the point: `genuinely_human`
splits into `secret_origination` / `physical_or_broker` / `judgement`; the
alternative is `defaulted_to_human` — *a wire exists, or could, and nobody
built it*. `unclassified` is refused.

**The shape choice, recorded rather than assumed.** Not a field on the backlog
rows: the backlogs hold DEFECTS, and most operator-owed items are not defects,
so admitting them re-creates the un-workable-row problem. Not a labelled-issue
queue: it cannot be read deterministically by CI and — decisively — it cannot
MEASURE carry. A committed JSON file has a **git history**, which is what turns
(d) from an assertion into a measurement.

**(b) The grader** — `src/runtime/operator_owed.py`, pure. Seven non-collapsed
states. `not_measurable` is deliberately not `moved`.

**(d) The standing check** — `scripts/ci/check_operator_owed.py`, registered as
`operator-owed-guard`, **ungated**, `notify: True`. Carry is counted from the
register's own git history.

**(c) The MHG over-cover, re-classified and given its selection** —
`src/runtime/over_cover_decision.py`, a pure function with seven non-collapsed
states, plus `scripts/ops/over_cover_proposal.py`, which renders the exact
`cancel-ib-order` body and **dispatches nothing**.

**The stray branch: the wire built rather than the item handed back** —
`.github/workflows/prune-merged-claude-branch.yml`.

**Binding, not optional** — `docs/CLAUDE-RULES-CANONICAL.md` § "Session-end
reconciliation pass" gained item 6, so a session's session-end duty now names
the register.

## Validation Performed

- `tests/test_operator_owed.py` (30) — the carry ladder, both trip paths, the
  defer/terminal states, the structural refusals, the anti-pattern gate, **and
  the git measurement itself** against real temporary repositories.
- `tests/test_over_cover_decision.py` (15) — anchored on
  `test_reproduces_the_2026_08_20_failure`, which replays that failure's own
  measured inputs and asserts the journal-matching leg is KEPT.
- Both new scripts' `--self-test` exercise their failure paths.
- `run_guards.py --all` — no failing guard. `collapsed-state-guard` carries two
  new contracts, both green. `canonical-doc-coherence`, `workflow-catalog`,
  `check_backlog_criteria`, `check_backlog_refs`, `ruff` clean.
- **Verified against reality, not only tests:** the guard's
  verified-not-presence-only check caught this session's own register declaring
  a `tested_decision_function` that did not yet exist on disk.
- CI: all four required workflows confirmed attached to head `f0aeab15` by
  branch-filtered `list_workflow_runs` — not by reading the PR page.

## Documentation Updated

- `docs/CLAUDE-RULES-CANONICAL.md` — § Session-end reconciliation pass, item 6.
- `docs/github-actions-workflows.md` — index row + detail section for the new
  workflow (required by `workflow-catalog`).
- `docs/claude/health-review-backlog.json` — the filing row updated with what
  shipped, the population, and what remains unproven. **Left `open`.**
- `.github/workflows/bootstrap-labels.yml` — the `prune-merged-branch` label.

## Contradictions or Drift Found

**A third copy of "is this leg a stop or a target" was avoided, not shipped.**
`IBClient._protective_leg_side` is the enforcing definition and
`broker_bracket_reconcile.protective_leg_side` is its deliberate, tested mirror
— whose own docstring says a second definition free to drift *"would be its own
defect"*. The first draft of `over_cover_decision.py` added a third. It was
removed: the module now requires the CALLER to pass a classified `side`, and a
leg arriving without one is `not_graded` — never guessed, and never silently
dropped from the coverage sum (dropping it would under-count and could turn a
real over-cover into a clean `no_over_cover`, the reassuring value fabricated).

**A truncated backlog id, caught by the guard that exists for it.** Three files
wrapped `BL-20260825-OPERATOR-OWED-…-NO-ESCALATION` across a line, so
`check_backlog_refs` saw truncated ids. This is the exact lapse `018aKyS3`
recorded hours earlier. Fixed by spelling the id once as a module constant.

**A claim in an earlier draft of this session's own register was wrong and was
corrected before commit:** the stray-branch item was first written as though a
push-delete simply had not been retried. Measured, it returns **HTTP 403** on
four attempts with backoff while a normal push from the same credential in the
same minute succeeds. The refusal is real; it is a boundary for a *session's
credential*, not for a runner. The item stayed `defaulted_to_human` — but on
measured grounds rather than the assumed ones.

## Risks and Follow-Ups

- **The carry count under-reports and can never over-report.** A session that
  never touches the register leaves no commit. Age is the second, independent
  trip path for exactly that reason.
- **Three items sit at `carried` 1 of 2.** The next register commit that leaves
  them alone fails CI. That is the designed pressure, and the four ways out are
  printed by the guard — act, move, defer behind a **named trigger**, withdraw.
  If it proves too aggressive for genuinely-human items in practice, the fix is
  a per-owner-class limit, not a raised global one.
- **`DEFAULT_CARRY_LIMIT = 2` is a chosen value with a measured basis, not a
  tuned one.** n=1 incident, 3 sessions. A limit of 3 would have graded that
  entire day green.

## Deferred Items

- **The stray branch is not yet deleted.** An issue trigger and a
  `workflow_dispatch` both resolve the workflow from the **default** branch, so
  it fires after merge. A sequencing fact, not a hand-off.
- **The MHG cancel is Tier-2 and was not taken.** The proposal tool emits the
  body; the dispatch needs one operator OK — on the very position whose last
  remediation is the subject of `BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG`.
- **Four items registered but not absorbed into this task**, deliberately: the
  `DIAG_READ_TOKEN` rotation, the `breakout_1` balance report, the
  `daily_usd: 200` decision, and the stray branch.

## Next Recommended Sprint

Close the loop, then judge it: dispatch the branch prune, put the MHG cancel to
the operator as a dry-run first, and **only then** consider closing the filing
row — on (1) an item reaching a terminal status through the register and (2)
the standing check having FAILED once on a real carry and been cleared by a
real disposition. A green that has never fired is not evidence the escalation
works.

## Wrap-Up Check

- [x] Board `START` posted before the first change; heads-up + wrap posted.
- [x] Backlog row updated with what shipped and what remains unproven; left open.
- [x] `doc-freshness` sweep: canonical-doc-coherence green; the rules doc, the
      workflow catalog and the register agree with the code.
- [x] Tier respected: nothing merged that needed an approval it did not have;
      no live-VM dispatch; the order path untouched.
