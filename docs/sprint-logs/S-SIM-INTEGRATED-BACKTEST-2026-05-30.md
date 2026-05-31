# Sprint Log: S-SIM-INTEGRATED-BACKTEST-2026-05-30

## Date Range
- Start: 2026-05-30
- End: 2026-05-31

## Objective
- Primary goal: operator's multi-part request — (1) extend ML training data
  toward 5 years, (2) find ML models "not moving in the right direction" and
  remediate, (3) build an **integrated simulation harness** that tests
  strategies AND ML models *together* through the real decision funnel over
  history, in variations (the thing per-strategy backtests can't show).
- Secondary goals: kick a training cycle + validate; hand off the FVG /
  complementary-strategy work as a prompt for a separate session.

## Tier
- Tier 1 (new tooling, docs, tests, read-only analysis) for the sim harness +
  data-window + reviews. Tier 3 (operator-approved) for the two manifest
  changes (setup-quality demote, mes-regime-1d add).
- Justification: sim/ is read-only against history/config/registry; it informs
  Tier-3 decisions but never makes them. Manifest changes were explicitly
  operator-approved in chat.

## Starting Context
- Active roadmap items: M7/M8 strategy-improvement program; M9/M10 AI traders
  (WS7 advisory influence, WS8 monitoring).
- Prior sprint reference: /ml-review + /health-review backlogs (2026-05-29).
- Known risks at start: single-core trainer VM; relay timeouts on long jobs.

## Work Completed
1. **5-year BTC training window** (PR #2399, merged 65ad955): rolling
   `date -d '5 years ago'` MARKET_START in build_trainer_datasets.sh. Verified
   on the trainer: BTC market_raw 2021-05-30→2026-05-29, 5m=525,888 bars.
2. **ML model remediation** (PR #2399 + trainer action #2411, merged):
   - `setup-quality-lgbm-v2` demoted shadow→research_only (manifest + live
     registry) — loses to its per-group-mean baseline at n_eval~78 and the
     live trade cadence can't reach n~1000 for years.
   - New recurring `mes-regime-1d-lgbm-v2` manifest + `build_mes_1d` (deep
     daily ES=F) — replaces the orphaned mes-regime-classifier-baseline-v0;
     trained manifest_ok in the validation cycle (f1_volatile best of the MES
     regime models).
   - btc-regime-1h-lgbm-v2 improved on the 5y window: n_eval 4214→8760,
     f1_volatile 0.446.
3. **Shadow-feed "stall" investigated → benign** (/health-review): shadow
   predictions are emitted only on actionable signals; an ~8h gap was a quiet
   sideways market (vwap regime-gated), not a regression. Resolved
   BL-20260529-003; retracted the earlier incorrect "write-path stall" entries.
4. **Integrated simulation harness** `sim/` (PRs #2415, #2426, #2437 — all
   merged to main). Four phases, all REUSING live code (never reimplementing
   trading logic):
   - Phase 1 (engine/ledger/fills): replay history through the REAL
     aggregate_intents → funnel attrition (emitted→survived_mux→passed_risk
     →filled).
   - Phase 2 (models): score each decision via the LIVE advisory_downsize_factor
     + regime_shadow feature row; with-model vs without-model portfolio diff.
   - Phase 3 (attrition): per-model funnel_scored vs holdout eval_n +
     promotion-readiness verdict.
   - Phase 4 (sweep): `python -m sim sweep --spec` ranks variants; writes the
     dashboard sweep format.
   - Later: regime-aware scoring (vol_bucket via regime_shadow, not a constant)
     + a regime-GATE policy (skip trades in the wrong regime). 49 tests, ruff
     clean.
5. **Real 5yr sweeps on the trainer** (read-only, detached): per-timeframe so
   each strategy is fed its native-TF candles.
6. **FVG / complementary-strategy work** handed off as a kickoff prompt
   (separate session → PR #2410 fvg_range_15m, owned there).

## Validation Performed
- Tests: sim suite 49 passing; intent regression 42/42; ruff clean. Real-loader
  regression test added (would have caught the ShadowPredictor `stage=` bug).
- Trainer cycle: cycle_end overall_rc=0; mes-regime-1d manifest_ok;
  setup-quality demotion honoured; BTC regime on 5y window. Verified via relay.
- Sweep numbers verified against the trainer's 5y market_raw.

## Documentation Updated
- docs/sprint-plans/ROADMAP-INTEGRATED-SIM-2026-05-30.md (design + phase status).
- docs/audits/backtest-harness-validation-2026-05-30.md.
- ml-review-backlog.json: MB-20260527-003/004, MB-20260528-001,
  MB-20260529-001 updated; MB-20260530-001 (backtest-augmentation) added.
- health-review-backlog.json: BL-20260529-003 resolved; FCM-404 items
  downgraded.
- This sprint log.

## Key Findings (carry forward)
- trend_donchian (2h) is the standout: +99R / 1691 trades over 5yr (135R maxDD).
  4h fade+squeeze +4.74R.
- **No current shadow model is fit as a downsize-advisor:** trade-quality
  baselines are near-constant (bearish on 100% of decisions → blanket-halve,
  hurts); regime classifiers output P(volatile) on the OPPOSITE polarity to the
  downsize policy → must be used as a GATE. Regime-gate machinery built +
  verified.
- Regime models exist only at 5m/15m/1h; the best roster earners (2h/4h) have
  no matching-TF regime model — training one is the prerequisite to gating them.

## Contradictions or Drift Found
- The v2 LightGBM regime models freeze their live-scoring spec FLAT in
  model_state (symbol/timeframe/edges at top level), surfaced via
  `LightGBMMulticlassPredictor.regime_spec` — verified working end-to-end
  (regime_spec_of finds it; a genuinely volatile input scores 0.94). No bug;
  earlier suspicion retracted.

## Risks and Follow-Ups
- **SIM model-in-loop perf on the single-core trainer:** model scoring calls a
  LightGBM booster per emitted signal; full-5yr ML sweeps take hours. Vectorize
  the scorer / use shorter windows / add cores. (Logged to ml-review backlog.)
- **HARNESS CONSOLIDATION (the active follow-up):** `sim/` (this session) and
  `scripts/backtest_system.py` (other session) are overlapping integrated
  backtests. Being consolidated into one canonical harness in the other
  session — see the handoff prompt in the 2026-05-31 session transcript.
- Tier-3 to revisit: promote a regime model to advisory only after a regime-gate
  sweep shows net improvement on a real-trade holdout; train a 2h/4h regime
  model to gate the roster's best earners.

## Deferred Items
- Regime-gate 5m/15m sweep numbers (running detached at wrap; single-core slow).
- 2h/4h regime model training (no matching-TF regime model exists yet).

## Next Recommended Sprint
- Consolidate the two integrated-backtest harnesses (other session).
- Then: regime-gate sweep on 5m/15m + train a 2h/4h regime model so the
  donchian/fade/squeeze earners can be gated.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [ ] Pipeline stage untouched (sim/ is read-only tooling; no TRADE-PIPELINE.md change needed).
- [x] Roadmap/backlog status was checked.
- [x] Contradictions were recorded (regime_spec — retracted).
- [x] Remaining unknowns stated (perf; consolidation; gate sweep pending).
