# Current Sprint Handoff

**Roadmap:** `docs/sprint-plans/ROADMAP-2026-05-19.md`  
**Last updated:** 2026-05-20 (Sprints 4–7 and 9 complete)

---

## STATUS: SPRINTS 4–7 AND 9 COMPLETE — MONITORING

**LAST_COMPLETED (this session):**
- Sprint 4 (S-VWAP-POLICY-LIVE-WIRE, 2026-05-20) — PR #1579 merged; policy gate live
- Sprint 6 (S-TEST-CACHE-FLAKE-FIX, 2026-05-20) — PR #1580 merged; FU-20260519-003 closed
- Sprint 5 (S-ML-REGIME-CLASSIFIER-FIX, 2026-05-20) — PR #1588 merged; FU-20260519-001 closed
- Sprint 9 (S-BACKTEST-DOC-DRIFT-FIX, 2026-05-20) — PR #1592 merged; stale comments fixed
- Sprint 7 (S-JANITOR-BRANCH-CLEANUP, 2026-05-20) — S-047-STATUS.md written; all branches accounted for

**READY_TO_CONTINUE:**
1. Monitor `/health-review` for regime skip events (weak-up/low + sideways/low suppressed by Sprint 4 gate)
2. Check FU-20260518-001 for long-side R improvement after policy gate live
3. Sprint 8 (S-OPS-COMMENT-RACE-FIX) — last remaining autonomous sprint (~1h)
4. Review PR #1026 (`no-auto-dry-flip-and-margin-cap`) — Prime Directive violation (circuit breaker) + linear perps margin fix; needs Ben's approval

---

## What was done in this session

### Sprint 4 — S-VWAP-POLICY-LIVE-WIRE (PR #1579)
- Policy gate wired into `build_vwap_signal`: weak-up/low + sideways/low → skip; strong-up/low → 2.0σ override
- 7 new `TestPolicyGate` tests; 7 pre-existing test fixes (DRY_RUN/MODE gate removed)
- 77/77 tests passing

### Sprint 6 — S-TEST-CACHE-FLAKE-FIX (PR #1580)
- `Coordinator.reload_strategy_config`: moved `_shadow_predictors_cache.clear()` before try/except
- test_reload_invalidates_cache: 3/3 passes

### Sprint 5 — S-ML-REGIME-CLASSIFIER-FIX (PR #1588)
- Root cause: per-bucket modal-class predictor cannot predict "trend" because vol_bucket doesn't separate trend from range in any bucket
- Fix step 1: collapse 3-class → 2-class (merge "trend" → "range")
- Fix step 2: recalibrate vol_threshold 0.005 → 0.003 (≈p50 of forward_vol) to prevent range-dominance
- Final metrics: f1_range=0.551, f1_volatile=0.661, macro_f1=0.606 (vs 0.0 for trend/volatile previously)
- 385/385 ML tests passing

### Sprint 9 — S-BACKTEST-DOC-DRIFT-FIX (PR #1592)
- Fixed stale comments in `vwap_backtest_sweep_action.sh` (header block) and `operator-actions.yml` (parser comment)

### Sprint 7 — S-JANITOR-BRANCH-CLEANUP
- Audited 25+ S-047/S-049 branches; all are squash-merge orphans (work completed 2026-05-07)
- Spot-margin code was deleted from `main` in PR #792 (2026-05-10) after bybit_2 migrated to linear perps
- Only active S-047 code remaining on `main`: VWAP monitor close logic (T4, PR #469)
- 0 open S-047 PRs to close
- PR #1026 (circuit breaker removal + linear perps margin fix) flagged for Ben — not superseded
- `docs/sprint-plans/S-047-STATUS.md` written

---

## Sprint 5 key findings

| vol_threshold | vol_b0→ | vol_b1→ | vol_b2→ | Notes |
|---|---|---|---|---|
| 0.005 | range | range | range | all-range degenerate (current before fix) |
| 0.003 | range | volatile | volatile | **non-degenerate, exploits autocorrelation** |
| 0.002 | volatile | volatile | volatile | all-volatile degenerate |

## Open follow-up items

| FU ID | Summary | Blocking? |
|---|---|---|
| FU-20260518-001 | VWAP performance tracking | Watch after policy gate deploys |
| FU-20260518-003 | Operator-action completion-comment race | No |
| ~~FU-20260519-001~~ | ~~regime-classifier f1_trend=0.0~~ | **CLOSED — Sprint 5** |
| FU-20260519-002 | prop_velotrade_1 at $0 balance → degenerate ML labels | No |
| ~~FU-20260519-003~~ | ~~test_reload_invalidates_cache flake~~ | **CLOSED — Sprint 6** |
| PR #1026 | Circuit breaker removal + linear perps margin fix | Needs Ben's review (Tier-3) |

## Next sprint options

| Priority | Sprint | Effort | Notes |
|---|---|---|---|
| 1 | Sprint 8: S-OPS-COMMENT-RACE-FIX | ~1h | Fix operator-action completion-comment race |
