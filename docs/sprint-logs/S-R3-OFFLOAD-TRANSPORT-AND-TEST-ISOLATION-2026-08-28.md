# Sprint Log: S-R3-OFFLOAD-TRANSPORT-AND-TEST-ISOLATION-2026-08-28

## Date Range

2026-08-28 (single session, continuing the 2026-08-27 R1–R6 lane).

## Objective

Close R3's **transport** half of the M40 research-workflow lane — a
runner-trained model reaching the repo — **leaving the trainer-side drain
UNARMED**; then, at operator direction, ship the **test-isolation CLASS fix**
(Lane A) that three separate 2026-08-27 defects had each been fixed as an
instance of.

## Tier

**Tier 1 throughout.** No `src/`, no `config/`, no live order path, no live-VM
mutation. One deliberate boundary: **arming the drain is Tier-2 and was not
done**, and a runner-trained model joining the live shadow fleet remains Tier-2
needing an explicit operator OK.

## Starting Context

R3's transport had merged (#10368, `97b432a4`) but **had never carried a real
model** — every test was a sandbox simulation across two tmpdirs on one
filesystem. Operator decisions carried in: commit the model artifact only for
runs that update a registry entry; the R5 dispatcher stays dry; the
test-isolation class fix is its own PR.

## Repo State Checked

`main` at `97b432a4` at session start, ending at `60274e0d`. Branch
`claude/r3-transport-offload-0oy5uh`, restarted from `main` between each PR.

## Files and Systems Inspected

`scripts/ci/assert_rows_landed.py` · `tests/conftest.py` (all 290 lines) ·
`tests/isolation_*` (new) · `tests/test_notify_session.py` ·
`tests/test_runtime_paths_alignment.py` · `ROADMAP.md` M40 ·
`docs/claude/OPEN-ITEMS.json` · `src/bot/cloud_notifier.py` +
`src/runtime/execution_diagnostics.py` (the pending-pings inbox) ·
the three 2026-08-2x test-isolation backlog rows.

## Work Completed

1. **`cffe9010` (#10372) — the first real offload drop.** A `publish=true`
   dispatch (run `33150949388`) trained `btc-regime-5m-lgbm-flow-v1` on a free
   GitHub runner and committed the result. Confirmed **in production rather
   than in a tmpdir**: `artifact_dir` repo-relative (not the runner's absolute
   path — the defect class R3 exists to remove, one level up), `n_train_final`
   `None` rather than a fabricated `0`, and `model_state_bytes` **1,410,298 =
   1.34 MB, matching the trainer-diag p50 exactly** — an independent
   confirmation of the figure the whole commit-vs-fetch decision was argued
   from. The model itself is uninteresting (`f1_volatile` 0.131, order-flow
   head on public data where every `ofi`/`vpin` column is `0.0`); this verifies
   the TRANSPORT.

2. **`6295f7f0` (#10373) — a defect that run found by RUNNING, not by testing.**
   `assert_rows_landed` returned `could_not_read` (exit 2) where `pending_merge`
   (exit 1) was correct: `read_store_at_ref` reported **any** non-zero
   `git show` as unreadable, collapsing *"the ref resolves and the path is not
   there yet"* into *"we could not look"*, and `check()` returned
   `COULD_NOT_READ` **before consulting `pushed_rows`** — making `pending_merge`
   **unreachable for the first drop of any new store**, i.e. exactly the case
   it was built for. Fixed by asking git a **structured** question
   (`rev-parse --verify ^{commit}`) rather than matching the prose of git's
   error, whose text genuinely differs between the production failure and the
   local repro. 10 → 13 planted controls.

3. **`60274e0d` (#10374) — the test-isolation audit (Lane A).** A
   conftest-level detector for the class: no test may leave `sys.modules`
   changed. It **stubs nothing** — the remedy is *never stub*, not *stub more*.
   Ships at `annotate` (reports, fails nothing) with a 13-entry declared
   baseline as a **ratchet**.

## Validation Performed

- Full local suite **13,359 passed / 0 failed**, and CI `pytest-run` green.
- Guards **PASS 38 / FAIL 0**; ruff clean repo-wide.
- **CI confirmed the audit baseline on a different dependency set**:
  `module_removed=2, module_replaced_real=11` — exactly the 13-entry baseline,
  `UNDECLARED` line absent, on a box collecting 13,407 tests vs the sandbox's
  13,359.
- Every fix mutation-tested; both discriminator reverts fail **by name**.

## Documentation Updated

`ROADMAP.md` M40 (this session, twice — see Contradictions below) ·
`docs/claude/OPEN-ITEMS.json` (the `loud` R3 row) · two new health-review
backlog rows · this log.

## Contradictions or Drift Found

- **M40 said the transport "has never carried a real model."** True when
  written; false ~25 minutes later when #10372 merged. Fixed here — the row now
  records what was carried **and** that it is still not R3 *holding*.
- **M40 said `assert_rows_landed` has "10 planted controls."** 13 after #10373.
  Fixed here.
- **`BL-20260827-NOTIFY-SESSION-TEST-POPS-SYS-MODULES-AND-NEVER-RESTORES` reads
  `resolved / FIXED same session`, and
  the fix was PARTIAL** — four call sites in that file still pop bare. Filed.

## Risks and Follow-Ups

- `BL-20260828-TEST-SUITE-WRITES-INTO-THE-LIVE-PENDING-PINGS-OUTBOX` — the
  suite writes into the outbound inbox `cloud_notifier` **drains and sends**;
  378 files in one working tree, count **varies between runs**. Risk today is
  low (tests don't run on the live VM; dir is gitignored) and the row says so;
  the class is not.
- `BL-20260828-NOTIFY-SESSION-POP-AND-RESTORE-FIX-WAS-PARTIAL`.
- Flipping the audit to `enforce` is now defensible (CI confirmed the baseline)
  but is deliberately a separate change.

## Deferred Items

**Not done, deliberately:** arming the drain (**Tier-2**); firing the R5
dispatcher (**stays dry** — no session has yet read a real dry-run report
against live queue contents); Lane B (Lane P measurement); Lane C (the 18
upload-and-land-nothing workflows).

## Next Recommended Sprint

**Observe R3 actually holding.** In order: confirm `trainer_git_sync.sh`
*carried* the drop; run the drain on the trainer **unarmed** and read its plan;
then, with an explicit operator OK, arm it and verify the artifact **LOADS**
from the registered `model_state_path` on the trainer — by loading it, not by
the entry existing. Only then may R6 be reasoned about.

## Wrap-Up Check

- Board `▶️ START` / `🔒 CLAIM` / `🔓 RELEASE` / `✅ DONE` posted on #6927 for
  every merge; tail proven by a **short page** each time.
- `OPEN-ITEMS` `loud` row re-affirmed and updated; **it does not clear**.
- ⚠️ **A lesson worth carrying:** that row *predicted the first verdict would
  be a failure and was right about that and wrong about why*. Had it not
  written the expected verdict down, the wrong red would have read as the
  predicted one.
