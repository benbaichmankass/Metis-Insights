# Sprint S-021 — BUG-048 hardening: config-drift contract + boot-time observability

**Dates:** 2026-05-04 (single session; continues from CP-2026-05-04-04 which delivered the BUG-049 code fix)
**Checkpoints:** CP-2026-05-04-04 → CP-2026-05-04-05
**Branch:** `claude/fix-trading-bot-push-o3J4w`
**PR:** [#402](https://github.com/the-lizardking/ict-trading-bot/pull/402)
**Outcome:** ✅ 2 hardening PRs shipped; 7 new tests; zero behaviour change to live trading.

## Context

BUG-048 (resolved in PRs #397–#401) had a textbook recurring shape: the monitor-loop reconciler was silently disabled for ~8 hours on the VM because `MONITOR_RECONCILE_ENABLED` existed in `.env.example` but was never emitted by `scripts/render_env_from_master.py::build_live`. This sprint closes the two gaps that allowed it to recur.

## PR list

| # | Commit | Title | Tests |
|---|---|---|---|
| PR 1 | `acf6542` | Config-drift contract: `.env.example` ↔ `build_live` parity | 3 new |
| PR 2 | `7b32d38` | Boot-time open-package observability ping | 4 new |
| Sprint summary | this commit | `docs/sprint-summaries/sprint-021-summary.md` + checkpoint | — |

## Deliverables (file/unit → tests)

| File / unit | Change | Tests added |
|---|---|---|
| `tests/test_env_render_contract.py` (new) | 3 contract tests: `.env.example`→renderer, renderer→`.env.example`, `MONITOR_RECONCILE_ENABLED=true` explicit pin | `test_env_example_keys_emitted_by_renderer`, `test_renderer_keys_present_in_env_example`, `test_monitor_reconcile_enabled_is_true_in_both` |
| `src/runtime/boot_audit.py` (new) | `report_open_packages_on_boot() → dict[str, int]` — reads linked open packages per strategy, logs summary, Telegram-pings when total > 0 | `test_no_open_packages_logs_only`, `test_open_packages_pings_telegram`, `test_open_packages_telegram_parse_mode_is_none`, `test_db_unavailable_no_raise` |
| `tests/test_boot_audit.py` (new) | 4 contract tests for boot audit | see above |
| `src/main.py` (modified) | 8-line best-effort wrapper calling `report_open_packages_on_boot()` between dup-key check and `_build_exchange_adapter` | covered by test_boot_audit.py |

## Test counts

| Test file | Before | After |
|---|---|---|
| `tests/test_render_env_from_master.py` | 53 | 53 (unchanged) |
| `tests/test_env_render_contract.py` | 0 | 3 |
| `tests/test_boot_audit.py` | 0 | 4 |
| **Total (session scope)** | **53** | **60** |

Full suite (including BUG-049 gate + reconciler tests from #401): **90/90 passed**.

## Deferred items

- **Boot-time SL/TP audit at the broker.** Would query the exchange for each open position and verify SL/TP are still set. Touches `src/units/accounts/*`; mandatorily triggers a ping-PR per CLAUDE.md § "Live-mode invariant" rule 3. Deferred until an incident shows manually-canceled SL/TP is a real failure mode.
- **Stale-package liveness watchdog.** Overlaps with the existing `liveness_watchdog` and BUG-042 reconciler. Would create false positives whenever `monitor()` legitimately returns None on a tick.

## Lessons learned

1. **Ignore lists need pre-population.** The `_IGNORE` set in `test_env_render_contract.py` required careful enumeration of all known intentional asymmetries before the tests could pass. "Start empty and add on failure" only works when the spec and the renderer are already in sync; otherwise all tests fail on the first run and the signal is lost.
2. **Branch sync matters before merging.** `claude/fix-trading-bot-push-o3J4w` was missing the BUG-049 changes from #401 (merged to main after the branch diverged). The `Database.linked_only` param wasn't available. Always `git merge origin/main` at the start of a sprint session on a long-running branch.
3. **Plain-text Telegram is the right default.** `_send_boot_ping` uses `parse_mode=None` from day one and the test pins it. Recurring failure shape (BUG-009, BUG-030, BUG-031) avoided.

## Architecture rules check

- No `src/runtime/orders.py`, `src/runtime/pipeline.py`, `src/runtime/trading_mode.py`, or `src/units/accounts/*` touched.
- `src/main.py` edit is a 5-line best-effort wrapper (read-only DB query + Telegram send) — not a routing-logic change.
- No new cross-unit imports outside `src/core/coordinator.py`. Boot audit reads `Database` and `notify` through existing public APIs.
- ✅ Live-mode invariant: no account in `config/accounts.yaml` has `mode: dry_run` added by this sprint.

## Next sprint

**Recurring Hardening Session 2** — architecture audit of `src/units/accounts/execute.py` and the Coordinator translator pattern (S-008). See `docs/sprints/recurring-hardening-prompt.md`.
