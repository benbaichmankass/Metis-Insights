# S-ML2-GATE-FLOOR-2026-08-07 — the ML2 cell verdicts were instrument artifacts; the gate had no sample floor

## Date Range

- **Start:** 2026-08-07 ~11:50Z (continuation session; ML2 line carried over)
- **End:** 2026-08-07 (in flight at time of writing)

## Objective

- **Primary (as briefed):** rebuild `market_features` BTCUSDT 15m past
  2026-06-30, re-replay, re-run `verify --min-overlap` with the replay pinned
  per-window — the "cheapest unblock on the board".
- **Secondary (as briefed):** act on the `squeeze_breakout_4h` trending/calm
  direction inversion — "highest-value cell finding and parity-independent".

**Both briefed items turned out to rest on false premises.** Neither was
executed as described, and the reason in each case is the deliverable.

## Tier

**Tier 1.** Research tooling, docs, tests, backlog. One `scripts/research/`
file changed. No `src/`, no `config/`, no ML config, no VM mutation. Every cell
finding remains a Tier-3 **proposal**; no cell was changed.

## Starting Context

- `main` @ `038574c` (PR #8553 merged). Prior sprint:
  `S-ML2-CELL-REAUDIT-2026-08-07`.
- Carried-in claims: 1 of 6 `trend_vol` cells justified · a
  `squeeze_breakout_4h` direction inversion · parity blocked on a stale dataset.

## Repo State Checked

- Branch `claude/metis-ml2-cell-audit-cont-6ewta2`, cut from `main` @ `038574c`.
- Coordination board #6927 read; `▶️ START` posted before the first change;
  `🔒 VM-LANE CLAIM` posted before trainer work.
- Canonical docs read: `CLAUDE-RULES-CANONICAL.md` (full), root `CLAUDE.md`,
  `.claude/skills/regime-selectivity/SKILL.md`, `WORKPLAN-2026-08-05.md`,
  `ML2-trend-vol-cell-walkforward-2026-08-07.md`, the prior sprint log.

## Files and Systems Inspected

- `scripts/research/regime_cell_walkforward.py` (`cell_verdict` + print block),
  `scripts/research/direction_walkforward.py` (`analyze`, `_dir_stats`, `_load`),
  `scripts/research/ml_vol_label_replay.py` (`run_verify`, CLI).
- `scripts/ml/walkforward_cell_selection.py` — the **evidence policy**.
- `ml/configs/btc-regime-15m-lgbm-{v2,fc-pcv-v1,fc-pcv-v2}.yaml`.
- `config/regime_policy.yaml` § `trend_vol`.
- `.github/workflows/trainer-vm-diag.yml` (the awk extractor).
- Relays: trainer-vm-diag #8570, #8571, #8572, #8573, #8574, #8575, #8577;
  vm-diag-snapshot #8578.

## Work Completed

1. **Refuted the dataset-staleness premise (trainer-diag #8570).** A
   nightly-fresh `v002` exists through **2026-08-06 22:30Z**; `v520` is `-v1`'s
   deliberately-**pinned** dataset. The replay had run the `-v2` head over
   `v520`. Both files are exactly **175,272 rows** — a fixed-width rolling
   window, so `v520` never stopped growing. `fc_*` live in `v002`'s tail
   (present 2,000/2,000 on all six). **No rebuild was needed or performed.**
2. **Found the walk-forward gate has no sample floor**, and proved it on
   fixtures rather than by argument: 3 losing trades one-per-fold →
   `long_stable_drag=True, fold_sensitive=False`; 2 trades pooling **−80 R** →
   `False` (empty folds dilute the `neg > k/2` denominator).
3. **Fixed it (PR #8576)** with `MIN_DIRECTION_TRADES = 10` — the *same* floor
   as the evidence policy that authored the cells — plus a tri-state
   `*_verdict`, `*_populated_folds`, the floor echoed in output, and the
   qualifier printed **on the verdict line**. 5 new tests, incl. an `ast`-based
   drift pin to the policy constant.
4. **Corrected the record**: `ML2-trend-vol-cell-walkforward-2026-08-07.md` and
   `WORKPLAN-2026-08-05.md` both appended in place; the backlog row marked
   `invalid` with the real cause.
5. **Filed four backlog items** (two `high`).
6. **Quantified the operator's output-layer hypothesis**:
   `check_diagnostic_provenance.py --all` = **52 findings**, guard diff-scoped,
   never drained.

## Validation Performed

- `tests/test_regime_cell_walkforward.py`: **13 passed** (8 pre-existing
  unchanged + 5 new).
- Both defect shapes pinned as regressions; `insufficient_n` proven
  distinguishable from a measured negative; `no_trades` kept distinct.
- `ruff check` clean on both changed files.
- `scripts/ci/run_guards.py --base-ref main`: `claim-basis-guard` PASS
  (0 basis-less new rows); `check_backlog_refs` PASS.
- PR #8576 CI: `guards`, `pytest-collect`, `repo-inventory` green;
  `pytest-run` in flight at time of writing.
- Live money DB independently verified healthy (`/api/diag/db_info`, #8578):
  16 tables, `error_per_table: {}`, `load_error: null`.

### Gaps not yet verified

- **Parity is still UNMEASURED.** The re-run over `v002` is blocked behind the
  corrupt trainer mirror (the live audit corpus lives there). It must be pinned
  per-window across three advisory heads.
- **The four gradable cells have not been re-graded against the floored gate.**
  The n figures (9/9/30/43) are read from the prior run's output, not re-derived.
- The `audit_query` diag path returned `{"error":"fetch_failed"}` (#8578); not
  chased, since the parity re-run is blocked upstream anyway. Cause unknown —
  stated rather than guessed.
- Whether `POOLED-AUGMENT-MERGE` and the torn rsync are one incident or two is
  **not established**; filed separately so fixing one is not mistaken for both.
- `htf_pullback_2h` trending-SHORT re-check: still owed, not started.
- P0.1 netting partial-close packet: not started.

## Documentation Updated

- **New:** this log.
- **Updated:** `docs/research/ML2-trend-vol-cell-walkforward-2026-08-07.md`
  (correction section), `docs/research/WORKPLAN-2026-08-05.md` (§7),
  `docs/claude/health-review-backlog.json` (4 new, 1 corrected to `invalid`).
- Not updated: `CLAUDE.md`, `ARCHITECTURE-CANONICAL.md` — no documented
  contract changed.

## Contradictions or Drift Found

1. **The re-audit gate was weaker than the authoring policy.** Cells were
   authored under `MIN_TRADES = 10` and re-graded under **no floor at all** —
   so the re-audit could "justify" a cell on 3 trades that the authoring policy
   would not have considered. Fixed.
2. **"1 of 6 justified" is wrong** — corrected to **0 of 6 affirmatively
   justified** (2 below the floor, 2 `fold_sensitive`, 2 ungradable).
3. **The `squeeze_breakout_4h` inversion is withdrawn** (6 short / 3 long).
4. A **third** `trainer-vm-diag` extraction trap: a column-0 Python `try:`
   matches the extractor's stop regex and silently truncates the command, while
   the naive fix (indenting the block) breaks heredoc delimiters instead.

## Risks and Follow-Ups

**Infrastructure (filed, not acted on — both need an operator Tier-2 nod):**

- `BL-20260807-TRAINER-JOURNAL-PULL-TORN-RSYNC` (**high**) — hot-DB rsync;
  prior pulls were lucky, not correct; no post-pull integrity check.
- `BL-20260807-POOLED-AUGMENT-MERGE-SILENT-FALLBACK` (**high**) — the P0
  augmentation feed degraded silently on 2026-08-07.

**Research hygiene:**

- `BL-20260807-DIAGPROV-STANDING-AUDIT-NEVER-DRAINED` (52 findings).
- `BL-20260807-TRAINERDIAG-COLUMN0-PYTHON-KEYWORD-TRUNCATES-CMD`.

**Unchanged:** P1.4b (trusted-live accrual) remains the binding constraint on
the board. Nothing this session touched it, and nothing here shortens it.

## Next Recommended Sprint

**Repair the trainer journal pull, then re-grade every cell against the floored
gate.**

Order matters: the pull fix (Tier-2, snapshot-then-rsync + post-pull
`quick_check`) unblocks the audit corpus; only then can the per-window pinned
parity run happen; only then is any cell verdict decision-grade. Re-running the
grading before the fix would produce another set of numbers with unconfirmed
provenance — the exact thing this session spent its time undoing.

## Wrap-Up Check

- [x] Code inspected directly — `cell_verdict` and `analyze` read in full before
      editing; the equal-count fold construction verified in source, and the two
      failure modes reproduced on fixtures rather than argued.
- [x] Docs reviewed and updated — research doc, workplan, backlog, this log.
- [ ] TRADE-PIPELINE updated — **N/A**, no pipeline stage changed.
- [x] Roadmap checked — ML2 re-opens; P1.4b unchanged as the binding constraint.
- [x] Contradictions recorded — incl. the two I introduced into the record by
      inheriting the prior session's framing before testing it.
- [x] Unknowns stated — parity unmeasured; cells not re-graded; `fetch_failed`
      cause unknown; augment-merge/rsync causal link unproven.
