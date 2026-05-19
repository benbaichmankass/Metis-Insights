# Sprint Log: S-VWAP-PARAM-SWEEP

## Date Range
- Start: 2026-05-19
- End: 2026-05-19

## Objective
- Primary goal: Add ENTRY × SL parameter sweep capability to `src/backtest/run_backtest_vwap.py` with long/short trade split in all output
- Secondary goals: Add `--entry-threshold` and `--sl-mult` standalone CLI overrides; surface `mean_total_r_long` / `mean_total_r_short` in per-window aggregates and regime tables

## Tier
- Tier 1 (self-merge)
- Justification: Backtest-only changes. No live constants touched (`ENTRY_STD_THRESHOLD`, `SL_STD_MULT_DEFAULT` in `vwap.py` unchanged). No `config/strategies.yaml`, no runtime, no risk code.

## Starting Context
- Active roadmap items: Sprint 1 of ROADMAP-2026-05-19.md (S-VWAP-PARAM-SWEEP)
- Prior sprint reference: `S-VWAP-POLICY-INVESTIGATION-2026-05-19.md` — concluded policy tuning is flat, pivot to strategy-parameter sweep
- Known risks at start: `ENTRY_STD_THRESHOLD` is referenced directly in `vwap.py` function body (not just as default) → requires module monkey-patch, not just parameter passing. `sl_std_mult` is a named parameter of `build_vwap_signal` → can be passed directly.

## Repo State Checked
- Branch: `claude/setup-coding-session-qCToW`
- Deployment state: live VWAP constants unchanged (ENTRY_STD_THRESHOLD=1.0σ, SL_STD_MULT_DEFAULT=0.5σ)
- Canonical docs reviewed: CLAUDE.md, CURRENT-SPRINT.md, ROADMAP-2026-05-19.md

## Files and Systems Inspected
- Code files inspected: `src/backtest/run_backtest_vwap.py` (1041 lines), `src/units/strategies/vwap.py` (47KB), `src/units/strategies/vwap_policy.py`
- Config files inspected: `config/strategies.yaml` (not modified)
- Docs inspected: `docs/sprint-plans/ROADMAP-2026-05-19.md`, `docs/sprint-logs/S-VWAP-POLICY-INVESTIGATION-2026-05-19.md`

## Work Completed

### Module constants (committed in prior session)
- Added `PARAM_SWEEP_ENTRY: list[float] = [0.8, 1.0, 1.2, 1.5]` after `THRESHOLD_SWEEP`
- Added `PARAM_SWEEP_SL: list[float] = [0.3, 0.5, 0.7]`

### `run_single` — long/short split + `sl_std_mult` parameter
- Added `sl_std_mult: float | None = None` parameter
- Pass `sl_std_mult` to `build_vwap_signal` via `**({"sl_std_mult": sl_std_mult} if sl_std_mult is not None else {})`
- Split trades into `long_trades` / `short_trades` after simulation loop
- Added `total_r_long`, `total_r_short`, `wins_long`, `wins_short` to aggregate
- Added `trades_long`, `trades_short`, `wins_long`, `wins_short`, `total_r_long`, `total_r_short` to return dict

### `run_windows` — threading + aggregate long/short
- Added `sl_std_mult: float | None = None` parameter
- Threaded `sl_std_mult` through both `run_single` call paths (main + adaptive policy-override re-run)
- Updated adaptive SKIP zero-result dict to include all new long/short fields (zeroed)
- Added `total_r_long_vals` / `total_r_short_vals` aggregation lists
- Added `mean_total_r_long` / `mean_total_r_short` to return dict
- Added `mean_total_r_long` / `mean_total_r_short` to each entry in `per_regime_stats`

### CLI — new flags
- `--param-sweep`: sweeps `PARAM_SWEEP_ENTRY × PARAM_SWEEP_SL` (12 combinations), no HTF gate; mutually exclusive with `--compare`, `--threshold-sweep`, `--adaptive`
- `--entry-threshold SIGMA`: standalone single-run override for `ENTRY_STD_THRESHOLD` (monkey-patched, restored in finally)
- `--sl-mult SIGMA`: standalone single-run override for `sl_std_mult`
- Added `args.param_sweep` to mutual-exclusion check

### `--param-sweep` handler in `main`
- Monkey-patches `ENTRY_STD_THRESHOLD` per ENTRY row; passes `sl_std_mult` directly (no monkey-patch)
- Supports both windowed (`--windows N`) and full-range modes
- Output key: `param_sweep_window` (windowed) or `param_sweep` (full range)
- Tags each result with `entry_std_threshold` and `sl_std_mult` fields

### Default `else` branch in `main`
- Wraps `run_single` / `run_windows` call in try/finally when `--entry-threshold` is provided
- Passes `args.sl_mult` to both call paths

### `_print_regime_coverage`
- Added `param_sweep_window` key to config-list discovery
- Added `L:{mean_total_r_long:+.2f} S:{mean_total_r_short:+.2f}` column to per-config table when fields present

## Validation Performed
- Tests run: `pytest tests/sprint015/test_run_backtest.py tests/test_vwap_timeframe_5m.py tests/units/strategies/` — 87 passed, 0 failed
- Import check: `python -c "import src.backtest.run_backtest_vwap"` — OK
- CLI help: `python -m src.backtest.run_backtest_vwap --help` — all three new flags visible
- Pre-existing failure confirmed pre-existing: `test_vwap_dry_run_does_not_call_exchange_place_order` fails on both stash-before and stash-after — not introduced by this sprint

## Documentation Updated
- Sprint log: this file
- `docs/sprint-plans/CURRENT-SPRINT.md`: to be updated to point to Sprint 2 (S-VWAP-SWEEP-DISPATCH)

## Contradictions or Drift Found
- None introduced. No vwap.py live constants changed.

## Risks and Follow-Ups
- Remaining product decisions: the winning (ENTRY, SL) pair from the sweep is a Tier-3 decision requiring operator approval before touching `vwap.py` constants
- FU-20260518-001 (VWAP performance tracking): Sprint 1 results (sweep output JSON) should be added once the operator dispatches the sweep via `vwap-backtest-sweep` operator action
- `bt_mode: compare` dispatch key for `vwap-backtest-sweep` — confirmed in `operator-actions.yml:231`

## Deferred Items
- Actual 12-combination dispatch: operator-action (S-VWAP-SWEEP-DISPATCH, Sprint 2) — Claude fires `vwap-backtest-sweep` with `bt_mode: compare` and `--param-sweep --windows 24 --window-days 14` after this PR merges
- Interpreting sweep results and proposing a (ENTRY, SL) change to `vwap.py`: Tier-3, requires Ben approval

## Next Recommended Sprint
- Sprint 2 (S-VWAP-SWEEP-DISPATCH): dispatch the 12-combination sweep via operator action, collect JSON output, surface the winning (ENTRY, SL) pair with confidence analysis
