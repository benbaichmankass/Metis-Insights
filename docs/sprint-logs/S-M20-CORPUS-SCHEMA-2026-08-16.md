# Sprint Log: S-M20-CORPUS-SCHEMA-2026-08-16

## Date Range
- Start: 2026-08-16 (~23:20Z)
- End: 2026-08-16

## Objective
- Primary goal: land the `tp_cap_pct` relabel `BL-20260813-TPCAP-REQUESTED-NOT-APPLIED`
  still owed, then explain a discrepancy noticed while doing it — tonight's 52
  corpus rows lacked `live_tp_reach_r_*` while `main`'s rows carry it.
- Secondary goals: none planned. What the discrepancy turned out to be set the
  rest of the session.

## Tier
- **Tier 1** (a CI workflow, a research corpus, two backlogs, this log).
- Justification: no `src/`, no `config/` behaviour, no order path. The one
  config file *read* (`config/lever_reachability.json`) was read only — no
  verdict, disposition or `arm_r` moved, and every one of those is Tier-3.

## Starting Context
- Active roadmap items: M20 exit-lever programme; M31 (position telemetry) P4.
- Prior sprint reference: `S-M20-PER-ERA-P80-2026-08-16.md` (same night, earlier).
- Known risks at start: none identified. The schema gap was not on any list —
  it was noticed only because the relabel work put me in the corpus.

## Repo State Checked
- Branch or commit reviewed: `main` @ `cb8bc5a2`, then `813c86d5` after the
  relabel merged.
- Deployment state reviewed: none — nothing here reaches a VM.
- Canonical docs reviewed: `CLAUDE.md` § diagnostic provenance / collapsed
  states / always state the population; the two backlog files; the sweep
  workflow's own header.

## Files and Systems Inspected
- Code files inspected: `.github/workflows/m20-exit-lever-sweep.yml` (the
  `corpus` job in full), `scripts/research/m20_corpus_extract.py`
  (`measurement_key`, the merge/supersede block, the `leg_common` field set),
  `scripts/research/m20_fleet_exit_sweep.py` (the `live_tp_reach_r` writer at
  :2283 and the SUMMARY reach table at :2858), `scripts/ci/check_lever_reachability.py`.
- Config files inspected: `config/lever_reachability.json`, `config/strategies.yaml`
  (`tp_r` per leg) — **both read-only**.
- Docs inspected: `docs/claude/health-review-backlog.json`,
  `docs/claude/performance-review-backlog.json`.
- GitHub Actions inspected: run `31976325152`, corpus job `95236789561` — the
  job LOG, not the run summary.

## Work Completed
- **Item 1 — the relabel (#9810, merged `813c86d5`).** 140 rows `null → 0.099`
  across 10 legs; 1264 in, 1264 out. `BL-20260813-TPCAP-REQUESTED-NOT-APPLIED`
  was marked `resolved` while its own criteria said one `--apply` run remained.
- **Item 2 — the schema downgrade, root-caused (#9812).** The sweep's conflict
  path does `git reset --hard origin/$TARGET` and then re-runs the extractor.
  The reset reverts the **whole worktree, including the extractor itself**, so
  the re-derive runs the target branch's copy. Fixed by preserving the
  dispatched extractor to `$RUNNER_TEMP` before any git operation can revert it.
- **Item 3 — two guards.** One asserts no bare worktree-path extractor call
  survives after the reset; one is a **negative control** on the exact pre-fix
  shape, because a guard never shown to fire has an unproven green.
- **Item 4 — the consumer gap.** Chasing the dropped field surfaced that where
  it *does* survive nothing reads it. Filed as
  `PB-20260816-REACHABILITY-REGISTRY-IGNORES-THE-CORPUS-TP-REACH-IT-KEEPS-RE-MEASURING`.
- **Item 5 — the data half left open, deliberately.** The 52 already-written
  rows are still degraded; the code fix does not repair them.

## Validation Performed
- Tests run: `tests/test_m20_sweep_workflow_inputs.py` +
  `test_m20_summary_split_line.py` — **16 passed** (5 pre-existing, 2 new, plus
  the file's existing negative control).
- Dry-runs or staging checks: `scripts/ci/run_guards.py` **28 PASS / 0 FAIL**
  with the work committed. Run twice on purpose: the first pass was made with
  the work uncommitted and correctly reported it had scanned nothing. That
  warning is why it was repeated rather than trusted — the same discipline the
  earlier sprint this night recorded.
- Manual verification: the merge of #9810 was confirmed by **re-reading
  `origin/main`**, not by the merge SHA — 1264 rows, `tp_cap_pct`
  `{'0.099': 1264}`, zero nulls.
- Root cause confirmed from the **job log**, not inferred from the symptom:
  `##[notice]rebase conflicted … HEAD is now at 9a085926`, then
  `runs merged: 13  new rows: 52`.
- Coverage roll-up re-read on merged `main`: headline **373/376 = 99.2%**,
  done-condition **25 cells** (14 actionable + 11 arithmetic) — unchanged by the
  relabel, as predicted and then checked rather than assumed.
- Gaps not yet verified: the fix is verified by test and by reading, **not by a
  live conflicting run**. Only a re-dispatch that actually conflicts proves it
  end-to-end, and one has not been made.

## Documentation Updated
- Roadmap updates: none required — this is tooling and record-keeping inside
  existing M20/M31 items.
- Backlogs: one health entry (the downgrade), one performance entry (the
  consumer gap).

## What the verification showed that I could not have asserted beforehand

- **The degraded path is the NORMAL path, not a rare race.** The workflow's own
  comment frames the conflict as ordinary concurrency ("both sides added rows").
  Measured: `claude/m20-sweep-corpus` is **123 commits behind `main`** and never
  merges it, so a `main`-dispatched run rebases `main`'s history onto a
  long-diverged branch and takes add/add conflicts across ~30 unrelated files
  **every time**. I had assumed I was looking at an edge case.
- **A PREDICTION I GOT WRONG, again, and it is the more useful half.** I
  expected the derived `cap_r_p50` and the measured `tp_r_effective` to differ
  because production places `tp = min(entry*1.099, entry + tp_r*risk)`, so the
  effective TP would be `min(cap_R, tp_r)`. Refuted in one read: **every leg
  declares the sentinel `tp_r = 50.0`**, so the venue cap always binds and the
  two quantities are the same thing. The gap (derived 11.91 vs measured 5.833;
  3.18 vs 1.918) is therefore real, one-directional, and NOT explained away.
- **And then that finding was already known** — `lever_reachability.json`'s own
  `basis_note` says the candle screen "is NOT a bound in either direction — on
  xrp it overstated reachability 90.5% vs 33.3%". Checking before filing is what
  kept this from becoming a duplicate; it is the fifth such near-duplicate this
  night that a prior read caught.
- **The corpus agrees with the bespoke relays.** `trend_donchian_sol_4h`: corpus
  max **3.723** < arm **5.57** ⇒ 0/135 reachable, independently reproducing
  relay #9715's 0/127 = 0.0%. `scha_trend_long_1d`: arm 2.00 below every corpus
  median, agreeing with relay #9710's 83.1%. Agreement across two independent
  computations is what turns "the field exists" into "the field can be joined".

## Contradictions or Drift Found
- **A field built to avoid a collapsed state acquired a new one via transport.**
  #9037 made `live_tp_reach_r`'s `None` deliberately three-way — predating the
  field / cap OFF / cap on but no measurable TP — with `_n` as the separator. A
  row where the **key is absent entirely** is a fourth state that design did not
  contemplate: *measured, then dropped in transit*.
- **The registry buys what the corpus already holds.** 376 rows across 34 legs
  carry an entry-conditioned cap_R; `check_lever_reachability.py` has **zero**
  references to it, and the registry has been sourcing the same quantity one leg
  at a time via bespoke relays (#9710, #9715).

## Risks and Follow-Ups
- Remaining technical risks: the corpus repair (below) is **conditional**.
  `measurement_key` includes the **derived** `split`, recomputed per leg from
  trainer frames under the default `oos-trades` mode. If those frames gained
  candles, the boundary moves, the key changes, and a repair run **adds** 52 rows
  beside the degraded ones instead of superseding them.
- Remaining product decisions (Tier 3): the three `queued_tier3` reachability
  rows (`xrp_pullback_2h`, `trend_donchian_sol_4h`, `scha_trend_long_1d`).
  **Not proposed here.** This session measured; it did not grade.
- Blockers: none for the code; the corpus union onto `main` is blocked on the
  data repair.

## Deferred Items
- **The 52 degraded rows are NOT repaired.** The underlying measurement is not
  lost — `verdicts.json` carries `live_tp_reach_r` per leg and the SUMMARY reach
  table is built from it, so only the corpus *hop* dropped it — but repair needs
  a re-dispatch once #9812 is on `main`, and this session cannot download run
  artifacts to re-extract locally. **Assert afterwards that the row count is
  unchanged and the extractor reported `superseded: 52`.**
- **The corpus union onto `main` stays blocked.** 885 shared keys, 113
  branch-only, 379 main-only; a union by key is the only safe operation, and
  doing it now would import the 52 degraded rows into the canonical corpus
  permanently.
- **The join itself is not wired.** Filed, not built — and the caveat matters
  more than the wiring: `tp_r_effective` is the venue-cap **identity**, not the
  operative ceiling, because `exit_plan_realism.DEFAULT_MAX_REACH_R = 5.0` binds
  first wherever `cap_R > 5.0`. `trend_donchian`'s corpus median is 5.833,
  already past it.

## Next Recommended Sprint
- Suggested next sprint: **the corpus repair + union**, in that order.
- Why next: it is the one item that gates other work rather than merely adding
  to it — every downstream consumer of the corpus reads `main`, and the union
  cannot land until the degraded rows are gone.
- Required verification before starting: #9812 merged, so the re-dispatch picks
  up the fixed workflow.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] N/A — this sprint touched no pipeline stage.
- [x] Roadmap status was checked.
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.
