# Sprint Log: S-M20-CORPUS-UNION-2026-08-17

## Date Range
- Start: 2026-08-17 (~00:00Z)
- End: 2026-08-17 (~01:50Z)

## Objective
- Primary goal: repair the 52 degraded corpus rows `S-M20-CORPUS-SCHEMA-2026-08-16`
  deferred, then land the corpus union onto `main` — the item that log named as
  the one gating other work rather than merely adding to it.
- Secondary goals: none planned. Three separate defects surfaced *while
  verifying* the repair and the union, and handling them set the rest of the
  session.

## Tier
- **Tier 1** throughout, with one deliberate exception noted below.
- Justification: a CI workflow, a research corpus, a research matrix `ref`, two
  backlogs, this log. The one file touched under `src/` —
  `src/runtime/market_data.py` — is Tier-2 by location, and is called out
  explicitly in "Work Completed" rather than folded into the Tier-1 total.
- **No Tier-3 decision was made.** Three reachability rows and two `stale_stop`
  adjudications remain `queued_tier3`; nothing here decided any of them.

## Starting Context
- Active roadmap items: M20 exit-lever programme.
- Prior sprint reference: `S-M20-CORPUS-SCHEMA-2026-08-16.md` (same night,
  earlier) — its "Next Recommended Sprint" is exactly this one.
- Known risks at start: the repair was recorded as **conditional**. Because
  `measurement_key` includes the *derived* `split`, a re-dispatch whose trainer
  frames had gained candles would move the boundary, change the key, and **add**
  52 rows beside the degraded ones instead of superseding them.

## Repo State Checked
- Branch or commit reviewed: `main` @ `cb8bc5a2` → `24846bfd` across the session.
- Deployment state reviewed: none directly, but see the market-data note — that
  file is on the live candle path and the trader redeploys on every merge to
  `main`.
- Canonical docs reviewed: `CLAUDE.md` § diagnostic provenance / collapsed
  states / always state the population; `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`
  (read rather than copied from the previous log); the coordination-board
  protocol.

## Files and Systems Inspected
- Code files inspected: `.github/workflows/m20-exit-lever-sweep.yml` (the
  `corpus` job, both the normal and the conflict path), `scripts/research/
  m20_corpus_extract.py` (`measurement_key` in full including its docstring,
  and the merge/supersede block at :558-606), `scripts/research/
  m20_coverage_rollup.py`, `scripts/ci/check_matrix_corpus_agreement.py`,
  `src/runtime/market_data.py` (`_candle_cache_key`, `_candle_cache_put`,
  `reset_candle_cache`).
- Config files inspected: `config/strategies.yaml` — **read-only**, to establish
  whether `stale_stop` is still declared for two legs.
- Docs inspected: both review backlogs, `docs/research/
  exit-refinement-coverage.json`, `docs/research/m20-sweep-corpus.jsonl` on two
  branches.

## Work Completed
- **The corpus repair (dispatched, verified).** Parameters were read *from the
  degraded rows themselves* rather than from my own board note — which is what
  caught `split_target_oos: 50`, a value my note had omitted and whose absence
  would have moved every derived boundary and turned the repair into an append.
  Result: **998 → 998 rows, delta 0**; 0 degraded remain; 52/52 carry all eight
  `live_tp_reach_r_*` keys; **all 13 derived splits reproduced exactly**;
  verdicts identical at 43 `is_oos_fail` / 4 `insufficient_base` / 3
  `path_b_wf_pass` / 1 `path_b_wf_fail` / 1 `PASS`.
- **#9820 — the commit message described the extraction that was discarded.**
  Found *while verifying the repair*: `.commitmsg` was built once, before the
  rebase, and reused by the conflict path that re-runs the extractor. Fix is one
  writer called again after the re-derive, plus a negative control on the
  pre-fix shape.
- **#9814 — a `tp_geometry` guard and the ratchet that makes it enforceable
  today.** Two checks: every present value must be a legend value, and the
  unstamped count may not exceed a declared ceiling (210). The ratchet bounds a
  known-bad population without requiring it be zero first.
- **#9817 — `src/runtime/market_data.py` (TIER-2, the live candle path).**
  `_candle_cache_key` keyed on `id(client)`. `id()` is unique only among
  *simultaneously live* objects and the cache stored the integer rather than a
  reference, so it never kept the client alive and CPython was free to reissue
  the address. Replaced with a lifetime-unique token that **refuses to cache**
  when it cannot be attached.
- **#9822 — `BL-20260817-FLEET-SWEEP-WF-COUNTS-INERT-FOLDS-AS-WINS`**, filed
  with a cross-reference to
  `BL-20260814-FLIP-VERDICT-PASS-IS-86PCT-DEGENERATE`
  so the two read as a pattern rather than two isolated cases.
- **#9823 — the union (1264 + 100 = 1364) and the two disagreements it
  surfaced.** Detail below; it is the substantive result of the session.
- **#9819 — a correction to my own earlier filing** (severity high → low), made
  after the fix removed the hazard I had described.

## Validation Performed
- Tests run: `tests/test_m20_sweep_workflow_inputs.py` (9), `tests/
  test_m20_tp_geometry_guard.py` (8, planted-omission), `tests/
  test_candle_cache_client_identity.py` (10).
- Guards: `scripts/ci/run_guards.py` **PASS 17 · FAIL 0** on the union branch,
  run with the work **committed** — an uncommitted run scans nothing and says
  so, which is why it is run after `git commit`, not before.
- Manual verification: every merge confirmed by **re-reading `origin/main`**,
  never by the merge SHA. `#9820` verified by finding `write_commitmsg` defined
  once and called at *both* sites (:636 normal, :694 re-derive); `#9822` by
  counting 638 backlog items and confirming both cross-referenced rows survived
  the union.
- Roll-up re-read fresh: headline **373/376 = 99.2%**, done-condition **25
  cells** (14 actionable + 11 arithmetic), geometry **166/376 = 44.1%**.
  Unchanged by the union, as predicted and then *checked* rather than assumed —
  the roll-up reads the matrix, not the corpus.
- Gaps not yet verified: `#9823` was open with CI running at the time of
  writing. The `#9820` fix is verified by test and by reading, **not** by a live
  conflicting run — only a re-dispatch that actually conflicts proves it
  end-to-end, and the 01:23Z run predates the merge.

## Documentation Updated
- Rules doc updates: none required.
- Architecture doc updates: none required.
- Trade pipeline doc updates: none — no pipeline stage was touched.
- Roadmap updates: none required; this is work inside the existing M20 item.
- GitHub Actions doc updates: none — the workflow fix is behavioural, and the
  workflow's own header already describes the job correctly.
- Subsystem doc updates: two `stale_stop` cell `ref`s in
  `docs/research/exit-refinement-coverage.json`.
- Backlogs: three health entries filed, one corrected, one performance entry
  carried from the earlier session.
- Historical docs marked superseded: none.

## What the verification showed that I could not have asserted beforehand

- **The union's merge rule is the opposite of the extractor's, and only
  measuring showed it.** The natural framing — "the branch has the new rows, so
  the branch is fresh" — is what the extractor's own merge implements
  (`fresh_keys` always supersede). Applied here it would have been **wrong**: of
  the 904 shared keys, 19 differ, and on **all 19** it is `main` that is newer
  (08-15 vs 08-13) and complete (8 `live_tp_reach_r_*` keys vs 0). A branch-wins
  union would have re-dropped precisely the schema `#9812` exists to stop
  losing — the same defect, reintroduced by the repair's own follow-up.
- **`git diff --numstat` returning `100 0` is a stronger claim than the row
  count.** 1364 rows could be reached while silently rewriting existing rows;
  "100 additions, zero deletions" proves all 1264 survive byte-identical. The
  count was the assertion I wrote first; the diff shape is the one that actually
  settles it.
- **My own board note was wrong about the two contradicting cells, and the
  correction makes the disagreement WORSE.** I had recorded that the newer
  config-exact rows carry "shipped levers in the base" and therefore ask a
  different question from the lever-off arm. Checking `config/strategies.yaml`
  and the rows' own `declared_levers_present: []` showed the lever was **removed
  from config on 2026-08-13**, so the config-exact base does not contain it —
  the base construction is the *same*, and only the split differs (corroborated
  by near-identical IS samples, 599/603 and 231/241). It is split sensitivity on
  one comparison, not two comparisons: a sharper finding, not a softer one.
- **Filing a defect an hour earlier changed how I read the next result.**
  Before quoting "wf 4/6" and "wf 5/6" as counter-evidence I checked the folds
  against `BL-20260817-FLEET-SWEEP-WF-COUNTS-INERT-FOLDS-AS-WINS`. Here all
  twelve carry non-zero deltas, so the counts are real — the check came back
  *favourable*, which is the outcome that makes running it feel unnecessary and
  is exactly why it had to be run.
- **The 42 branch-only rows with no `live_tp_reach_r_*` keys are not degraded.**
  They date to 08-13/08-14 and predate the field, the same state as `main`'s own
  884. Assuming they were more degraded rows would have manufactured a repair
  task that does not exist.

## Contradictions or Drift Found
- **Two matrix cells contradicted by the corpus the union imported.**
  `trend_donchian_eth / stale_stop` (`path_b_wf_pass`, wf 4/6, OOS n=49) and
  `trend_donchian_sol / stale_stop` (**PASS**, wf 5/6, OOS n=49) both record
  `honest_negative`. Resolved as the guard directs — evidence appended to the
  `ref`, **status untouched** (416 status cells before and after, zero changes).
  A passing CELL is not a passing LEVER disposition, and a live-leg status
  change is Tier-3.
- **A commit message contradicting its own commit** (`#9820`) — `superseded: 0
  … 1316 rows` over a file holding 998 with 52 superseded. Had I trusted it I
  would have reported a clean repair as failed.
- **My own severity rating contradicted by my own later fix** (`#9819`) — I
  filed `reset_candle_cache()`'s dead-code state as a high-severity hazard, then
  removed its cause an hour later without re-reading the filing.
- **A backlog id wrapped across a line break, for the THIRD time in one
  session** — this file's own first draft split
  `BL-20260814-FLIP-VERDICT-PASS-IS-86PCT-DEGENERATE`, so
  `artifact-validity-guard` resolved the prefix to nothing and failed `#9826`.
  Caught by the guard all three times, which is the guard working; but three
  occurrences in one night is not three accidents, it is the line-wrapping
  habit meeting an identifier that must not be wrapped. The earlier two were in
  Python and were fixed by hoisting the id to a named constant with a comment
  saying why it stays unwrapped. **Prose has no such hoist**, so the only
  remedy here is to break the line before the id rather than inside it — which
  is what this file now does in all three places it names one.

## Risks and Follow-Ups
- **The union does not fix the divergence mechanism.** The sweep workflow still
  routes corpus commits to `claude/m20-sweep-corpus` when dispatched from the
  default branch, and that branch still never merges `main`. This union is a
  snapshot sync; the two corpora will diverge again with the next run, and the
  next union will face the same newest-wins decision. Making the workflow merge
  `main` before extracting, or targeting `main` directly, is the durable fix and
  is **not** attempted here.
- Remaining product decisions (Tier 3): the three `queued_tier3` reachability
  rows (`xrp_pullback_2h`, `trend_donchian_sol_4h`, `scha_trend_long_1d`), and
  now the two `stale_stop` adjudications — which are better posed than before,
  since the disagreeing measurements sit side by side in one `ref`.
- Blockers: none.

## Deferred Items
- **The `#9820` fix is unproven end-to-end.** It needs a re-dispatch that
  actually takes a rebase conflict; the 01:23Z run predates the merge.
- **`matrix-corpus-agreement` reports 141 live cells NOT CHECKED** across
  `exit_head_ml`, `exit_ladder`, `regime_flip_exit` — no corpus rows exist for
  those levers. The guard states this honestly rather than scoring them clean,
  and closing that gap is its own work.
- **The `vol_trail` cells are now measurable but ungraded.** Both legs cleared
  the OOS floor (34 and 35 against 25). The single PASS was **not** recorded,
  because its six folds are all exactly zero.

## Next Recommended Sprint
- Suggested next sprint: **stop the corpus branch diverging** — make the sweep
  workflow merge `main` before it extracts, or write the corpus to `main`
  directly.
- Why next: every corpus finding this session traces to that one branch never
  merging `main`. It produced the schema drop (`#9812`), the stale commit
  message that path exposed (`#9820`), and the 100-row/360-row divergence this
  union closed by hand. Fixing the mechanism retires the whole class; unioning
  again does not.
- Required verification before starting: `#9823` merged, so the corpora start
  reconciled rather than mid-divergence.
- ✅ **DONE IN-SESSION (`#9827`, merged `5eb2aa11`).** The session continued past
  this log's first draft and executed its own recommendation rather than
  deferring it, so the recommendation is recorded as *completed* instead of
  being left to read as outstanding work.
  - `scripts/research/m20_corpus_union.py` — union one corpus into another,
    importing `measurement_key` rather than re-deriving it. Wired into the
    conflict re-derive **after** the reset and **before** the extract; ordering
    is guarded, because unioning afterwards merges into rows already derived
    against a truncated corpus.
  - **The merge rule is `sweep_generated_at`, not "the side being merged in
    wins."** That intuitive rule is the extractor's — right for
    artefacts→corpus, backwards corpus→corpus: on all 19 differing shared keys
    `main` was the newer and complete side, so a side-wins union re-drops the
    very schema `#9812` protects. A negative control runs the wrong rule over
    the same fixtures so the passing guard is not an unproven green.
  - Where a timestamp cannot order two rows the tie breaks only on a strict
    superset, and otherwise the union **refuses (exit 2)** rather than picking.
  - Validation: replaying the real pair reproduces the merged `#9823` artifact
    **byte-identically**; the workflow's own direction gives the mirror result
    (19 replaced + 360 appended → the same 1364); the outcome is
    **order-independent**, so the rule is a resolution rather than an artifact
    of which branch the job stands on.
- **Still open, and stated as open:** `#9827` is verified by test and by replay,
  **not** by a live conflicting run — the same gap `#9812` carries. A sweep was
  dispatched on `main` at 02:22Z (run `31987769491`, one leg, free runners) to
  exercise the conflict path for real. The falsifiable check was recorded
  BEFORE the run so it cannot be judged by impression: the corpus branch was
  missing **360** main-only measurement keys; after the run that must be **0**.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] N/A — this sprint touched no pipeline stage.
- [x] Roadmap status was checked.
- [x] Contradictions were recorded — including two of my own.
- [x] Remaining unknowns were stated clearly.
