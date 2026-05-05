# Audit log

Append-only log of recurring hardening session findings.
One entry per session; newest on top.

---

## 2026-05-04 — execute.py deep-dive + Coordinator translator audit (Session 2)

**Score**: Session 2 predetermined target per `docs/sprints/recurring-hardening-prompt.md` § 2A
**Time**: ~1h
**Phase 1**: N/A (sandbox — live VM health checks skipped; no access to `/proc`, runtime_logs, or Telegram smoke-test from agent-side).

**Findings**:

1. **Dead code: `close_all_bybit_positions` in bot + `close_all_bybit_positions_for_strategy` in data_loaders (low)**
   — Both functions call `client.place_order()` directly, bypassing `execute_pkg`. However, they are DEAD CODE
   in the production path. The `/closeall` Telegram command routes through
   `cmd_closeall` → `_do_closeall_strategy` → `processor.close_open_positions` (fixed in S-031 PR4).
   The dead functions are only exercised by tests (`test_telegram_query_bot.py:1812`,
   `test_data_loaders.py:613`). Filed as BUG-050. Cleanup sprint needed.

2. **`_fetch_balance()` returns 0.0 silently on exceptions (medium)**
   — `src/units/accounts/execute.py` line 245: any exception during balance fetch returns `0.0` with
   no structured logging of which exchange/account failed. Callers cannot distinguish "balance is $0"
   from "exchange unreachable" — both produce `below_min_balance` rejections. The operator's
   `/accounts_status` shows the error via `accounts_status` in the coordinator but the trade-journal
   rejection reason is the same. Filed for follow-up.

3. **`report_api_failure()` exception silently swallowed (low)**
   — `src/units/accounts/execute.py` line 340: bare `except` on `report_api_failure()`. If the
   diagnostic ping fails (e.g. Telegram is unreachable), the failure is silently dropped. This is
   best-effort by design — a failing diagnostic ping must never abort a real trade — but the
   bare `except` should log a debug line rather than swallow entirely.

4. **Legacy `safe_place_order` fallback path in pipeline (low / documented)**
   — `src/runtime/pipeline.py` lines 889-901: when `MULTI_ACCOUNT_DISPATCH` is off, the pipeline
   falls back to `safe_place_order → client.place_order()` without per-account risk gates or
   journal writes. This is documented in the code and is intentional for smoke/synthetic signals
   on single-account deployments. Not a bug; confirmed by comment "Use DRY_MODE_PLACEHOLDER_QTY".

**Core architecture verified (all ✅)**:

- `execute_pkg` is the **single canonical live-order entry point** for real positions. ✅
  Coordinator's `multi_account_execute` routes exclusively through `execute_pkg` (verified at
  `src/core/coordinator.py:786`). `account_execute` also routes through it (line 224).
- `close_open_position` in `execute.py` is the canonical position-flattening path (lines 554-615):
  proper error handling, structured logging, Bybit `retCode` check. ✅
- `modify_open_order` in `execute.py` handles SL/TP amendments (lines 493-551). ✅
- **Coordinator translator pattern is correct**: all cross-unit calls go through
  `src/core/coordinator.py`. No new direct unit-to-unit imports found. ✅
- `OrderPackage` dataclass defined in `coordinator.py` (lines 43-57) — single source of truth for
  the strategy-to-account interface. ✅
- Pause/resume guard (`is_paused`, `_PAUSED_ACCOUNTS`) wired at `execute_pkg` entry via
  `from src.core.coordinator import is_paused`. ✅
- Per-account `mode: live | dry_run` is the only dry/live toggle — verified in `execute_pkg`
  (reads `account_cfg.get("mode")`). No process-level interlock. ✅

**Fixes shipped this session**: none (findings are dead-code cleanup + medium severity; no
critical live-trading bugs found; clean architecture confirms S-031 fix is solid)
**Issues filed**: BUG-050 (dead close-all code) in `docs/claude/bug-log.md`
**Follow-up sprint candidates**:
  - Remove dead `close_all_bybit_positions` + `close_all_bybit_positions_for_strategy` and
    their tests (Tier-1 cleanup; file focused PR)
  - Add structured logging to `_fetch_balance()` failure path (low, Tier-1 improvement)
  - Replace bare `except` in `report_api_failure` call with `except Exception: logger.debug(...)`
**Operator pings**: 0

---

## 2026-05-04 — execute_pkg / coordinator / comms (Session 1)

**Score**: Session 1 predetermined target per `docs/sprints/recurring-hardening-prompt.md` § 2A
**Time**: ~1h
**Phase 1**: All green (sandbox — no runtime logs or hourly reports; N/A for live-process checks).
Accounts.yaml verified: `bybit_1` + `bybit_2` = `mode: live` ✅; `prop_velotrade_1` = `mode: dry_run`
(intentional — SDK not wired, empty strategies list). ALLOW_LIVE_TRADING/DRY_RUN env vars removed per
BUG-039 ✅. Working tree clean on correct branch ✅.

**Findings**:

1. **Test assertion mismatch (low / FIXED)** — `test_s028_vwap_execute_routing.py:262` asserted
   `"missing API credentials"` in the error string but BUG-034 + BUG-045 rewrites changed the
   coordinator message to `"not fully configured: api_key_env=..."`. 102 tests, 1 failed pre-fix.
   Fixed: updated assertion to check `"not fully configured"` instead.

2. **Dead code: legacy `account.place_order` / `integrator.route_order` / `BreakoutAPI` (low)**
   — `src/units/accounts/account.py` still imports `route_order` from `integrator.py`. `integrator.py`
   still has `BreakoutAPI` class. No production code calls these paths — the coordinator routes
   exclusively through `execute_pkg` post BUG-034. Only tests exercise `TradingAccount.place_order`
   (which is the legacy entry point tests verified still functions). Flagged in BUG-034 as a follow-up
   sprint candidate; not removed here per cleanup-policy (dead code = separate focused PR).

3. **`.env.example` doc drift (low / FIXED)** — `MODE=LIVE`, `DRY_RUN=false`,
   `ALLOW_LIVE_TRADING=true` still present with a comment saying "Real orders only when
   DRY_RUN=false AND ALLOW_LIVE_TRADING=true" — directly contradicted by BUG-039's
   single-source-of-truth fix. An operator copying this file could believe these flags still matter.
   Fixed: removed the three stale variables and replaced the comment block with a correct description
   of the per-account `mode:` toggle in `config/accounts.yaml`.

4. **`COMMS_PUSH_ENABLED` undocumented in `.env.example` (low / FIXED)** — `GitPusher.from_env()`
   reads `COMMS_PUSH_ENABLED` (default "0") but it was not in `.env.example`. Operators enabling the
   comms channel (S-027) had to find the env var in source. Fixed: added entry with safe default `0`
   and a comment pointing to the enable/disable semantics.

**Core architecture verified (all ✅)**:

- `execute_pkg` is the **only live order entry point** — coordinator's `multi_account_execute` routes
  via `from src.units.accounts.execute import execute_pkg` on every live tick. No legacy
  `BybitAPI.place` / `integrator.route_order` calls in production.
- Per-account `mode: live | dry_run` in `config/accounts.yaml` is the **only dry/live toggle** —
  `execute_pkg` reads `account_cfg.get("mode")` when `dry_run is None`; coordinator resolves
  `effective_dry` from `account.dry_run` (loaded from YAML) when called without explicit override.
- `CommsPoller` **correctly started** — `install_comms_handlers` is imported and called in
  `telegram_query_bot.py` line 2986; registers `CallbackQueryHandler` + `MessageHandler` and sets
  `Application.post_init` to start the poll task. `COMMS_PUSH_ENABLED=1` required on VM for
  git writeback.
- Import isolation: `execute` and `coordinator` import cleanly. `comms_handler` requires `telegram`
  (python-telegram-bot), not installed in sandbox — expected for VM-only module.

**Fixes shipped this session**: PR#TBD (test assertion + .env.example doc drift + audit-log)
**Issues filed**: BUG-047 (test assertion mismatch) in `docs/claude/bug-log.md`
**Follow-up sprint candidates**:
  - Drop legacy `account.place_order` / `integrator.py` / `BreakoutAPI` (Tier 1 cleanup sprint)
  - Session 2 target: architecture audit of `execute.py` + Coordinator translator pattern (S-008)
**Operator pings**: 0

---

## 2026-05-05 — Mode flag plumbing (Session 3)

**Score**: Session 3 predetermined target per `docs/sprints/recurring-hardening-prompt.md` § 2A
**Time**: ~1h 30m
**Phase 1**: All green (sandbox — no runtime logs; N/A for live-process checks).
  Accounts.yaml: `bybit_1` + `bybit_2` = `mode: live` ✅; `prop_velotrade_1` = `mode: dry_run`
  (intentional — SDK not wired, empty strategies list) ✅. Working tree clean on correct branch ✅.
  No `DRY_RUN` / `ALLOW_LIVE_TRADING` env-var reads in production order path ✅.

**Session 3 target**: Full trace of every place `DRY_RUN`, `ALLOW_LIVE_TRADING`, and `mode:` are
read; verify single source of truth; verify operator gets pinged if any flag is in unexpected state.

**Mode-flag plumbing trace (all correct ✅)**:
- `config/accounts.yaml` → `_resolve_mode(cfg, name)` (accounts/__init__.py):
  1. Checks `_DRY_RUN_OVERRIDES[name]` (runtime flip via Telegram `/accounts` command).
  2. Reads `cfg["mode"]` field (accepts `live`/`dry`/`dry_run`/`paper`; default=live).
- → `RiskManager(config, dry_run=dry_run)` → `self.dry_run = bool(dry_run)`.
- → `account.dry_run = dry_run` (mirrored on TradingAccount for read-only observability).
- → `Coordinator.multi_account_execute()` calls `load_accounts()` per dispatch (live override
  takes effect on next call). Reads `account.dry_run` → `effective_dry` → passes to `execute_pkg`.
- → `execute_pkg(dry_run=exec_dry_run)`: when True, skips exchange call; generates `dry-<uuid>` trade_id.
- → `RiskManager.evaluate()`: if `self.dry_run`, returns `(False, "account_mode_dry_run")` immediately.
  The coordinator receives the reason, logs a rejection row to trade_journal, emits a diagnostic ping.
- **No env-var reads of `DRY_RUN` or `ALLOW_LIVE_TRADING` anywhere in the production order path.** ✅
- **`set_account_dry_run` Telegram runtime flip**: updates `_DRY_RUN_OVERRIDES` → picked up by next
  `load_accounts()` call (which happens per-dispatch in `multi_account_execute`). ✅

**Findings**:
- BUG-051 (medium): `scripts/smoke_test_trade.py` hard-blocked on `ALLOW_LIVE_TRADING` env var at
  line 259 — live smoke silently broken on VM since BUG-039 removed this var from `.env`. Also
  injected stale `DRY_RUN=true` / `ALLOW_LIVE_TRADING=false` into settings dict in `_dispatch()`
  (no-op since `safe_place_order` ignores them). FIXED this session.
- BUG-052 (low): `scripts/startup_env_check.py` listed `MODE` in `REQUIRED_STRINGS` → exits 1 and
  sends false "Trader will NOT start" Telegram on every VM boot. Listed `DRY_RUN` + `ALLOW_LIVE_TRADING`
  in `SAFETY_FLAGS` → always reported `NOT SET`. FIXED this session.
- BUG-053 (low): `src/units/accounts/execute.py` module docstring (lines 9, 13) referenced stale
  `DRY_RUN=true` as a trigger. Actual code correct; docstring lagged. FIXED this session.
- BUG-054 (low): `scripts/print_runtime_profile.py` printed stale `DRY_RUN` and `ALLOW_LIVE_TRADING`
  fields (always None / empty since BUG-039). FIXED this session.
- BUG-055 (low): `scripts/deploy_pull_restart.sh` + `scripts/run_smoke_once.sh` comments described
  `ALLOW_LIVE_TRADING` as a safety rail. FIXED this session.

**Fixes shipped this session**: PR#TBD — BUG-051 through BUG-055 (all Tier 1, self-merge).
**Issues filed**: BUG-051 through BUG-055 in `docs/claude/bug-log.md`.
**Follow-up sprint candidates**:
  - Add a contract test pinning that `smoke_test_trade.py` can run without `ALLOW_LIVE_TRADING`
    in env (already addressed by the renamed `test_no_allow_live_env_no_longer_blocks` test).
  - Session 4+: use prioritization formula (§ 2B) to pick next subsystem.
**Operator pings**: 0
