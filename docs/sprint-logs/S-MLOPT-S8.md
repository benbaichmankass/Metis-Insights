# Sprint Log: S-MLOPT-S8

## Date Range
- Start: 2026-06-03
- End: 2026-06-06 (infra 06-03; train + evaluate + decide 06-06)

## Objective
- Primary goal: **cross-symbol transfer** — train the decision model jointly on
  BTC + MES so the smaller-data symbol (MES) borrows statistical strength from
  the larger (BTC). A cheap small-data lever, natural since we already run two
  symbols, and a direct response to the S6 finding that the per-symbol real
  population is too sparse.
- Success (roadmap): the joint config beats the per-symbol (MES-only) model on
  the MES holdout.

## Tier
- **Tier-1** for the multi-symbol dataset build (additive, read-only over built
  `market_raw`). **Tier-3** for the manifest
  (`ml/configs/setup-candidates-metalabel-xsym-v1.yaml`) + any promotion past
  `shadow` — operator-gated; ships at `research_only`. Draft PR; the manifest is
  a proposal.

## Starting Context
- M14 Phase 1.4, the last Phase-1 sprint, on top of S5/S6/S7. The `setup_candidates`
  family already carries a `symbol` column; S8 makes it **buildable across
  symbols at once** so the joint dataset exists, and proposes the model that uses
  `symbol` as a transfer feature.

## Files and Systems Inspected
- `ml/datasets/families/setup_candidates.py` (per-symbol build internals — vol
  bucketing is per-dataset, so per-symbol independent bucketing then
  concatenation is the correct joint build), `ml/datasets/cli.py` (`key=value`
  family args → `market_raw_paths` as a comma-separated string), the S6
  `setup-candidates-metalabel-v1.yaml` stack being mirrored.

## Work Completed
- **Multi-symbol build** — `setup_candidates.iter_rows` gained `market_raw_paths`
  (a list, or a comma-separated string from the build CLI). The per-symbol body
  was refactored into `_iter_one_symbol`; `iter_rows` resolves the path(s)
  (`_resolve_market_raw_paths`) and concatenates. **Each symbol is CUSUM-sampled
  + vol-bucketed against its OWN distribution** (BTC ≫ MES volatility — global
  buckets would be wrong), so the joint dataset is a clean union with `symbol`
  carried as a column. The singular `market_raw_path` path is unchanged.
- **Cross-symbol manifest** `ml/configs/setup-candidates-metalabel-xsym-v1.yaml`
  (Tier-3 proposal) — the S6 meta-label with `symbol_scope: all` + a `symbol`
  categorical feature.
- **Tests** (`tests/ml/test_cross_symbol.py`): `_resolve_market_raw_paths` forms
  (single / list / comma-string / error), joint build concatenates both symbols
  with per-symbol bucketing and yields more rows than either alone.

## Validation Performed
- `tests/ml/test_cross_symbol.py` → 3 passed; full `tests/ml/` (ex. the 2
  pandas-only modules) → **509 passed, 1 skipped**; `ruff` clean. The refactor is
  default-preserving (all existing `setup_candidates` tests pass unchanged).
- Trainer-VM transfer experiment (joint BTC+MES vs MES-only meta-label, scored on
  the REAL MES holdout): reported below.

## Trainer-VM transfer experiment — build works; claim not yet measurable (MES has 0 real trades)
Ran via `trainer-vm-diag` (#2702). The joint build assembles cleanly:

```
MES rows=6723 (synth=6723, live=0)   BTC rows=16084 (synth=15732, live=352)
MES real holdout too small (0) for a powered transfer test — reporting availability only.
```

**The cross-symbol *capability* is verified** — the joint dataset assembles
6,723 MES synthetic candidates + 16,084 BTC candidates, each vol-bucketed against
its own scale. **But the transfer *claim* cannot be validated yet: MES has ZERO
real closed trades** in the journal (consistent with `setup_labels` MES datasets
being 0 rows, #2696). There is no MES real holdout to measure "does adding BTC
help MES" against — the experiment correctly refused to fabricate one and
reported availability only.

Honest read:
- This is itself a finding: **MES isn't producing real trades** (a known
  operational theme — MES/IBKR has gone dark before; out of scope here, belongs
  to /health-review or /performance-review). Cross-symbol transfer is *exactly*
  the lever that would help MES once it trades — and the joint-build machinery is
  now ready for the moment it does (or once S7 backtest-augments MES labels:
  `include_backtest` + the recorder can manufacture an MES holdout from MES
  backtests).
- **Deliverable stands:** the Tier-1 joint-build enabler + the xsym manifest are
  built, tested, and CI-green. The empirical transfer number is deferred to when
  an MES real (or backtest-augmented) holdout exists — not claimed prematurely.

## FINISH — actually train + evaluate (2026-06-06, trainer-vm-diag #2891–#2894)

The 06-03 infra sprint left the empirical number deferred and the daily build
unwired, so the xsym manifest skipped every cycle (exit 78, `dataset_absent`;
PR #2886). This finish trains + evaluates the model and decides the build wiring.

**Data fact (unchanged, now n=354):** all 354 real closed trades are BTCUSDT;
**MES still has ZERO closed trades.** So there is **no real MES holdout** and the
intended **BTC→MES transfer remains unmeasurable**. The only runnable cross-symbol
test is the **reverse** — does pooling MES *synthetic* + a `symbol` feature help
the **real-BTC** holdout (the actual n~352 floor)?

**Joint build (#2892):** `setup_candidates/all/all/v001` = 22,814 rows (BTC synth
15,737 + MES synth 6,723 + BTC real 354 + MES real 0); `symbol` column present,
both symbols present; synthetic win-rate 0.452 vs real-BTC 0.249.

**Clean ablation (#2893–#2894)** via `eval_split_compare.py` on the **identical**
354-trade real-BTC holdout (BTC-only rebuilt `--overwrite` from the **same** BTC
1h v002 + live trades → the only diff vs joint is `{+MES synthetic, +symbol}`):

| Model | acc | precision | recall | f1 | brier | purged-WF f1 |
|---|---|---|---|---|---|---|
| **JOINT (xsym)** | **0.7571** | **0.5417** | 0.1477 | 0.2321 | 0.2201 | 0.2092 |
| BTC-only (v1) | 0.6808 | 0.2093 | 0.1023 | 0.1374 | 0.2220 | 0.3693 |
| Majority baseline | 0.7514 | — (base rate 0.2486) | | | | |

The joint beats BTC-only by **+0.076 acc / +0.33 precision** and is the **first
meta-label in this family to clear the majority baseline** on the real holdout
(0.7571 > 0.7514), with a decision-useful precision (0.54 = **2.2× base rate**;
BTC-only 0.21 is *below* base rate).

**Honest verdict — does cross-symbol pooling break the n~352 floor? Weakly / not
decisively.** Best real-holdout meta-label to date, but: (1) win is on BTC
(reverse transfer), not MES; (2) leak-free purged-WF does **not** corroborate
(BTC-only f1 0.369 > joint 0.209 — though joint folds pool both symbols); (3)
n=354 is noisy (BTC-only acc swung 0.68–0.74 across builds); (4) "cross-symbol"
vs "more rows" (22k vs 16k) not fully disentangled. The binding constraint stays
**data scale** (354 BTC trades, 0 MES).

**Decisions:**
- **Promotion (Tier-3, operator-gated):** **PROPOSE** `research_only → shadow`
  (observe-only track record; never influences an order). **Not flipped
  autonomously** — manifest stays `research_only`.
- **Daily build:** wired **opt-in/default-off** — `build_xsym_setup_candidates()`
  gated `ICT_BUILD_XSYM=1` in `scripts/ops/build_trainer_datasets.sh` (mirrors
  `build_funding_oi`). Default off → cycle byte-identical, manifest keeps
  skipping (PR #2886). Flip it on the trainer unit if/when the model is promoted.

**Follow-up levers:** re-run with a real MES holdout once MES accrues closed
trades (the *intended* transfer); add MGC/MHG; combine with the S9 range-vol/yz
features (the lever that *did* lift, MB-20260603-004). Tracked in `MB-20260606-002`.

## Documentation Updated
- `ROADMAP.md` S-MLOPT-S8 row; `docs/ml/optimization-roadmap.md` Session 1.4
  (06-03 infra; 06-06 EVAL DONE block + status → DONE/qualified-positive);
  `docs/architecture/ai-model-platform.md` change-log; this sprint log (FINISH
  section); `ml/configs/setup-candidates-metalabel-xsym-v1.yaml` header/notes
  (eval result + promotion proposal); `scripts/ops/build_trainer_datasets.sh`
  (opt-in `build_xsym_setup_candidates`); `docs/claude/ml-review-backlog.json`
  (`MB-20260606-002`).

## Risks and Follow-Ups
- **MES real holdout is small** — the transfer claim is only as powerful as the
  real MES trade count; a low-power result is honest, not a failure. Pairs with
  S7 (backtest-augmented MES labels) to strengthen it.
- **Per-symbol bucketing** keeps the vol feature comparable; if a future joint
  regime model wants a shared vol scale, that's a separate choice.
- **Tier-3 gate stands**: the manifest is a proposal; the model is `research_only`.

## Next Recommended Sprint
- Phase 1 is complete (S5–S8). Phase 2 (better features: range-based vol
  estimators S9, order-flow/VPIN S10, funding/OI S11) or the highest-leverage
  **S13 (3.1) per-bar regime scoring** (Tier-2, live-runtime — needs operator
  approval to ship). Good milestone to take stock.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage / live-path file touched; manifest is a Tier-3 proposal.
- [x] Roadmap status was checked + updated.
- [x] Contradictions were recorded (none new).
- [x] Remaining unknowns were stated clearly.
