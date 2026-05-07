# Janitor 2026-05-07 — Missing-test audit (S-046 T3)

**Sprint:** S-046 (M4 step 3) | **Date:** 2026-05-07 | **Scope:** every module under `src/units/<unit>/`

## Method

For every `.py` under `src/units/<unit>/` (excluding `__init__.py`), count `from src.units.<unit>.<module> import …` and `import src.units.<unit>.<module>` lines in `tests/`. A module with **0 direct imports** is the missing-test gap; the canonical-path test stub in this sprint closes that gap. Indirect coverage via legacy shims (e.g. `src.data_layer.data_loader`) was tracked separately and is acceptable as long as a shim-resolution test exists too.

## Results

| Module | Direct imports in `tests/` | Status |
|---|---:|---|
| `src/units/accounts/account.py` | 3 | ✅ covered |
| `src/units/accounts/clients.py` | 2 | ✅ covered |
| `src/units/accounts/dup_key_check.py` | 1 | ✅ covered |
| `src/units/accounts/dxtrade_client.py` | 2 | ✅ covered |
| `src/units/accounts/execute.py` | 8 | ✅ covered |
| `src/units/accounts/integrator.py` | 2 | ✅ covered |
| `src/units/accounts/precision.py` | 1 | ✅ covered |
| `src/units/accounts/prop_risk.py` | 2 | ✅ covered |
| `src/units/accounts/prop_state_io.py` | 1 | ✅ covered |
| `src/units/accounts/risk.py` | 8 | ✅ covered |
| `src/units/dashboards/alerts.py` | 5 | ✅ covered |
| `src/units/dashboards/stats.py` | 1 | ✅ covered |
| `src/units/db/data_loader.py` | **0** → 1 | ⚠️ → ✅ stub filed (`tests/test_units_db_data_loader.py`) |
| `src/units/db/database.py` | 6 | ✅ covered |
| `src/units/strategies/_base.py` | 2 | ✅ covered |
| `src/units/strategies/smoke_test.py` | 1 | ✅ covered |
| `src/units/strategies/turtle_soup.py` | 2 | ✅ covered |
| `src/units/strategies/vwap.py` | 3 | ✅ covered |
| `src/units/trading_school/validator.py` | 1 | ✅ covered |
| `src/units/ui/data_loaders.py` | 2 | ✅ covered |
| `src/units/ui/processor.py` | 11 | ✅ covered |
| `src/units/ui/telegram_format.py` | 1 | ✅ covered |

## Gap closed

`src/units/db/data_loader.py` had no direct canonical-path tests. The behaviour was exercised through the legacy shim (`tests/test_data_loader.py` imports from `src.data_layer.data_loader`), but the audit treats absent canonical-path imports as a presence-guard gap.

**Stub filed:** `tests/test_units_db_data_loader.py` with two assertions:
1. The canonical path imports cleanly and exposes `DataLoader` + `load_data`.
2. The legacy shim and the canonical path resolve to the same module object.

The new test is a presence guard, not a coverage extension. Behaviour tests stay in `tests/test_data_loader.py`.

## Hand-off

Every `src/units/<unit>/<module>.py` now has at least one test that imports it via its canonical path. Future Janitor passes can extend the audit to:

1. **Top-level `tests/` files** that test legacy paths only (e.g. `tests/test_data_loader.py` itself uses the `src.data_layer.*` shim — works, but a future migration should rewrite to canonical paths).
2. **Coverage gaps** (vs presence gaps) — the audit only ensures *some* test imports each module. A coverage tool would flag modules with thin behavioural coverage.

Both follow-ups are out of scope for S-046's "presence-guard" Janitor pass.

## Live-mode check

✅ No live-trading code touched. T3 added one new test file under `tests/`. No source code edits.
