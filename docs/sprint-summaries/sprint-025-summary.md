# Sprint 025 — UI processor migration step 1 + remaining G4 button flows

**Dates:** 2026-05-02 (continuation of the same-day cadence; PRs #276 → #279 + summary)
**Checkpoints:** CP-2026-05-02-14 → CP-2026-05-02-18
**Outcome:** ✅ all four tasks shipped. The S-024 deferred-items list is fully drained.

## PR list

| # | Goal | PR | Title | Status |
|---|---|---|---|---|
| 1 | T1 (audit doc § 5 step 1) | #276 | `refactor(bot): cmd_hourly routes through src.ui.processor (S-025 T1)` | merged |
| 2 | T2 (G4 slice 3) | #277 | `feat(telegram): /smoke_test account picker (S-025 T2 / G4 slice 3)` | merged |
| 3 | T3 (G4 slice 2) | #278 | `feat(telegram): /signals two-step stepper (S-025 T3 / G4 slice 2)` | merged |
| 4 | T4 (G4 slice 4) | #279 | `feat(telegram): /accounts mode toggle with confirm step (S-025 T4 / G4 slice 4)` | merged |

## Deliverables (file/unit → tests)

| File / unit | Tests added |
|---|---|
| `src/ui/processor.py::get_hourly_report(now_utc, tick_interval_s)` (T1) | `tests/test_ui_processor.py` × 2 (kwarg forwarding + absent-keyword default) |
| `src/bot/telegram_query_bot.py::cmd_hourly` routing (T1) | `tests/test_telegram_query_bot.py::TestCmdHourlyReplyMarkdown::test_hourly_routes_through_ui_processor` |
| `src/bot/telegram_query_bot.py::_account_picker_keyboard(include_all=…)` (T2) | covered indirectly by `TestCmdSmokeTestButtonFlow` |
| `src/bot/telegram_query_bot.py::_render_smoke_test_result + cmd_smoke_test + smoke callback` (T2) | `tests/test_telegram_query_bot.py::TestCmdSmokeTestButtonFlow` × 7 |
| `src/bot/telegram_query_bot.py::_render_signals_block + signals stepper callbacks` (T3) | `tests/test_telegram_query_bot.py::TestCmdSignalsStepper` × 7 |
| `src/bot/telegram_query_bot.py::_accounts_toggle_keyboard + _accounts_confirm_keyboard + acct_flip_* callbacks` (T4) | `tests/test_telegram_query_bot.py::TestCmdAccountsToggleConfirm` × 7 |

Net: +24 new tests this sprint.

## Highlights

* **First processor-migration PR (T1).** `cmd_hourly` is the smallest possible step from the audit doc — the processor API already existed; this PR just routes the bot through it. Pattern set for the remaining 13 steps in `docs/claude/ui-processor-audit.md` § 5.
* **/smoke_test picker (T2).** Reused `_account_picker_keyboard` from G4 slice 1 (#268) by adding `include_all=True` so the same helper now serves both `/risk_check` (per-account only) and `/smoke_test` (per-account + 🌐 All accounts). Pure renderer + async helper shared between typed-arg path and button callback.
* **/signals stepper (T3).** Two-step button flow: pick strategy → pick N. Strategy encoded in `callback_data` so no per-chat state. Buckets [10, 25, 50, 100] cover the operator's normal usage patterns; arbitrary N still available via the typed `/signals <N> [strategy]` shortcut.
* **/accounts mode toggle with confirm (T4).** Sensitive — flipping mode changes whether real orders fire on an account. Two-tap UX: pick account → "❓ Confirm flip" prompt → Confirm or Cancel. Flipping **to LIVE** triggers an explicit "REAL orders" warning in the confirmation prompt; flipping to dry doesn't (always safe). Strictly safer than the existing typed `/accounts dry|live <name>` path which still works one-shot for power users.

## Architectural patterns this sprint solidified

1. **Pure renderer + sync/async helper, shared between typed-arg and button-callback paths.** All four G4 slices (#268 risk_check, #277 smoke_test, #278 signals, #279 accounts) follow this shape. The pattern guarantees the typed power-user path and the button path produce identical text. Worth lifting into the audit doc as a Class-A migration template.
2. **Encode flow state in `callback_data`, not in module state.** `signals_n:<strategy>:<N>` and `acct_flip_do:<name>:<target>` both pack the entire state needed to apply the action into the callback string, avoiding the dict-of-pending-actions pattern that `vm_write_confirm` uses. Easier to reason about; no expiry to handle.
3. **Confirmation step for mode-changing actions.** T4 establishes the pattern for the next sprint's `set_kill_switch` migration (audit doc Class C). Heavier UX is the right default for actions whose blast radius extends past the operator's current chat session.

## Deferred items

* **UI processor migration steps 2–14.** The audit doc has 13 more PR-sized steps from `cmd_balance` (step 2 — processor API already exists) through the Class C write-path migrations. Each can land as an independent PR.
* **A small lint for `parse_mode="Markdown"` on dynamic content.** Mentioned in S-024 lessons learned. Not built this sprint; S-024's `Always do` rule in CLAUDE.md is the manual-review gate for now.
* **G4 slice 5+** — none planned. `/help`, `/last5`, `/balance`, `/trades`, `/log`, `/closeall` either don't take args (button-flow already at `/help`/`/balance`/`/trades`) or already use buttons (`/closeall`).

## Lessons learned (carried into the next sprint)

1. **`callback_data`-encoded state scales further than I expected.** A two-step flow like `/signals` would normally need a `_PENDING_SIGNALS_REQUEST: dict[chat_id, dict]` to hold the strategy choice between taps. Encoding it directly in `signals_n:<strategy>:<N>` worked fine and is simpler. Worth the new "encode flow state in callback_data" guidance for future button flows.
2. **Renderer purity tests catch real regressions.** `test_render_smoke_test_result_is_pure` (T2) and the renderer-parity tests in T3/T4 are quick to write and pinpoint regressions in renderer code that has no I/O. Worth requiring these on any new pure renderer.
3. **The audit-doc migration order is realistic.** Step 1 (`cmd_hourly`) was a literal one-line change in the bot + a small kwargs forwarder in the processor. The next steps in the doc — `cmd_balance` and `cmd_signals` (which already have processor APIs) — should be similarly small. The plan worked.

## Sprint completion checklist

- [x] PR list (`#276`, `#277`, `#278`, `#279`).
- [x] Tests added (per-deliverable table above; +24 tests).
- [x] Checkpoint IDs (`CP-2026-05-02-14` → `CP-2026-05-02-18`).
- [x] Deliverables table.
- [x] Deferred items.
- [x] Lessons learned.
- [ ] Self-merge this summary PR (docs-only, no code risk).
- [ ] Append final `CP-2026-05-02-18` checkpoint to `CHECKPOINT_LOG.md`.
- [ ] Telegram `/sprintlet_complete S-025` — fires automatically off the checkpoint commit per the existing VM wiring.

## Proposed CLAUDE.md improvement (1)

Add a "Use `callback_data`-encoded flow state for multi-step button flows; only fall back to module state when the payload would exceed Telegram's 64-byte `callback_data` limit" bullet to either CLAUDE.md or `docs/claude/ui-processor-audit.md`. Drives consistency for future button flows.
