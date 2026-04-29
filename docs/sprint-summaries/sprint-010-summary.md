# Sprint S-010 Summary — Per-Account Risk Engine + Accounts Modularisation

**Sprint date:** 2026-04-29
**Checkpoint:** CP-2026-04-29-60
**PRs:** #135 – #138

## PR List

| PR | Title | Status |
|----|-------|--------|
| #135 | S-010 PR #1: Modular account refactor — TradingAccount, RiskManager, Integrator | merged |
| #136 | S-010 PR #2: Coordinator + Risk Wiring — multi-account execution | merged |
| #137 | S-010 PR #3: Telegram Bot — /accounts_status and /risk_check commands | merged |
| #138 | S-010 PR #4: Docs + Integration Tests — accounts-risk workflow | merged |

## Deliverables

| File / Unit | Description | Tests |
|-------------|-------------|-------|
| `src/units/accounts/risk.py` | Added `RiskManager` class (stateful daily-PnL tracking, approve/record/reset/report) alongside existing `size_order` functions | `tests/test_s010_accounts.py::TestRiskManager` (9 tests) |
| `src/units/accounts/account.py` | `TradingAccount` + `RiskBreach` exception — per-account order placement with risk gate | `tests/test_s010_accounts.py::TestTradingAccount` (5 tests) |
| `src/units/accounts/integrator.py` | `EXCHANGE_MAP`, `route_order()`, `BybitAPI`, `BreakoutAPI` dry-run stubs | `tests/test_s010_accounts.py::TestIntegrator` (4 tests) |
| `src/units/accounts/__init__.py` | `load_accounts()` — reads `accounts.yaml`, returns `[TradingAccount]` | `tests/test_s010_accounts.py::TestLoadAccounts` (5 tests) |
| `config/accounts.yaml` | Per-account risk config (env var refs only, no secrets) | — |
| `src/core/coordinator.py` | `accounts_status()`, `multi_account_execute()`, `reload_accounts()` | `tests/test_coordinator_flow.py` (19 new tests) |
| `src/bot/telegram_query_bot.py` | `/accounts_status` and `/risk_check` bot commands | — |
| `docs/workflows/accounts-risk.md` | Architecture doc: schema, RiskManager logic, extension guides | — |
| `tests/test_accounts_integration.py` | 20 end-to-end integration tests | all pass |

## Tests Added

- `tests/test_s010_accounts.py` — 23 tests (RiskManager, TradingAccount, Integrator, load_accounts)
- `tests/test_coordinator_flow.py` — 19 new tests (accounts_status, multi_account_execute, reload_accounts)
- `tests/test_accounts_integration.py` — 20 integration tests (full data path)
- **Total new tests:** 62

## Architecture Decisions

- **API key security**: `api_key_env` stores env var NAME only — never the actual key
- **Backward compatibility**: `RiskManager` class added to `risk.py` without touching existing `size_order`/`size_order_from_cfg` used by `execute_pkg()`
- **Coordinator as TRANSLATOR**: all cross-unit calls go through `Coordinator`; bot commands call `coord.accounts_status()` not the accounts layer directly
- **Per-account breach isolation**: `multi_account_execute()` catches `RiskBreach` per-account so a breach on one never blocks others
- **BreakoutAPI**: dry-run stub implemented; live raises `NotImplementedError` (future work)

## Deferred Items

- BreakoutAPI live implementation (requires Breakout exchange credentials and API client)
- Async `place_order` wrapper (deferred — Coordinator is synchronous throughout)
- `test_runtime_validation.py` failures: 15 pre-existing failures on `main` before this sprint; out of scope for S-010

## Lessons Learned

1. **Squash-merge rebase pattern**: After each squash-merge, the branch diverges. Always run `git pull --rebase origin <branch>` before pushing. This pattern repeats every PR.
2. **Draft PR cannot be merged**: `mcp__github__merge_pull_request` fails on draft PRs — call `mcp__github__update_pull_request` with `draft=false` first.
3. **Config separation**: `accounts.yaml` (per-account risk) vs `units.yaml` (9-unit architecture) should stay separate — mixing them would couple risk config to architectural config unnecessarily.
