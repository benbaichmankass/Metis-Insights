# Sprint Log: S-VWAP-POLICY-LIVE-WIRE

## Date Range
- Start: 2026-05-19
- End: 2026-05-19

## Objective
- Primary: Wire `vwap_policy.policy_for_candles` into `build_vwap_signal` in `src/units/strategies/vwap.py`
  - If policy says skip → return `side="none"`
  - If policy says threshold override → use overridden σ instead of `ENTRY_STD_THRESHOLD`
  - Policy meta surfaced in signal `meta` for telemetry on every signal
- Secondary: Write tests; open Tier-3 draft PR for Ben approval before merge

## Tier
- Tier 3: touches `src/units/strategies/vwap.py` which is consumed by the live VM
- Code + tests: Tier-1 self-merge (no live code lands until Ben approves the PR)
- Draft PR opened for Ben approval before merge to `main`

## Starting Context
- Active roadmap items: Sprint 4 of ROADMAP-2026-05-19.md (S-VWAP-POLICY-LIVE-WIRE)
- Prior sprints: S-VWAP-ANCHOR-EXPERIMENT concluded (session anchor wins, no flip). Ben authorized "continue straight to sprint four."
- Policy table (`vwap_policy.py`) and regime classifier (`regime.py`) already existed and were production-ready
- `policy_for_candles(candles_df)` was the canonical entry point per the policy module docstring

## Repo State Checked
- Branch: `claude/setup-coding-session-qCToW`
- Live VWAP constants: ENTRY_STD_THRESHOLD=1.0σ, SL_STD_MULT_DEFAULT=0.3σ
- `vwap_policy.POLICY_TABLE` contents:
  - `weak-up/low` → skip (allow=False)
  - `sideways/low` → skip (allow=False)
  - `strong-up/low` → override threshold=2.0σ (allow=True)
  - all other regimes → DEFAULT_POLICY (allow=True, threshold=None → use ENTRY_STD_THRESHOLD)

## Files Changed

### `src/units/strategies/vwap.py`
1. Added top-level import: `from src.units.strategies.vwap_policy import policy_for_candles`
2. In `build_vwap_signal`: after computing `deviation`, added policy gate block:
   - Calls `policy_for_candles(candles_df)` (full lookback, not session slice — broader window gives stable regime reading)
   - Extracts `policy_regime`, `effective_threshold`
   - If `allow=False` → returns `side="none"` with `reason="regime_policy_skip: regime=<regime>"`; includes full meta (vwap, current_price, std_dev, deviation_std, policy fields)
   - If `allow=True, threshold=N` → uses N as effective threshold for deviation comparisons
   - If `allow=True, threshold=None` → uses ENTRY_STD_THRESHOLD (unchanged behavior)
3. `confidence` formula updated to use `effective_threshold` (not ENTRY_STD_THRESHOLD) — ensures confidence ≤ 1.0 relative to the active threshold
4. `base_meta` extended with `policy_regime`, `policy_allow`, `policy_threshold` on every actionable signal

### `tests/test_vwap_strategy.py` — Sprint 4 tests
Added `TestPolicyGate` class with 6 tests:
- `test_policy_skip_suppresses_buy_signal` — allow=False returns side="none" on a buy-triggering deviation
- `test_policy_skip_suppresses_sell_signal` — allow=False on a sell-triggering deviation
- `test_policy_skip_meta_includes_vwap_and_deviation` — skip meta is auditable
- `test_policy_threshold_override_raises_entry_bar` — 2.0σ override suppresses a 1.41σ signal that would fire at 1.0σ default
- `test_policy_threshold_override_allows_deep_signal` — deviation > 2.0σ still fires with 2.0σ override
- `test_unknown_regime_falls_through_to_module_constant` — small fixtures (< 10 bars) → classify_regime returns "unknown" → DEFAULT_POLICY → ENTRY_STD_THRESHOLD
- `test_policy_meta_present_on_actionable_signal` — policy fields in meta on every signal

### `tests/test_vwap_strategy.py` — Pre-existing test corrections
Fixed 7 pre-existing test failures (4 in `TestVwapPipelineRouting`, 3 in `TestLiveSafetyGate`) caused by the 2026-05-03 operator directive removing DRY_RUN and MODE as process-level gates. Tests were asserting old behavior (`status="dry_run"`, `EnvironmentError` for MODE=PAPER) that was intentionally removed. Updated all 7 to assert current reality:
- `TestVwapPipelineRouting::test_legacy_path_calls_exchange_for_validation` — legacy path (signal without sl/tp) calls exchange regardless of DRY_RUN
- `TestVwapPipelineRouting::test_legacy_path_returns_submitted_status` — status="submitted" (not "dry_run")
- `TestLiveSafetyGate::test_dry_run_flag_does_not_gate_safe_place_order` — safe_place_order is validation-only, no DRY_RUN gate
- `TestLiveSafetyGate::test_mode_and_dry_run_flags_are_ignored_by_validate_startup` — validates that 4 different MODE/DRY_RUN combinations no longer raise EnvironmentError

## Test Results
- 77/77 passing (including 7 new policy gate tests, 7 corrected pre-existing assertions)
- 0 regressions introduced

## Design Notes
- Policy classification uses `candles_df` (full lookback), not `window` (session slice) — more stable regime signal from a broader sample; aligned with how the backtest classifies windows
- `classify_regime` requires ≥10 candles; all existing test fixtures use <10 bars → return "unknown" → DEFAULT_POLICY → no behavior change for existing tests
- No changes to SL/TP/exit paths — only the entry deviation check and signal emission are gated
- Policy meta always present in signal meta for telemetry (whether skipped, overridden, or passed through)

## PR Status
- Tier-3 draft PR opened: awaiting Ben approval before merge to `main`
- Deploy path after approval: `ict-git-sync.timer` auto-deploys `main` to live VM

## Open Follow-Up Items
- FU-20260518-001: VWAP performance tracking — pending first live-data /health-review after policy gate lands
- Sprint 5 (S-REGIME-CLASSIFIER-BASELINE): fix `f1_trend=0.0` in regime classifier baseline (separate concern)
- Sprint 6 (S-FLAKE-RELOAD-CACHE): fix `test_reload_invalidates_cache` flake (separate concern)
