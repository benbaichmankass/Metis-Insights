# Janitor 2026-05-07 — Dead-file audit (S-046 T1)

**Sprint:** S-046 (M4 step 3) | **Date:** 2026-05-07 | **Scope:** `scripts/`, top-level `*.py`

## Method

For every `.py` under `scripts/` and at the repo root, count inbound references in:

- `*.py` (imports + path strings)
- `*.sh`, `Makefile`
- `.github/workflows/*.yml`
- `deploy/*.service`, `deploy/*.timer`
- `notebooks/*.ipynb`
- `docs/**/*.md`

Then read each candidate to confirm its imports still resolve and it isn't load-bearing infrastructure (e.g. a CI workflow, systemd unit, or an entry-point referenced only by string).

The `repo-inventory.yml` artifact (S-044) only flags `*.bak`/`*.tmp`/`*.save`/`*~` suffixes and large files; for orphan-module detection a manual reference scan is required. This audit is the first such scan; future PRs can rely on the artifact diff once we have a longer baseline.

## Results

### Deleted this sprint

| File | Last commit | Why dead |
|---|---|---|
| `scripts/verify_deploy.py` | `4e24042` | Validates `MODE`, `DRY_RUN`, `ALLOW_LIVE_TRADING` env vars — all **removed** per BUG-039 (2026-05-03 autonomous-live-trading rule). 0 inbound refs in `deploy/`, `.github/`, `docs/claude/`. Would fail on first call. |
| `test_order.py` (top-level) | — | `from bybit_config import …` — `bybit_config` is not a top-level module (only `config/bybit_config_template.py` exists). Sprint-012 prompt explicitly flagged for cleanup. 0 active callers. |
| `test_order_safe.py` (top-level) | — | Same broken `bybit_config` import. Sprint-012 flagged. |
| `test_bybit_connection.py` (top-level) | — | Same broken `bybit_config` import. Sprint-012 flagged. |
| `download_bybit_history.py` (top-level) | — | Hardcoded BTCUSDT/`linear` 1m downloader. 0 callers in `src/`, `tests/`, `scripts/`, `notebooks/`, `deploy/`, `.github/`. Only doc reference is `docs/claude/cleanup-report.md` listing it as a previous candidate. |
| `download_data.py` (top-level) | — | Binance downloader. CLAUDE.md § "Always do" rule: *"For tests and notebooks, never pull market data from Binance or other key-gated exchanges."* 0 active callers. |
| `run_comparison_backtest.py` (top-level) | — | `from alert_manager import AlertManager` — `alert_manager` is at `src.bot.alert_manager`, not the top level. Import is broken. 0 active callers. |
| `config.py` (top-level) | — | Single-line file: `STRATEGY_CLASS = 'TurtleSoupMTFv1'`. 0 `import config` / `from config` sites anywhere. False-positive grep refs are README/ROADMAP namespace mentions. |

### Deferred (keep this sprint, re-audit next pass)

| File | Why deferred |
|---|---|
| `visualize_swings.py` (top-level) | Imports resolve (`src.ict_detection.swing_points` exists). Referenced in `tests/test_swing_detection.py:76` as a developer-hint `print(...)` statement. Low harm to keep; could be moved under `tools/` in a follow-up janitor pass. |
| `visualize_all.py` (top-level) | Same shape: imports resolve, referenced in `tests/test_fvg_ob.py:125` as a `print(...)` hint. Defer with `visualize_swings.py`. |

### Kept (not dead)

`scripts/` modules with ≥ 2 active references and resolving imports were left alone. The lowest-ref entries (`scripts/training/data_loader.py`, `scripts/sprint015/sample_data.py`, `scripts/training/backtest_helpers.py`) are imported from `tests/sprint015/` and `experiments/2026-05-01-strategy-tuning-dryrun/` — confirmed live.

## Diff summary

- **8 files deleted.** All top-level dead scripts or stale validators.
- **0 files moved** (T2 covers the `src/ui/` → `src/units/ui/` consolidation).
- **0 source files modified.** Pure deletions.

## Live-mode check

✅ No live-trading code touched. `verify_deploy.py` *imported* `src/runtime/pipeline.py` but the pipeline file itself is unchanged — only the stale caller is removed. `scripts/check_dry_run_in_diff.py` clean.

## Hand-off

The `visualize_*.py` defer is the only follow-up. Either move them under `tools/` (clearer namespace) or delete with a brief git-history note. Keep this report as the diff baseline for the next janitor pass.
