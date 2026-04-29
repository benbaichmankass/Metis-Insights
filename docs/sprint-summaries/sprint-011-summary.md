# Sprint S-011 Summary — Text Milestones: Backtesting UI + Strategy Config

**Sprint date:** 2026-04-29
**Checkpoint:** CP-2026-04-29-61
**PRs:** #141 – #145 (+ roadmap mini-PR #140)

## PR List

| PR | Title | Status |
|----|-------|--------|
| #140 | Roadmap: S-010 done, prop deferred, Phase 3.5 Text Milestones | merged |
| #141 | S-011 PR #1: /accounts dry/live toggle — per-account execution mode | merged |
| #142 | S-011 PR #2: Strategies — pure signals, no dry_run coupling | merged |
| #143 | S-011 PR #3: Backtesting UI — Streamlit dashboard + /backtest_ui | merged |
| #144 | S-011 PR #4: Strategy Config UI — Streamlit editor + /reload_strats | merged |

## Deliverables

| File / Unit | Description | Tests |
|-------------|-------------|-------|
| `src/units/accounts/__init__.py` | `set_account_dry_run()`, `get_dry_run_overrides()`, per-call override in `load_accounts()` | `TestDryRunOverrides` (4 tests) |
| `src/units/accounts/account.py` | `TradingAccount.dry_run` flag (default True); `place_order()` uses `self.dry_run`; `status()` includes flag | `TestDryRunFlag` (5 tests) |
| `src/core/coordinator.py` | `set_account_dry_run()`, `reload_strategy_config()` | `TestCoordinatorSetAccountDryRun` (4 tests), `TestCoordinatorReloadStrategyConfig` (6 tests) |
| `src/bot/telegram_query_bot.py` | `/accounts` (list + toggle), `/reload_strats`, `/backtest_ui` commands | — |
| `src/units/strategies/_base.py` | Pure-signal contract documented | `TestStrategyHasNoDryRunFlag` (9 tests) |
| `src/units/strategies/__init__.py` | `load_strategy_config()`, `save_strategy_config()` | `TestLoadStrategyConfig` (5 tests), `TestSaveStrategyConfig` (4 tests) |
| `config/strategies.yaml` | Extended with enabled, risk_pct, timeframe, symbols, strategy-specific params | — |
| `src/web/backtest_ui.py` | Streamlit backtesting dashboard (sidebar filters, equity curve, results table, mock fallback) | `TestMockBacktestDf` (5), `TestLoadBacktestData` (5), `TestFilterBacktestData` (7), `TestBuildEquityCurve` (5), `TestSummaryStats` (4) = 26 tests |
| `src/web/config_ui.py` | Streamlit strategy config editor (per-strategy expanders, validation, Save → YAML) | `TestApplyEdits` (5), `TestValidateStrategyParams` (7), `TestGetEditableFields` (2) = 14 tests |
| `docs/workflows/backtest-ui.md` | Backtest UI workflow doc | — |
| `requirements.txt` | Added `streamlit>=1.30.0` | — |

## Tests Added

| File | Tests |
|------|-------|
| `tests/test_s010_accounts.py` (extended) | 17 new (TestDryRunFlag, TestDryRunOverrides, TestCoordinatorSetAccountDryRun) |
| `tests/test_s011_strategy_purity.py` | 18 |
| `tests/test_s011_backtest_ui.py` | 26 |
| `tests/test_s011_config_ui.py` | 29 |
| **Total new tests:** | **90** |

## Architecture Decisions

- **`self.dry_run` on TradingAccount**: account-level flag (default True = safe); `place_order()` uses it when no explicit kwarg is passed; kwarg still overrides for backward compatibility in tests
- **`_DRY_RUN_OVERRIDES` module dict**: persists toggle state across `load_accounts()` calls for the process lifetime; Coordinator.set_account_dry_run() calls the package helper
- **Strategies are pure signal generators**: formally documented; no dry_run param in any strategy module or `_base` helpers; enforced by 9 structural tests
- **Streamlit helpers without Streamlit**: data loading/formatting logic extracted into importable functions so they're testable without Streamlit installed
- **strategies.yaml ordering**: must keep `breakout_confirmation → vwap → killzone → ict` order (ict last) — S-007 tests assert this; documented in YAML comment

## Deferred Items

- PR #5 was combined with this summary PR (no separate PR)
- BreakoutAPI live implementation (future sprint)
- Streamlit deployment / hosting on Oracle VM (future)

## Lessons Learned

1. **YAML key order matters for existing tests**: Changing strategy ordering in `strategies.yaml` broke 2 S-007 tests that asserted `ict` is last. Always check downstream consumers of config files before reordering.
2. **Streamlit testability**: Extracting data helpers into importable functions (separate from the `run_app()` entry point) makes UIs testable without the UI framework installed.
3. **Pre-existing failures isolation**: `test_runtime_validation.py` has 23 pre-existing failures on `main`; important to re-verify count after each PR to confirm no new regressions.
