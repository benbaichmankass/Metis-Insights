# Sprint 026 — Decouple position sizing from strategies + audit-log "unknown" attribution

**Dates:** 2026-05-02 (single-session sprint; PRs #281 → #287 + this summary)
**Checkpoints:** CP-2026-05-02-19 → CP-2026-05-02-22
**Outcome:** ✅ all four goals shipped + BUG-033 logged + diagnostic instrumentation in place. Operator overrode the "one-task-per-session" rule and ran G1 → G4 serially in one conversation.

## PR list

| # | Goal | PR | Title | Status |
|---|---|---|---|---|
| 1 | G1 — strategy signals lose `qty` | #281 | `feat(strategy): decouple qty from strategy signals (S-026 G1)` | merged |
| 2 | G1 ping | #282 | `ping: S-026 G1 — PM review needed on PR #281` | merged |
| 3 | G2 — sizing in `RiskManager.position_size` | #283 | `feat(risk): move position sizing into per-account RiskManager (S-026 G2)` | merged |
| 4 | G2 ping | #284 | `ping: S-026 G2 — PM review needed on PR #283` | merged |
| 5 | G3 — dynamic sizing | #285 | `feat(risk): dynamic sizing — live balance + daily-loss budget (S-026 G3)` | merged |
| 6 | G3 ping | #286 | `ping: S-026 G3 — PM review needed on PR #285` | merged |
| 7 | G4 — audit-log "unknown ×4" + sprint COMPLETE | #287 | `fix(audit): defensive default + diagnostic for 'unknown' attribution (S-026 G4 / BUG-033)` | merged |
| 8 | G4 ping | #288 | `ping: S-026 G4 — PM review needed on PR #287 (sprint-end)` | merged |

## Deliverables (file/unit → tests)

| File / unit | Tests added |
|---|---|
| `src/units/strategies/vwap.py::build_vwap_signal(df, symbol, sl_std_mult=…)` — drops `qty` parameter + key (G1) | `tests/test_vwap_strategy.py` updated; new `test_signal_does_not_carry_qty` + `TestQtylessSignalRoutesToMultiAccountDispatch` |
| `src/runtime/pipeline.py` — every signal builder drops `qty`; multiplexer records `meta["strategy_risk_pct"]`; G1 placeholder cleanup (G1 + G2) | covered via the vwap, turtle_soup, outcomes-integration test files |
| `src/units/accounts/risk.py::RiskManager.position_size(pkg, balance_usd) → qty` — single sizing site (G2) | `tests/test_s026_g2_position_size.py` × 11 |
| `src/units/accounts/risk.py::_floor_to_step` + daily-loss-budget gate (G3) | `tests/test_s026_g3_dynamic_sizing.py` × 13 |
| `src/core/coordinator.py::multi_account_execute(balance_fetcher=…)` — per-account sizing + live balance (G2 + G3) | covered in both G2 + G3 test files (`TestMultiAccountDispatchSizesPerAccount` + `TestLiveBalanceFetcher`) |
| `src/runtime/pipeline.py::run_pipeline` — defensive `multiplexed` fallback + BUG-033 diagnostic warning (G4) | `tests/test_s026_g4_audit_attribution.py` × 8 |
| `config/accounts.yaml` — operator-confirmed `risk_pct: 0.01`, `min_balance_usd: 50` per account | covered indirectly via the accounts-integration tests |

Net: **+33 new tests this sprint** (G2 +12, G3 +13, G4 +8). Plus updates to ~5 existing tests for the new contract.

## Highlights

* **G1 — strategy ↔ sizing decoupling.** Strategies now emit only the trade idea (`symbol`, `side`, `entry_price`, `stop_loss`, `take_profit`, `meta`). Quantity is no longer a strategy concern. The transitional G1 placeholder qty-injection in `run_pipeline` lasted exactly one PR — G2 deleted it. Per-strategy risk allocation (`STRATEGY_RISK_PCT`) is now recorded in `meta["strategy_risk_pct"]` so the downstream sizer can apply it per-account.
* **G2 — single sizing site.** `RiskManager.position_size(pkg, balance_usd) → qty` is the only function that decides quantity in the codebase post-G2. Inputs: trade idea + per-account balance; output: qty in base-asset units. Operator-confirmed defaults landed in `config/accounts.yaml`: `risk_pct: 0.01` (1 % balance per trade), `min_balance_usd: 50`, **no max-position cap on sizing** (the `pos_size` field is retained for the post-sizing approval gate only). Smoke-test bypass + per-strategy-risk-pct multiplier baked in.
* **G3 — dynamic sizing.** Three additions:
  - **Live balance fetcher** in `Coordinator.multi_account_execute` consults `processor.get_account_balances()` once per dispatch round, caches `account_id → total_usdt` locally. Lookup order: `pkg.meta["account_balances_usd"]` override → live → `account.cached_balance_usd` fallback.
  - **Floor-rounding** (`_floor_to_step`) replaces banker's `round()` in the sizing kernel so realised risk never exceeds the cap by one step-size.
  - **Daily-loss-budget gate**: if a full SL hit on this trade would push `daily_pnl` past `-max_daily_loss_usd`, the qty scales down to fit the remaining budget; below `min_qty` → refuse with `qty=0.0`. Strict tightening (can only reduce qty).
* **G4 — observability fix for BUG-033.** The "unknown ×4" attribution drift is bounded but not yet root-caused. Defensive default flipped from `"unknown"` to `"multiplexed"` (the actual production builder name when `STRATEGY` is unset) so aggregators don't bucket missing labels. One-shot diagnostic `logger.warning` fires when an actionable signal still resolves via the safety default, capturing `signal_keys` / `meta_keys` / env state — the next operator-side hourly cycle on the VM will pinpoint the leak source via `journalctl`.
* **BUG-033 logged** in `docs/claude/bug-log.md` with the full bug shape + the diagnostic-instrumentation approach taken.

## Architectural patterns this sprint solidified

1. **One sizing site, period.** Pre-S-026 the codebase had at least four places that set or computed qty (strategies' `MAX_QTY` plumbing, multiplexer's `risk_scale * qty`, `safe_place_order`'s caps, `size_order_from_cfg`). Post-S-026 there's exactly one: `RiskManager.position_size`. The functional `size_order` / `size_order_from_cfg` helpers still exist for backwards compat but delegate to the canonical method. `grep -rn "qty.*MAX_QTY\|qty = .*\* risk_scale" src/` returns nothing.
2. **Stateful per-tick injection on `pkg.meta`, not on the package shape.** Per-account sized qtys land under `pkg.meta["sized_qty_by_account"]` so downstream readers (Telegram, audit log) can inspect what the sizer decided without the OrderPackage itself growing a `qty` field. Same pattern used for `meta["strategy_risk_pct"]` (G1 → G2 handoff) and `meta["account_balances_usd"]` (caller override of the live fetcher).
3. **Defensive defaults match the actual producer name, not generic placeholders.** G4's fallback flip from `"unknown"` → `"multiplexed"` is the lesson: when a fallback chain resolves a string field that aggregators bucket on, the final default should be a real producer name from the system. `"unknown"` was a generic placeholder that the hourly summary counted as a real bucket — it created the appearance of a separate failure mode.
4. **Diagnostic instrumentation as a deliverable.** When a bug needs operator-side data to root-cause and the fix can't fully land in-session, the right ship is: bound the bug (defensive default) + add a one-shot diagnostic that fires only on the bad path + log it in `bug-log.md`. The next operator-side ping cycle closes the loop.
5. **Per-PR ping-PR pattern scales to multiple back-to-back PRs in one session.** Operator-overridden continuous mode shipped 4 work-PRs + 4 ping-PRs in one conversation without losing operator visibility — each PM-review prompt arrived with full context and the operator answered four times.

## Deferred items

* **BUG-033 root-cause fix.** The diagnostic warning landed in G4 captures the data; the next operator-side ping cycle on the VM will surface the leak source via `journalctl`. Once identified, a follow-up PR removes the diagnostic + fixes the source.
* **Legacy single-client path in `run_pipeline`.** G2/G3 left this path intact (gated by `MULTI_ACCOUNT_DISPATCH=false` OR global `DRY_RUN=true` OR signal lacks sl/tp) with a clearly named `_DRY_MODE_PLACEHOLDER_QTY = 1.0` sentinel. The path is dry-only by virtue of its trigger conditions; if the operator confirms it's never used in production, a future PR can delete it.
* **`pos_size` removal from sizing.** Operator chose "no max-position cap on sizing" but `pos_size` is retained on the `risk:` block for the post-sizing approval gate (`RiskManager.approve(order)` against `meta.estimated_value`). If the operator wants the gate gone too, a small follow-up PR.
* **UI processor migration steps 2–14** (carried over from S-025; out of scope for S-026).

## Lessons learned (carried into the next sprint)

1. **Operator-overridden continuous mode is viable when the session has bandwidth.** Sprint S-026 shipped four goals + a diagnostic-only fix in a single conversation. The per-PR ping-PR + PM-review pattern scales — the operator answered four "merge this?" prompts and got four merges with full context each time. Worth noting that the standard "one task per session" rule is the *default*, not a constraint, when the operator is engaged in real-time.
2. **Schema additions to `config/accounts.yaml` are non-breaking when the loaders are tolerant.** G2 added `risk_pct` + `min_balance_usd` per account and both `_load_yaml_accounts` (Telegram bot) and `load_accounts` (production wiring) handled the new keys without changes. The `risk:` sub-block is the right home for sizing knobs — keeps the schema self-documenting.
3. **Test isolation under module-level stubs needs a survival strategy.** `tests/test_kill_switch.py` stubs `src.utils.signal_audit_logger` at module level; that stub survives across test files in the full sweep. The G4 test file initially monkeypatched `sal.SIGNAL_FILE` (which got swallowed by the stub) — the working approach is to patch `src.runtime.pipeline.log_signal` directly. Worth a CLAUDE.md / testing-policy note for the next sprint.
4. **Diagnostic-only fixes are a legitimate ship vehicle.** When the operator's data is the missing piece and the in-session sandbox can't reach the VM, shipping a defensive default + a one-shot diagnostic + a bug-log row is preferable to either skipping the goal or guessing at the root cause. The next operator ping cycle closes the loop.

## Sprint completion checklist

- [x] PR list (`#281`, `#283`, `#285`, `#287` work; `#282`, `#284`, `#286`, `#288` ping).
- [x] Tests added (+33 new tests across G2/G3/G4).
- [x] Checkpoint IDs (`CP-2026-05-02-19` → `CP-2026-05-02-22`).
- [x] Deliverables table.
- [x] Deferred items.
- [x] Lessons learned.
- [x] BUG-033 logged in `docs/claude/bug-log.md`.
- [x] Final checkpoint `CP-2026-05-02-22` (with `COMPLETE / WRAPPED` keywords) appended to `CHECKPOINT_LOG.md`.
- [ ] Self-merge this summary PR (docs-only, no code risk).
- [ ] Telegram `/sprintlet_complete S-026` — fires automatically off the final checkpoint commit per the existing VM wiring.

## Proposed CLAUDE.md improvements

1. **Test-isolation pattern note.** When a test patches a function imported from another module, prefer patching the *importing* module's reference (`src.runtime.pipeline.log_signal`) over the *exporting* module's attribute (`src.utils.signal_audit_logger.SIGNAL_FILE`). Survives third-party stubs from other test files. Add to `docs/claude/testing-policy.md`.
2. **Defensive-default rule.** When a fallback chain resolves a string field that aggregators bucket on, the final default must be a real producer name from the system, not a generic placeholder like `"unknown"`. Add to CLAUDE.md § "Always do" (right after the parse_mode rule).
