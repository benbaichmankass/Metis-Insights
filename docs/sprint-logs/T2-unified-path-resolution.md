# Sprint Log: T2 — Unified Path Resolution

## Date Range
- Start: 2026-05-12
- End: 2026-05-12 (same-session ship)

## Objective
- **Primary:** Eliminate the writer/reader path-divergence patch class. Every runtime-log path in `src/` resolves through `src.utils.paths` helpers; the `_REPO_ROOT / "runtime_logs" / ...` anti-pattern is structurally blocked by a CI-gated lint test.
- **Secondary:** Future tracks (T3 structured audit, T5 secondary watchdogs) can assume reader/writer alignment without re-deriving it.

## Tier
- **Tier 2** — touches web-API router code (paths only — no behavior change), one writer (validation_logger), one runtime reader (health.py).
- Justification: No Tier-3 paths (`config/*.yaml`, `orders.py`, `risk_counters.py`) touched. Live trade flow is unaffected — paths now follow `DATA_DIR` consistently, which is the *intended* behavior of the existing systemd drop-in.

## Starting Context
- **Roadmap item:** T2 from `docs/audit/2026-05-12-end-to-end-audit.md` § 6 ("Unified Path Resolution", rank #1, S effort).
- **Patch class this kills:**
  - 2026-05-11 Signals tab blank (dashboard.py reader vs signal_audit_logger writer)
  - 2026-05-11 Bot Status stuck "stopped" (heartbeat reader vs writer)
  - 2026-05-11 Settings stale (bot_config.py reader vs runtime_status.py writer)
  - +3 more single-file patches over 2 weeks.
- **Helper already exists** at `src/utils/paths.py` (with `data_dir()`, `runtime_logs_dir()`, `runtime_state_dir()`, `artifacts_dir()`, 18 tests in `tests/test_paths_external.py`). Problem was reader-side adoption, not the helper itself.

## Repo State Checked
- Branch: `claude/fix-trade-pipeline-MG5qb` (post-T1 merge).
- Canonical docs reviewed: `CLAUDE.md`, `docs/audit/2026-05-12-end-to-end-audit.md`, `docs/architecture/oci-block-storage.md`.

## Files and Systems Inspected
- `src/utils/paths.py` — confirmed helper API + resolution order.
- `src/utils/signal_audit_logger.py:21` — writer uses `runtime_logs_dir()` (migrated 2026-05-11).
- `src/runtime/heartbeat.py:34` — writer uses `runtime_logs_dir()`.
- `src/web/runtime_status.py:26` — writer uses `runtime_logs_dir()`.
- `src/runtime/liquidity_state.py:36` — writer uses `runtime_logs_dir()`.
- `tests/test_paths_external.py` — existing helper tests (18 cases).

## Work Completed

### Reader migrations
- `src/web/api/routers/dashboard.py:32-41` — `_AUDIT_LOG`, `_HEARTBEAT` now route through `runtime_logs_dir()`. Stale "writer still hardcodes" comment removed.
- `src/web/api/routers/diag.py:43-59` — `_AUDIT_LOG`, `_HEARTBEAT`, `_STATUS_JSON` all via `runtime_logs_dir()`. Dead `_RUNTIME_LOGS` variable + stale comment removed.
- `src/web/api/routers/bot_config.py:47-50` — `_RUNTIME_STATUS_JSON` via `runtime_logs_dir()`.
- `src/web/api/routers/shadow.py` — `_DEFAULT_LOG` removed; `_log_path()` falls through `runtime_logs_dir()` when `SHADOW_PREDICTIONS_LOG` env override is absent.
- `src/web/api/routers/trade_scores.py:33` — `_SHADOW_LOG` via `runtime_logs_dir()`.
- `src/runtime/health.py:46,294-298` — `tick_check_heartbeat` resolves heartbeat + signal_audit via `runtime_logs_dir()`.

### Writer migration (in scope, found during scoping)
- `src/utils/validation_logger.py:36-47` — `_DEFAULT_BASE` removed; `_log_path()` falls through `runtime_logs_dir()` when `VALIDATION_LOG_PATH` env override is absent. M5 backtest consumer validation log now respects `DATA_DIR`.

### Tests + lint guard
- New: `tests/test_runtime_paths_alignment.py`
  - **6 alignment tests** — pin reader/writer path equality under (a) defaults, (b) `DATA_DIR=/tmp/...`, (c) `RUNTIME_LOGS_DIR` overriding `DATA_DIR`. Files covered: heartbeat, signal_audit, runtime_status, shadow_predictions.
  - **1 anti-pattern lint guard** — `tokenize`-aware scan across `src/`. Catches `_REPO_ROOT / "runtime_logs"`, `parents[N] / "runtime_logs"`, and equivalents for `runtime_state` / `artifacts`. Skips lines inside strings + comments (legitimate docstring references to incident history don't false-fire).
- All 7 tests green locally (`pytest tests/test_runtime_paths_alignment.py -v`).

### Docs
- This sprint log.
- `docs/audit/2026-05-12-end-to-end-audit.md` § 6 item 1 marked **DONE (T2)** with implementation file list.

## Validation Performed
- **Local pytest run:** 7/7 alignment + lint tests pass under `DATA_DIR=None`, `DATA_DIR=<tmp_path>`, and `RUNTIME_LOGS_DIR=<override>` configurations.
- **Behavior preservation:** every migration is path-only; the resolved default path under no env vars matches the previous hardcoded value (`<repo>/runtime_logs/...`).
- **Lint guard self-test:** introducing a hardcoded `_REPO_ROOT / "runtime_logs"` reader during development triggered the guard correctly; existing docstring references (signal_audit_logger.py:16's incident-history mention) correctly do NOT trigger.

## Documentation Updated
- New sprint log (this file).
- Audit doc § 6 item 1 marked DONE.
- No CLAUDE.md / canonical doc changes (the helper's API + env var contract was already documented in `src/utils/paths.py` module docstring).

## Tier-3 paths NOT touched
- `config/strategies.yaml`, `config/accounts.yaml`, `config/risk_caps.yaml` — unchanged.
- `src/runtime/orders.py`, `src/runtime/risk_counters.py` — unchanged.
- All `Tier-3` paths under the CLAUDE rules — unchanged.

## Known follow-ups (queued, NOT in this PR)
- **TRADE_JOURNAL_DB migration** — 18+ readers directly call `os.environ.get("TRADE_JOURNAL_DB") or "trade_journal.db"`. Same divergence risk if a writer ever respects a different default. Tracked separately because the env-var-driven pattern is distinct from the `_REPO_ROOT / ...` pattern T2 closes.
- **BACKTEST_DATA_PATH** — similar env-var pattern, only one reader (`src/backtest/run_backtest.py:21`). Lower risk.
- **bot.log path** — still at `_REPO_ROOT / "bot.log"` (legacy log file). Likely fine where it is, but worth a thought when the trader is moved off the repo working tree.

## Why this kills the recurring patch class

Six one-off path patches in two weeks (Signals blank → heartbeat-not-seen → Settings stale → +3 others) all shared one root cause: a reader hardcoded the repo path while the matching writer respected DATA_DIR. Each fix was identical in shape — add `runtime_logs_dir()` to one file. T2 does it for the remaining 7 reader sites in a single PR AND adds the lint guard that prevents a future reader from regressing. The next time someone introduces a hardcoded reader, the `pytest-collect` CI job catches it before merge.
