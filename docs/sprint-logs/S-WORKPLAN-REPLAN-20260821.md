# Sprint Log: S-WORKPLAN-REPLAN-20260821

## Date Range
2026-08-21 16:50Z → 2026-08-21 19:05Z (single session, `spsxq6`), overlapping
the tail of `dcf5220b`'s `/system-review` and the whole of `wave0-8g7443`'s
Wave-0.1 session.

## Objective
Began as a continuation session working three operator-decided items (a live
falsifier, a cross-branch catalog row, and evidence for an unchosen Tier-3
tolerance). **Mid-session the operator judged the programme itself**: the work
had become self-referential — guards about docs about workflows — while the
system's measured condition did not move. The objective became: state that
honestly, take four replanning decisions, and land them where the next session
inherits them.

## Tier
Tier-1 throughout. **No `src/`, no `config/`, no unit file, no order path, no
Tier-3 flip.** One live falsifier run against the real cloud (read-only
inventory; two operator Telegrams, both intended).

## Starting Context
`main` at `9616928`. Three items carried in from the prior session, all
operator-answered: run the OCI live falsifier, push #10068's catalog row, build
#10081's tolerance evidence.

## Repo State Checked
`main` moved **six times** during the session (`9616928 → a252119 → 6cdacee →
0649418 → 6d77066`, plus two concurrent merges). Every merge in this log was
preceded by a board tail proven with a SHORT or EMPTY page, never a full one.

## Files and Systems Inspected
`.github/workflows/{oci-inventory,health-snapshot,claude-run-failure-alert}.yml`
· `scripts/notify_session.py` · `scripts/ci/{check_workflow_catalog,run_guards,
guard_selftests}.py` · `docs/github-actions-workflows.md` ·
`docs/ARCHITECTURE-CANONICAL.md` · `docs/claude/{health-review-backlog,
session-board}.json` · `docs/claude/WORKPLAN-2026-08-21.md` ·
`config/instruments.yaml` · live `/api/diag/{version,ib_open_orders,ib_state}`
and `/api/bot/positions`.

## Work Completed
Seven PRs merged, each verified on `main` by reading it back:

- **#10094** `e6327f7` — `workflow-catalog` guard. The catalog claimed to name
  every workflow and was **45.9% incomplete**; 51 rows backfilled, 12 phantom
  `.yml` suffixes stripped. Zero exemptions, both directions enforced.
- **#10100** `af1e14f` — resolved the row that finding opened.
- **#10104** `2d2db79` — required contexts corrected. Three "canonical" surfaces
  said **15, 9 and 3**; only 3 was true. Also recorded that `layer-guard` does
  gate `main`.
- **#10082** `fd6c2ca` — unstuck `sysrev-0816`'s PR with one ordinary commit
  (1 check → 7), confirming that remedy in production.
- **#10105** `fe93f99` — self-ping dedupe, then listing the two cron'd
  workflows whose failures nothing watched.
- **#10109** `6cdacee` — **fixed a defect #10105 introduced.** See below.
- **#10111** `6d77066` — the work-plan rewrite + session-board prune.

Plus `7cc9d16` pushed to #10068's branch (one catalog row) and the #10081
tolerance evidence posted as a PR comment.

**The OCI live falsifier ran, both arms, and cost exactly the two operator
Telegrams predicted.** Arm A (run `32506941877`, failure induced *before* the
verdict step) left the sentinel `skipped` and the listener **pinged**. Arm B
(run `32507240243`, declared `ict-ib-gateway` ocpus 1.0→3.0) produced verdict
`drift` with summary *"1 finding(s) over 3 declared+live instances"* — the
stated denominator is what proves the tool read the live cloud rather than
dying first — the sentinel concluded `success`, and the listener **stayed
quiet**. Cleanup verified: probe branch hard-reset, drift issue #10108
auto-closed by a clean run at zero ping cost.

## Validation Performed
- `run_guards.py --all` → **PASS 49 · FAIL 1** on every PR. The one failure is
  `layer-guard`, `lint-imports … exited 127` — the binary is absent in this
  sandbox and it fails **identically on unmodified `main`**, checked rather than
  assumed. It passes in CI.
- New dedupe tests **shown to FIRE**: run against the pre-fix
  `oci-inventory.yml` both fail with their real assertion text; against the
  fixed file 14/14 pass. An assertion never observed failing is not evidence.
- Backlog serializer proven to round-trip byte-for-byte **before** every edit.
- #10068's remedy validated by **test-merging both placements**, not by
  inspection — see Drift below.

## Documentation Updated
`docs/github-actions-workflows.md` (51 rows) · `docs/ARCHITECTURE-CANONICAL.md`
(required contexts) · `docs/claude/WORKPLAN-2026-08-21.md` (rewritten) ·
`docs/claude/session-board.json` (pruned) · `docs/claude/health-review-backlog.json`
(3 rows) · this log · the ROADMAP ledger row.

## Contradictions or Drift Found
- **A defect I shipped, found four hours later.** #10105's dedupe suppressed the
  listener whenever a `[operator-ping]` step concluded `success`, and its own
  comment asserted that meant *"a message really went out."* It did not: both
  sentinel steps ran `… || echo "(non-fatal)"`, so each exited 0 on a Telegram
  outage too. **`notify_session.py` was already exiting 1 on a real delivery
  failure and the caller discarded it** — written-and-never-read, one level up
  from a journal column. Fixed in #10109 by moving the sentinel to a confirm
  step gated on the send's `outcome`. **The falsifier does NOT cover this** —
  both arms ran with healthy secrets — and the resolved backlog row says so.
- **Three copies of the required-check list disagreed** (15 / 9 / 3).
- **A one-row edit that would have made things worse.** #10068's branch has
  **zero** `| Research |` rows — that category arrived in main's backfill — so
  inserting there **creates a conflict in a file that merges cleanly today**.
  Measured by test-merging, then placed in the only 28-line stretch both sides
  leave untouched.
- **My own population error.** I quoted the backlog as "774 rows, 333 open".
  That is the **health file alone**; across all three it is **984 / 378**. The
  "always state the population" rule, broken by me, in a session enforcing it.

## Risks and Follow-Ups
- `BL-20260821-SELF-PING-SENTINEL-SURVIVES-A-FAILED-SEND` — filed **and
  resolved** here.
- `BL-20260821-IB-OPEN-ORDERS-COULD-NOT-LOOK-CARRIES-NO-REASON` — **open.**
  `read_state: could_not_look` with an empty `error`, while `/api/diag/ib_state`
  showed the trader's own clients connected with `last_ok` 4.4s old. Different
  clients by design; the route just cannot say which failure it hit.
- `BL-20260821-OCI-INVENTORY-CRON-UNWATCHED` — **resolved** (falsifier, both
  arms, with the coverage caveat recorded inside the row).

## Deferred Items
- **T.1** (exit-eval fetch cost) is **Tier-3 approved 2026-08-21T18:05Z and NOT
  implemented.** `src/` untouched. Queued after Phase 0 because the
  cleanup-first directive (18:15Z) postdates the approval; the approval stands
  and needs no re-ask.
- **#10081's tolerance remains unchosen by data.** Aligned cases sit at 0.43 and
  0.47 ticks — both under the 0.5-tick arithmetic ceiling for nearest-tick
  rounding — against a single diverged case at 68.79. **Any threshold in that
  147× gap grades all three identically**, and nothing stores resting leg prices
  over time, so the distribution cannot be reconstructed backwards. Proposed
  1 tick, labelled a judgement rather than a measurement.
- Phase 0 itself — deliberately **not** started here.

## Next Recommended Sprint
**Phase 0 of `docs/claude/WORKPLAN-2026-08-21.md`, items 0.1 → 0.6**, in a fresh
session. It is a gate: nothing in Phase 1 starts until it is met. It is also a
**retirement** pass — if it produces new guards, new docs, or backlog rows about
the backlog, it has failed.

## Wrap-Up Check
`doc-freshness` run: `check_canonical_doc_coherence.py` **5/5 PASS**. Board
`🔓 RELEASE + ✅ DONE` posted; `session-board.json` `active_sessions: []`,
`merge_slot.held_by: null`; no scheduled wakeups remain.

**The honest headline: almost none of this moved the money path.** Two of the
seven merges existed only to fix defects introduced by earlier ones. That is the
operator's finding, this session is its largest contributor, and the work-plan
rewrite is the correction rather than a defence.
