# Sprint summary — S-telegram-format

**Sprint date:** 2026-05-03
**Outcome:** ✅ COMPLETE / WRAPPED — every recurring Telegram message in the bot now uses the unified collapsable formatter.
**Status:** Self-contained; no open follow-ups blocking the next sprint.

## What was asked

Operator flagged four issues during a single conversation:

1. **Uniform formatting** — every Telegram message (pings + command responses) should follow one shape: a one-line summary header per section, with the long detail inside collapsable sections so the chat stays scannable but data is one tap away.
2. **Pipeline-result enrichment** — the recurring `Pipeline result: status=failed_validation … reason=ALLOW_LIVE_TRADING=true is required for live submission` ping needed to also name the firing strategy and, when an order package was generated, its details.
3. **Two parallel hourly summaries** — one focused on strategies (signals fired, errors, health), one focused on accounts/trades (placed/closed + per-account balance + open positions).
4. **Investigate the recurring failure** — figure out whether it's a hardcoded message or a real bug in the live-trading interlock.

## PR list

| PR | Surface | Tier | Sha |
|---|---|---|---|
| **#342** | `src/units/ui/telegram_format.py` (new), per-tick Pipeline result enrichment, ALLOW_LIVE_TRADING diagnostic, dual hourly summaries (strategies + accounts) | 2 (PM review) | `c648c71` |
| **#343** | Ping-PR for #342 (operator notification channel) | — | `7bde8b0` |
| **#344** | `/health` + `/accounts_status` → collapsable | 1 | `679fcef` |
| **#345** | `/signals` (grouped by status) + `/last5` (one message, expandable trades) | 1 | `41527f9` |
| **#346** | `/status` + `/balance` + `/trades` + `/log` → collapsable | 1 | `f5d3dd9` |

## Tests added

- `tests/test_telegram_format.py` — 10 tests pinning the formatter contract: HTML escape, expandable blockquote per section, priority-based ordering, message-length truncation, `kv_block` / `bullet_list` helpers, empty-body placeholder, `body_is_html` opt-in.
- `tests/test_processor_collapsable_renderers.py` — 6 tests pinning `/health` HTML mode + `render_accounts_status_collapsable`.
- `tests/test_processor_signals_trades_collapsable.py` — 8 tests pinning `/signals` grouped-by-status mode + `render_recent_trades_collapsable`.
- `tests/test_processor_per_account_collapsable.py` — 6 tests pinning the generic `render_per_account_collapsable` helper.
- `tests/test_orders.py` — 3 new tests covering the ALLOW_LIVE_TRADING diagnostic (value + source) and the per-tick Pipeline-result HTML envelope (Strategy / Order package / failure remediation sections).
- Existing test updates: `tests/test_hourly_report.py`, `tests/test_hourly_dispatch.py`, `tests/test_telegram_query_bot.py` updated to match the new shape (no behavioural regressions; only formatting expectations).

**Total: 30+ new tests pinning the contract.**

## Checkpoint IDs

- CP-2026-05-03-06 — Phase 1 close (PR #342 awaiting operator review).
- CP-2026-05-03-07 — Phase 2 close (PR #344 self-merged).
- CP-2026-05-03-08 — Phase 3 close (PR #345 self-merged).
- CP-2026-05-03-09 — Phase 4 close (PR #346 self-merged).
- CP-2026-05-03-10 — Sprint **WRAPPED** (this summary, see `docs/claude/checkpoints/CHECKPOINT_LOG.md`).

## Deliverables matrix

| Capability | Module / file | Tests | Status |
|---|---|---|---|
| Unified formatter (`Section`, `render_html`, `render_plain`, `kv_block`, `bullet_list`) | `src/units/ui/telegram_format.py` | `tests/test_telegram_format.py` | ✅ |
| Per-tick Pipeline result envelope (Strategy / Order package / Multi-account / Why-and-next-step) | `src/runtime/pipeline.py::_pipeline_result_sections` | `tests/test_orders.py` (3 new) | ✅ |
| ALLOW_LIVE_TRADING three-tier resolver + value-and-source in failure reason | `src/runtime/orders.py::safe_place_order` | `tests/test_orders.py::test_safe_place_order_allow_live_diagnostic_includes_source_and_value` | ✅ |
| Dual hourly reports (strategies + accounts) | `src/runtime/hourly_report.py::build_hourly_report` + `build_accounts_hourly_report` + `assemble_hourly_data` | `tests/test_hourly_report.py` | ✅ |
| `main.py` hourly dispatch sends both reports via HTML | `src/main.py` | `tests/test_hourly_dispatch.py` | ✅ |
| `/health` collapsable | `src/units/ui/processor.py::get_health_summary(use_html=True)` | `tests/test_processor_collapsable_renderers.py` | ✅ |
| `/accounts_status` collapsable | `src/units/ui/processor.py::render_accounts_status_collapsable` | `tests/test_processor_collapsable_renderers.py` | ✅ |
| `/signals` grouped-by-status | `src/units/ui/processor.py::get_signals_block(use_html=True)` | `tests/test_processor_signals_trades_collapsable.py` | ✅ |
| `/last5` one-message-many-sections | `src/units/ui/processor.py::render_recent_trades_collapsable` | `tests/test_processor_signals_trades_collapsable.py` | ✅ |
| Generic per-account wrapper (used by `/balance`, `/trades`, `/log`) | `src/units/ui/processor.py::render_per_account_collapsable` | `tests/test_processor_per_account_collapsable.py` | ✅ |
| `/status` collapsable (kill-switch + per-account) | `src/bot/telegram_query_bot.py::cmd_status` | `tests/test_telegram_query_bot.py` | ✅ |
| `/balance` collapsable | `src/bot/telegram_query_bot.py::cmd_balance` | (existing tests updated) | ✅ |
| `/trades` collapsable | `src/bot/telegram_query_bot.py::cmd_trades` | (existing tests updated) | ✅ |
| `/log` consolidated single-message | `src/bot/telegram_query_bot.py::cmd_log` | `tests/test_telegram_query_bot.py::test_sends_one_message_per_account` | ✅ |

## Findings

### The recurring `failed_validation` message

The user-reported message `Pipeline result: status=failed_validation | symbol=BTCUSDT | side=sell | qty=0.001 | reason=ALLOW_LIVE_TRADING=true is required for live submission` lacks `strategy=` even though G5 (CP-2026-05-02-09) added it. The deployed VM is running pre-G5 code — the in-tree pipeline already includes the strategy attribution.

The remaining defensive change shipped under #342: the failure-path *diagnostic* now names the actual value read AND its source, e.g. `…reason=ALLOW_LIVE_TRADING=true is required for live submission (read 'false' from settings; expected one of true|1|yes|on|live)`. The next time the diagnostic recurs, the operator will know in one glance whether settings, env, or default is the offender — no journalctl round-trip.

Logged as **BUG-035** in `docs/claude/bug-log.md`.

### Live-mode invariant

PR #342 touched `src/runtime/orders.py` and `src/runtime/pipeline.py` — both flagged "live-mode invariant" surfaces. Per CLAUDE.md the operator was pinged regardless of test outcome (via the separate ping-PR #343). Approved with `merge and continue`. Phases 2-4 stayed inside `src/units/ui/` + `src/bot/` and self-merged Tier-1.

## Lessons learned

1. **Self-debugging diagnostics over generic strings.** When the same alert fires every tick for hours, the message itself must include enough state for the operator to act without grepping logs. "Generic phrasing is a smell." Whenever a failure reason mentions a config key, surface the actual value-and-source. (#342, BUG-035)
2. **Phase the rollout when the formatter touches every command.** The unified formatter is one new module + an adoption sweep. Splitting into 4 PRs (one foundation + three command-batch PRs) made review manageable and tested each transition independently. The first PR was Tier-2; the rest landed Tier-1 self-merge in minutes.
3. **`<blockquote expandable>` is the right Telegram primitive for collapsable sections.** Bot API 7.0+ supports it; older clients fall back to a plain blockquote (still readable). The fallback shape is fine — no per-client branching needed.

## Proposed CLAUDE.md improvements (1-2 for next sprint)

1. **Add a "telegram messaging" section to CLAUDE.md.** The repo now has a single canonical formatter (`src/units/ui/telegram_format.py`); future bot-message work should always route through it. A two-paragraph rule under § Architecture rules § 5 (next to the existing "Bot is a thin shell" rule) would prevent any future ad-hoc Markdown formatting from re-fragmenting the surface.
2. **Codify the "self-debugging diagnostic" pattern.** Add a one-line rule under § "Always do" in CLAUDE.md: *"When a failure path emits an operator-facing alert, the alert must include the actual value(s) read AND their source. Generic phrasing without state is a smell."* Three of the recurring `parse_mode='Markdown'` BadRequests (BUG-009/030/031) and the recurring `ALLOW_LIVE_TRADING` failure (BUG-035) all share the same shape: the diagnostic was generic. The rule prevents the next instance.

## Deferred items

- **DXtrade SDK contract drop** (unchanged from CP-04/05) — the only outstanding item from the prior Velotrade phase-2 sprint. Not blocked by anything in this sprint.

## Sprint metrics

- 5 PRs (4 work + 1 ping), all merged.
- 30+ new tests; no regressions.
- Bot files touched: `src/bot/telegram_query_bot.py`, `src/main.py`.
- UI unit files touched: `src/units/ui/processor.py`, `src/units/ui/telegram_format.py` (new), `src/runtime/hourly_report.py`.
- Runtime files touched: `src/runtime/orders.py`, `src/runtime/pipeline.py` (Phase 1 only — gated through PM review per § Live-mode invariant).
