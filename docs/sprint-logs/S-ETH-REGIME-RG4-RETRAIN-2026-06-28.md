# Sprint Log: S-ETH-REGIME-RG4-RETRAIN-2026-06-28

## Date Range
- Start: 2026-06-28
- End: 2026-06-28

## Objective
- Primary goal: Root-cause why the ETH 1h regime heads discriminate the vol regime offline (RG3 0.70–0.73) but are NO_EDGE on live logged rows (RG4 ~0.46), and retrain ETH regime head candidate(s) that clear RG4 live (≥0.55).
- Secondary goals: Gate every candidate through RG3 + RG4; re-run the ETH A/B + cell-selection walk-forward only for a head that robustly clears RG4; close/advance MB-20260627-003.

## Tier
- Tier 1 (trainer-VM research, autonomous). No live config/order-path change. Any advisory promotion or live-cell authoring remains Tier 3 (operator-gated) and is only *proposed* here.
- Justification: builds datasets, trains shadow-stage heads, runs read-only gates on the trainer VM; writes only research docs + backlog + manifests (shadow target stage).

## Starting Context
- Active roadmap items: Design-A multi-symbol (#1) — ETH confirmation; MB-20260627-003 (ETH head NO_EDGE live; needs retrain).
- Prior sprint reference: the multi-symbol-A session on branch `claude/ml-strategies-deep-review-51n3cw` (merged into this branch for its tooling: `--symbol` backtest harness, `walkforward_cell_selection.py`, `rg4_targeted.sh`, the labeling-gap fix 7a051e5, the ETH research doc).
- Known risks at start: RG4 scores the EXACT rows the live runtime logged, so a brand-new head has none until it soaks — the literal "retrain a head that clears RG4 live" bar cannot be met in-session for a new head without a soak.

## Repo State Checked
- Branch or commit reviewed: `claude/eth-regime-retrain-skew-mnske0` (merged origin/claude/ml-strategies-deep-review-51n3cw for tooling).
- Deployment state reviewed: trainer VM registry (eth-regime/direction heads), ETH datasets, mirrored live `shadow_predictions.jsonl` (125,965 rows).
- Canonical docs reviewed: CLAUDE.md (env-gates, VM authority split), trainer-vm-mode.md (relay), the prior ETH research doc.

## Files and Systems Inspected
- Code files inspected: `scripts/ml/replay_pregate_live.py` (RG4), `replay_pregate_fleet.py` (RG3), `fleet_scorecard.sh`, `rg4_targeted.sh`, `src/runtime/regime_shadow.py` (live feature builder), `ml/configs/btc-regime-{5m,15m,1h}-lgbm-v2.yaml`, `ml/configs/eth-regime-1h-lgbm-{v1,xasset-v1}.yaml`.
- Config files inspected: `ml/datasets/families/market_features.py` (vol_threshold/regime_label), `scripts/ops/build_trainer_datasets.sh` (build_bybit_pair — vol_threshold=0.005).
- Services or timers inspected: trainer-vm-diag relay; registry publish path (`publish_trainer_mirror.sh`).
- GitHub Actions workflows inspected: `trainer-vm-diag.yml` (relay contract, 10-min timeout, nohup pattern).

## Work Completed
- **Root-caused the skew** (`scripts/ml/_feature_parity_probe.py`, trainer-diag #4869): both ETH 1h heads emit a near-constant high P(volatile) on live rows (no discrimination). v1: live `rolling_log_return_vol` +51% vs the 5yr train mean → 73% of live rows in top `vol_b2`. xasset: `xa_breadth_up` all-zeros in the TRAINING dataset (dead cross_asset side-stream) but ~0.45 live.
- **Found the RG4 harness threshold mismatch**: RG4 defaults `vol_threshold=0.003`; Bybit datasets build `regime_label` at 0.005. Recalibrated via `scripts/ml/rg4_vt_sweep.sh` (trainer-diag #4893). At matched 0.005, eth-regime-1h-lgbm-v1 RG4 = 0.58 but **knife-edge** (0.46–0.58 across 0.003–0.007 on 111 rows) → NOT robust; xasset stays NO_EDGE at all thresholds. BTC heads pass robustly at both thresholds (5m 0.79/0.83, 15m 0.73/0.71, 1h 0.62/0.58).
- **Trained the strong-timeframe replacements** (trainer-diag #4870–#4889): built ETH 5m/15m `market_features` (525,864 / 175,272 rows), added ETH 5m/15m to the daily build, wrote + trained `eth-regime-{5m,15m}-lgbm-v1` (mirror BTC 5m/15m v2). RG3: 5m 0.770 / 15m 0.788 TRUSTWORTHY (recent-fold 0.738/0.751). Both register at shadow → soaking.
- Authored research write-up, backlog updates, and two tooling-fix items.

## Validation Performed
- Tests run: ruff clean on new Python helpers; manifests parse; `bash -n` on shell helpers; PR #4868 CI green (15/15 checks incl. ruff-lint, pytest-run/collect, all guards).
- Dry-runs or staging checks: RG3 (clean-candle, 8000 bars/head) + RG4 (logged-live rows) + RG4 threshold sweep, all on the trainer VM.
- Manual code verification: confirmed the dataset-build vol_threshold (0.005) vs the RG4 default (0.003) directly in source.
- Gaps not yet verified: RG4 on the new 5m/15m heads (0 live rows today — soak-pending); the post-soak A/B re-run.

## Documentation Updated
- Subsystem doc updates: `docs/research/A-multisymbol-ETH-2026-06-28` section appended to `docs/research/A-multisymbol-ETH-2026-06-27.md`.
- Backlog: `docs/claude/ml-review-backlog.json` — MB-20260627-003 → in_progress with the corrected verdict; added MB-20260628-RG4-THRESH + BL-20260628-XA-TRAINING-ZERO.
- Manifests added: `ml/configs/eth-regime-{5m,15m}-lgbm-v1.yaml`; build script: `scripts/ops/build_trainer_datasets.sh` (+ ETH 5m/15m).
- Rules/Architecture/Roadmap/TRADE-PIPELINE: no change (no pipeline-stage or contract change; shadow-stage research only).

## Contradictions or Drift Found
- The RG4 harness (0.003) vs the dataset build (0.005) is an internal inconsistency that mis-scored every Bybit head's RG4 — logged as MB-20260628-RG4-THRESH (and noted the prior fleet scorecard MB-20260626-001 numbers were all at 0.003).
- The prior research doc's "ETH head fails RG4 (0.46, train/serve skew)" was over-stated in severity by the harness threshold; corrected in the appended section (the *verdict* — 1h head not live-ready — still holds).

## Risks and Follow-Ups
- Remaining technical risks: the new 5m/15m heads add two `(symbol,tf)` groups to the live per-bar regime scorer on the 2-core money VM — watch heartbeat/CPU over the first cycles (cf. 2026-06-09/10 wedges, MB-20260618-XA-SOAK-WATCH).
- Remaining product decisions (Tier 3): advisory promotion of any ETH regime head — gated on a robust post-soak RG4 ≥ 0.55 + operator approval.
- Blockers: RG4 on the new heads is future-dated until they accrue live shadow rows.

## Deferred Items
- Implement the RG4 metadata-threshold fix (MB-20260628-RG4-THRESH) and re-run the fleet scorecard at matched thresholds.
- Rebuild the ETH cross_asset side-stream in the daily build and retrain the xasset head (BL-20260628-XA-TRAINING-ZERO).
- SOL regime head (follow-on once ETH is solved).

## Next Recommended Sprint
- Suggested next sprint: post-soak RG4 of `eth-regime-{5m,15m}-lgbm-v1` (≈ 1–2 wk out), then — if robustly ≥ 0.55 — re-run the ETH vol-split A/B + cell-selection walk-forward with the passing head and propose advisory promotion.
- Why next: it's the only step that converts the RG3-strong replacement heads into a live-viable, operator-approvable ETH regime gate.
- Required verification before starting: confirm the new heads accrued live shadow rows (a few hundred) and the daily build kept ETH 5m/15m datasets fresh.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] If this sprint touched any pipeline stage, `docs/TRADE-PIPELINE.md` was updated and the dashboard's Trade Process tab was visually verified. — N/A: no pipeline-stage change (shadow-stage research only).
- [x] Roadmap status was checked.
- [x] Contradictions were recorded.
