# S-AUDIT-P1-TRAINER-HONESTY-2026-07-31 — full-system-audit P1 execution (trainer honesty)

## Date Range
2026-07-31 → 2026-07-31 (same session as S-AUDIT-P0-CLOSEOUT; P1 started on operator go)

## Objective
Execute the P1 tier of `docs/audits/full-system-audit-2026-07-31.md`
("trainer honesty"): P1.1 the vt004 ManifestDatasetMismatch failed cycle,
P1.2 the vol_threshold-less dataset dirs, P1.3 outcome-family starvation
(reconcile 0-vs-506 + untrained-N-cycles escalation + FLAGGED-skip test),
P1.4 disk cleanup (86% root), P1.5 rc=0 ambiguity. Also merged **P0.2**
(PR #8163) on the operator's explicit go this session.

## Tier
Tier 1 throughout — trainer tooling, cycle observability, tests, a retired
candidate/offline manifest, docs/backlog. No order path, no config/strategies,
no live-VM mutation. The P1.4 `--apply` deletion on the trainer VM is
autonomous-territory trainer maintenance (report reviewed before apply).

## Starting Context
- P0 tier fully merged (#8179, #8180 + dashboard#203 + android#115, #8181).
- PR #8163 (P0.2 claim-correction) operator-approved this session; a
  concurrent session (the PR's original author) resolved the same
  behind-main conflict in parallel — both resolutions converged (backlog =
  main's row per its `note_dedupe`; docstring correction intact).
- Trainer baseline pulled live via trainer-vm-diag **#8184**.

## Repo State Checked
- `main` at `6c1ffd32` (post-#8181); session branch re-rooted from it.
- Trainer VM at `6c1ffd32` (cycle self-heals onto main), root disk **86%**
  (39G/45G; `datasets-out` 15G, `ml/` 2.9G), last cycle `overall_rc: 1`.

## Files and Systems Inspected
- `scripts/ops/run_training_cycle.sh` (full read), `scripts/ops/build_trainer_datasets.sh` (build_family + invocations)
- `ml/experiments/runner.py::_verify_declared_build_params`, `ml/manifest.py` (DatasetRef/build_params), `ml/cli.py` (train output shape), `ml/datasets/cli.py` (build output shape), `ml/datasets/builder.py::DatasetPaths`
- `ml/configs/btc-regime-15m-lgbm-{vt004-pcv,vt003-pin,vt004-pin,vt005-pin}-v1.yaml`
- `scripts/ml/backfill_dataset_vol_threshold.py` (+ its #8141 history), `scripts/ops/dataset_unchanged_check.py`
- `src/web/api/routers/training_center.py` (cycle-event consumers — pass-through, no field coupling)
- `scripts/check_diagnostic_provenance.py` (rule set, to write compliant new scripts)
- Trainer VM via diag #8184: cycle log tail, all 15 BTCUSDT/15m dataset metadatas, the report-only vol_threshold scan (38 dirs), outcome-family files + build log, disk.

## Work Completed

1. **P0.2 MERGED (#8163, operator-approved this session).** Conflict vs the
   four P0 merges resolved (kept main's `BL-20260731-FILLS-STORE-PREDATES-THE-FABRICATION`
   row per its recorded `note_dedupe`; final net diff = the docstring
   correction only). A concurrent session pushed an equivalent resolution
   seconds later; content converged, auto-merge landed it.

2. **P1.3(a) RECONCILED — the "0-vs-506" starvation was a diagnostic lie,
   not starvation.** `build_family` in `build_trainer_datasets.sh` read
   `row_count` from the FIRST bash-expanded glob match
   (`<family>/*/*/*/metadata.json`) — alphabetical order, i.e. the empty
   May-22 `MES/all/v001` dir — not the dir the build wrote. Live proof
   (diag #8184): `trade_outcomes` logged `"ok, row_count 0"` at 09:35:59Z
   while `all/all/v002/data.jsonl` was 254,957 bytes **modified the same
   morning** (execution_quality: 840,991 bytes, same shape).
   `trade-outcome-lgbm-v1` trained `manifest_ok` the same cycle. Sub-class-B
   unprovenanced diagnostic (implicit input selection), fixed at the source:
   build_family now parses the build's own `wrote dataset under <dir>` line
   and reads THAT dir's metadata (`dataset_dir` + honest
   `row_count_note: count UNKNOWN (not zero)` when unparseable).
   **Genuinely empty** (not lied about): the MES-scoped variants
   (`{trade_outcomes,setup_labels,execution_quality}/MES/all/*`),
   `review_journal` (both versions 0 rows), and the rc-78 manifests
   (exit-policy, mes-execution-quality, mes-setup-quality,
   mes-trade-outcome-winrate, setup-candidates-metalabel-paper) — these now
   surface per-cycle via the new staleness escalation (below); their
   keep-vs-retire call routes to the next `/ml-review` with real evidence.

3. **P1.3(c) untrained-N-cycles escalation** — new
   `scripts/ops/manifest_training_staleness.py`: after every cycle, one
   `manifest_untrained_stale` event per roster manifest with no registered
   run in `TRAINING_STALENESS_ALERT_DAYS` (default 7; grace window for
   fresh manifests; unresolvable model_id reported, never dropped) + an
   always-emitted `training_staleness_summary` denominator line. Rides the
   cycle log → mirror → `/api/bot/ml/cycle` → the review skills. This is
   the adder-up over the four independent skip paths the audit flagged.

4. **P1.3(d) FLAGGED-skip test** — the enforce branch
   (`manifest_audit_skipped_enforced` → skip) now has a real end-to-end
   test: a stub `ml` package in the bash fixture gives the in-script audit
   heredoc a controllable verdict (`AUDIT_FLAG_TARGET`); asserts the
   flagged manifest is skipped `reason=audit_flagged`, never handed to
   `ml train`, cycle stays green, counts correct.

5. **P1.5 cycle_end legibility** — `cycle_end` now carries
   `trained/skipped/failed/already_done` counts + `outcome ∈ {trained,
   nothing_trained, already_complete}`. Exit codes deliberately UNCHANGED
   (systemd treats non-zero as unit failure; a routine lock-skip must not
   read as one) — consumers read `outcome`, never infer from rc. Tested.

6. **Summary-extraction fix (found in passing, P1.5-adjacent)** — `ml train`
   prints its summary as **multi-line** `indent=2` JSON; the cycle's
   `grep -E '^{' | tail -1` captured the bare `{` line, so **every**
   `manifest_ok` logged `model_id: null` (live-verified across the whole
   2026-07-31 cycle) and every rc-78 skip lost its real `reason`
   (dataset_absent mislabelled as the empty_dataset default). New shared
   `scripts/ops/_last_json_object.py` recovers the last complete object;
   `manifest_ok` now carries `model_id`/`registered`/`experiment_dir`
   (replacing the never-populated `metrics_path` — the train summary never
   had that key). The test shim now mirrors the real multi-line shape —
   the old single-line shim is exactly why tests never caught this.

7. **P1.1 vt004 failed cycle FIXED — manifest retired.**
   `btc-regime-15m-lgbm-vt004-pcv-v1` declared `build_params.vol_threshold:
   0.004` against `v004`, whose measured metadata records **0.003**
   (`derived_from_labels`) — the historically mislabeled probe
   (MB-20260701-001/MB-20260716-BUILDPARAMS-IGNORED). The guard failing it
   every cycle was CORRECT; the manifest is superseded by the matched-sibling
   pin triple (`vt003/vt004/vt005-pin` on genuine v513/v514/v515 —
   `vt004-pin-v1` trained `manifest_ok` this very cycle). Retired the pcv
   manifest (registry history retained); supersession recorded in the
   vt004-pin header. This was the only failing manifest → next cycle is
   green (`outcome: trained`).

8. **P1.2 VERIFIED ALREADY DONE — closed with numbers.** The report-only
   scan (diag #8184) shows **34/38** market_features dirs carry a recorded
   point `vol_threshold` (incl. `BTCUSDT/15m/v520 = 0.005`, the LIVE BTC
   vol-gate head's dataset) and the remaining **4** v001 dirs carry explicit
   `vol_threshold_bracket` + `vol_threshold_source: bracket_only_no_candidate`
   annotations — i.e. the #8141 backfill's `--apply` already ran; coverage
   is 100% annotated (point value or honest bracket), 0 wholly absent.

9. **P1.4 disk-cleanup tool** — new `scripts/ops/trainer_dataset_gc.py`:
   report-only by default, `--apply` deletes; keeps anything pinned by any
   `ml/configs` manifest, the canonical `v001/v002` nightly versions, and
   anything younger than `--min-age-days` (14). Tested (pinned/canonical/
   fresh kept; aged unpinned collected; missing root = loud absent-result).
   Trainer-side report → apply dispatched post-merge (see Verification).

## Validation
- `tests/test_run_training_cycle_sh.py` — **11/11** (3 pre-existing + 8 incl.
  the new legibility/FLAGGED classes; the multi-line-summary test FAILS
  against the pre-fix extractor by construction).
- `tests/test_trainer_cycle_legibility_helpers.py` — **14/14** (extractor,
  staleness incl. grace + unresolvable + always-summary, GC keep/collect/
  apply/absent-root).
- `tests/test_training_center_api.py` + `test_trainer_manifest_health.py` +
  `test_build_trainer_datasets_provenance_flags.py` — 35/35.
- `bash -n` clean on both edited shell scripts; `ruff` clean;
  `check_diagnostic_provenance.py --all` reports nothing on the new/edited
  files (annotations verified).

## Verification (live)
- Trainer deploys these changes automatically (the cycle force-checkouts
  `origin/main` at start); the next nightly cycle is the live verification:
  expect `cycle_end` with counts + `outcome`, populated `model_id` on
  `manifest_ok`, real `row_count` on family builds, the first
  `training_staleness_summary`, and NO vt004 failure.
- P1.4 executed via trainer-vm-diag after merge: GC report reviewed, then
  `--apply`; post-state `df` recorded in the dispatch issues.

## Follow-ups (logged, not silently dropped)
- The first staleness report will enumerate the never-trained manifests
  (exit-policy, mes-* quality/outcome, metalabel-paper, review_journal
  consumers) — route the keep-vs-retire decision to the next `/ml-review`.
- `review_journal` builds 0 rows from `comms/` — either the source dir has
  no answers or the builder's source contract drifted; covered by the same
  escalation.

## Docs Updated
- `ROADMAP.md` — ledger row **S-AUDIT-P1-TRAINER-HONESTY**.
- `docs/claude/health-review-backlog.json` — `BL-20260731-AUDIT-0731-NEW-FINDINGS`
  updated (items (7)/(9) resolution note); new row
  `BL-20260731-TRAINER-BUILDLOG-ROWCOUNT-LIE` (resolved, the reconcile record).
- `ml/configs/btc-regime-15m-lgbm-vt004-pin-v1.yaml` — supersession note.
