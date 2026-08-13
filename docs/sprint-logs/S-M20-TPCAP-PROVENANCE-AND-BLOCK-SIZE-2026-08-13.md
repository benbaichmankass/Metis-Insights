# S-M20-TPCAP-PROVENANCE-AND-BLOCK-SIZE-2026-08-13

## Date Range

- **Start:** 2026-08-13 ~13:00 UTC
- **End:** 2026-08-13 (in flight at time of writing; overnight autonomous session)

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

### Gaps not yet verified

- The **block-size derivation is incomplete.** It needs the dispersion of the
  paired per-trade difference `d_i = R_model,i − R_actual,i`; relay #9063 is
  pending. Nothing has been concluded about the right value.
- Whether a longer history actually yields the projected trades — the earlier
  regime need not signal at the recent rate. Every projection here applies a
  *recent* measured rate to *older* history and is labelled as such.
- CI on PR #9048's current head: four checks read green on an **older** SHA
  while `get_status` reports `total_count: 0` for `74fdbdde`. Re-dispatched;
  not treated as green.

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

Land the block-size derivation from relay #9063's per-fold data, then re-grade
the eight blocked `exit_head_ml` cells against the derived value — **including
the possibility that it lands stricter than 50 and blocks legs currently
gradeable.** Required verification: the derived block must satisfy both bounds
recorded here (`b >= 13` from `MIN_OOS_TRADES=25` with `u >= 2`; `b <= N/3`
for a leg to be gradeable at all), and any re-grade must state which value it
was graded under, since verdicts at different blocks are not comparable.

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

Five times this session I read an **absent or assumed** value as a **measured**
one: a null cap as "uncapped"; my own hand-count as authoritative over the
canonical roll-up (22 vs 14); a backtest quantity as a live one ("6–15 years"
of waiting); a shell variable as the artifact it configures; and "100 trades"
inherited from a ref instead of derived from the code. Four were caught by
checking, one by the operator.

The common shape is that each wrong reading was the *reassuring* one — it made
a problem smaller, closer to solved, or someone else's. The corpus relabel, the
roll-up tool, and the run-SHA check all exist because earlier sessions made the
same class of error. That is worth stating plainly in the record: the tooling
caught what it was built to catch, and the two that got furthest were the ones
no tool covered.
