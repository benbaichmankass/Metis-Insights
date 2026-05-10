# Trading-mode flags

> **The single dry/live toggle is `mode: live | dry_run` per account in
> `config/accounts.yaml`** — applied via `RiskManager.dry_run`
> (checked inside `RiskManager.evaluate()`, reason
> `"account_mode_dry_run"`). There is no process-level interlock,
> no strategy-level toggle, and no env-variable toggle. See
> CLAUDE.md § "Autonomous live-trading rule" and BUG-039 in
> `docs/claude/bug-log.md` for the full rationale.

## Removed env vars (BUG-039 + follow-ups)

These were the legacy switches; **none remain in the codebase**:

| Name | Removed by | Why |
|---|---|---|
| `DRY_RUN` | BUG-039 (2026-05-03) | Replaced by per-account `mode`. |
| `ALLOW_LIVE_TRADING` | BUG-039 (2026-05-03) | Same. |
| `MODE` (`LIVE` / `BACKTEST`) | BUG-039 (2026-05-03) | Replaced by per-account `mode`. |
| `MONITOR_APPLY_TO_EXCHANGE` | PR #630 (2026-05-09) | Could silently downgrade live → dry. |

## Surviving env vars matching the suspect patterns

S-067 follow-up #4 (`docs/audits/env-gate-purge-2026-05-10.md`)
audited every `MULTI_ACCOUNT_*`, `MONITOR_*`, `DISPATCH_*`,
`*_APPLY_TO_*`, `*_DRY_*`, `*_ENABLED` site under `src/`. Only
two survive, both with explicit operational purposes that **cannot
suppress live exchange writes**:

| Env var | File:line | Default | Purpose |
|---|---|---|---|
| `MULTI_ACCOUNT_DISPATCH` | `src/runtime/pipeline.py:194` | `true` | Operator escape hatch — pin to legacy single-client path for single-account smoke deployments that don't load Coordinator. **Both branches route through `RiskManager.evaluate`**, so flipping this does not bypass the live/dry contract. |
| `MONITOR_RECONCILE_ENABLED` | `src/runtime/order_monitor.py:680` | `false` | SSOT-from-Bybit reconciler gate (issue #502). Default off — explicit operator opt-in for the post-S-055 reconciler. **Reads only**, no order placement. |

Both are documented in the audit. The phase-2 follow-up PR (Tier 2,
operator-ack required) will add inline `# allow-silent: …`
annotations + per-survivor regression tests asserting the
"can't suppress live writes" contract.

## How to add a new mode-controlling switch

> **Default answer: don't.** Reach for the per-account
> `RiskManager.dry_run` first.

If a new env-var gate is genuinely required:

1. Document it in this file under § Surviving env vars with a
   plain-English statement of why it cannot suppress live writes.
2. Add an inline `# allow-silent: <reason>` comment on the
   `os.environ.get("…")` line so the
   `.github/workflows/env-gate-guard.yml` CI check accepts the
   new gate. Without the comment, the guard fails the PR.
3. Add a regression test asserting the gate does not bypass
   `RiskManager.evaluate`.
4. Tier 2 PR — requires operator ack pre-merge.

The CI guard's source: `scripts/check_env_gate_in_diff.py`.

---

## Historical context (deprecated, retained for archeology)

The content below describes the pre-BUG-039 multi-flag system. It
is **not** current specification. See § Removed env vars above for
the current state.

---

Authoritative reference for every flag that controls whether a service
in this repo trades **live** or **dry-run / paper / simulated**.
~~Maintained in lock-step with `src/runtime/trading_mode.py` (the single
source of truth for the truthy parsers + defaults).~~ File deleted —
see deprecation notice above.

## Default

> The system **defaults to live**. Per CLAUDE.md "Autonomous live-trading
> rule" the safety rails are the `RiskManager` (per-account caps), the
> `safe_place_order` chokepoint, and the `/halt` kill-switch — not an
> opt-in env var.

A service that is **silently** put into dry-run is a regression. The
`scripts/check_dry_run_in_diff.py` guard (run by `.github/workflows/
dry-run-guard.yml` on every PR) pings the operator on Telegram when a
PR introduces such a flip.

## Process-level env vars

| Name | Default | Truthy parser | Effect when truthy |
|---|---|---|---|
| `ALLOW_LIVE_TRADING` | `true` | `is_live_truthy` accepts `true`/`1`/`yes`/`on`/`live` (case-insensitive) | live order submission is allowed at the `safe_place_order` chokepoint |
| `DRY_RUN` | `false` | `is_dry_truthy` accepts `true`/`1`/`yes`/`on`/`dry`/`dry_run`/`dry-run`/`paper` (case-insensitive) | every order short-circuits to a dry-run result before exchange contact |
| `MODE` | `LIVE` (when used) | exact match `LIVE` or `BACKTEST` (case-insensitive) | switches the strategy runtime between live ticks and the backtester |

### Refused combinations

`validate_startup` (`src/runtime/validation.py`) refuses to start in only
two configurations:

1. `DRY_RUN` truthy **and** `ALLOW_LIVE_TRADING` truthy — contradictory.
2. `MODE=LIVE` **and** `DRY_RUN` truthy **and** `ALLOW_LIVE_TRADING` not
   truthy — contradictory.

Everything else passes. In particular, **all flags unset** is a valid
live config.

## Per-account override

`config/accounts.yaml` does **not** carry a per-account `dry_run` field.
The override is in-memory and lives in
`src.units.accounts._DRY_RUN_OVERRIDES` (set via the Telegram
`/accounts dry|live <name>` command). Use `/set_all_live` to flip every
account out of dry-run in one call.

## Runtime flag files

| Path | Set by | Effect |
|---|---|---|
| `/tmp/trader_halt.flag` | Telegram `/halt` | `safe_place_order` returns `{"status": "halted"}` for every order until the file is removed (`/resume`). |

## Files that read/write trading-mode flags

| File | Lines | Reads | Notes |
|---|---|---|---|
| `src/runtime/trading_mode.py` | entire file | `ALLOW_LIVE_TRADING`, `DRY_RUN` | Single source of truth. |
| `src/runtime/orders.py` | `safe_place_order` (≈170-180) | `DRY_RUN`, `ALLOW_LIVE_TRADING` | Routes via `trading_mode.is_live_truthy` / `is_dry_truthy`. |
| `src/runtime/validation.py` | `validate_startup` (≈130-150) + `build_settings_from_env` (≈170-200) | `DRY_RUN`, `ALLOW_LIVE_TRADING`, `MODE` | Routes via `trading_mode`. |
| `src/main.py` | `main()` | reads via `build_settings_from_env` | Indirect. |
| `src/runtime/pipeline.py` | `build_exchange_client` | reads `MODE` indirectly | Indirect. |
| `src/bot/telegram_query_bot.py` | `cmd_set_all_live`, `cmd_accounts` (`/accounts dry|live`) | per-account `dry_run` toggle | Operator UI. |
| `src/units/accounts/__init__.py` | `set_account_dry_run`, `_DRY_RUN_OVERRIDES` | per-account `dry_run` toggle | Storage. |

## How to add a new mode-controlling switch

1. Stop. Reach for the existing flags first. New switches multiply
   ways the system can be confused.
2. If genuinely required, add the flag to `src/runtime/trading_mode.py`,
   default-live, and route every consumer through the helper. Do **not**
   add a fresh `os.environ.get("...").lower() == "true"` site.
3. Update **this file** with the new flag.
4. Add a regex to `scripts/check_dry_run_in_diff.py` so future PRs that
   would set the flag to a non-live value ping the operator.
