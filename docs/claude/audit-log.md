# Audit log

Append-only log of recurring hardening session findings.
One entry per session; newest on top.

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
