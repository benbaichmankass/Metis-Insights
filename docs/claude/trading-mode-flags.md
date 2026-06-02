# Trading-mode flags

> **Telegram command surface has changed (PR #1933, 2026-05-25).**
> The slash commands referenced in this doc (`/halt`, `/resume`,
> `/accounts dry|live`, `/set_all_live`, `/accounts_status`) **no longer
> exist** — the trader bot (`@bict_trading_bot`) is now menu-driven; see
> [`docs/TELEGRAM-SPEC.md`](../TELEGRAM-SPEC.md). The operator kill switch
> + per-account mode flip live under **🛑 Kill switch → By account** in the
> menu (which dispatches the `set-account-mode` operator action under the
> hood). The Tier-3 mutation contract (`set-account-mode` is the only
> sanctioned path) is unchanged; only the front-door command surface moved.
> Body text below that still says "Telegram `/halt`" / "Telegram `/accounts
> dry|live`" describes the historical surface, not the current one.

> **The single dry/live toggle is `mode: live | dry_run` per account in
> `config/accounts.yaml`.** Mutated via the `set-account-mode` operator
> action (PR #978, 2026-05-12) — the only sanctioned path. Applied via
> `RiskManager.dry_run` (checked inside `RiskManager.evaluate()`, reason
> `"account_mode_dry_run"`). There is no process-level interlock and no
> env-variable toggle. The ONE additional declared execution gate is the
> per-strategy `execution: live | shadow` (S9, `config/strategies.yaml`):
> `shadow` logs order packages but never sends a live order. BOTH
> demotions (`mode: dry_run`, `execution: shadow`) are CI-guarded against
> silent introduction — see "Guarded against silent demotion" below. See
> [`docs/CLAUDE-RULES-CANONICAL.md`](../CLAUDE-RULES-CANONICAL.md)
> § Prime Directive and
> [`docs/ARCHITECTURE-CANONICAL.md`](../ARCHITECTURE-CANONICAL.md)
> § Mode Mutation Contract for the binding rules.

## Prime Directive recap (2026-05-12)

The trader runs 24/7 in YAML-declared mode. The system never
switches itself off. There is exactly ONE switch (`set-account-mode`),
and the OPERATOR controls it. Transient runtime issues route through
the RiskManager as per-trade `reject(reason=…)` calls with a per-trade
Telegram, **not** as account-mode flips. Full text:
[`docs/CLAUDE-RULES-CANONICAL.md`](../CLAUDE-RULES-CANONICAL.md)
§ Prime Directive.

The 2026-05-12 silent-flip incident drove this rule: an in-process
auto-flip put `bybit_2` in dry without operator action, the operator
wasn't clearly notified, and the bot sat off-live for hours. The
sanctioned path that exists now (`set-account-mode`) is audited,
Telegram-notified, and the only allowed mutation surface. Code paths
that write to account mode outside this wire are Tier-3 violations
regardless of intent.

## Guarded against silent demotion (`dry-run-guard`)

Per the operator directive of **2026-06-02** — *Claude must never set a
thing to `dry_run` or `shadow` without explicit operator permission* —
the `dry-run-guard` CI check (`scripts/check_dry_run_in_diff.py`,
`.github/workflows/dry-run-guard.yml`) scans every PR diff and **fails**
when an added line introduces either demotion:

| Demotion | Field | File |
|---|---|---|
| Account out of live | `mode: dry_run` / `paper` | `config/accounts.yaml` |
| Strategy out of live | `execution: shadow` | `config/strategies.yaml` |

The escape hatch is an inline **allow marker** on the same line, which
records the operator's explicit approval directly in the diff for the
audit trail:

```yaml
    mode: dry_run        # dry-run-guard: allow — new IB real-money acct, held dry
    execution: shadow    # shadow-guard: allow — operator-approved real-money shadow A/B
```

Either marker name (`dry-run-guard` / `shadow-guard` / `mode-guard`)
works on either demotion. Without a marker the check fails and pings the
operator. This is the durable fix for the class of bug where a new
strategy shipped `execution: shadow` onto the IBKR **paper** account and
silently never traded (signals, no fills): a paper/demo account exists to
TEST strategies, so its strategies must execute — see the `new-strategy`
skill's 2026-06-02 amendment.

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

## Per-account override (current and queued)

**Source of truth:** `config/accounts.yaml` `mode:` per account.
`_resolve_mode(cfg, name)` in `src/units/accounts/__init__.py` reads
it on every call. Accepts case-insensitive `live` / `dry` / `dry_run`
/ `dry-run` / `paper`. Default = `live` per Prime Directive.

**Sanctioned mutation path:** `set-account-mode` operator action
(`scripts/ops/set_account_mode.sh`, allowlisted in
`.github/workflows/system-actions.yml`). Edits YAML, restarts the
trader, Telegrams the operator with the diff. Dispatch via labelled
issue (`system-action`) with body:

```
action: set-account-mode
account: <name from accounts.yaml>
mode: <live|dry_run>
reason: <one-line audit text>
```

Doc: [`docs/claude/system-actions.md`](system-actions.md) § 2.1
+ § 7.1.

**Queued for deletion in the safeguards PR (follow-on to PR #978):**

- `_DRY_RUN_OVERRIDES` dict in `src/units/accounts/__init__.py`
  (lines ~33-43). Currently used as an in-memory shim that
  `_resolve_mode()` consults before YAML. After the safeguards PR:
  deleted; `_resolve_mode()` reads YAML directly with no override
  layer.
- `set_account_dry_run()` function in `src/units/accounts/__init__.py`
  (lines ~36-38). After the safeguards PR: deleted. The only
  mutation wire is `set-account-mode`.
- Telegram `/accounts dry|live <name>` handler in
  `src/bot/telegram_query_bot.py` and `/set_all_live` companion.
  Currently call `set_account_dry_run()` directly. After the
  safeguards PR: refactored to dispatch the `set-account-mode`
  operator action so exactly one mutation path exists on disk.
- Breaker auto-flip in `src/core/coordinator.py:1048-1068`
  ("after 3 consecutive exchange rejections →
  `set_account_dry_run(account, True)`"). After the safeguards PR:
  deleted. The rejection counter remains as RiskManager input only,
  feeding per-trade `reject(reason=…)` decisions without ever
  touching account mode.

## Runtime flag files

| Path | Set by | Effect |
|---|---|---|
| `/tmp/trader_halt.flag` | Telegram `/halt` | `safe_place_order` returns `{"status": "halted"}` for every order until the file is removed (`/resume`). |

The halt flag is a **kill-switch**, not a mode flip. It pauses order
placement universally and is intentionally separate from the
per-account `mode:` field. Removing the flag via `/resume` returns
the bot to YAML-driven behaviour.

## Files that read/write trading-mode flags

| File | Lines | Reads | Notes |
|---|---|---|---|
| `src/units/accounts/__init__.py` | `_resolve_mode`, `load_accounts` | `config/accounts.yaml` `mode:` | Source of truth. After the safeguards PR, this is the only reader; the override layer is gone. |
| `src/web/runtime_status.py` | `_read_live_per_account` | accounts.yaml + (legacy) override dict | Mirrors `_resolve_mode`. After the safeguards PR follow-on, the override-dict arg becomes vestigial. |
| `src/runtime/orders.py` | `safe_place_order` | nothing mode-related directly | Routes via `RiskManager.evaluate()`; the manager's `dry_run` flag is set from the resolved mode. |
| `src/runtime/validation.py` | `validate_startup` | nothing mode-related | Boot-time. Per the Prime Directive: boot always starts the trader live (per YAML); no refuse-to-start logic. |
| `src/main.py` | `main()` | indirectly via `load_accounts()` | Reads YAML at boot, every restart. |
| `src/units/accounts/execute.py` | `_submit_order` | `RiskManager.dry_run` | Final gate before the exchange call. |
| `src/bot/telegram_query_bot.py` | `cmd_set_all_live`, `cmd_accounts` | `set_account_dry_run()` (legacy) | Scheduled for refactor to dispatch `set-account-mode` in the safeguards PR. |
| `scripts/ops/set_account_mode.sh` | wrapper body | `config/accounts.yaml` | The sanctioned mutation wire. Edits YAML in place, restarts the trader, audits the diff. |

## How to flip an account's mode

Dispatch the operator action. There is exactly one way:

**Via the Actions UI** (operator clicks):
1. Actions → system-actions → Run workflow.
2. Pick `set-account-mode`, fill `account_id`, `mode`, `reason`. Run.
3. Workflow Telegrams the result; audit artifact attached to the run.

**Via labelled issue** (autonomous dispatch after explicit operator ack):
```
Title: [system-action] set-account-mode — <reason>
Labels: system-action
Body:
  action: set-account-mode
  account: bybit_2
  mode: live
  reason: <one-line audit text>
```
The workflow comments back on the issue with the run URL + result + audit
bundle, then closes the issue.

## How to add a new mode-controlling switch

> **You don't.** The Prime Directive is unambiguous: there is exactly
> one mode switch per account, and the operator controls it via
> `set-account-mode`. New switches are Tier-3 violations regardless
> of how convenient they look.

If you believe the project genuinely needs a new toggle that affects
live-trading behaviour, the path is **not** "add a flag, add a guard,
add a default":

1. Open an issue tagging the operator. Describe what runtime
   condition the new control is meant to address.
2. If the answer is "refuse individual trades when condition X is
   true," the right place is `RiskManager.approve()` returning
   `reject(reason=“X”, trade=…)` for the specific trade in the
   specific condition. Each rejection emits its own per-trade
   Telegram. The account mode is never touched.
3. If the answer is "durably change which accounts trade," the
   right path is `set-account-mode` (existing).
4. **Do not** add a new env var, a new override dict, a new
   process-level interlock, or a new "kill-this-account-only"
   surface. Those add ways the system can be confused; the Prime
   Directive exists to prevent that confusion.

The CI guard `scripts/check_dry_run_in_diff.py` (run by
`.github/workflows/dry-run-guard.yml`) Telegrams the operator when a
PR introduces a code path that could put any account in dry without
the sanctioned mutation. The safeguards-PR follow-on tightens this
to block-on-fail.

---

## Historical context (deprecated, retained for archeology)

The content below describes the pre-BUG-039 multi-flag system and the
pre-2026-05-12 override-dict layer. It is **not** current
specification.

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

## Per-account override (legacy text — pre-2026-05-12)

`config/accounts.yaml` does **not** carry a per-account `dry_run` field.
The override is in-memory and lives in
`src.units.accounts._DRY_RUN_OVERRIDES` (set via the Telegram
`/accounts dry|live <name>` command). Use `/set_all_live` to flip every
account out of dry-run in one call.

*This text described the pre-Mode-Mutation-Contract design. After the
safeguards PR follow-on to PR #978, `_DRY_RUN_OVERRIDES` and
`set_account_dry_run()` are deleted, and the only mutation path is
`set-account-mode`. See the current section above.*
