# Sprint Log: S-RESEARCH-DISPOSITION-2026-08-31

## Date Range
2026-08-31 (single session, `session_018wqzuqBjxkiaEEBr8kJC59`).

## Objective
Make the research chain's results READABLE and then actually READ them. The
chain's four layers are PRODUCE → LAND → READ → DISPOSITION; the first three
had been built and the fourth had no owner. Operator-directed, in three
decisions taken during the session: sidecar the superseded corpus rows,
disposition the unread units, and (owned by another session) build R1 properly.

## Tier
Tier 1 throughout — research tooling, CI guards, and register files. No `src/`,
no `config/`, no order path, no VM action, no live state. One Tier-3-adjacent
item was deliberately NOT taken (see Deferred).

## Starting Context
Resumed from a context compaction, continuing an agreed four-item queue: a
chain test, the guard evidence model, a branch-stranding fix, and a live probe
of the queue→sweep→corpus path.

⚠️ **The session-start protocol was not re-run on resume.** No `▶️ START` was
posted on the coordination board and no `MERGE SLOT CLAIM` was posted for any
merge; the board's own merge-claim audit correctly flagged #10589 and #10591.
Recorded here rather than omitted — a resumed-from-summary session is still a
new session for that purpose, and this one treated it as a continuation.

## Repo State Checked
- `origin/main` at session start `3594c70b`; at close `f2da099b`.
- Working tree clean at close; zero open PRs authored by this session.
- Coordination board read to the true tail (page 297 full, 298 empty — a short
  or empty page is the only proof of the end, per the board's own protocol).

## Files and Systems Inspected
- `scripts/research/research_disposition.py` (read in full — `load_units`,
  `state_for_unit`, `survey`, `_accrual_check`, `append`, `main`).
- `scripts/research/e35_corpus_extract.py` (`merge`, `measurement_key`, `main`).
- `scripts/ci/check_collapsed_states.py`, `scripts/ops/render_session_brief.py`,
  `.github/actions/commit-to-main/action.yml`, `.github/workflows/e35-bracket-sweep.yml`.
- `docs/research/e35-bracket-corpus.jsonl` (8,000+ rows), the disposition
  ledger, and both affected registers.

## Work Completed
Nine PRs merged: **#10571, #10572, #10573, #10575, #10579, #10580, #10583,
#10589, #10591**. #10538/#10539 were unstranded and merged.

1. **Guard evidence model** (#10573) — `collapsed-state-guard` now credits
   module constants and excludes its own registry from counting as a consumer.
   `GRANDFATHERED_UNREAD` 4 → 1; three of the four were the guard's own blind
   spot.
2. **Branch stranding fixed** (#10575) — `session-brief-guard` diff-scoped, so a
   brief going stale on the CLOCK no longer reds a PR that did not touch the
   registers. Plus a one-shot stale-branch refresh in `commit-to-main`.
3. **Live probe** — dispatch `33388490432` → sweep `33388518030` → #10576/#10577
   merged. 995 corpus rows now carry `RQ-20260831-002`/`accruing` where the
   baseline had `research_unit: None` on all 9,744.
4. **e35 history sidecar** (#10583) — displaced rows archive to
   `e35-bracket-corpus-history.jsonl`, written BEFORE the corpus (the corpus
   write is what destroys them), non-zero exit on archive failure.
5. **Disposition write surface** (#10589) — `--record` on the CLI, plus a
   pre-flight refusal to record a unit the corpus does not hold.
6. **14 units read** (#10589) — 42 unread → 28; ledger 61 → 75 units / 89 rows.
   One `no_action_warranted` (`trend_donchian_sol_4h`), 13 `underpowered`.
7. **The finding** (#10591) — `BL-20260831-EVERY-PASSING-E35-CELL-IS-IN-THE-BATCH-THE-POWER-GATE-CANNOT-READ`.

## Validation Performed
- **Mutation testing on every load-bearing test.** The sidecar's ordering test
  fails under both plausible regressions (drop the `OSError` refusal; swap the
  corpus write ahead of the archive). The `--record` pre-flight test fails when
  the check is removed. A guard self-test was planted-and-verified.
- **Artifact verified, not just CI** (the `session-handoff` gate): the ledger's
  14 new rows are well-formed, all carry `accrual_check` (so all went through
  `append()` rather than a raw write), and none dangles. `survey()` asserted to
  reproduce `dispositioned=75, unread=28` — the numbers reported to the operator.
- CI green on every merged PR; `run_guards.py` clean on each committed range.
- Three CLI controls: positive records, typo'd leg refused (rc 2), accruing unit
  closed terminally refused (rc 1).

## Documentation Updated
- `docs/claude/OPEN-ITEMS.json` — `OI-20260831-42-RESEARCH-UNITS-…` filed, then
  corrected twice (see Contradictions).
- `docs/claude/research-review-backlog.json` — 4 rows filed.
- Coordination board `✅ DONE` posted, naming the protocol lapse.

## Contradictions or Drift Found
1. **I filed a wrong number and corrected it.** The OPEN-ITEMS row first said
   "24 of the 42 read `n_oos=n/a`". Measured, it is **28** — wrong in the
   direction that understated how much of the pile is ungradeable.
2. **I wrote a true sentence that misleads.** "The sweep's answer for every
   readable leg is: change nothing" is correct as scoped and invites exactly the
   reading that the sweep found nothing. Replaced with the scope clause loud.
3. **My own probe committed the error it was written to catch.** A pass-matcher
   looking for `pass`/`ok`/`promote` returned zero across 28 units; every
   passing verdict in this corpus is spelled `wf_pass` or `path_b_wf_pass`.
4. `n_oos` is a `max` over rows while `power_state` two lines above is
   worst-state-wins — filed, harmless today only because each unit has one
   distinct value.

## Risks and Follow-Ups
- `BL-20260831-EVERY-PASSING-E35-CELL-IS-IN-THE-BATCH-THE-POWER-GATE-CANNOT-READ`
  (high) — the pipeline currently reports "nothing found" while all 17 of its
  passing cells sit in the batch it cannot grade.
- `BL-20260831-28-E35-UNITS-ARE-PERMANENTLY-UNGRADEABLE-AND-THE-VERDICT-VOCABULARY-CANNOT-SAY-SO`
  — the verdict
  vocabulary has no member meaning NEVER MEASURED.
- The sidecar stops future overwrites but **nothing reads it back** — a store,
  not a consumer. The same written-and-never-read shape one step earlier.
- The 229 `superseded_unread` measurements already destroyed are unrecoverable.

## Deferred Items
- **Re-sweeping the 28 legs from the 2026-08-29 batch.** This is the obvious
  next step and was deliberately NOT started: it is a multi-hour runner job, so
  it went to the operator rather than being fired autonomously.
- R1 (`RESEARCH-WORKFLOW-ARCHITECTURE`) is owned by `session_012LgMzB…`, not
  this session.

## Next Recommended Sprint
Re-sweep the 28 legs so their 17 passing cells become power-gradeable, then
disposition each pass on the record — actioned with the change named, or refused
with the reason (path_b-only, sub-economic delta, or under the floor). Read the
backlog row's `resolution_criteria` first: closing it by dispositioning the 28
without power, or by re-running the sweep and not reading the passes, does not
count.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage was touched (`docs/TRADE-PIPELINE.md` not applicable).
- [x] Roadmap status checked — no milestone row moves; this is research
      infrastructure under the existing research-queue work, not a milestone.
- [x] Contradictions were recorded, including three of my own.
- [x] Remaining unknowns stated: whether any of the 17 passing cells survives a
      powered re-sweep is UNKNOWN and is the whole point of the next sprint.
