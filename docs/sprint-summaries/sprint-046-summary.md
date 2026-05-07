# Sprint S-046 — M4 step 3: Janitor audits

**Status:** CLOSED ✅ (M4 formally closed) | **Date:** 2026-05-07 | **Branch:** `claude/sprint-planning-status-ZMePk`
**Predecessors:** `CP-2026-05-07-06-s045-followup-auto-sync` | **Successor:** M5 — Strategy testing workflow
**Type:** Roadmap (M4) | **Risk tier:** Tier 1 (work-PR + sprint-close PR self-merge; T4 ping-PR work-PR DRAFT for operator)

## Goal achieved

S-046 closed M4 by running the three Janitor audits the workplan defines:

1. **Dead-file audit** — 8 stale top-level / scripts files removed.
2. **UI consolidation** — `src/ui/` shim deleted; canonical home is now `src/units/ui/` everywhere.
3. **Missing-test audit** — every `src/units/<unit>/` module has at least one canonical-path test.

T4 also rolled forward the operator-hold lint residuals carried over from S-045 as a separate ping-PR pair (work-PR DRAFT for operator review; ping-PR self-merged to fire the Telegram notification).

## PR ledger

| PR | Title | Branch | Status |
|---|---|---|---|
| #442 | S-046: M4 step 3 — Janitor audits | `claude/sprint-planning-status-ZMePk` | self-merged at T5 close |
| #443 | (PM REVIEW): S-046 — apply 15 mechanical ruff fixes | `claude/ping-s046-ruff-residuals` | DRAFT — operator must approve |
| #444 | ping(S-046): notify operator about #443 | `claude/ping-s046-ruff-residuals-ping` | self-merged after CI green |

## Deliverables

### T0 — Sprint kickoff

- `docs/sprints/sprint-046-prompt.md` (new) — 8-section prompt per `sprint-planning.md`.
- `docs/claude/checkpoints/CHECKPOINT_LOG.md` — `CP-2026-05-07-07-s046-kickoff` prepended.
- `docs/claude/milestone-state.md` — S-046 marked active under M4.
- `docs/claude/pending-pings.jsonl` — sprint-start row appended.

### T1 — Dead-file audit (8 deletions)

- `docs/claude/janitor-2026-05-07-deadfiles.md` (new) — full audit report with per-file decisions.
- **Deleted:**
  - `scripts/verify_deploy.py` — validated removed env vars (DRY_RUN/ALLOW_LIVE_TRADING/MODE) per BUG-039.
  - `test_order.py`, `test_order_safe.py`, `test_bybit_connection.py` — broken `from bybit_config import …`; sprint-012 flagged.
  - `download_bybit_history.py`, `download_data.py` — keyless data downloaders, 0 callers; Binance use violates CLAUDE.md test-data policy.
  - `run_comparison_backtest.py` — broken `from alert_manager import AlertManager`; 0 callers.
  - `config.py` — single-line orphan, 0 imports.
- **Deferred:** `visualize_swings.py`, `visualize_all.py` — referenced as developer hints in test print statements; documented for next janitor pass.

### T2 — UI consolidation

- `docs/claude/janitor-2026-05-07-ui-consolidation.md` (new) — full report.
- **Deleted** `src/ui/__init__.py`, `src/ui/data_loaders.py`, `src/ui/processor.py` (S-035 back-compat shim).
- **Rewrote** `from src.ui import …` → `from src.units.ui import …` in 6 S-031 test files + `tests/test_ui_processor.py`.
- **Rewrote** `monkeypatch.setattr("src.ui.processor.…", …)` strings in `tests/test_s026_g3_dynamic_sizing.py` and `tests/test_telegram_query_bot.py`.
- **Deleted** `tests/test_s032_data_loaders_move.py` — assertions about the S-032 intermediate state are subsumed by `test_s035_folder_reshuffle.py::test_bot_data_loaders_shim_chain_preserved`.
- **Updated** `tests/test_s035_folder_reshuffle.py` — removed `test_legacy_ui_path_resolves_to_canonical_module`; updated docstring to record S-046's UI-shim removal. DB shim retained per scope.
- **Rewrote** 3 docstring/comment refs in `src/bot/telegram_query_bot.py` (cosmetic, no code change).

Verification: `grep 'src\.ui\b'` returns 0 hits anywhere.

### T3 — Missing-test audit

- `docs/claude/janitor-2026-05-07-missing-tests.md` (new) — full report with per-module direct-import counts.
- **New stub:** `tests/test_units_db_data_loader.py` — closes the only gap (`src/units/db/data_loader.py` had 0 direct canonical-path tests; behaviour was already covered through the `src.data_layer.data_loader` shim).
- 21 of 22 `src/units/<unit>/` modules already had ≥ 1 direct test.

### T4 — Operator-hold lint residuals (ping-PR pair)

- **PR #443** (DRAFT, PM review) — work-PR on `claude/ping-s046-ruff-residuals` carrying the 15 mechanical fixes:
  - 9 × `# noqa: E402` annotations in `src/runtime/pipeline.py` (pattern matches S-045 T3f).
  - 4 × unused-import drops (`Any` in `dxtrade_client.py` and `integrator.py`; `os` in `integrator.py`; `time` in `prop_risk.py`).
  - 2 × `f`-prefix drops in `execute.py` (return strings without placeholders).
  - `ruff.toml` `[lint.per-file-ignores]` table pruned to empty.
- **PR #444** (self-merged) — ping-PR on `claude/ping-s046-ruff-residuals-ping`. Single-line append to `pending-pings.jsonl` linking to #443. Per CLAUDE.md § Telegram Reporting "Ping-PR vs work-PR separation".

### T5 — Sprint close

- This summary (`docs/sprint-summaries/sprint-046-summary.md`).
- `docs/claude/milestone-state.md` — **M4 → CLOSED** (✅ all four pieces shipped: CI suite + conftest + ruff cleanup + auto-sync + Janitor); **M5 → active**.
- `docs/claude/checkpoints/CHECKPOINT_LOG.md` — `CP-2026-05-07-NN-s046-complete` final checkpoint.
- `docs/claude/pending-pings.jsonl` — sprint-complete ping appended.

## Validation checklist

| Check | Status |
|---|---|
| All three audit reports filed under `docs/claude/janitor-2026-05-07-*.md` | ✅ |
| `src/ui/` no longer exists | ✅ |
| `grep 'from src\.ui'` returns 0 hits | ✅ |
| Every `src/units/<unit>/<module>.py` has ≥ 1 direct test | ✅ |
| `pytest --collect-only -q tests/` collection unchanged from baseline | ✅ (CI green on PR #442) |
| `ruff check .` clean | ✅ |
| `python scripts/secret_scan.py` clean | ✅ |
| `python scripts/check_dry_run_in_diff.py` clean | ✅ |
| Operator-hold ping-PR pair opened (work-PR DRAFT, ping-PR self-merged) | ✅ |
| `docs/claude/milestone-state.md` shows M4 → CLOSED, M5 → active | ✅ |
| Live-mode invariant: no edits to `src/runtime/{orders,pipeline,trading_mode}.py`, `src/units/accounts/*`, `config/accounts.yaml`, `deploy/*` in the work-PR (#442) | ✅ |
| T4 work-PR (#443) edits to operator-hold paths routed via DRAFT + ping-PR | ✅ |

## Files changed (work-PR #442)

**New (5):**
- `docs/sprints/sprint-046-prompt.md`
- `docs/claude/janitor-2026-05-07-deadfiles.md`
- `docs/claude/janitor-2026-05-07-ui-consolidation.md`
- `docs/claude/janitor-2026-05-07-missing-tests.md`
- `tests/test_units_db_data_loader.py`

**Modified (12):**
- `docs/claude/checkpoints/CHECKPOINT_LOG.md` (kickoff + final CPs)
- `docs/claude/milestone-state.md` (S-046 active → M4 CLOSED, M5 active)
- `docs/claude/pending-pings.jsonl` (sprint-start + sprint-complete)
- `src/bot/telegram_query_bot.py` (3 docstring path refs)
- `tests/test_s026_g3_dynamic_sizing.py` (monkeypatch strings)
- `tests/test_s031_pr1_status_helpers_in_ui.py` (imports)
- `tests/test_s031_pr2_signals_block_in_ui.py` (imports)
- `tests/test_s031_pr3_price_helper.py` (imports)
- `tests/test_s031_pr4_closeall_helper.py` (imports)
- `tests/test_s031_pr5_file_reads_in_ui.py` (imports)
- `tests/test_s035_folder_reshuffle.py` (drop legacy-UI test + docstring)
- `tests/test_telegram_query_bot.py` (monkeypatch + docstring)
- `tests/test_ui_processor.py` (imports)
- `docs/sprint-summaries/sprint-046-summary.md` (new — this file)

**Deleted (12):**
- `config.py`
- `download_bybit_history.py`
- `download_data.py`
- `run_comparison_backtest.py`
- `scripts/verify_deploy.py`
- `src/ui/__init__.py`
- `src/ui/data_loaders.py`
- `src/ui/processor.py`
- `test_bybit_connection.py`
- `test_order.py`
- `test_order_safe.py`
- `tests/test_s032_data_loaders_move.py`

## Hand-off

**M4 is CLOSED.** Workplan order says **M5 — Strategy testing workflow** is next:

1. Telegram-triggered `/test <strategy_name>` command writing a structured request to the repo.
2. Validation logging (signals + decisions + outcomes per workplan § Required logs).
3. Backtest workflow docs (`docs/claude/backtest-workflow.md`) per workplan § Backtesting sessions.

Open items at sprint close:
- **PR #443** (operator-hold ruff fixes) DRAFT, awaiting operator approval. The ping-PR (#444) carried the Telegram notification. If operator declines, close #443 and the existing `ruff.toml` `[lint.per-file-ignores]` retains the suppressions.
- **`visualize_swings.py` / `visualize_all.py`** — deferred from T1; either move under `tools/` or delete in a follow-up.
- **`tests/test_data_loader.py`** uses the legacy `src.data_layer.*` shim — could be migrated to canonical path in a future Janitor pass.
- S-015 operator hold (M6 blocker) — unchanged.
- BUG-057 — awaiting VM diag (unchanged).

## Lessons learned

1. **Local pytest baseline lies about regressions.** The local sandbox has 42 environmental collection errors (missing pandas/numpy/yaml). Always cross-check with CI on the open PR before claiming "I broke collection". S-046 T1 looked like a regression locally; CI confirmed it wasn't.
2. **`from X import` ≠ `from X.foo import` in greps.** First-pass dead-file audit missed `scripts/training/{data_loader,backtest_helpers}.py` and `scripts/sprint015/sample_data.py` because the grep was too narrow. Verify with multiple patterns (path-form and dotted-module-form, plus broad name match) before deleting.
3. **The `ruff.toml [lint.per-file-ignores]` block is a backlog ledger.** Each entry should name the ping-PR that will land its fix — when the ping-PR merges, the corresponding entry must be removed in the same PR. S-046 T4 followed this contract; future sessions should keep the discipline.
