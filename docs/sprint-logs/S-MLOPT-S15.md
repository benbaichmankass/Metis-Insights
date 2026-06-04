# Sprint Log: S-MLOPT-S15

## Date Range
- Start: 2026-06-04
- End: 2026-06-04 (S15a shipped; S15b queued)

## Objective
- Primary goal: **regime-router phase-4** (Phase 3.3) — make a registry model
  usable as the live regime detector. Closes the taxonomy half of
  `MB-20260601-002`.
- The scoping pass found phase-4 was mis-specified: the ADX detector + policy
  table key on a **trend** axis (`chop`/`transitional`/`trending`), but every
  registry regime model predicts a **volatility** axis (`range`/`volatile`).
  Operator chose to pursue **both** resolutions as separate tracks:
  - **S15a — trend-regime model** (this commit): a model that predicts the
    trend axis, a true drop-in for the ADX detector.
  - **S15b — volatility as a 2nd axis** (queued): wire the existing vol
    classifier as a second, **observe-only** regime gate (2-D trend×vol policy).

## Tier
- **Tier 1** for the labeler + dataset wiring + tests (trainer-side tooling).
  **Tier 3** for the new manifest (`btc-regime-1h-trend-lgbm-v1`, research_only)
  and the stage-drift reconcile. S15b will be **Tier 2/3** but kept observe-only.

## Starting Context
- Active roadmap items: M14 Phase 3.3. S13 (per-bar scoring) merged + verified
  live this session; S14 (HMM) negative. The merged S13 unblocks regime heads
  accruing a shadow track record (needed before any detector goes live).
- Prior reference: `MB-20260601-002` (regime-router phase-4), the regime-router
  design `docs/research/regime-router-design-2026-06-01.md`.

## Files and Systems Inspected
- `src/runtime/regime/{detector,policy,__init__}.py` (the router: ADX-14
  detector + the `{chop,transitional,trending}` policy table — observe-only
  today, phases 1+2), `src/runtime/strategy_signal_builders.py` +
  `src/runtime/intents.py` (where the router stamps/shadow-gates),
  `docs/research/regime-router-design-2026-06-01.md` (§3 + phase-4 intent),
  `ml/configs/baseline-regime-classifier.yaml` (stage drift),
  `ml/datasets/families/market_features.py` (the forward-label machinery).

## Work Completed (S15a)
- **`ml/datasets/labeling/trend_regime.py`** — forward trend-regime labeler.
  Kaufman **efficiency ratio** (`|Σr| / Σ|r|`) of the forward log-return window
  → `chop`/`transitional`/`trending` by two thresholds. ER is short-window-stable
  (where forward ADX-14 is not) and maps to the policy taxonomy. A future-only
  **label**, never a feature → leak-safe.
- **`market_features` wiring** — added `trend_regime_label` (computed over the
  SAME forward window as `regime_label`), `builder_version v6 → v7`,
  default-preserving (existing manifests select their own features/target, so
  they're unaffected). Leakage doc updated to forbid `trend_regime_label` as a
  feature.
- **`ml/configs/btc-regime-1h-trend-lgbm-v1.yaml`** (Tier-3, research_only) —
  LightGBM multiclass on `trend_regime_label`, same past features + dataset +
  split + recency weighting as the `btc-regime-1h-lgbm-v2` vol head, so the A/B
  isolates "trend label vs vol label."
- **Stage-drift reconcile (`MB-20260601-002`)** — `baseline-regime-classifier.yaml`
  `target_deployment_stage: shadow → research_only` to match the registry's
  effective stage (field-beats-comment); it collapses to majority class so it is
  NOT a viable phase-4 detector (documented in-file).
- **Tests** `tests/ml/test_trend_regime.py` (16) — efficiency-ratio (trend=1,
  chop=0, partial, None/zero-gross guards), label thresholds, and
  market_features integration (uptrend window → trending; alternating window →
  chop; schema parity). Updated the builder-version test v6 → v7.

## Validation Performed
- Tests: `tests/ml/test_trend_regime.py` + `tests/ml/datasets/test_market_features.py`
  + `tests/ml/test_regime_classifier.py` → 69 passed.
- Lint: ruff clean (labeler, market_features, manifests, test).
- Manifest: `btc-regime-1h-trend-lgbm-v1` loads (research_only, target
  `trend_regime_label`); baseline now `research_only`.
- Gaps not yet verified: the **trainer-VM purged-CV A/B** — needs a
  `market_features` v7 rebuild (to emit `trend_regime_label`) then
  `eval_split_compare` on the trend manifest, vs the ADX-14 base rates. Dispatched
  this session (trainer relay).

## A/B Result (trainer-VM #2787, 2026-06-04) — POSITIVE-but-modest, leak-free
Built `market_features` 1h on v7 (trend_regime_label confirmed on real data) and
ran `eval_split_compare` on the trend manifest. Purged WF-CV (n_eval=21,900, 5 folds):

| metric | value | note |
|---|---|---|
| macro_f1 | **0.3248** | vs ~0.185 majority-class baseline → **does NOT collapse** |
| accuracy | 0.3677 | ≈ the 0.383 always-chop rate (spreads across classes) |
| f1_chop | **0.4634** | prec 0.391 / rec 0.570 — the strongest, most-actionable regime |
| f1_trending | 0.3479 | prec 0.363 / rec 0.337 — decent |
| f1_transitional | 0.1632 | prec 0.265 / rec 0.118 — weak (the ambiguous middle class) |

Label distribution (support): chop 38.3% / transitional 27.0% / trending 34.7% —
sensibly balanced straight from the default ER thresholds (0.30 / 0.55), no tuning.
**No leakage:** purged − holdout deltas are tiny and *positive* (macro_f1 +0.013), so
the forward-ER label carries no optimism gap.

**Verdict:** S15a produced a **viable, non-degenerate, leak-free trend-axis model** —
the artifact that did not exist before (every prior regime model is vol-axis, and the
old `regime-classifier-baseline-v0` collapsed). It is *modest*, not strong: chop is
well-separated, trending is at the base rate, transitional is poorly predicted.
**Stays research_only.** Two follow-ups before it could replace the ADX detector:
(1) class-weight tuning to rescue transitional/trending (mirror the vol heads'
`class_weight`); (2) a head-to-head vs ADX-14's own forward-predictiveness (does the
ADX threshold at t predict the forward-ER regime better or worse than this model?).

## Documentation Updated
- `ROADMAP.md` S15 row; `docs/ml/optimization-roadmap.md` Session 3.3;
  `docs/claude/ml-review-backlog.json` (`MB-20260601-002` progress;
  `MB-20260529-001` resolved — S13 verified live).

## Risks and Follow-Ups
- The ER thresholds (chop≤0.30 / trending≥0.55) are first-pass defaults; the
  class distribution + class-weight tuning are a follow-up once the first cycle
  reports the chop/transitional/trending base rates.
- Live phase-4 enforcement (replacing ADX with the trend model) is Tier-3 and
  waits on (a) a shadow track record and (b) feature parity (`MB-20260604-005`).
- **S15b (vol axis)** queued: a 2-D (trend×vol) **observe-only** shadow gate —
  extends `regime_shadow_gate` logging to a second axis using the existing vol
  classifier; enforcement waits on backtest/shadow PnL.

## Next Recommended Sprint
- Read back the S15a trainer-VM A/B (does the trend model forecast the regime
  better than the ADX threshold?), then build **S15b** (the observe-only 2-D
  gate). After both have evidence, the Tier-3 phase-4 enforcement decision.

## Wrap-Up Check
- [x] Code inspected directly, not inferred from summaries.
- [x] Docs reviewed + updated.
- [x] No order-path / pipeline-stage logic changed (labeler + dataset column +
      research_only manifest + a stage-string reconcile on a research_only model).
- [x] Roadmap status checked + updated.
- [x] Contradictions recorded (the phase-4 taxonomy mis-spec → two tracks).
- [x] Remaining unknowns: the trainer-VM A/B result (dispatched); ER-threshold
      calibration; S15b PnL evidence (weeks).
