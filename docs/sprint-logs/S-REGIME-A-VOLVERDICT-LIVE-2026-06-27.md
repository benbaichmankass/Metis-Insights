# Sprint Log: S-REGIME-A-VOLVERDICT-LIVE

## Date Range
- Start: 2026-06-27
- End: 2026-06-27

## Objective
- Primary goal: Make the ML regime heads able to influence live orders (Design A),
  landing only the safe observe/annotate/backtest layers — and gather the backtest
  evidence that gates the eventual live flip.
- Secondary goals: re-promote a vetted regime head (btc-regime-15m-lgbm-v2) to
  advisory once the live code can score it; decide B (conviction sizing apply) on
  evidence.

## Tier
- Tier 1: harness + research tooling + docs (autonomous).
- Tier 2: A's order-routing-adjacent modules (intents.py, regime_bar_scoring.py) —
  default-off flag → deploy is a no-op; operator-ack PR.
- Tier 3 (operator-approved 2026-06-27): merge of PR #4748 (lands the live hooks,
  inert); shadow→advisory promotion of v2. Each explicitly approved in chat.

## Starting Context
- Active roadmap items: M14 regime router (A); unified-confidence (B); MB-20260626-003
  (regime-head promotion gate).
- Prior sprint reference: fleet-model-scorecard (RG3/RG4) + Option-A gate
  (PR #4700) + the wiring audit that found promotion-to-advisory is currently a
  no-op for orders.
- Known risks at start: promoting a regime head to advisory does NOT influence
  orders today (advisory downsize quorum excludes regime heads; router uses ADX-14;
  c_reg stubbed; A's flag default-off) — so promotion alone could make the head go
  *dark* on live rather than gain influence.

## Repo State Checked
- Branch/commit: `claude/ml-strategies-deep-review-51n3cw` → merged to main (squash
  113b8522); branch recreated for follow-up docs.
- Deployment state: live VM pulled to 113b8522 (pull-and-deploy #4785), trader active.
- Canonical docs reviewed: CLAUDE.md (env gates, VM split), regime/policy.py,
  ml/shadow/factory.py, ml/predictors/{shadow,lightgbm}.py, regime_shadow.py.

## Files and Systems Inspected
- Code: scripts/backtest_system.py, src/runtime/regime/{policy,vol_detector,ml_vol_verdict}.py,
  src/runtime/regime_shadow.py, src/runtime/intents.py (_hard_regime_gate),
  ml/registry/model_registry.py, ml/shadow/factory.py, ml/predictors/{shadow,lightgbm}.py.
- Config: config/regime_policy.yaml (trend_vol empty).
- Services/timers: trainer mirror publisher (2-min), ict-git-sync (live auto-deploy).
- Workflows: trainer-vm-diag, system-actions (pull-and-deploy).

## Work Completed
- **Option-2 harness lever** (`--ml-stage {advisory,shadow}` + `--ml-model-id`) so the
  backtest can replay a shadow-stage head before promotion. Validating it on the
  trainer caught + fixed **4 latent harness bugs** that would have silently made the
  ML arm == the frozen arm: (1) `scripts/ml/` package shadowed the repo `ml/` under
  `PYTHONPATH=.` (sys.path fix); (2) `class_labels` read from the spec dict instead of
  the predictor; (3) `predict_proba` called on the `ShadowPredictor` wrapper (scored
  the wrapped base instead); (4) opaque error/skip reporting (now surfaces messages +
  per-window `ml_vol_skips`). Validated: `scored=1123, fell_back=0` over full history.
- **B conviction-sizing A/B**: apply path FAILS its gate (symmetric c_strat-only sizing
  = 4.5× worse maxDD) → B stays at `annotate`/off (code lands inert).
- **Merged PR #4748** (A observe + harness + calibrator + B inert + docs; all default-off)
  → deployed to live.
- **Promoted btc-regime-15m-lgbm-v2 shadow→advisory** (gate PASS: RG4 0.726, oos +0.264,
  31.8d soak, drift clean) — sequenced AFTER A reached live (no dark-window), verified.
- **Vol-gating A/B** (4 arms): ML-vol-gated net $424 / maxDD 8.07% / ret-DD 0.47 vs
  frozen-vol $59 / 10.1% / 0.05 vs ungated $353 / 8.24% / 0.39. The ML vol label
  decisively beats the frozen-edge label with the same OFF-cells.
- Candidate `trend_vol` OFF-cells + A/B plan + walk-forward driver committed.

## Validation Performed
- Tests run: `tests/test_backtest_system_evidence.py` (9 pass after each harness fix);
  CI green on PR #4748 (19 checks incl. pytest-run, env-gate-guard).
- Dry-runs/staging: every A/B + validation run on the trainer via the trainer-vm-diag
  relay (read-only research; per-arm JSON captured).
- Manual code verification: read the promote_stage transition guard, the _hard_regime_gate
  vol_gated drop, the predictor regime_spec/class_labels sources directly.
- Gaps not yet verified: walk-forward (running) + multi-symbol; live Phase-1 agreement-log
  accrual (just started).

## Documentation Updated
- Roadmap updates: (pending — see follow-up).
- Subsystem docs: `docs/research/{A-…-DESIGN, B-…-DESIGN, A-vol-gating-AB-plan,
  A-vol-gating-AB-evidence, B-conviction-sizing-backtest-evidence}-2026-06-27.md`,
  `regime_policy_trend_vol_candidate-2026-06-27.yaml`; ml-review-backlog MB-20260626-003
  (full update chain).
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- None new. (The wiring reality — promotion-to-advisory is order-inert for regime heads
  — was already documented in MB-20260626-003 and is the premise of A.)

## Risks and Follow-Ups
- Remaining technical risks: the A/B is one in-sample BTCUSDT pass; the directional
  finding (ML label > frozen label) is robust but the live-flip bar is a walk-forward.
- Remaining product decisions (Tier 3, all gated): (1) purged walk-forward + multi-symbol
  A/B; (2) author live `trend_vol` OFF-cells; (3) flip `REGIME_ML_VERDICT_MODE=use` then
  `REGIME_ROUTER_ENABLED`.
- Blockers: multi-symbol A/B needs per-symbol advisory heads (only v2/BTC at advisory).

## Deferred Items
- B graduation past annotate (needs the full multi-input conviction + c_reg live).
- Multi-symbol vol-router (per-symbol heads).

## Next Recommended Sprint
- Suggested next sprint: walk-forward + multi-symbol vol-gating A/B → if it holds,
  author live `trend_vol` OFF-cells (Tier-3) with Phase-1 agreement-log cross-check.
- Why next: it's the last evidence gate before A can influence a live order.
- Required verification before starting: confirm the live Phase-1 `regime_ml_vol_shadow`
  agreement log is accruing sanely (v2 at advisory + A deployed).

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [ ] Pipeline-stage change → TRADE-PIPELINE.md: A's hooks are observe-only/default-off;
  no live pipeline-stage behaviour changed, so no TRADE-PIPELINE.md update this sprint.
- [x] Roadmap status was checked (update pending in the docs PR).
- [x] Contradictions were recorded (none new).
- [x] Remaining unknowns were stated clearly.
