> ✅ **S-041 STATUS NOTE (2026-05-06 — verify-before-trusting-done sweep):**
> Sprint **completed**. Closing checkpoint: CP-2026-05-04-04. Deliverables confirmed
> on-disk: `tests/test_env_render_contract.py` (3 tests, 59 total pass),
> `src/runtime/boot_audit.py`, `tests/test_boot_audit.py` (3 tests),
> `src/main.py` insertion, and `docs/sprint-summaries/sprint-021-summary.md`.
> Under workplan M0..M10, this sprint maps to **M3** (risk controls / config
> hardening) and **M4** (repo hygiene + CI). No further action required.

# Sprint S-021 — BUG-048 hardening: config-drift contract + boot-time observability

**Mode:** Single Claude Code session, ~60–90 min wall-clock total. PM available for ad-hoc questions.
**Judged on:** (a) CI fails on a contrived `.env.example`/renderer drift; (b) trader restart on the VM produces a Telegram ping listing open packages by strategy.
**Created:** 2026-05-04. **Predecessors:** PRs #397 / #398 / #399 merged (CP-2026-05-04-03 wraps BUG-048). Read `/root/.claude/plans/i-want-the-next-resilient-puddle.md` first — it's the source of truth this prompt was generated from.

## 1. Goal

Close the two real gaps that BUG-048 exposed: (1) `.env.example` and `scripts/render_env_from_master.py::build_live` can drift apart silently; (2) the operator has no observable signal that a trader restart resumed monitoring of open trades. After this sprint, CI fails on env-key drift in either direction, and every trader startup with carried-forward open packages produces a Telegram message naming the strategies and counts. Both fixes are docs/tests/`src/runtime/`-only — the literal restart-monitoring logic the operator initially asked for is already implemented (DB-backed monitor loop at `src/runtime/order_monitor.py:919-1028` re-attaches on tick 1; BUG-046 gate prevents new signals; Bybit holds SL/TP atomically per `src/units/accounts/execute.py:310-319`).

## 2. Dependencies

- **Sprint dependency** — PRs #397 (`notebooks/operator/push_notebook_to_repo.ipynb`), #398 (`MONITOR_RECONCILE_ENABLED=true` in renderer), #399 (CP-2026-05-04-03 + BUG-048 row) merged on `main`. Verified via `git log --oneline main | grep -E "397|398|399"`.
- **Infra dependency** — the existing `tests/test_render_env_from_master.py` `FAKE_DATA` fixture is on `main` and exercised by 53 tests. The boot audit reuses `Database.get_order_packages_by_strategy` (`src/units/db/database.py:521`), `_load_strategies` (`src/runtime/order_monitor.py`), and `send_telegram_direct` (`src/runtime/notify.py`).
- **External dependency** — none. All work is local repo + pytest. Live verification is operator-driven (restart `ict-trader-live.service` on the VM, watch Telegram).

## 3. Deliverables

1. `tests/test_env_render_contract.py` — 3 contract tests pinning `.env.example` ↔ `build_live(FAKE_DATA)` parity, with an explicit ignore-list module constant for intentionally one-sided keys.
2. `src/runtime/boot_audit.py` — `report_open_packages_on_boot() -> dict[str, int]` helper. Logs a one-line summary, pings Telegram with per-strategy counts when total > 0, silent on clean restart. Best-effort; never raises.
3. `tests/test_boot_audit.py` — 3 cases (no-open / has-open / DB-unavailable).
4. `src/main.py` insertion (~5 lines) calling `report_open_packages_on_boot()` between `validate_startup()` (line 142) and the start of the main loop (line 177), wrapped in best-effort try/except matching the existing dup-key-check shape.
5. `docs/sprint-summaries/sprint-021-summary.md` — PR list, tests added, checkpoint ID, deliverables table, deferred items (boot-time SL/TP audit at the broker, deferred for lack of incident justification).
6. Checkpoint entry in `docs/claude/checkpoints/CHECKPOINT_LOG.md` per `HANDOFF_TEMPLATE.md`.

## 4. Checkpoints

| # | Checkpoint title | What completes by then | Risk class | Wall-clock | Gates which next checkpoint |
|---|---|---|---|---|---|
| T0 | Branch + scaffolding | `claude/sprint-021-bug048-hardening` branch off `main`; read `/root/.claude/plans/i-want-the-next-resilient-puddle.md` and skim the cited file:line refs to confirm they still match. | infra | 5 min | T1, T2 |
| T1 | PR 1 — Config-drift contract test | `tests/test_env_render_contract.py` lands; `pytest tests/test_env_render_contract.py tests/test_render_env_from_master.py -q` returns 56/56; PR opened, `scan` green, **self-merged** (infra / docs-only). | infra | 25 min | T2 (independent), T3 |
| T2 | PR 2 — Boot-time observability ping | `src/runtime/boot_audit.py` + `tests/test_boot_audit.py` + `src/main.py` insertion. `pytest tests/test_boot_audit.py -q` returns 3/3. PR opened, `scan` green, **self-merged** (infra — does not touch live-trading paths; see § 4b unit boundary). | infra | 30 min | T3 |
| T3 | PR 3 — Sprint summary + checkpoint | `docs/sprint-summaries/sprint-021-summary.md` + `CP-2026-05-04-04` (or next available NN) entry in `CHECKPOINT_LOG.md`. **Self-merged** (docs-only). Telegram session-end ping rides on the checkpoint commit via VM wiring. | docs-only | 15 min | (sprint end) |

If T1 or T2 blows past 2× its wall-clock estimate (50 min for T1 or 60 min for T2), stop and revise the prompt rather than push through.

### 4b. Unit boundary declaration

| Unit | Role in this sprint |
|---|---|
| `src/units/strategies/` | untouched |
| `src/units/accounts/` | untouched |
| `src/data_layer/` (DB unit) | reads only — `Database.get_order_packages_by_strategy` from `src/units/db/database.py:521` |
| `src/ui/` | untouched |
| `src/runtime/` | owns — new `src/runtime/boot_audit.py`; reads `_load_strategies` from `src/runtime/order_monitor.py` and `send_telegram_direct` from `src/runtime/notify.py` |
| `src/bot/` | untouched |
| `src/core/coordinator.py` | untouched |

No new cross-unit imports outside `src/core/coordinator.py`. The boot audit reads from the DB unit and the notify helper; both calls are through their existing public APIs (no internal modules / private helpers reached into).

## 5. Risk class & merge model

| PR | Class | Self-merge? |
|---|---|:-:|
| PR 1 — `tests/test_env_render_contract.py` | infra | ✅ |
| PR 2 — `src/runtime/boot_audit.py` + `tests/test_boot_audit.py` + `src/main.py` insertion | infra | ✅ |
| PR 3 — sprint summary + checkpoint | docs-only | ✅ |

Live-mode invariant check (CLAUDE.md § "Live-mode invariant"): no PR in this sprint touches `src/runtime/orders.py`, `src/runtime/pipeline.py`, `src/runtime/trading_mode.py`, or `src/units/accounts/*`. The `src/main.py` edit in PR 2 is a 5-line best-effort wrapper around a read-only DB query and a Telegram send — not a routing-logic change. ✅ all three PRs self-mergeable.

## 6. Success criteria

- ✅ `PYTHONPATH=. python -m pytest tests/test_env_render_contract.py tests/test_render_env_from_master.py tests/test_boot_audit.py -q` returns ≥ 56 + 3 = **59 passed, 0 failed**.
- ✅ Contrived drift on a scratch branch (e.g. add `MONITOR_DEBUG_FOO=true` to `.env.example` only) makes `tests/test_env_render_contract.py::test_env_example_keys_emitted_by_renderer` fail. Revert before opening any PR.
- ✅ Operator-driven VM verification: stop the trader (`sudo systemctl stop ict-trader-live.service`), restart it. With ≥ 1 open package in `trade_journal.db::order_packages` matching a known strategy, the operator receives a Telegram message starting "🔁 Trader restart — resuming monitoring" listing the strategy + count. With 0 open packages, the operator receives no Telegram message and `journalctl -u ict-trader-live` shows "boot_audit: 0 open packages on boot".
- ✅ `docs/sprint-summaries/sprint-021-summary.md` exists, lists PR numbers + tests + deferred items, links the checkpoint ID.
- ✅ `CHECKPOINT_LOG.md` has a new top entry with all 5 sections from `HANDOFF_TEMPLATE.md` filled.
- ❌ No PR in this sprint flips `live`/`dry_run` on any account in `config/accounts.yaml`. (None should — but the live-mode-invariant check in each PR body must explicitly call this out.)

## 7. Hard guardrails

Inherited from CLAUDE.md and worth re-stating:

- **No live-trading-logic touches.** Off-limits this sprint: `src/runtime/orders.py`, `src/runtime/pipeline.py`, `src/runtime/trading_mode.py`, `src/units/accounts/execute.py`, `src/units/accounts/clients.py`, `config/accounts.yaml`. If a fix appears to require touching one of these, **stop** and file a ping-PR per CLAUDE.md § "Telegram Reporting" rather than push through.
- **Telegram body must use plain text — no `parse_mode`** for the boot ping. Recurring failure shape (BUG-009, BUG-030, BUG-031): dynamic content in legacy Markdown blows up on unbalanced delimiters.
- **No new cross-unit imports** outside `src/core/coordinator.py`. The boot audit reads `Database` and `notify` through existing public APIs only.
- **No process-level dry/live interlock added.** Per BUG-039, the per-account `mode` field in `config/accounts.yaml` is the only toggle. Boot audit must not gate any of its behaviour on env vars or strategy-level mode flags.
- **No "just one more thing"** — when the contract test or the boot ping is shipped, sprint ends. Defer any additional hardening ideas to a follow-up sprint with its own prompt.

## 8. Hand-off

The next sprint after S-021 is the **Recurring Hardening Session 2** queued by CP-2026-05-04-02: architecture audit of `src/units/accounts/execute.py` and the Coordinator translator pattern (S-008). That sprint is unrelated to S-021 and reads from `docs/sprints/recurring-hardening-prompt.md`.

S-021's deferred follow-ups (NOT to be picked up next):

- **Boot-time SL/TP audit at the broker.** Touches `src/units/accounts/*`; mandatorily triggers a ping-PR per CLAUDE.md § "Live-mode invariant" rule 3. Defer until an incident shows manually-canceled or partial-fill-broken broker SL/TP is a real failure mode.
- **Stale-package liveness watchdog.** Overlaps with the existing `liveness_watchdog` (`src/main.py:286-289`) and the BUG-042 reconciler. Would create false positives whenever `monitor()` legitimately returns None.

## Cross-references

- Approved plan: `/root/.claude/plans/i-want-the-next-resilient-puddle.md`
- BUG-048 row: `docs/claude/bug-log.md`
- Closing checkpoint of the BUG-048 fix session: `docs/claude/checkpoints/CHECKPOINT_LOG.md` § CP-2026-05-04-03
- Sprint planning template: `docs/claude/sprint-planning.md`
- Handoff template: `docs/claude/checkpoints/HANDOFF_TEMPLATE.md`
- Architecture rules: `CLAUDE.md` § "Architecture rules" (especially § 6 "Live by default + tell-me-if-not")
