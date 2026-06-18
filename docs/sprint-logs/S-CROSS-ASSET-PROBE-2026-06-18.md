# Sprint Log: S-CROSS-ASSET-PROBE

## Date Range
- Start: 2026-06-18
- End: 2026-06-18

## Objective
- Primary goal: Answer "do peer assets predict a given asset?" cheaply (research-framework step 1), then — per operator steer — route the validated signal into the live conviction blend as a regime/sizing lens.
- Secondary goals: keep everything leakage-safe + reproducible; touch the live trader minimally and observe-only.

## Tier
- Tier 1 (research, datasets, tooling, docs) + Tier 2 (the D2a live feature-path wiring, observe-only) + Tier 3 (candidate→shadow promotion, operator-approved).
- Justification: the probe/ablation/manifests/scripts are Tier-1; `cross_asset_live.py` + the per-bar-scorer wiring touch the live feature path (Tier-2, observe-only, kill-switched); promoting the model to shadow is the autonomous step of the ladder (shadow→advisory stays the operator gate).

## Starting Context
- Active roadmap items: S-RESEARCH-FRAMEWORK (step 1 = cross-asset probe), M16 unified-confidence.
- Prior sprint reference: S-RECOMB-SWEEP / S-RESEARCH-FRAMEWORK / S-STRAT-REFINE-0618 (same day).
- Known risks at start: leakage in same-cadence peer features; CPU-wedge history on the per-bar regime scorer (2026-06-09/10).

## Repo State Checked
- Branch/commit: `claude/ict-strategy-expansion-awpv8x` off `main` (078bcc0); PR #3965 merged to main (3a771e5).
- Deployment state: live trader healthy (heartbeat running) at the start; bybit_2 live; the 5 ADX-gated pullback cells loaded.
- Canonical docs reviewed: CLAUDE.md, unified-confidence-risk-DESIGN.md, conviction_inputs.py contract.

## Files and Systems Inspected
- Code: `ml/datasets/families/market_features.py`, `ml/datasets/macro_features.py`, `ml/datasets/cross_asset_features.py`, `src/runtime/{regime_bar_scoring,regime_shadow,conviction,conviction_inputs}.py`, `ml/predictors/{shadow,lightgbm}.py`, `ml/trainers/lightgbm_multiclass.py`, `ml/cli.py`.
- Config: `ml/configs/btc-regime-1h-lgbm-v2.yaml`, `config/cross_asset.yaml` (new).
- Services: per-bar regime scorer (observe-only), `ict-trader-live`, trainer cycle.
- Workflows: vm-driver, system-actions (pull-and-deploy), vm-diag relay.

## Work Completed
- **Probe (step 1):** `ml/datasets/cross_asset_features.py` (pure peer-asset feature block, positional slots, past-only/leakage-safe), wired into `market_features` behind `cross_asset_path` (builder v7→v8, default-preserving), producer `scripts/ml/build_cross_asset.py`, A/B manifests `eth-regime-1h-lgbm-{v1,xasset-v1}`.
- **Results (trainer A/B):** regime probe POSITIVE (weighted-f1 +0.054 single-holdout) → CORROBORATED under purged-WF-CV (+0.026, leak-free) → ABLATED (SOL peer + relative-strength/beta carry it; lead-lag inert) → DIRECTIONAL probe WEAK-POSITIVE (sign-accuracy 50.6%→51.5%). Strategic read: strong regime conditioner, weak directional signal.
- **M16 routing (operator steer):** DESIGN `docs/cross-asset-regime-conviction-DESIGN.md` (fill the inert `c_reg` lens). **D2a built + shipped:** `config/cross_asset.yaml`, `src/runtime/cross_asset_live.py` (live xa computation reusing the pure fns, kill-switched `CROSS_ASSET_LIVE_DISABLED`, fail-permissive NaN-not-zeros, fetch-gate-bounded), wired into the per-bar regime scorer (additive `cross_asset_row` merge; non-xa heads project only their own cols).
- **Activation:** trained+registered `eth-regime-1h-lgbm-xasset-v1` in the production registry, **promoted candidate→shadow**, published the registry mirror to the live VM; dispatched `pull-and-deploy` to load the code + shadow head.
- Builder version v8→v9 (added `direction_label` forward label for the directional probe).

## Validation Performed
- Tests: new — `tests/ml/test_cross_asset_features.py`, `TestCrossAssetFeatures`/`TestDirectionLabel` in `tests/ml/datasets/test_market_features.py`, `tests/runtime/test_cross_asset_live.py`, cross_asset merge test in `test_regime_shadow_parity.py`. All green; 171 existing runtime tests + the dataset suite green; ruff clean.
- Trainer A/B run leak-free (purged-WF-CV) on real 5y data; default-preservation verified (non-xa columns byte-identical with `cross_asset_path` omitted).
- env-gate-guard + the env-gate survivor regression test satisfied via the inline `# allow-silent:` marker (kill-switch is default-ON, never the order path); documented in the env-gate-purge audit.
- Gaps not yet verified: live post-deploy health + the soak actually writing the xasset head to `shadow_predictions.jsonl` (verifying via the diag relay at wrap); D2b not built.

## Documentation Updated
- Roadmap: S-CROSS-ASSET-PROBE row (probe → corroboration → ablation → directional → M16 D2a).
- Subsystem docs: `docs/research/cross-asset-feature-probe-2026-06-18.md` (full results), `docs/cross-asset-regime-conviction-DESIGN.md`, `docs/research/research-framework-DESIGN.md` (step 1 reference), `docs/audits/env-gate-purge-2026-05-10.md` (new survivor), CLAUDE.md env table (`CROSS_ASSET_LIVE_DISABLED`).
- Backlogs: ml-review (D2b, daily-build, soak-watch), performance-review (convert-to-PnL gate, trend-side OOP, eth bybit_2 watch).

## Contradictions or Drift Found
- None new. The conviction blend's `c_reg` lens was already declared (weight 0.15) but inert (skipped for lack of a class-vector/calibrator) — this sprint sets up filling it rather than adding a new lens.

## Risks and Follow-Ups
- Remaining technical risks: the per-bar scorer now does an extra ETH peer fetch when the xasset head is loaded (bounded by the fetch-gate + budget; CPU-wedge history on this path → soak-watch backlog item MB-20260618-XA-SOAK-WATCH; kill-switch + demote are the rollbacks). The ad-hoc-built ETH cross-asset dataset will go stale until added to the daily build (MB-20260618-XA-DAILYBUILD).
- Remaining product decisions (Tier 3): shadow→advisory promotion + D4 (letting `c_reg` actually influence sizing) — both stay operator + M16-P2+ backtest gated.
- Blockers: none.

## Deferred Items
- D2b: regime class-probability vector + `c_reg` `regime_alignment`/calibrator + signal-time xa feed (MB-20260618-XA-D2B).
- Convert-to-PnL gate for the directional signal (PB-20260618-013).

## Next Recommended Sprint
- Suggested next: D2b (make `c_reg` real) once the D2a soak has accrued sane predictions; in parallel add ETH cross-asset to the daily dataset build.
- Why next: D2a only soaks the head; `c_reg` contributes nothing until D2b exposes the prob vector + alignment.
- Required verification before starting: D2a soak healthy (no wedge) + sane shadow score distribution.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] If this sprint touched any pipeline stage, `docs/TRADE-PIPELINE.md` was updated and the dashboard's Trade Process tab was visually verified. — N/A: observe-only shadow path only; no order-pipeline stage changed (no TRADE-PIPELINE.md edit needed).
- [x] Roadmap status was checked.
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.
