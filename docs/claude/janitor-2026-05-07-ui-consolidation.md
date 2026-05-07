# Janitor 2026-05-07 — UI consolidation (S-046 T2)

**Sprint:** S-046 (M4 step 3) | **Date:** 2026-05-07 | **Scope:** `src/ui/` → `src/units/ui/`

## Background

S-035 (architecture-audit-2026-05-02 § P2-10) moved the UI unit from `src/ui/` to `src/units/ui/` to satisfy CLAUDE.md § Architecture rules § 1 ("every unit lives under `src/units/`"). It kept a back-compat shim at `src/ui/` so existing call sites + test fixtures keep working through the move:

```
src/ui/__init__.py        ← shim
src/ui/data_loaders.py    ← `sys.modules[__name__] = _canonical` shim
src/ui/processor.py       ← same shim
```

The shim was always intended as transitional. S-046 T2 closes that out.

## Method

1. `grep -rEn --include='*.py' 'src\.ui\b'` to enumerate every reference.
2. Rewrite `from src.ui import …` → `from src.units.ui import …` across the call sites.
3. Rewrite `monkeypatch.setattr("src.ui.processor.…", …)` strings + docstring references.
4. Update / remove the back-compat invariant tests (`test_s032`, parts of `test_s035`).
5. Delete `src/ui/`.
6. Re-grep for `src.ui` to confirm zero residual references.
7. `ruff check .` clean and `pytest --collect-only` collection unaffected (the same 42 environmental errors as the pre-T1 baseline; no new errors).

## Changes

### Source

- **Deleted** `src/ui/__init__.py`, `src/ui/data_loaders.py`, `src/ui/processor.py` (3 shim files, ≤ 9 lines each).
- **`src/bot/telegram_query_bot.py`** — 3 docstring/comment references rewritten to canonical path. No code change.

### Tests

- **Rewrote `from src.ui import …`** to `from src.units.ui import …` in:
  - `tests/test_s031_pr1_status_helpers_in_ui.py`
  - `tests/test_s031_pr2_signals_block_in_ui.py`
  - `tests/test_s031_pr3_price_helper.py`
  - `tests/test_s031_pr4_closeall_helper.py`
  - `tests/test_s031_pr5_file_reads_in_ui.py`
  - `tests/test_ui_processor.py`
- **Rewrote `monkeypatch.setattr("src.ui.processor.…", …)`** strings in:
  - `tests/test_s026_g3_dynamic_sizing.py`
  - `tests/test_telegram_query_bot.py`
- **Deleted `tests/test_s032_data_loaders_move.py`** — its assertions were about the S-032 intermediate state where `src.ui.data_loaders` was canonical and `src.bot.data_loaders` was the back-compat shim. After S-035 + S-046 the canonical home is `src.units.ui.data_loaders`; the bot-shim chain is already covered by `tests/test_s035_folder_reshuffle.py::test_bot_data_loaders_shim_chain_preserved`.
- **`tests/test_s035_folder_reshuffle.py`** — removed `test_legacy_ui_path_resolves_to_canonical_module` (the shim it pinned no longer exists). Updated module docstring to record S-046's UI-shim removal. The DB-shim test (`test_legacy_data_layer_path_resolves_to_canonical_module`) is retained — `src/data_layer/` shim stays per the S-046 prompt scope ("UI consolidation only").

## Rationale for keeping the DB shim

The S-046 sprint prompt (`docs/sprints/sprint-046-prompt.md`) scopes T2 explicitly to `src/ui/` vs `src/units/ui/`. The DB unit's legacy path (`src/data_layer/` → `src/units/db/`) has wider blast radius — many runtime call sites and test fixtures use it — and is not part of this audit. A future Janitor sprint can address it as a separate workplan item.

## Live-mode check

✅ No live-trading code touched. Edits confined to `tests/`, `src/bot/telegram_query_bot.py` (docstrings only — no code path), and `src/ui/` deletion. `src/runtime/orders.py`, `src/runtime/pipeline.py`, `src/runtime/trading_mode.py`, `src/units/accounts/*`, `config/accounts.yaml`, `deploy/*` all unchanged. `scripts/check_dry_run_in_diff.py` clean.

## Hand-off

`src/ui/` is gone. `from src.units.ui import …` is now the only path. The DB shim (`src/data_layer/`) remains and is documented in the updated `test_s035_folder_reshuffle.py` docstring as deliberately retained.
