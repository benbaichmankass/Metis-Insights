# S-M20-TPCAP-PROVENANCE-AND-BLOCK-SIZE-2026-08-13

## Date Range

- **Start:** 2026-08-13 ~13:00 UTC
- **End:** 2026-08-14 (still in flight; overnight autonomous session, ran past midnight UTC)

## Objective

- **Primary:** advance M20 exit-refinement coverage — grade what the corpus can
  answer, and work the open cells down.
- **Secondary:** fix the sweep→corpus provenance defects surfaced along the way,
  and answer the operator's question of whether E1's fold minimum is derived.

## Tier

**Tier 1** for everything shipped (research tooling, corpus data, matrix
statuses, docs, tests). One Tier-2-shaped action — the corpus-branch write —
was explicitly authorised by the operator in-session. **No live lever was
touched, no Tier-3 change enacted.**

## Starting Context

- M20 headline 362/376, done-condition 36 open (14 pending + 22 blocked).
- Both prop legs (`trend_donchian_sol_prop`, `trend_donchian_eth_prop`) had
  entered the denominator on 2026-08-13 with all 8 columns `pending`.
- Known risk carried in: `BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP`
  — the harnesses modelled no take-profit while production clamps to 9.9%.

## Repo State Checked

- Branch `claude/m20-exit-coverage-matrix-8d3he7`; `main` at `ea3dd512` at start.
- `origin/claude/m20-sweep-corpus` at `0f2c791e` (925 rows).
- **The working copy's corpus was 42 rows STALE** (883 vs 925) — see
  *Contradictions*.

## Files and Systems Inspected

- `scripts/research/m20_corpus_extract.py`, `m20_fleet_exit_sweep.py`,
  `m20_coverage_rollup.py`, `m20_corpus_relabel_tp_cap.py` (new)
- `scripts/ml/train_exit_head.py` — `fold_blocks`, `per_leg_summary`,
  `eval_split`, `agg`
- `scripts/backtest_trend.py` (TP-cap application, lines 461-472)
- `scripts/ops/build_trainer_datasets.sh` (`EQ_1D_START`, `build_equity_daily`)
- `.github/workflows/m20-exit-lever-sweep.yml`
- `docs/research/exit-refinement-coverage.json`, `m20-sweep-corpus.jsonl`
- Trainer relays #9049 (data spans + block-size grep), #9055 (E1 artifacts),
  #9063 (per-fold arms — pending at time of writing)

## Work Completed

1. **PR #9037 (merged).** Fixed the `split: null` regression I introduced in
   #8965 (the boundary moved per-leg; the extractor still read the doc level);
   added the workflow's missing `split_mode` input; plumbed `live_tp_reach_r`
   onto every corpus row so a row can state whether the TP cap **bound**, not
   only which value was requested. 3 tests, can-fail verified.
2. **Corpus relabel applied** (`dea29fec`, operator-authorised cross-branch
   write). 140 rows recording `tp_cap_pct: null` were measured **at 0.099**.
3. **8 prop-leg cells graded** `honest_negative` (PR #9048) — headline
   **362/376 → 370/376**, done-condition **36 → 28**.
4. **`--min-fold-trades` audited** at the operator's direction: it is an
   **undefended default**.
5. Filed `BL-20260813-TPCAP-REQUESTED-NOT-APPLIED` (resolved) and
   `BL-20260813-EXIT-HEAD-ML-1D-LEGS-UNREACHABLE` (open, reframed twice).
6. **Block-size derivation landed** (`docs/research/M20-E1-block-size-derivation-2026-08-13.md`)
   at the operator's direction to derive the value from a stated statistical
   target and accept whatever it says. It says **keep 50**, and it says the
   more useful finding is upstream of the block size — filed as
   `BL-20260813-EXIT-HEAD-EDGE-SMALL-AND-INCONSISTENT`.
7. **Both of that row's open measurements are now done** (relays #9071 / #9072 /
   #9074 / #9075, 31 reports — up from the 21 the derivation used), and both
   land badly for the exit head. See *Validation* below. The doc gained §§ 6–8
   and the backlog row was rewritten around the results.
8. **Hindsight-free τ selection measured** (#9077, 514 folds) — the fleet's
   entire edge is the best-arm selection. Doc § 9.
9. **`train_exit_head._select_tau_holdout` shipped** — per-fold nested τ
   selection on a validation block carved from that fold's own training window,
   three-state, observe-only, 3 can-fail-verified tests. The gate is untouched.
10. **Followed the thread to a live lever.** The API twins of the two pending
    prop legs are `trend_donchian_sol` / `_eth` at 1h, whose `exit_head_ml`
    cells read `shipped`. Verifying that rather than inheriting it turned up a
    stale docstring on a money path, a confirmed-open apply gate, and a lever
    that may never have fired. Filed as
    `BL-20260813-SHIPPED-DONCHIAN-1H-HEAD-RESTS-ON-BESTARM`.
11. **PR #9099 (merged `92759864`).** Two things: an audit of the **blocked
    bucket** — seven `*_trend_long_1d` legs read
    `blocked:insufficient_lifetime_trades` while all seven carry an E1 verdict;
    the STATUS is defensible (those verdicts are calendar-geometry and
    `per_leg_note` says the geometries are not comparable evidence) but the
    **ref never records that the choice was made**, so the next auditor reads
    it as staleness — and the nested-τ measurement below.
12. **The nested-τ measurement landed** (relay #9101) — the number § 9 left
    open. Over the donchian-1h family (3 legs, `trades` geometry, b=50, **21 of
    22 folds `selected`**, 1 `no_validation_block`, 63 leg-folds): nested
    holdout τ scores **+0.137R vs actual (57.1% positive)** against the
    **+2.788R** the gate credits and **−0.367R** for a τ-blind median-arm
    control. Causal and credited are an order of magnitude apart, and the
    selector does beat the blind control — so it finds something real that is
    nowhere near the headline. Doc § 10; backlog item 6 closed for this family.
    **The finding that decides the cell: no leg beats BOTH the actual exit and
    the cheap deterministic lever** — BTC −1.118R vs actual / +1.351R vs
    `stale_8_0`; ETH +0.692 / −0.070; SOL +0.837 / −0.311. The thing that wins
    on two of three legs is a fixed rule with no model, no training and no τ.
13. **Self-corrected the same hour.** I first wrote "+0.137R is 4.9% of
    +2.788R, so ~95% of the edge is hindsight." Arithmetically right, **not a
    usable statistic** — and the backlog row already said so about "% of edge"
    ratios. It applies here for a *different* reason: the instability is in the
    **numerator** (per-leg means span −1.118 to +0.837, so the pooled mean
    carries ±0.5R at n=63 and the ratio swings through zero). Corrected in the
    doc and the backlog before it could propagate.
14. **Scalp nested round launched** (relay #9103, backgrounded on the trainer).
    Scalp is the ONLY family positive under both causal rules, so it is the one
    whose nested number could move a decision permissively — and all 7 legs are
    **single-leg families**, i.e. `n_leg` = b = 50 exactly (§ 6), which makes
    them the strongest evidence in the fleet rather than the weakest. Each leg
    runs against a **symlink to the original `rows.jsonl`**, so the comparison
    isolates the τ-selection change from any dataset rebuild.
15. **The scalp nested round LANDED, and it splits the verdict by family** (relays
    #9103 run / #9108 read). All 7 scalp legs, 73 leg-folds (80 folds less exactly
    one `no_validation_block` per leg — each leg's first fold): nested holdout τ
    scores **+3.848R vs actual, 79.5% positive**, against **+6.022R** credited and
    **+3.475R** for the τ-blind median-arm control. **5 of 7 legs pass the
    two-sided test** (beat actual AND `stale_8_0`) — against **2 of 7** under § 9's
    PREV rule, which is that section's own prediction confirmed.
    **The τ-blind control is the sharpest statement of the split:** on scalp the
    median arm alone scores +3.475R, so τ choice barely matters and the edge is in
    the head; on donchian it was −0.367R, so what the gate credited there WAS the
    τ choice. Between-leg SE: scalp is **6.4 SE** from zero, donchian **0.22 SE**.
    Doc § 11. **Three caveats ride with it and are recorded, not buried:** the
    aggregate margin over `stale_8_0` is thin (+0.668R, 56.2% — a coin flip with a
    lean); the two 15m PASSes rest on 5 and 6 leg-folds; and `eth_15m`, the
    weakest leg, is the one that **already has a shipped stale-stop**, so the cheap
    lever winning there is coherent with what is deployed.
16. **PR #9114 (merged).** The seven `*_trend_long_1d` `exit_head_ml` refs now
    **say** they are declining a verdict they already hold. The status was
    always right; the ref never recorded that the choice had been made, so the
    next auditor reads the matrix as stale. Numbers **re-verified from the
    report** (relays #9112 + #9113) rather than quoted from my own audit note —
    and the first probe read key `per_leg_summary`, which is the **function**
    name, not a key. It returned "no entry in any report" for all seven legs,
    the *opposite* of the truth; its positive control (0 legs found anywhere,
    while `per_leg_note` resolved fine) is what flagged the probe rather than
    the data. Both relays carry a positive control for that reason. Internal
    check recorded with the numbers: the only two `insufficient_base` legs are
    exactly the two under the floor of 25 (qld 23, tqqq 24). Same PR fixed the
    CLAUDE.md `db/table` row, which still documented the pre-`filter_state`
    return shape.
17. **PR #9115 (merged) — the lever-column sweep #9099 declared as its own gap.**
    Result is **negative**: across **all 28 open cells on live legs**, every one
    carries a measured, dated, numeric ref. The `exit_head_ml` 1d bucket was the
    only instance of that shape. **Denominator stated**, because a clean sweep
    without one is the unasserted-negative shape: 17 membership + 6 pattern
    validations, and the count (28) reconciles with `--done-condition` (28).
    Two shadow-leg cosmetics filed (`BL-20260814-COVERAGE-MATRIX-SHADOW-ROW-METADATA`):
    `fvg_range_15m/trail_geometry` is `blocked` while its ref describes `n/a`,
    and `shadow fleet/giveback_stop` has no `ref` key. **Neither moves a
    published number — checked against `--done-condition`, not assumed.**
18. **PR #9116 — `collapsed-state-guard` had a real hole, and it was MEASURED.**
    I registered `db_explorer.filter_state` with that guard earlier in the
    session and recorded at the time that the registration was weaker than I had
    claimed. Confirmed: collapsing `filter_state` so it could only ever return
    `"applied"` left the guard **clean**, because the sibling `order_state` in
    the same file still carried the other two literals — producer integrity was
    file-scoped. That is the guard's own *"cheaper to lie to than to satisfy"*
    failure one level up: not a false annotation, but a **neighbouring field
    standing in as evidence**. Fixed with an opt-in `producer_field` (line-scoped,
    because producers here emit states via bare returns, tuple returns and module
    constants, so an assignment regex would match almost none of them);
    registered `order_state` as its own contract (it was unguarded entirely,
    which is how it came to be supplying its sibling's evidence); and added the
    **self-test this guard never had** — the reason the hole survived its own
    review.
19. **The db-explorer fix is LIVE-VERIFIED, not merely merged** (relay #9117) —
    a change is done when it is active in production. The probe was
    **discriminating**, since one call returning `ignored_unknown_column` would
    not prove `applied` is reachable: a bad column returns
    `ignored_unknown_column` with `total` **4643** (the whole table), a real
    column returns `applied` with `total` **1109**. *(4643 vs the 4639 measured
    2026-08-13 is four rows of overnight growth, not a contradiction.)*
    `BL-20260813-DB-EXPLORER-SILENTLY-IGNORES-UNKNOWN-FILTER-COLUMN` is now
    fully closed — all six sub-items.
20. **Two concurrent-merge conflicts, both resolved with invariant assertions**
    rather than by eye: the sprint log (my own #9104 squash vs item 15) and the
    backlog (`updated_at` only — items merged cleanly). The backlog resolution
    asserted item count **529**, **no duplicate ids**, and that *both* sessions'
    edits survived, because a union merge that silently drops one side is the
    failure mode a byte-count check would miss.

## Validation Performed

- 125 tests pass across the four M20 suites; 58 after the merge resolution.
- `m20_coverage_rollup.py --validate` OK after every matrix write.
- All relevant guards pass locally against `main` before each push.
- **Corpus write verified independently of the script that made it:** 925→925
  rows, cap distribution `{0.099: 785, null: 140}` → `{0.099: 925}`, exactly
  140 rows changed and all `null → 0.099`, 0 outside the evidenced window, and
  every row's **non-cap content byte-identical** (symmetric diff 0 over a
  dict-compare excluding `tp_cap_pct`).
- **Positive control for the cap:** `backtest_trend.py` on a 4,000-bar
  synthetic trending series, identical args but for the cap — 57 trades /
  net_R 466.37 / `tp_r_effective_n` 0 versus 127 / 347.55 / 127 at median reach
  4.41R. This is what made the corpus's identical books evidence that *both*
  arms were capped rather than evidence the flag was inert.

- **The exit head's edge, measured two ways it had not been.** Both from the
  same 31 `e1_report.json` files, per fold-geometry × family:
  - **Selection is the effect, not a bias on it.** Scoring the *median* of the
    7 tau arms instead of the max the gate credits flips the sign in **all six
    non-scalp groups** (`pullback` b=50 +1.362R → **−1.379R**; `donchian`
    calendar +0.426R → **−4.399R**), while every scalp group stays positive
    (`ict_scalp_5m` +6.037R). 23–50% of non-scalp folds are *flips* — the
    median arm loses to doing nothing while the selected arm wins.
  - **`beats_hard` fails the 2/3 bar in every fold geometry** (58.3% calendar /
    62.2% b=15 / 61.8% b=50) while `beats_actual` clears it in two of three.
    The fixed-rule comparison is the binding condition fleet-wide.
- **Verdict reconstruction verified against the field.** I derived the
  `candidate`/`honest_negative`/`insufficient_base` formula from
  `per_leg_summary` source and reconstructed all 35 leg-rows before pulling the
  actual `verdict` key; the reconstruction matched **35/35**. (The first pull
  read the key as `candidate`, which does not exist — it returned null for
  every row. Caught as a key-name miss, not reported as "no leg is a
  candidate".)

### Gaps not yet verified

- **The nested tau selection has NOT been run, and it gates the conclusion.**
  Median-arm is the expected edge of a tau chosen at *random*: it bounds a
  sensibly-chosen tau from below and a badly-chosen one from above. The
  deployment truth sits between the median-arm and best-arm columns, and
  locating it needs tau picked on each fold's TRAINING half and scored on its
  TEST half. The reports do not contain that. **"Median-arm is negative" must
  not be read as "the deployed head would be negative"** — it is not that
  claim, and the distinction is the whole reason item 4 was added to the
  backlog row's remaining work.
- The **"% of edge" ratio is not a usable statistic** and is quoted nowhere:
  it is unstable near a zero denominator (`pullback` b=15 computes to −3421%
  purely because its edge is −0.027R).
- `slv_trend_1h` (4 folds) and `uso_trend_1h` (3 folds) are too thin to carry
  weight and are excluded from every reading above.
- **Hindsight-free τ selection, 514 folds (#9077).** best-arm **+1.217R /
  70.2%** of folds positive; expanding-window causal **−0.341R / 54.1%**;
  previous-fold causal **−0.674R / 50.8%**. The fleet edge is the selection.
  Every scalp group stays positive under both causal rules; **no** non-scalp
  group does. Against `stale_8_0`, 2 of 7 scalp legs clear.
- **The live exit head's three apply conditions, each READ rather than
  inherited from the July ref:** (a) `exit_head_action: close` on three
  `execution: live` strategies — confirmed in `config/strategies.yaml`;
  (b) artifact `exit-head-donchian-1h-v1` at stage **`advisory`**, shape
  `below_half_r @ tau=0.1` (#9078) — the promotion gate is open, and the
  sibling `-peak-1h-v1` is at `shadow` and not applying; (c) the per-bar
  policy. So the head is live and applying.
- **…and it may never have fired.** In 137 parsed rows of a `lines=400` tail of
  the 430,743-byte `exit_lever_soak.jsonl` spanning 2026-08-09→08-12 (#9080),
  the lever counts are `trail_decay` 111, `stale_stop` 26, **`exit_head` 0**.
  The **positive control** is what makes that zero mean anything: 32 of those
  rows are donchian-family strategies, so donchian monitors are running and
  writing to this exact log in this exact window. Three days is not the head's
  month-long lifetime and the payload was truncated, so this is suggestive, not
  a lifetime count — recorded as such.

### Gaps not yet verified (continued)

- **Whether the live head has EVER fired is still open, and could not be
  answered from here.** `/api/diag/log_file` only *tails*; there is no
  relay-reachable way to count occurrences across a whole live log. So "how
  many times has this lever fired" is currently unanswerable from a sandbox
  session for **any** lever, not just this one. That is a read-surface gap, and
  it matters because the answer flips the urgency completely: an inert lever
  cannot make bad exits, but a lever that is live in config and has never acted
  is not what `shipped` = "validated + live" conveys either.
- **The live VM's published copy of the artifact was not read** — #9078 read the
  *trainer's* mirror, which is the rsync source. A month-old artifact with
  continuous publishing makes them almost certainly identical; "almost
  certainly" is not a reading. Related: exit_head artifacts are absent from the
  `/api/diag/log_file` allowlist, so the artifact that can close a live position
  has no live read surface — the same shape as the 2026-08-12
  `exit_loop_health` omission (writer shipped, allowlist entry not).
- **`_select_tau_holdout` has not produced a single number yet.** Every
  `e1_report.json` on the trainer predates it, so `selected_tau` is absent
  everywhere. The code is landed and tested; the measurement needs a fresh
  round. Until then the causal interval keeps only its lower end.
- **Relay output truncation is a live hazard on this workflow.** #9071's FOLD
  block hit the comment cap and came back carrying a `... (truncated)` marker;
  241 of the rows parsed cleanly, which would have read as a complete
  population had the marker not been checked for. No statistic was computed
  from it — the aggregates were re-derived on the trainer instead (#9074,
  #9075) so the arithmetic is never done over a silently-short list. This is
  sub-class **C** of the diagnostic-provenance rule (unasserted denominator)
  arriving through the relay rather than through a script.
- Whether a longer history actually yields the projected trades — the earlier
  regime need not signal at the recent rate. Every projection here applies a
  *recent* measured rate to *older* history and is labelled as such.
- **CI attachment on PR #9048 is intermittent, and I mischaracterised it
  twice before measuring.** What the run list actually shows: `pull_request`
  runs fired and passed for the first four commits after the PR opened
  (`f8f274ba`, `4c84c01c`, `9eee230e`, `74fdbdde`), then stopped from
  `599885cd` (~19:00Z) onward — five consecutive commits with no
  `pull_request` run while `get_status` reported `total_count: 0`. So it is a
  real recurrence of `BL-20260730-PR-CI-NOT-ATTACHING`, but **not** the
  "never attaches" I reported in two hourly pings, and my earlier reading of
  "four green checks on a stale SHA" was a *correct* observation given a
  *wrong* inference — I treated a lag as an absence, dispatched, and the
  dispatch then looked like the fix. Green evidence on this branch comes from
  the `workflow_dispatch` runs (`044afec3`, `74fdbdde`, `e43423cc` — all
  success) plus a full local guard run before every push. The current head is
  dispatched and not assumed green.

## Documentation Updated

- `docs/claude/health-review-backlog.json` — two rows (one resolved, one open
  and twice-reframed).
- `docs/research/RESEARCH-CAPABILITY-INDEX.md` — routed the new relabel script
  (caught by `artifact-validity-guard`).
- `docs/research/exit-refinement-coverage.json` — 8 cells graded, 8 cells
  annotated then twice corrected in place.
- This log.

## Contradictions or Drift Found

1. **`tp_cap_pct: null` did not mean "uncapped".** 140 rows were measured at
   0.099 by a run whose sweep script predated the provenance field. Diagnosed
   from the **run head SHAs** (`d76643b9` no field, `e6e519d1` field, ~8 min
   apart on the feature branch) after both runs proved to be `event: push`, so
   `inputs` was empty and `TP_CAP_PCT` fell to the workflow default.
2. **A feature branch's corpus goes stale silently.** The sweep commits to
   `claude/m20-sweep-corpus`; the working copy was 42 rows behind, and a leg
   appeared to have *no* capped rows when it had seven.
3. **`--min-fold-trades` is asserted, not derived** — a bare `default=50`
   forty lines below `_MIN_FOLD_TRAIN_ROWS = 500`, which explains exactly what
   it floors and why. One of the two numbers gating every E1 verdict is
   derived; the other is not.
4. **The E1 bar is 3× the block, not 2×.** `u = floor(N/b) − 1` and the gate
   requires `u >= 2`, so block 50 needs **150** trades to be graded at all.
   ⚠️ **This entry originally said the matrix refs and the `fold_blocks`
   docstring "stated 100" and were wrong. BOTH halves of that were my error,
   and it is instance SIX of the very pattern this log's postscript catalogues
   — asserted, in the document about not asserting.** (a) `100` appears
   NOWHERE in `train_exit_head.py`; the docstring does not reason from a wrong
   figure, it is SILENT on the trades-mode requirement, which is a gap and not
   a defect. I claimed a defect in a file without grepping it. (b) The matrix
   ref's "98 lifetime harness trades vs the 100 needed for a single 50-trade
   block" is **literally correct** — `N >= 2b` does yield one block. Its error
   is incompleteness: one fold does not pass a gate requiring `u >= 2`. Saying
   it "stated the wrong number" overstated a real but different problem.
   The genuine finding survives — 150, not 100, is what the gate needs — and
   the docstring gap is now closed rather than a "correction" being applied to
   text that never made the claim.
5. **`EQ_1D_START` does not describe the built files.** Declared default
   `2015-01-01`; measured spans SPY 2010, QQQ 2007, TQQQ/QLD 2016.
6. **The per-fold vote is cast on `n_leg`, not on the block — my own § 3 was
   wrong, and it is instance SEVEN of the postscript's pattern.** The
   derivation models the vote as `P(correct) = Φ(√b · δ)`, substituting the
   block size for the count the vote is actually computed over. `fold_blocks`
   cuts blocks over the **family's** pooled trades; `per_leg_summary` casts one
   vote per **leg** per fold on that leg's own `n_trades`. Measured (#9074):
   multi-leg families vote at median `n_leg` of **3** (`pullback` b=15, min 1),
   **5** (`allmix`), and **12** (`pullback` b=50), while every single-leg family
   sits exactly at 50. So the doc's headline 0.771 per-fold reliability is true
   only for single-leg families; the 1d equity legs the document is *about*
   vote at 0.57–0.64. Same shape as the others — I read a value I had assumed
   (`b`) as the one that was measured (`n_leg`), and it was the reassuring
   reading. Corrected in § 6; the keep-50 recommendation is **strengthened**,
   not reversed, because raising `b` in a multi-leg family buys `n_leg` only in
   proportion to that leg's share.
8. **`exit_head_shadow.py` said "observe-only by construction" while its output
   closes live positions.** The module docstring claimed the function *"returns
   `None` always"*, that *"nothing reads the logs back"*, and that *"graduation
   to a real exit influence is E3"* — future tense. The field:
   `trend_donchian.py:909-911` does `eh_verdict = _exit_head_verdict(...)` /
   `if eh_verdict is not None: return eh_verdict`, and
   `config/strategies.yaml` declares `exit_head_action: close` on
   `trend_donchian` (:213), `trend_donchian_sol` (:1102) and
   `trend_donchian_eth` (:1172), all `execution: live`. The *function*
   docstring was already correct; only the module header — the one a reader
   hits first — had gone stale. It was load-bearing in the wrong direction:
   it did not merely fail to describe the apply path, it argued there wasn't
   one. Corrected (docstring only, field untouched).
9. **Verdicts flip across fold geometries, as the report's own note warns.** Of
   the 8 legs measured under both trade-folds and calendar-folds, 7 agree and
   `iaum_pullback_1d` disagrees (`honest_negative` under trade-folds,
   `candidate` under calendar-folds). Low rate, stated rather than smoothed —
   the `fold_mode` field exists precisely so this is legible.

## Risks and Follow-Ups

- **Open, operator-gated:** `BL-20260813-EXIT-HEAD-ML-1D-LEGS-UNREACHABLE`.
  Full-inception history does **not** rescue the seven equity legs at block 50
  (projected 50–104 lifetime trades against 150 needed, 0 of 7 reach it), so
  the block size is the binding constraint and is being derived.
- **`live_tp_reach_r` has the same field-after-behaviour window open right
  now** that caused the relabel: rows written between the flag and the next
  sweep will read `null` meaning *unrecorded*. This is the third instance of
  the class in this repo and deserves a guard rather than a third cleanup.
- The `fold_blocks` docstring's "100" and the E1 `--min-fold-trades` semantics
  (one number doing two jobs) both need fixing once the derivation lands.

## Deferred Items

- 6 cells stay `pending` — `exit_ladder` / `exit_head_ml` / `regime_flip_exit`
  on both prop legs. Not swept by this harness; each needs its own pipeline.
- `squeeze_breakout_4h` needs 2 more trades at block 50 — but under the
  corrected 3× bar it needs **52** more, which changes its disposition and is
  unresolved pending the derivation.

## Next Recommended Sprint

**The measurement this section previously asked for HAS BEEN RUN — do not
re-run it.** Nested (contemporaneous) τ selection landed 2026-08-14 for both
decision-relevant families, and the answer is family-dependent:

| family | causal | credited | τ-blind control | from zero | two-sided PASS |
|---|---:|---:|---:|---|---|
| **scalp** (7 legs, 73 leg-folds) | **+3.848R** | +6.022R | +3.475R | **6.4 SE** | **5 of 7** |
| **donchian-1h** (3 legs, 63 leg-folds) | +0.137R | +2.788R | −0.367R | 0.22 SE | 0 of 3 |

**There is no fleet-wide nested figure and none may be quoted.** Doc §§ 10–11.

What is actually next, in order:

1. **The family-scope decision is the whole critical path, and it is the
   OPERATOR's.** Both halves are now evidenced. Recommendation recorded in
   `BL-20260813-EXIT-HEAD-EDGE-SMALL-AND-INCONSISTENT` item 3 and explicitly
   **not enacted**: scope `exit_head_ml` to the scalp family, and record the
   trend/pullback cells as a question *answered in the negative* rather than a
   coverage gap. Two caveats must ride with any such decision — scalp's
   aggregate margin over `stale_8_0` is thin (+0.668R, 56.2%), and the two 15m
   PASSes rest on 5 and 6 leg-folds.
2. **The three live donchian-1h `exit_head_ml` cells read `shipped` while the
   nested read says 0 of 3 clear the two-sided bar.** Hold / pull / re-validate
   is a Tier-3 disposition on a live lever. Also operator-gated, and it is the
   one item on this list with money attached.
3. **Only if (1) lands permissively:** the remaining scalp work is a bigger
   sample on the `stale_8_0` margin, not another τ rule. The head clearly beats
   *doing nothing*; whether it beats an eight-bar stale-stop is the close
   contest, and it is carried by two legs out of seven.
4. **`pullback` / `allmix` / the 1d legs have no nested read** and are low
   priority — already negative under *both* causal rules, so a nested read
   there refines a negative rather than changing a decision. Say so rather than
   listing it as a gap.
5. **Do not resolve any of this by moving a threshold.** `beats_hard` failing
   the 2/3 bar in all three fold geometries is the gate working. Relaxing it
   would convert a measured negative into a recorded positive.

Required verification for any re-grade: state which `fold_mode` **and** which
`--min-fold-trades` it was graded under (verdicts differ across both), and
whether the figure quoted is best-arm, τ-blind, or nested — the three differ by
an order of magnitude on donchian and by ~1.6× on scalp.

## Wrap-Up Check

- [x] Code inspected directly (not inferred from docs) — `fold_blocks`,
      `per_leg_summary`, `eval_split`, `backtest_trend` TP application.
- [x] Canonical docs reviewed; CLAUDE.md § provenance rules applied throughout.
- [ ] TRADE-PIPELINE unchanged — no pipeline stage touched.
- [x] ROADMAP checked — M20 headline moved 362→370; roadmap row update is a
      follow-up once the derivation resolves the blocked bucket.
- [x] Contradictions recorded above, including five of my own.
- [x] Unknowns stated rather than asserted — the derivation is explicitly
      incomplete.

## Postscript — the failure mode that recurred all session

**Seven times** this session I read an **absent or assumed** value as a
**measured** one: a null cap as "uncapped"; my own hand-count as authoritative
over the canonical roll-up (22 vs 14); a backtest quantity as a live one
("6–15 years" of waiting); a shell variable as the artifact it configures;
"100 trades" inherited from a ref instead of derived from the code; a defect
asserted in `train_exit_head.py` without grepping it, *inside this very
document*; and the block size `b` standing in for the per-leg vote count
`n_leg` in a derivation whose entire arithmetic rests on that number. Five were
caught by checking, one by the operator, one by a later measurement.

The common shape is that each wrong reading was the *reassuring* one — it made
a problem smaller, closer to solved, or someone else's. The corpus relabel, the
roll-up tool, and the run-SHA check all exist because earlier sessions made the
same class of error. That is worth stating plainly in the record: the tooling
caught what it was built to catch, and the two that got furthest were the ones
no tool covered.
