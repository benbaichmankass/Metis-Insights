# Sprint 024 — Telegram bot debug + UI overhaul + repo cleanup

**Dates:** 2026-05-02 (single-day autonomous sprint; PRs #265 → #273)
**Checkpoints:** CP-2026-05-02-03 → CP-2026-05-02-13
**Outcome:** ✅ all six sprint goals + the architecture-audit deliverable + an out-of-band hourly-summary hotfix landed.

## PR list

| # | Goal | PR | Title | Status |
|---|---|---|---|---|
| 1 | G1 | #265 | `fix(telegram): /last5 no longer crashes on Markdown specials in DB columns` | merged |
| 2 | G2 | #266 | `feat(telegram): hamburger menu mirrors /help (BOT_COMMANDS as single source)` | merged |
| 3 | G3 | #267 | `feat(telegram): /help becomes a button-driven category menu (G3)` | merged |
| 4 | G4 (slice 1) | #268 | `feat(telegram): /risk_check is button-driven (G4 slice 1)` | merged |
| 5 | architecture audit | #269 | `docs(audit): UI processor migration plan for the Telegram bot` | merged |
| 6 | G6 | #270 | `chore(cleanup): trim signal_notifications.py to its live surface (G6)` | merged |
| 7 | G5 work-PR | #271 | `fix(pipeline+vwap): G5 — VWAP populates entry/sl/tp; failed_validation eliminated` | merged (option a per operator) |
| 8 | G5 ping-PR | #272 | `PING: G5 — operator decision needed on VWAP signal shape` | merged |
| 9 | hourly hotfix | #273 | `fix(notify): hourly summary delivery + /hourly Markdown crash (BUG-031 + BUG-032)` | merged |

## Deliverables (file/unit → tests)

| File / unit | Tests added |
|---|---|
| `src/bot/telegram_query_bot.py::_format_trade_row` (G1) | `tests/test_telegram_query_bot.py::TestCmdLast5IteratesAccounts` (`test_format_trade_row_handles_markdown_special_chars`, `test_last5_does_not_use_markdown_parse_mode`) |
| `src/bot/telegram_query_bot.py::BOT_COMMANDS` (G2) | `tests/test_telegram_query_bot.py::TestHelpCommandParity` × 5 |
| `src/bot/telegram_query_bot.py::BOT_COMMAND_SPECS / render_help_top / render_help_category` (G3) | `TestHelpCommandParity` × 8 + `TestHelpButtonCallbacks` × 4 |
| `src/bot/telegram_query_bot.py::cmd_risk_check + _account_picker_keyboard` (G4) | `tests/test_telegram_query_bot.py::TestCmdRiskCheckButtonFlow` × 6 |
| `docs/claude/ui-processor-audit.md` (audit) | doc-only |
| `src/runtime/signal_notifications.py` (G6) | trims dead surface; existing tests still pass; fixed pre-existing G3-induced regression in `tests/test_telegram_surface_cleanup.py::test_botcommand_registry_includes_vm_commands` |
| `src/runtime/pipeline.py::_signal_carries_full_sltp + Pipeline-result Telegram message` (G5) | `tests/test_orders.py` × 5 |
| `src/units/strategies/vwap.py::build_vwap_signal` (G5 option a) | `tests/test_vwap_strategy.py::TestBuildVwapSignal` × 5 (skipped in sandbox without pandas; runs on CI/VM) |
| `src/runtime/notify.py::send_via_alert_manager + send_telegram_direct` (hotfix) | `tests/test_notify_send_via_alert_manager.py` × 6 + `tests/test_telegram_query_bot.py::TestCmdHourlyReplyMarkdown` |

Bug log entries added: BUG-030 (#265), BUG-031 (#273), BUG-032 (#273).

## Highlights

* **`/last5` Markdown crash (G1).** Same shape as BUG-009 — DB columns containing `*`, `_`, `[`, backticks broke legacy Markdown. Plain-text reply.
* **Hamburger menu parity (G2).** Drift between `set_my_commands` and `/help` removed. New parity test catches "registered handler not surfaced in menu" at PR time.
* **Button-driven `/help` (G3).** First reply is six category buttons; tap edits the message in place to a drill-down with a "« Back" button. Power-user `/help <category>` typed shortcut preserved.
* **Account-picker `/risk_check` (G4 slice 1).** No-args invocation now replies with an inline-button account picker. Pure renderer is shared between typed-arg path and button path. Reusable `_account_picker_keyboard` helper for future per-account flows.
* **Architecture-audit doc.** Catalogues every command handler and proposes a 14-step migration order from the current bot-internal reads to a `src/ui/processor.py`-mediated read surface, so a webapp UI can plug in without forking logic.
* **Repo cleanup (G6).** Trimmed `src/runtime/signal_notifications.py` from 175 lines to 94 by removing 10 helpers with zero callers (matplotlib chart renderers, twice-daily summary, per-trade msg formatters). Removed the matplotlib import.
* **`failed_validation` root cause + fix (G5).** Identified VWAP's `build_vwap_signal` as the source of the per-tick `ALLOW_LIVE_TRADING=true is required` message. Operator picked option (a): VWAP now populates `entry_price` / `stop_loss` / `take_profit` (mean-reversion logic: TP=VWAP, SL=entry ± `sl_std_mult` × std_dev). Multi-account dispatch fans VWAP out and per-account dry/live state takes over. New `signal_missing_sltp` warning + report at the source for any future strategy that ships an actionable signal without sl/tp. Telegram "Pipeline result" line now includes `strategy=…`.
* **Hourly summary delivery (out-of-band).** Two bugs:
  * **BUG-032 (silent — operator never received hourlies for an entire sprint cycle):** `notify.py::_send_via_alert_manager_async` called `mgr.send(message)` on `AlertManager`, which only exposes `send_alert`. Every send raised `AttributeError`, was caught upstream, and queued silently. Replaced the AlertManager dance with a direct stdlib `send_telegram_direct(parse_mode=None)` call.
  * **BUG-031 (visible — `/hourly failed: BadRequest`):** third occurrence of "underscored identifier in Markdown reply" — the success-line contained `send_via_alert_manager` and `pending_pings.jsonl`. Drop `parse_mode="Markdown"`.

## Deferred items

* **G4 slices 2–4** — `/signals` (numeric stepper + strategy picker), `/smoke_test` (account picker incl. "all"), `/accounts dry|live` (mode toggle with confirm step). Each is a follow-up sub-PR. Slice 1 (`/risk_check`) is the pattern they reuse.
* **UI processor migration** — `docs/claude/ui-processor-audit.md` § 5 lists 14 PR-sized steps starting with `cmd_hourly` (one-line change because `processor.get_hourly_report()` already exists). None executed in this sprint per the audit prompt's "audit-only" directive.
* **Strict numeric ordering of CHECKPOINT_LOG entries** — the merge of #271 onto a main that had #273 produced an out-of-order run (CP-12 → CP-10 → CP-09 → CP-08). Functionally fine; if strict numeric ordering matters, a future cleanup PR can reorder.

## Lessons learned (next sprint)

1. **Telegram parse_mode=Markdown on dynamic content is a recurring footgun** (BUG-009 → BUG-030 → BUG-031, three occurrences). Add a small lint that grep's for `reply_text(... parse_mode="Markdown"` across `src/bot/` and surfaces it for review at PR time. `parse_mode="HTML"` with explicit `&lt;`/`&gt;` escapes (used by `/accounts_status`) is the safer alternative; plain text (no parse_mode) is the simplest.
2. **Silent-failure swallow + queue-on-error is dangerous** (BUG-032). The `try/except logger.warning + queue` pattern in `outcomes._send_telegram_or_queue` correctly handles transient failures, but it also hid a structural bug for an entire sprint cycle because the wrapper above it had a method-name typo. Lesson: when a wrapper fails, re-raise (not log-and-return) so the outer queue mechanism can do its job and the operator sees the queue grow visibly. Applied in this sprint's `send_via_alert_manager` rewrite.
3. **Ping-PR vs work-PR pattern works.** G5 demonstrated the pattern end-to-end: draft work-PR + tiny ping-PR (which self-merged) + operator reply on the work-PR + follow-up commit + merge. The Telegram alert fired on the ping-PR merge as designed, the operator clicked through, replied (a), and the follow-up landed cleanly.

## Sprint completion checklist

- [x] PR list (`#265–#273`).
- [x] Tests added (per-deliverable table above).
- [x] Checkpoint IDs (CP-2026-05-02-03 → CP-2026-05-02-13).
- [x] Deliverables table.
- [x] Deferred items (4 items above).
- [x] Lessons learned (3 bullets above).
- [ ] Self-merge this summary PR (docs-only, no code risk).
- [ ] Proposed 1–2 CLAUDE.md improvements (see "Lessons learned" #1 — "no parse_mode='Markdown' on dynamic content" is worth a CLAUDE.md "Always do" bullet).
- [ ] Append final CP-2026-05-02-13 checkpoint to `CHECKPOINT_LOG.md`.
