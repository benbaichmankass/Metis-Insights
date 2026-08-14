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

21. **PR #9123 (merged) — the session's biggest error, corrected.** I applied the
    §§ 10–11 nested-τ figures to the three SHIPPED donchian-1h cells and told the
    operator "0 of 3 clear the bar" in two pings, a board post and two PR bodies.
    The nested read measures τ **selection**; those cells run a **fixed** arm
    (`model_cond["below_half_r_tau_0.1"]`), a different object. Re-measured on the
    arm the cells actually run: BTC **−1.801R (FAIL)**, ETH **+1.810R / +1.263R
    (PASS)**, SOL **+1.589R / +0.603R (PASS)** — two of three supported, not zero.
    Corrected in the doc (§ 12), the backlog row, the board and the PR bodies.
    Nearly made it twice: the first re-probe read `model["tau_0.1"]` and produced a
    plausible publishable table (ETH +2.097R, SOL +2.192R); reading
    `train_exit_head.py:418` against `:423` before publishing caught it.

22. **PR #9127 — the drawdown axis § 12 declared unmeasured** (relays #9125/#9126,
    doc § 13). `agg()` (`train_exit_head.py:369`) carries `max_dd_r` and
    `mean_hold_bars`, so the axis the condition's *own justification* rests on was
    measurable all along. Per fold, 22 folds/leg, never summed (drawdown is not
    additive). The condition **holds materially longer** on all three legs (`t`
    +3.79/+5.75/+6.37) — the mechanism works — but **drawdown is worse with `t > 2`
    on every leg** and net_R is worse in the typical fold (median negative on all
    three). Readable at n=22 because the distribution is one-sided: median
    `Δ maxDD` is **+0.000** on BTC and SOL, so across 22 folds it essentially never
    *lowered* drawdown — it either did nothing or made it worse.

23. **…and it is conditioning *per se*, not the shape** (#9128, doc § 14). All four
    `_SHAPES` at the same τ: **all twelve shape×leg cells cost drawdown, all twelve
    at `t > 2`**, all twelve hold longer, eleven of twelve lose net_R. No shape on
    this family conditions the head and gets paid for it. The **live** choice
    `below_half_r` is the **mildest of the four** on every leg — the gentlest
    available conditioning was picked, which defends the live config against the
    alternatives but not against *no* conditioning. Also killed the tempting
    mechanism: `Δ maxDD` does **not** track `Δ hold` (on ETH, `age8` and
    `below_half_r` buy identical +3.595 bars at +1.131 vs +0.424 drawdown). This
    removes "try a different shape" from the queued Tier-3 option set.

24. **Recorded a defect in my own probe rather than quietly fixing it.** #9125
    printed "folds where cond LOWERS maxDD: 63.6/45.5/72.7%" — the complement of
    `frac > 0`, i.e. it counted **ties as wins**. Label said *lowers*, code computed
    `<= 0`, and the tied folds are ones where the condition never bound. Sub-class
    **A** of the UNPROVENANCED-DIAGNOSTIC-OUTPUT family, self-inflicted. Caught only
    because the apparent mean-vs-majority disagreement was treated as a reason to
    pull the distribution *before* publishing; had #9126 not been run, "the
    condition lowers drawdown on most folds" would have gone into the doc and a
    ping. Filed `BL-20260814-PROBE-COUNTED-TIES-AS-WINS`.

25. **`artifact-validity-guard` caught my own new backlog row** (missing
    `resolution_criteria`) on PR #9127's first push. A correct catch, and exactly
    the failure mode that guard exists for — a row nobody can tell is finished never
    closes. Fixed in `5adef613` with criteria naming an observable decision and two
    acceptable outcomes (adopt with a demonstrated positive control, or close with a
    written reason); "be more careful with probe labels" is explicitly excluded.

26. **The penalty is not a donchian artefact** (#9130/#9131, doc § 15). §§ 13–14
    cover the family whose head has **no** causal edge, leaving the reading
    "conditioning only looks bad bolted to a weak head". Scalp is the control —
    the family whose edge *survives* nested τ selection. **All twelve scalp
    shape×τ cells cost drawdown, all at `t ≥ 3.01`, with 6 or 7 of 7 legs
    individually agreeing.** And on that family the live-style shape's net_R cost
    is **larger**, not smaller (`below_half_r` `t` −6.76/−6.47/−4.62 vs
    −0.50/−0.41/−1.18 on donchian) — the coherent direction, since overriding a
    head that is right more often is more expensive. **One exception recorded
    rather than smoothed over:** `age8` does not cost net_R on scalp (positive at
    all three τ, never significantly), so the universal claim is about
    **drawdown**, not net_R; `age8` also carries the largest drawdown cost, so it
    is not a way out.

    **Caught a 1-of-7 sample before quoting it.** The scalp round writes **one
    report per leg** (7 files) where donchian wrote one combined report. The first
    probe took `cands[0]` and measured `ict_scalp_5m` alone — a single leg that
    would have carried a family label. The probe's own discovery block printed all
    seven paths, which is the only reason the gap was visible; re-run across all
    seven with the count as an explicit denominator and a per-leg agreement
    column. Also tightened the write-up so the two 12-cell tables are not summed
    as 24 interchangeable observations — they are **shape×leg** and **shape×τ**,
    different units.

27. **PR #9127 merged (`29a5cf85`)** — doc §§ 13–15, sprint items 21–26,
    `BL-20260814-PROBE-COUNTED-TIES-AS-WINS`. Docs + backlog only. Notable: the
    first push failed `artifact-validity-guard`, and marking the PR
    ready-for-review re-fires the whole check suite, so a merge attempted
    immediately after the conversion 405s on `guards` still in progress — worth
    knowing rather than re-diagnosing.

28. **PR #9132 merged (`b8e9ab66`) — the matrix itself, with no re-grade.** Two
    changes to `exit-refinement-coverage.json`, both verified by **diffing
    `m20_coverage_rollup.py --json` before and after** rather than asserted:

    (a) The three shipped donchian-1h `exit_head_ml` refs carried an **identical
    July-only ref** with nothing recording that an August re-sweep had been run —
    the same defect item 11 found in the blocked bucket. Each now carries its own
    **per-leg** result plus an **inline warning** that the §§ 10–11 nested figures
    grade τ *selection* and do not describe these cells. That warning exists
    because I got it wrong (item 21); it means the matrix alone is now sufficient
    to stop the next reader repeating it, without their having to find the doc.
    **0 statuses changed across all 52 rows**, asserted by the edit script.

    (b) `fvg_range_15m / trail_geometry` was `blocked` while its own ref read
    *"lever not applicable … nothing to vary"*. The legend defines `blocked` as
    "cannot process yet (**reason given**)" and `n/a` as "structurally
    inapplicable", so this was a **mis-status**, not a judgement call.
    Re-statused; the only field that moved in the rollup JSON was
    `matrix_updated_at`.

    Filed `BL-20260814-COVERAGE-MATRIX-BLOCKED-REASON-VOCABULARY` for two defects
    **not** fixed: `xauusd_trend_1h / exit_head_ml` carries a **bare `blocked`**
    with no reason suffix and a ref that **begins with the word "same"** — a
    dangling antecedent left by the 2026-08-09 row explosion — so its reason is
    genuinely unrecoverable from the file and **was not invented**; and
    `blocked:native-history-thin` uses hyphens where all five sibling reasons use
    underscores. The suffix is the only machine-readable statement of *why* a cell
    is open, and the done-condition can only be driven down by resolving those
    reasons.

    **Two of my own re-derivations were wrong here and caught by cross-checking
    the authoritative script.** An ad-hoc scan tested `status == 'blocked'` and
    silently missed every `blocked:<reason>` variant, returning an empty set that
    looked like a clean answer — the same shape as the ties-as-wins bug in item
    24, and the reason the measured status vocabulary (12 distinct values across
    416 cells) went into the backlog row. `m20_coverage_rollup.py` is the
    authority on this file's population; hand-iterating it is how you get a
    confident wrong count.

29. **PR #9134 merged (`7efd3899`) — CORRECTION § 16, and it strengthens § 13.**
    § 13 listed "why the condition never binds on most folds" as unexplained
    while its own prose had already answered it, glossing the zero-`Δ maxDD`
    folds as *"folds where the condition never bound"*. **An inference from a
    zero, not a measurement, and wrong.** Measured (#9133): no-op share is
    **13.6% / 0.0% / 9.1%** — the condition is **active on 86–100% of folds**.
    BTC has ~64% of folds at exactly zero `Δ maxDD` but only 13.6% no-ops, so
    roughly half its folds are ones where the condition *acted* and the
    **maximum** drawdown still did not move — ordinary for an extremum. This
    **strengthens** § 13: restricted to active folds the effect is the same size
    or slightly larger (+0.198 vs +0.171 BTC). A clean-comparison check came free
    — trade counts identical on **100%** of active folds, mean `Δ trades` +0.00,
    so both arms are compared over the **same trade population**, which §§ 13–15
    had assumed and never checked. The § 13 parenthetical is struck in place and
    points at § 16.

30. **PR #9135 — the first done-condition movement since the prop legs entered
    the denominator, and the session's only genuine re-grade.** Headline
    **370 → 371/376**, done-condition **28 → 27**.
    `trend_donchian_sol_prop / regime_flip_exit` graded `honest_negative` **by
    structural derivation, not by a sweep** — the lever *cannot fire* on that
    leg, so no book and no exit geometry is needed to grade it. Four links, each
    read from the primary source rather than inherited from a ref:
    `regime_label` emits only `unknown/chop/transitional/trending`
    (`detector.py:95-113`); `off_cell` looks those 1-D labels up and **never**
    consults the 2-D `trend_vol` block (`m20_regime_flip_replay.py:61-65`);
    `regime_policy.yaml` has `trend_donchian` `long: true` in all three 1-D cells
    and no per-symbol keys; `strategies.yaml:1187` sets `long_only: true`. So
    `flip == actual` on every trade — the identical degenerate tie the API twin
    is already graded for.

    **Why the blanket `pending` reasoning did not reach this lever:** all six
    prop cells shared one ref saying a parent verdict cannot transfer because the
    exits are inherited and different. True for `exit_head_ml` and `exit_ladder`,
    whose outcomes depend on geometry — but whether the *flip fires* is a
    function of `(1-D label, family key, direction)` only, and there is no
    outcome for geometry to affect when the trigger never occurs.

    **The conditional is recorded in the ref because it is the part that breaks:**
    `trend_vol` *does* set `trend_donchian` `long: false` in three 2-D cells, so
    the grade rests entirely on the replay harness reading 1-D cells only. Extend
    that harness, or turn off a 1-D donchian long cell, and the cell re-opens.

    **Scope is SOL only.** `trend_donchian_eth_prop` is deliberately two-sided
    ("ETH's edge is short-side-dependent"), so the flip *can* fire there and its
    outcome *does* depend on the inherited geometry — that cell stays `pending`.

31. **Established that `m20-exit-lever-sweep` cannot close the remaining five
    pending cells.** Its `levers` input accepts
    `stale_stop,giveback_stop,trail_geometry,trail_decay,vol_trail` — **none of
    the three levers that are actually pending**. `exit_head_ml` needs an E1 run
    on each leg's own book. Read the workflow inputs rather than dispatching and
    discovering it from a useless run.

32. **The prop-leg flip replays already existed, and `verdict: PASS` is 86%
    degenerate ties** (relays #9138–#9141, doc § 17). Chasing the E1 path for the
    remaining pending cells turned up three things:

    (a) **A factual error in the matrix.** Two prop-leg `regime_flip_exit` cells
    read `pending` because the leg was *"NOT exit-processed on THIS leg's own
    book"* — false, and false when written:
    `m20_flip_replay/2026-07-13/` holds both legs' own `_flip.json` **and**
    `_trades.jsonl`, a month before that ref was authored. Corrected in the refs,
    scoped to the flip lever only (the clause **is** true for `exit_head_ml` —
    the `m20_exit_head` tree has no prop-leg directory — and for `exit_ladder`).

    (b) **Item 30's grade survived a falsification test.** The SOL grade was made
    by structural derivation; the artifact tests it directly and confirms it —
    `flip_exits: 0`, `flip_pct: 0.0` over 412 trades, `overall_flip` byte-identical
    to `overall_actual`, all six folds equal to four decimals. Right for the reason
    it gave.

    (c) **The finding.** The artifacts carry their own `verdict`/`walkforward`
    fields, which I had *listed and not read* — reading the field instead of
    re-deriving one from the fold table is what surfaced it. Fleet-wide: **14
    `PASS` / 28 `fail`, and 12 of the 14 passes have `flip_pct = 0.0`.** The lever
    never fired, `flip` is identical to `actual`, and the walk-forward reads a
    perfect **6/6 because every fold is a tie counted as a beat**. `PASS` means
    "changed nothing" 86% of the time. Same shape as item 24's probe defect, but
    in **shipped tooling**, so it is durable and re-read by every future session.
    Filed `BL-20260814-FLIP-VERDICT-PASS-IS-86PCT-DEGENERATE`.

    **Left ungraded on purpose:** `trend_donchian_eth_prop / regime_flip_exit` has
    complete evidence (PASS, 4/6, +19.38R, −21.95R maxDD) and a defensible
    `honest_negative` case on the fleet ref's own rationale — the improvement lands
    on a **money-losing book** (−85.76R → −66.38R) and the **two most recent folds
    are the two failures** (2026 swings −18.8R). But that is an *interpretive
    override of a recorded `PASS` on a live leg*, unlike the mechanical SOL case,
    so it goes to the operator. The full argument **and** the fleet context are
    written into the cell's own ref, so the decision needs no archaeology.

33. **⛔ Every exit-head round on disk was built on a NO-TAKE-PROFIT book** (doc
    § 18, `BL-20260814-EXIT-HEAD-ROUNDS-CANNOT-MODEL-LIVE-TP`, **high**). The
    session's largest-scope finding, and it was found by *launching a round and
    reading what it actually executed* rather than by reading the code first.

    **`m20_exit_head_round.py` structurally cannot produce a live-parity book:**
    line 145 calls `base_args` with five positional args so `tp_cap_pct` defaults
    to `0.0`; `base_args` appends `--tp-r`/`--tp-cap-pct` only when that is
    `> 0.0` (line 386); and the driver's argparse has **no `--tp-cap-pct` option
    at all**. Every harness runs at `tp_cap_pct=0.0, tp_r=50.0` — no TP. Visible
    in my own launch log: `--trail-mult 3.5` present, `--tp-r` **absent**, on a
    leg declaring `tp_r: 6.0`. Aborted and **quarantined** to
    `prop_1h.ABORTED-WRONG-GEOMETRY/` + `DO-NOT-USE.txt`.

    **A method I tried first was void, and its own positive control caught it.**
    Searching `round_report.json` for the flags returned `--trail-mult: False` on
    *every* round including ones whose logs carry it — that file does not record
    args at all. **It would have returned exactly the answer I expected.** The
    conclusive method was independent: exit-reason distributions over 11 of the 13
    round dirs with a readable `exit_reason` (n=63…977) — `trail_stop`/`stop`/
    `timeout`/`flip`/`stale_stop`, **zero take-profit exits**, `donchian_1h_nested`
    included. `scalp_15m`/`scalp_5m` stamp none, so **undetermined, not confirmed**.

    **The self-caveat this forces, stated precisely:** it does **not** invalidate
    §§ 13–16 — those are *within-book* arm contrasts over identical folds and
    trades (61 of 66 active leg-folds, counts identical on all). A shared geometry
    bias cancels in the difference. It **does** condition their transfer to
    production, where the clamp truncates exactly the long right tail the
    conditional arm buys, so magnitudes and possibly sign need not carry. **The
    direction of that error is not known and is not guessed at.**

34. **`claim-basis-guard` caught my own new row** for quoting `100%` and `9.9%`
    with no parseable denominator — a correct catch, and *"always state the
    population"* is a rule this session leaned on repeatedly, so failing it was my
    own inconsistency. Populations now stated in the row.

35. **Fixed the driver, and annotated the eleven rounds it had already spoiled**
    (PR #9150) — parts **(a)(b)(c)** of
    `BL-20260814-EXIT-HEAD-ROUNDS-CANNOT-MODEL-LIVE-TP`, filed only ~40 min
    earlier in item 33. **Only (d) remains, and it is the operator's.**

    **(a)(b)** `m20_exit_head_round.py` now takes `--tp-cap-pct` **with live
    parity as the DEFAULT (`0.099`)**. The default is the point: an opt-in is a
    flag every future caller can forget, and forgetting it is precisely what
    produced eleven untransferable rounds — the same argument
    `m20-exit-lever-sweep.yml`'s header makes for its own default. Passing `0`
    still works, to reproduce a historical no-TP verdict, and the help text says
    to **declare it when quoting one**. `round_report.json` now also stamps
    `_round_meta` (`tp_cap_pct`, `tp_geometry`, tf, legs), so a round is
    self-describing on the one parameter that decides whether its verdict
    transfers — closing the gap that made my own first audit method void.

    **Four tests, can-fail verified** by reverting both fix points: exactly the
    two argparse tests fail, and all four pass once restored. The fourth pins the
    forwarding mechanism itself (at cap 0 *neither* flag is forwarded), so a later
    edit that re-breaks it fails here rather than passing quietly.

    **(c)** Each affected round now carries a `GEOMETRY-NO-TAKE-PROFIT.txt` with
    the mechanism, its **own** evidence, and what it does and does not invalidate.
    Artifacts outlive docs and are read directly, so the doc caveat alone was not
    sufficient. **The marking pass read every emit file per round** rather than
    the one-file sample the original audit used — full populations, n=100…1992,
    and **zero take-profit exits in all eleven**. That is a *stronger*
    confirmation than the finding it records, not a restatement.

    **`scalp_15m` / `scalp_5m` were deliberately NOT marked** and are listed as
    undetermined: they stamp no `exit_reason`, and labelling a round I cannot
    measure would be the same error class the row is about.

36. **The staleness classifier could not see the defect I had just fixed**
    (same PR #9150) — `BL-20260814-COVERAGE-STALENESS-GRADED-BY-DATE-NOT-GEOMETRY`,
    found by re-running the roll-up after item 35 rather than by auditing it.

    `--stale-decisions` is the surface that names the CLOSED, non-negative cells
    resting on unreproduced evidence, and its own output calls a stale SHIPPED
    cell the expensive kind. It listed five and **read as the complete set**.
    Three SHIPPED real-money `exit_head_ml` cells were missing from it.

    **Two causes, and the second is the interesting one.**

    (1) **One cutover for two harnesses.** `GEOMETRY_CUTOVER` (2026-08-10) is
    when the *lever-sweep* harness learned to place the live capped TP.
    `exit_head_ml` does not ride that harness — it rides the driver item 35 had
    just fixed, four days later. Twelve cells cleared a bar their evidence never
    met (stale 174 → 186 of 286 closed).

    (2) **The date is only a proxy, and it failed outright.** The three shipped
    `trend_donchian*` 1h cells carry a **genuine** `RE-SWEPT 2026-08-14` ref — a
    real measurement, on that date — which re-read the **existing** round dirs
    rather than building new ones. So the cell is *fresh by date and stale by
    geometry at the same time*, and **no choice of cutover date separates
    those**: the property that decides transfer is not WHEN a cell was measured
    but WHETHER the round behind it placed the live TP.

    So the fix is not a better date. A per-cell **`tp_geometry`** overrides the
    date **in both directions** — `no_take_profit` is stale whatever the date
    says, `live_parity` is not stale whatever the date says — and an absent
    field falls back to the date while counting into `geometry_undeclared`, so
    *"we did not look"* stays visible instead of reading as a clean pass. It is
    a declared **measurement**, not a presence-only marker: the eleven rounds of
    item 35 are its evidence, each carrying its own on-disk marker.

    `--stale-decisions` now lists **8**, shipped **4 → 7**.

    **Six tests; both fix points can-fail verified independently** (reverting the
    per-lever map fails exactly 1, reverting the geometry override fails exactly
    4). One asserts the three cells against the **real** matrix, so a regression
    fails CI rather than quietly under-reporting.

    **A correction to my own reasoning inside this item.** I expected reverting
    the per-lever cutover to also break the real-matrix test; it did not, because
    the `tp_geometry` stamp alone is sufficient for those three. The date is the
    backstop, not the mechanism — which is the right layering, but I had it
    backwards until the can-fail run said otherwise.

    **What this does NOT resolve, deliberately:** the disposition of the three
    cells is part (d) of `BL-20260814-EXIT-HEAD-ROUNDS-CANNOT-MODEL-LIVE-TP`,
    Tier-3, and is *not* double-counted as this row's remit. This change makes
    the classifier tell the truth about which cells are affected; it does not
    decide what to do about them.

37. **Re-swept the other five stale decisions at live parity**
    (`m20-exit-lever-sweep` run 31771809102) — `htf_pullback_trend_2h`,
    `mes_trend_long_1d`, `mhg_pullback_1d`, `tlt_pullback_1h` ×
    `trail_geometry,stale_stop`. These are the stale decisions the *lever-sweep*
    harness owns, so unlike the `exit_head_ml` three they were runnable
    immediately — that harness has taken `--tp-cap-pct` since 2026-08-10.

    **Two checks before dispatch, both of which changed what I ran.**

    First, the workflow's own input docs say *"trail_geometry is not offerable:
    `trail_mult` has no OFF state"*, and I read that as "cannot be graded" — four
    of the five cells are `trail_geometry`, so that would have made them
    permanently unmeasurable. It is **not** what it says. "Not offerable" scopes
    the *lever-off arm*; the ordinary arm sweeps a `trail_mult ± 1.0` grid
    (`m20_fleet_exit_sweep.py:618-622`), which is a real comparison against the
    shipped value. The code settled a question the prose left ambiguous, in the
    direction that meant the work was possible.

    Second, the live cap only applies to `LIVE_TP_CAPPED_FAMILIES`, so a leg
    classifying outside it would have re-run at the OLD geometry and cleared its
    stale flag on evidence that had not changed. Checked all four through
    `classify()` first: `pullback` ×3, `donchian` ×1 — all capped. `tlt_pullback_1h`
    additionally declares `tp_r: None`, which `base_args` guards
    (`if tpr is not None`), so it takes the cap without a malformed `--tp-r`.

## SWEEP RESULT (run 31771809102) — the answer is a NEGATIVE, and it is the useful kind

All four legs returned, all four green, **every cell `insufficient_base`**.
Live parity WAS applied on all four (`capped TP 0.099 APPLIED` on each).

| leg | base n IS | base n OOS | vs floor 25 | cells |
|---|--:|--:|---|---|
| htf_pullback_trend_2h | 382 | **24** | −1 | 4/4 insufficient_base |
| mhg_pullback_1d       |  55 | **24** | −1 | 4/4 insufficient_base |
| tlt_pullback_1h       | 502 | **22** | −3 | 4/4 insufficient_base |
| mes_trend_long_1d     |  29 |  **4** | −21 | 4/4 insufficient_base (split fell back: leg_too_thin, lifetime=33) |

`MIN_OOS_TRADES = 25` (m20_fleet_exit_sweep.py:402, operator decision 2026-08-11).

**So none of the five stale `trail_geometry` / `stale_stop` decisions can be
re-graded today** — and NOT for the reason the stale flag implies. The geometry
was fixed and applied; the legs simply sit below the operator's own OOS evidence
floor. Three of the four are short by **1-3 trades**, so they become gradeable
with a little more live history; `mes_trend_long_1d` (lifetime 33) is nowhere
near.

This is worth stating precisely because the stale-decision list reads as a
to-do. For these five it is not: it is a **wait**, and re-running the sweep
before those legs accumulate OOS trades will return exactly this again.

### Side finding: the warning threshold and the grading floor disagree

`_THIN_OOS_TRADES = 20` (the ⚠️ **THIN OOS** banner) vs `MIN_OOS_TRADES = 25`
(the gradeability floor). A leg in the **20-24 band is entirely ungradeable and
gets no banner** — which is exactly where `htf_pullback_trend_2h` (24),
`mhg_pullback_1d` (24) and `tlt_pullback_1h` (22) landed. Only `mes` (4) was
flagged.

Stated precisely: this is **legibility, not falsehood.** The per-cell
`PathA: insufficient_base` column does say it, and the harness even records
`would_have_been`. But the header is where a reader calibrates, and three legs
printed a full four-row delta table under no warning at all while nothing in
them was graded.

38. **Followed a citation, and it did not lead where it said** — filed
    `BL-20260814-EXIT-LADDER-CELLS-CITED-THE-WRONG-MEMO-SECTION` (resolved
    same session).

    Seven `exit_ladder` cells cited **memo §7.2** for the partial-TP banking
    verdict. §7.2 is *"Trail4 walk-forward"* and contains nothing about
    banking; the verdict is **§6.2** *"Trailing-stop geometry + exit-ladder
    banking"* — *"partial-TP banking reduced net_R in every one of the 20
    banking cells"*. No other memo in the repo has a §7.2, so it could not have
    resolved elsewhere. The claim was right and the pointer was off by one
    section — which matters because a reader verifying the grade lands on a
    section about a **different lever**, and §7.2's content is a *PASS*.

    All seven now cite §6.2 with the section title and the verdict sentence
    inline, so the correction is checkable from the cell rather than by trusting
    the backlog row. **The corrective note carries no date, deliberately** —
    five of the seven are `honest_negative` cells the vintage counter buckets,
    and a date would have flipped them `pre_cutover → post_cutover`, marking
    pre-cutover evidence as refreshed. That is the same date-proxy hazard item
    36 exists to fix, and it would have been self-inflicted an hour later.

    **Two counting errors of mine that the asserts caught, both before any
    write.** I first said *45* cells cited §7.2 — I had extrapolated from a
    three-row sample, and a regex that read decimals out of data-rich refs as
    section numbers agreed with me. The real count is 7 (34 of the 45 carry no
    citation at all). Then my write script asserted "no ref still contains
    §7.2" and tripped **on its own correction note**, which quotes the wrong
    section in order to explain it. Neither reached the file.

    **Left stated, not fixed:** 34 of the 45 `honest_negative` `exit_ladder`
    cells carry no section citation at all. Wider than this row's remit, and
    folding it in silently would have hidden it.

39. **The two pending prop `exit_ladder` cells have a named path now, and it is
    not the one the fleet verdict implies.** This is the substantive find of the
    stretch, and it came out of the wrong-citation chase above.

    Memo §6.2 grades banking on **net_R** and then explicitly carves out this
    exact case: *"The one venue where this trade could still be RIGHT is the
    **prop ruleset** (survival-weighted EV, daily-loss/DD breach rules —
    smoothness is worth net_R there)"*, logging `PB-20260712-PROP-BANKING-EV`.

    So grading the two prop cells `honest_negative` by inheriting §6.2 would
    **apply a verdict the memo excludes prop from** — and the axis is wrong
    besides: survival-weighted EV, not net_R. Their `pending` status is correct,
    but their stated reason (*"not exit-processed on this leg's own book"*) was
    generic and understated it.

    **What it would take, pieces verified present rather than assumed:** the
    banking lever exists in the harness (`--bank-frac` / `--bank-at-r`,
    `m20_exit_sweep.py --phase2`, which produced §6.2's own 20 cells); the prop
    scorer exists (`run_ev_montecarlo`, already called by three
    `scripts/prop/*` scripts — so the health-review row's "unused for a month"
    is about the *evaluation*, not a dead function); `breakout.yaml` exists.
    **The only missing piece is the join** — `montecarlo_prop.py` has no banking
    arm. Chaining two built pieces, not new modelling.

    **Deliberately not built tonight.** The output grades a live prop leg's
    exits, and a harness authored at speed that emits a confident EV verdict is
    the wrong artifact to leave on disk unattended — the same reasoning that had
    me quarantine the wrong-geometry round rather than let its numbers stand.
    The path is written into both cells so the next session does not re-derive it.

40. **Recorded the re-sweep on the five stale-decision cells — and watched the
    new mechanism do its job.** All five now carry a genuine `2026-08-14` ref
    date. Under the date-only logic that shipped this morning, that date alone
    would have dropped every one of them off `--stale-decisions`. They stay
    correctly flagged because `tp_geometry: no_take_profit` overrides it. The
    hazard item 36 was written against is not hypothetical: I walked into it
    within the hour, on cells I was annotating myself.



41. **Ran the first exit-head round that models the live take-profit — and it
    closed both remaining runnable cells.** Coverage **371 → 373/376 (99.2%)**,
    done-condition **27 → 25**.

    Sequence: pulled `main` onto the trainer and **verified the fix was on disk
    three ways** (#9154) rather than trusting the pull — HEAD == `origin/main`,
    `--help` lists `--tp-cap-pct` with the `0.099` default, and `base_args` is
    called with six args. The flag's *absence* was the original defect, so its
    presence is the direct probe.

    **The first launch attempt (#9155) started nothing, which is what I wanted
    from a broken launcher.** The relay indents the `cmd:` block, so my indented
    `<<'PY'` heredocs were swallowed (`delimited by end-of-file`) and every
    subsequent line was consumed into them. Relaunched heredoc-free via
    base64 → `python3 -` (#9156), with the **geometry probe as a precondition**:
    it prints the exact argv and emits `GEOMETRY_PROBE_OK`/`FAILED`, and the
    launch is skipped unless it passes. #9144 burned compute on a wrong-geometry
    book precisely because nothing checked the command *before* running it.

    **Launched at the DEFAULT, without passing `--tp-cap-pct`** — live parity
    being the default *is* the shipped contract, and a run told the right
    geometry does not test it.

    **The positive control, criterion (b), is satisfied by MEASUREMENT.** The
    launch log did show `--tp-cap-pct 0.099 --tp-r 6.0` where #9144 showed no
    `--tp-r` at all — but that is an arg-string observation, and the arg-string
    method was voided earlier tonight. So the settling evidence is the emitted
    trades' own `exit_reason` distribution (#9157):

    | leg | trades | take-profit exits |
    |---|--:|--:|
    | `trend_donchian_eth_prop` | 984 | **50** (5.1%) |
    | `trend_donchian_sol_prop` | 310 | **45** (14.5%) |

    **95 take-profit exits where the eleven pre-fix rounds contain zero.** The
    round also self-reports `_round_meta.tp_geometry: live_parity`, so it needs
    no marker file to be read correctly.

    **Verdicts** (per-leg matrix-unit line, same shape the `ict_scalp_sol_5m`
    cell was graded from):

    - `trend_donchian_eth_prop` — n_oos 902, auc 0.6138, beats_actual 20/24,
      beats_hard 16/24 → **candidate** → `passed_unshipped`.
    - `trend_donchian_sol_prop` — n_oos 298, auc 0.5635, beats_actual 15/23,
      beats_hard 11/23 → **honest_negative**.

    `live_trades: 0` is expected and non-blocking per the operator's 2026-08-13
    decision that the E1→E2 gate's LIVE arm is a wiring check, not the evidence.
    I pulled the full report (#9158) rather than grading two live-leg cells off
    a log tail — the truncation trap that bit at #9139.

42. **The first non-scalp E1 candidate — and why it is not the counterexample it
    looks like.** The standing read, recorded on the `ict_scalp_sol_5m` cell, is
    *"all 5 surviving candidates are ict_scalp; every non-scalp family in the
    re-run is honest_negative"*, and it is the basis of the queued operator
    question *"is `exit_head_ml` scalp-family-scoped?"*. A **donchian** leg just
    returned candidate at essentially the same AUC (0.6138 vs 0.6149) on a
    larger OOS sample (902 vs 800).

    **This does not overturn that read, and I am not recording it as if it
    does.** Every non-scalp `honest_negative` that formed the scalp-scoping
    hypothesis was measured on a **no-take-profit** book; this cell is the first
    at live parity. The two are not comparable, so this is not a like-for-like
    counterexample — it is a result from a different population.

    What it *does* establish is narrower and more useful: the scalp-scoping read
    rests on evidence now known to be **geometry-conditioned**. Re-running the
    non-scalp legs at live parity is what would test it, and one leg is not that
    test. That raises the value of queued item (d) rather than settling it, and
    the caveat is written into the cell so the next reader cannot take the
    shortcut I was one step from taking.

    **Not run tonight:** that re-run is fleet-scale and the disposition of what
    it would find is Tier-3.



43. **Asked what would actually unblock the 22 blocked cells, and found two
    waiting on data nothing produces** —
    `BL-20260814-MGC-MHG-IBKR-HISTORY-NEVER-PULLED`.

    Most of the 22 are volume constraints (10 `insufficient_lifetime_trades`, 3
    `insufficient_base`, 2 `insufficient_oos_base_at_derived_split`, 1
    `native-history-thin`) — the same **wait, not to-do** shape as item 37, and 2
    are structural (`no_harness_levers`). The interesting four were
    `data_missing`.

    Three of those are IBKR futures legs needing native history for E0
    (`mes_trend_long_1d`, `mgc_pullback_1d`, `mhg_pullback_1d`). `data_missing`
    reads as *"the data will arrive"*. **For two of the three, nothing is
    bringing it.** The only puller in the repo is `pull_mes_ibkr_history.sh`; it
    *is* symbol-parameterised (`MES_SYMBOL`, default `MES`), but the scheduled
    unit invokes it with **no override**, so the daily timer is MES-only.

    **And the tool built to fix exactly this already exists.** `pull-ibkr-history`
    (Tier-2, allowlisted MES/MGC/MHG) says in its own entry that it was *"Added
    2026-07-07 to backtest the metals sleeve (`mgc_trend_1h` / `mgc_pullback_1d`
    / `mhg_pullback_1d`) on native IBKR history"* — the same two cells, still
    blocked five weeks on.

    Measured with the denominator stated: across the window the relay returned
    (07-30 → 08-05, 44 `pull_start` runs) **44/44 are MES**, zero MGC, zero MHG.
    I could not see 07-07 → 07-30, so the log alone does not exclude a run there;
    what makes it unlikely is that both cells still read *"needs native IBKR
    history"*.

    **A false alarm I did not file, recorded because the trap is reusable.** The
    truncated 400-line payload's newest visible stamp was **2026-08-05** — nine
    days stale on a *daily* timer, which reads exactly like a recurrence of
    `BL-20260626-MES-BASE-STALE` ("the pull … stopped running 2026-06-14,
    freezing MES 5m/15m"). **It is not.** A small complete read (`lines=12`)
    shows the pull ran last night, 2026-08-13T23:33 → 23:45, both timeframes
    `ok` (47,190 and 15,751 rows). The truncation had handed me the *older* part
    of the 400. **When the question is "what happened most recently", a large
    truncated read is worse than a small complete one** — and a false alarm about
    a previously-real incident is the most expensive kind.

    **Not dispatched tonight, and the reason is timing rather than tier** (Tier-2
    is pre-granted). The pull shares the **live** trading gateway on clientId
    450 and takes ~27 min; at the time of writing (~05:32Z) we are inside IBKR's
    own ~03:45–05:45 UTC reset window and ~33 min from the 06:05 UTC gateway
    reset, so a run started now would span a session being restarted and burn
    pacing budget for nothing. The two must also run **sequentially** — the
    wrapper takes a single-instance lock, so a concurrent second invocation
    exits cleanly and would look like a silent no-op. Both constraints are
    written into the row's resolution criteria rather than left as folklore.


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

- ~~**The nested tau selection has NOT been run, and it gates the conclusion.**~~
  **CLOSED later the same session** — items 12/15 (doc §§ 10–11). Kept as written
  because the reasoning is still the right reasoning; only the status changed.
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
- ~~**`_select_tau_holdout` has not produced a single number yet.**~~ **CLOSED**
  by the donchian and scalp nested rounds (items 12/15). Every `e1_report.json`
  *at the time of writing* predated it, so `selected_tau` was absent everywhere;
  the fresh rounds supplied it and the causal interval now has both ends.
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

### Gaps not yet verified (added ~03:40Z, items 21–25)

- **Whether BTC's re-sweep failure reproduces off this one book.** The corrected
  per-cell read (item 21) fails `trend_donchian` on **one** 22-fold book, one
  family, one block size. A leg-level demotion wants more than a single re-sweep,
  and this session did not run a second one.
- **The three donchian legs are not three independent confirmations, and every
  `t` in items 22–23 inherits that.** One strategy's logic on three correlated
  crypto majors over the same calendar window and the same 22 folds. Twelve
  `t > 2` results are one result seen twelve times under shared conditions; they
  must not be pooled as if independent, and nothing in this log does.
- **The conditioning finding is scoped to donchian-1h and was not tested on the
  scalp family**, which is the one family where the head's edge survives causal
  τ selection (item 15). Whether conditioning is also unearned *there* is
  unmeasured and is the natural next probe — the same `_SHAPES` columns exist in
  the scalp nested report.
- **`Δ maxDD` is a per-fold within-arm comparison and nothing more.** It says the
  conditional arm's worst intra-fold drawdown is larger; it does **not** translate
  to a portfolio drawdown figure, and no attempt was made to convert it. Drawdown
  is not additive across folds and was never summed anywhere in items 22–23.
- **Why the condition never binds on most folds is not explained.** Median
  `Δ maxDD` is exactly +0.000 on BTC and SOL — ~64%/~73% of folds where the shape
  made no difference at all. That could be a rarely-satisfied predicate or a
  predicate that fires and rarely changes the exit; the report cannot distinguish
  them, and the difference matters for how much weight the non-zero folds carry.

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
2. **The three live donchian-1h `exit_head_ml` cells — the one item on this list
   with money attached, and the one whose framing was WRONG until 2026-08-14.**
   An earlier version of this bullet read *"the nested read says 0 of 3 clear the
   two-sided bar"*. **That was an error** (item 21, doc § 12): the nested read
   grades τ *selection*, while these cells run a **fixed** arm. On the arm they
   actually run, the result is **per-leg, not uniform** — BTC **−1.801R (FAIL)**,
   ETH **+1.810R / +1.263R (PASS)**, SOL **+1.589R / +0.603R (PASS)**. Two of
   three are supported. Do not re-derive the old framing from §§ 10–11.

   Tonight's two follow-ups narrow the disposition further (items 22–23,
   §§ 13–14): the `below_half_r` conditioning **costs** drawdown on all three
   legs at `t > 2` and buys only hold time, and that holds for **all four**
   `_SHAPES` — so "try a different shape" is **off the table** for this family.
   The live shape is nonetheless the mildest of the four. The remaining options
   are (a) keep as-is, (b) drop the conditioning and run the unconditional head
   at the same τ, (c) act on BTC alone, which is the only leg failing its own
   re-sweep. **Tier-3, operator-gated, and not enacted here.**
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

44. **Pulled the missing history — then found the pull could never have
    worked, because the resolver could not see native data at all** —
    `BL-20260814-PROXY-MAP-SHADOWS-NATIVE-DATA`, PR #9180.

    Item 43 established that nothing was pulling MGC/MHG history. I ran the
    pulls (MGC clean: 5m 32,751 rows, 15m 22,293 rows, `pull_end any_ok:1` at
    06:38:36Z; MHG dispatched 07:28:49Z) and then, before planning the E1
    rounds, asked whether the data would actually be *reachable*. It would not.

    `m20_fleet_exit_sweep.resolve_data` opened with
    `sym = PROXY_DATA.get(symbol, symbol)` — the proxy applied
    **unconditionally**. So `resolve_data("MGC", "1d", …)` looked for
    `GC_F_1d.csv` and **never** for `MGC_1d.csv`; the glob fallback too, since
    its prefixes were built from the proxied spelling (`{'gc_f'}`). And
    `m20_exit_head_round.py:154` **refuses** a proxied leg ("native history
    required for head training"). `proxy` was therefore unconditionally `True`
    for MES/MGC/MHG/XAUUSD, the head round skipped them on every call, and the
    three `exit_head_ml` cells could not close **whatever was on disk**.

    So the Tier-2 action added 2026-07-07 *for these exact cells* was **inert
    against this resolver** — five weeks of "still blocked" had two independent
    causes, and item 43 found only the first. The docstring already said the map
    is *"for futures without their own file"*; the code never checked that
    condition. Field beats comment, and here the field was the wrong one.

    **The fix is a caller decision, and the default deliberately did not move.**
    My first instinct — native-first unconditionally — is a **regression**, and I
    wrote it before catching it: `GC_F` is the deep COMEX series reaching the
    full 2021..2026 fold structure while native IBKR intraday history is ~1y, so
    silently preferring a shallow native file would collapse a 6-fold
    walk-forward to ~1 fold and quietly invalidate recorded lever verdicts.
    Depth (levers) and fidelity (head training) are genuinely different needs.
    `prefer_native=False` default; `m20_exit_head_round` passes `True`. I also
    removed a native *fallback* I had added to the default path for the same
    reason — a missing proxy file must keep reading `data_missing` rather than
    resolving shallow native data into a 1-fold run.

    Can-fail verified: removing the `prefer_native` branch fails **exactly one**
    of the 7 new tests (`assert True is False`), and only that one — the
    fallback and no-proxy cases correctly still pass because proxy-first gives
    the same answer there.

45. **Two corrections to my own 07:28Z ping, both in the over-claiming
    direction.**

    (a) I told the operator that once MHG landed, "both `mgc_pullback_1d` and
    `mhg_pullback_1d` `exit_head_ml` cells become runnable via an E1 round …
    That would take done-condition 25 → 23." **Wrong on the mechanism** — item
    44 is why, and the resolver fix was a prerequisite I had not yet found.

    (b) The pull I ran was **5m/15m over the script's default 365-day window**
    (`MES_HIST_START='365 days ago'`), which is the wrong *shape* for a **1d**
    leg facing a 6-fold 2021..2026 structure and `MIN_OOS_TRADES=25`. The right
    attempt is a **1d deep pull** — the `pull-mes-ibkr-history-daily` precedent
    uses `MES_HIST_START=2019-05-06` with `MES_MAX_CONTRACTS=28`, and IBKR's
    daily-bar retention is far deeper than its intraday retention. I had also
    been about to accept the matrix's *"IBKR serves only ~1yr"* as a measured
    ceiling; it is a **claim I would have been inheriting**, and the script says
    plainly that "depth is ultimately capped by IBKR's per-contract intraday
    retention" — a bound on *intraday*, which is not the frame these legs need.

    **Coverage is unchanged at 373/376 and no cell status moved.** Reaching a
    cell is not clearing it: the shard must still reach the trainer
    (`sync_trainer_data.sh` on the ~00:50 UTC cycle, then
    `market_raw_to_csv.py`), and whether the OOS floor clears is a measurement
    still to take.

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
