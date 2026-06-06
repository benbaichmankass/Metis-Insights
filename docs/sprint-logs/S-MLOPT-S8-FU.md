# Sprint Log: S-MLOPT-S8-FU

## Date Range
- Start: 2026-06-06
- End: 2026-06-06

## Objective
- **Experiment 1 (Phase 1.4 + 2.1, highest priority):** stack the two
  *independent* levers the prior sprints isolated — S8 cross-symbol pooling and
  S9 range-vol features — into one meta-label and test whether they combine.
  Build `setup-candidates-metalabel-xsym-yz-v1` (joint BTC+MES + `symbol`
  feature PLUS the four range-vol estimators) and run the three-way ablation
  (joint+yz vs joint-only vs BTC-only) on the IDENTICAL real-BTC holdout.
- **Experiment 2 (Phase 4.2 tooling):** wire a sync of the live VM's
  `shadow_predictions.jsonl` into the trainer so `python -m ml gate-check` can
  compute `live_agreement` + drift LOCALLY (today they report `insufficient`
  because the log isn't on the trainer and `/api/bot/trades/scores` is
  unreachable from a web session). Re-run gate-check on
  `btc-regime-1h-lgbm-yz-v1` and quote the full packet.
- **Experiment 3 (gated):** check the trainer `trade_journal.db` for closed MES
  trades; if still ~0, the intended BTC→MES transfer stays unmeasurable —
  document, do NOT fabricate a holdout.

## Tier
- **Tier-1** for the `setup_candidates` family extension (additive, leak-safe,
  past-only) + the `sync_trainer_data.sh` shadow-log sync (read-only artifact
  pull, mirrors the existing `trade_journal.db` sync).
- **Tier-3** for the new manifest `setup-candidates-metalabel-xsym-yz-v1.yaml`
  and any promotion past `shadow` — operator-gated; ships at `research_only`.
- Draft PR #2903; the manifest is a documented-negative proposal.

## Starting Context
- M14 ML-Optimization follow-ups from the 2026-06-06 `/ml-review`
  (`MB-20260606-002` + `MB-20260527-004`), roadmap Sessions 1.4 / 2.1 / 4.2.
- S8 left a qualified-positive cross-symbol meta-label (joint acc 0.7571 /
  precision 0.5417 on the real-BTC holdout vs BTC-only 0.6808 / 0.2093) and a
  follow-up lever: "combine with the S9 range-vol/yz features (the feature lever
  that *did* lift)." This sprint tests that combination.
- `MB-20260527-004` / `MB-20260529-001` repeatedly hit the same wall: the
  realized join (`/api/bot/trades/scores`) is unreachable from a web session,
  so `live_agreement` / `drift` never compute for any shadow regime head.

## Repo State Checked
- Branch `claude/mlopt-followups-VwDhX` off `main` (HEAD e9c3efa).
- Trainer VM `main` tree at e9c3efa; experiments run from an isolated git
  worktree of the branch (`/tmp/mlopt-wt*`) so the daily cycle's
  `git reset --hard origin/main` never touches them and `python -m ml` picks up
  the worktree code (cwd precedence; verified `BUILDER_VERSION=v2`).

## Files and Systems Inspected
- `ml/datasets/families/setup_candidates.py`, `ml/datasets/volatility_estimators.py`,
  `ml/datasets/families/market_features.py` (range-vol computation to mirror).
- `ml/configs/setup-candidates-metalabel-{xsym-v1,v1}.yaml`.
- `scripts/ops/sync_trainer_data.sh`, `scripts/ops/build_trainer_datasets.sh`.
- `ml/cli.py` (`gate-check`, `model-attribution`), `ml/promotion/{gates,attribution}.py`.
- `scripts/ml/eval_split_compare.py`.

## Work Completed
- **Experiment 1 — family + manifest (Tier-1 + Tier-3):**
  - `setup_candidates` builder **v1 → v2**: every emitted row (cusum /
    signal_log / backtest / live) now carries the four S9 range-vol estimators
    (`parkinson_vol` / `garman_klass_vol` / `rogers_satchell_vol` /
    `yang_zhang_vol`), computed past-only over the same window as
    `rolling_log_return_vol` (the same `volatility_estimators` computation
    `market_features` uses). Confirmed the range-vol features ARE available at
    signal-time in this family (it builds from `market_raw` OHLC) — the task's
    "extend the family if needed" was satisfied additively, no new feed.
  - New `ml/configs/setup-candidates-metalabel-xsym-yz-v1.yaml` (`research_only`)
    stacks the S8 joint pooling with the four range-vol features.
  - Tests for the new columns on synthetic + live rows; ruff clean; manifest
    loads.
- **Experiment 2 — trainer-side realized join (Tier-1):**
  - `scripts/ops/sync_trainer_data.sh` now pulls the live VM's
    `shadow_predictions.jsonl` (+ `_backfill`) into `runtime_logs/` (same rsync
    pattern as `trade_journal.db`), landing them where the `gate-check` CLI
    defaults resolve.

## Validation Performed
- **Local:** `pytest tests/ml/test_setup_candidates.py tests/ml/test_cross_symbol.py
  tests/ml/test_metalabel.py tests/ml/test_volatility_estimators.py` → 40 passed;
  `ruff check` clean; both manifests load via `TrainingManifest`;
  `bash -n sync_trainer_data.sh` OK.
- **Experiment 1 (trainer-vm-diag #2906):** joint dataset built on builder v2
  (22,814 rows = BTC synth 15,737 + MES synth 6,723 + BTC real 354 + MES real 0;
  `has_yang_zhang_vol=True`), BTC-only rebuilt (16,091 rows) — identical
  composition to the S8 ablation, same 354-trade real-BTC holdout. **HONEST
  NEGATIVE** on the real-BTC `live_holdout` (majority baseline acc 0.7514):

  | model | acc | precision | recall | f1 | brier |
  |---|---|---|---|---|---|
  | joint **+yz** (xsym-yz-v1) | 0.7203 | 0.2105 | 0.0455 | 0.0748 | 0.2185 |
  | joint-only (xsym-v1, S8) | **0.7571** | **0.5417** | 0.1477 | 0.2321 | 0.2201 |
  | BTC-only (metalabel-v1) | 0.6808 | 0.2093 | 0.1023 | 0.1374 | 0.2220 |

  Adding the range-vol features **degraded** the cross-symbol meta-label —
  precision collapsed 0.54 → 0.21 (down to ~base rate) and accuracy fell below
  the majority baseline, erasing the S8 gain. Purged-WF within-distribution is a
  wash (joint+yz f1 0.246 / joint-only 0.209 / BTC-only 0.369). The two levers
  do **not** stack: the S9 range-vol lever is specific to the volatility-regime
  label; it does not transfer to the win/loss DECISION target, and at n=354 the
  extra features overfit the synthetic train distribution. **Do NOT adopt
  xsym-yz-v1**; the joint-only `xsym-v1` remains the family's best meta-label
  and the S8 research_only→shadow proposal stands unchanged.
- **Experiment 2 (trainer-vm-diag #2907, #2908):** the updated sync pulled
  `shadow_predictions.jsonl` (28,666 fresh lines) + `_backfill` (11,606) into the
  trainer; the yz head carries 51 real-time per-bar records. **The realized join
  now COMPUTES locally** (the unblock). `gate-check btc-regime-1h-lgbm-yz-v1 →
  advisory` full packet, **READY: false**:
  - `non_degenerate` PASS (min per-class F1 0.483)
  - `sample_sufficiency` PASS (n_eval 8760)
  - `shadow_soak` **FAIL** 1.8 / 7 d (stage entry 2026-06-05T00:59Z — young)
  - `beats_baseline` insufficient (no brier_lift live attribution)
  - `oos_edge` insufficient even WITH `--datasets-root` (pre-existing — the yz
    `market_features` v3 dataset is not built in `datasets-out`; same as the 6/6
    ml-review #2900, a separate follow-up, NOT a regression from this sprint)
  - `cross_run_stability` insufficient (2 of 3 runs carry macro_f1)
  - `live_agreement` insufficient — `model-attribution` for the yz head returns
    `[]`: the 51 per-bar records (only ~1.8 d old, since 6/5) don't yet overlap
    a closed-BTC trade window (and there were ~0 closed BTC trades in that
    window). **Honest data-thinness, not an infrastructure block** — the join
    runs; it just has nothing to score yet.
  - `drift_clean` insufficient (no two-window drift report).
- **Experiment 3 (trainer-vm-diag #2905):** `trade_journal.db` (trainer copy)
  has 354 closed real (non-bt/non-demo) trades, **ALL BTCUSDT; MES = 0**. The
  intended BTC→MES transfer remains unmeasurable — no real MES holdout exists.
  Documented; no holdout fabricated.

## Documentation Updated
- `ml/configs/setup-candidates-metalabel-xsym-yz-v1.yaml` — eval result + verdict.
- `scripts/ops/sync_trainer_data.sh` — header documents the shadow-log artifact.
- `docs/ml/optimization-roadmap.md` — Session 1.4 / 2.1 / 4.2 progress notes.
- `docs/claude/ml-review-backlog.json` — `MB-20260606-003` (new, combined-lever
  negative); evidence appended to `MB-20260527-004` (realized join now wired).
- `docs/claude/trainer-vm-mode.md` — sync artifact list includes the shadow log.
- This sprint log.

## Contradictions or Drift Found
- None. The `oos_edge` insufficiency with `--datasets-root` is a pre-existing
  dataset-build gap (yz `market_features` v3 not in `datasets-out`), already
  tracked via the 6/6 ml-review; logged as a follow-up, not a contradiction.

## Risks and Follow-Ups
- **xsym-yz-v1** stays `research_only` as a documented negative; safe to retire
  if the registry slot is wanted (it is not in the daily cycle's manifest set).
- **Realized-join evidence** will accrue: as the post-6/5 per-bar shadow record
  ages and BTC trades close inside its window, `live_agreement` will yield a
  real AUC. Re-run `gate-check` (and add `--datasets-root` after a yz
  `market_features` v3 rebuild) in ~1–2 weeks.
- Wire the shadow-log sync into the trainer's daily `sync_trainer_data.sh` call
  (already in the script; runs automatically on `main` once merged).

## Deferred Items
- **Experiment 3 (BTC→MES transfer):** blocked on MES closed trades (0). Re-run
  the joint-vs-MES-only comparison on a real MES holdout once MES accrues
  trades; extend the joint dataset to MGC/MHG once those symbols trade.
- yz `market_features` v3 dataset build so `oos_edge` computes for the yz heads.

## Next Recommended Sprint
- Phase 4.1 (drift-triggered retrain) or close out the regime-head promotion
  evidence once the soak + realized join mature.

## Wrap-Up Check
- doc-freshness run; ml-review-backlog updated; Claude-channel ping sent.
