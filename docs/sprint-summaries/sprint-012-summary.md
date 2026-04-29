# Sprint S-012 — Production Wiring Audit & Full Live Activation

> Finalized in PR F5 alongside the closing CHECKPOINT_LOG.md entry.
>
> **Sprint type:** All-night, slow-and-thorough audit + remediation.
> **Owner:** Claude Code (autonomous).
> **PM:** Ben.
> **Tech Lead:** Perplexity.
> **Created:** 2026-04-29. **Closed:** 2026-04-29.
> **Goal:** Every component production-ready, fully live, no orphan
> service references. Strategy roster reduced to **turtle_soup + vwap**
> only.

## Outcome at a glance

| DoD checkbox | Status | Closed by |
|---|---|---|
| `strategies.yaml` lists exactly turtle_soup + vwap, both enabled | ✅ | B1 |
| `units.yaml` strategies match | ✅ | B2 |
| `accounts.yaml` references only the new roster, caps non-zero | ✅ | B3 |
| Exactly **one** strategy directory | ✅ | C5 |
| Exactly **one** strategy registry | ✅ | C5 |
| Every `.service` file corresponds to a real need; bidirectional | ✅ | D2 |
| Phantom services gone + regression test | ✅ | D3 |
| No `DRY_RUN=true` reachable for production strategies; startup hard-fails | ✅ | E1 |
| Risk caps fire for both strategies (pos_size, daily_usd, kill, drawdown) | ✅ | E3 + E3a |
| `pytest` green; `secret_scan.py` clean | ⚠️ | F1 — 1153 pass, 17 pre-existing fail (S-009 carry); secret scan clean |
| Deployment runbook exists, followed on VM | ⚠️ | F4 ships the doc; PM/Colab runs it post-merge on the VM |
| Live trader uptime preserved | ✅ | guardrail #1; no service touched during the sprint window |

## PRs merged (Phase A → Phase E)

| PR | Title |
|---|---|
| [#147](https://github.com/the-lizardking/ict-trading-bot/pull/147) | S-012 Phase A: production wiring audit (docs only) |
| [#148](https://github.com/the-lizardking/ict-trading-bot/pull/148) | S-012: append CP-2026-04-29-62 — Phase A done |
| [#149](https://github.com/the-lizardking/ict-trading-bot/pull/149) | S-012 PR B1: rewrite config/strategies.yaml — turtle_soup + vwap only |
| [#150](https://github.com/the-lizardking/ict-trading-bot/pull/150) | S-012 PR B2: rewrite config/units.yaml — match B1 roster |
| [#151](https://github.com/the-lizardking/ict-trading-bot/pull/151) | S-012 PR B3: collapse units.accounts → accounts.yaml |
| [#152](https://github.com/the-lizardking/ict-trading-bot/pull/152) | S-012 PR B4: update tests to new production roster + heal B3 fixtures |
| [#153](https://github.com/the-lizardking/ict-trading-bot/pull/153) | S-012 PR C1: port turtle_soup into src/units/strategies/ |
| [#154](https://github.com/the-lizardking/ict-trading-bot/pull/154) | S-012 PR C2: unit tests for turtle_soup module |
| [#155](https://github.com/the-lizardking/ict-trading-bot/pull/155) | S-012 PR C3: wire turtle_soup into runtime pipeline |
| [#156](https://github.com/the-lizardking/ict-trading-bot/pull/156) | S-012 PR C4: drop service: field + PM § 8 #1 shared-state audit |
| [#157](https://github.com/the-lizardking/ict-trading-bot/pull/157) | S-012 PR C5: delete out-of-scope strategies + strategies_manager.py |
| [#158](https://github.com/the-lizardking/ict-trading-bot/pull/158) | S-012 PR C6: reconcile entrypoints, delete automated_trading_loop.py |
| [#159](https://github.com/the-lizardking/ict-trading-bot/pull/159) | S-012 PR D2: single-process consolidation regression test |
| [#160](https://github.com/the-lizardking/ict-trading-bot/pull/160) | S-012 PR D3: harden Telegram start-services + phantom regression test |
| [#161](https://github.com/the-lizardking/ict-trading-bot/pull/161) | S-012 PR E1: hard live-mode interlock |
| [#162](https://github.com/the-lizardking/ict-trading-bot/pull/162) | S-012 PR E2: document /accounts dry/live toggle + defaults |
| [#163](https://github.com/the-lizardking/ict-trading-bot/pull/163) | S-012 PR E3: risk-cap firing tests for both turtle_soup and vwap |
| [#164](https://github.com/the-lizardking/ict-trading-bot/pull/164) | S-012 PR E3a: implement max_dd_pct intra-day UTC reset |
| [#165](https://github.com/the-lizardking/ict-trading-bot/pull/165) | S-012 PR E4: strategy-attributed signal audit log |
| [#166](https://github.com/the-lizardking/ict-trading-bot/pull/166) | S-012 PR F1: full-suite verification + initial sprint summary |
| [#167](https://github.com/the-lizardking/ict-trading-bot/pull/167) | S-012 PR F4: deployment runbook |
| #168 | S-012 PR F5: finalize sprint summary + close CHECKPOINT_LOG (this PR) |

**Total:** 22 PRs (one audit + one Phase-A checkpoint + four configs +
six code + one tests + two services + four live-mode + three
verification/runbook/close).

## Tests added

| Test file | Count | Coverage |
|---|---:|---|
| `tests/test_s012_turtle_soup.py` | 23 | Turtle Soup adapter — happy path, no-signal, edge cases, cfg overrides, Coordinator integration |
| `tests/test_s012_pipeline.py` | 11 | Pipeline-level wiring — multiplexer integration, `STRATEGY=turtle_soup` env routing |
| `tests/test_s012_service_consolidation.py` | 7 | Single-process architecture lock |
| `tests/test_s012_phantom_services.py` | 9 | `.env.example`/`.env.bak` filter + `toggle_service` unit-file validation |
| `tests/test_s012_live_mode.py` | 10 | DRY_RUN/ALLOW_LIVE_TRADING interlock — closes the unset-DRY_RUN hole |
| `tests/test_s012_risk_caps.py` | 24 | pos_size, daily_usd, kill-switch, max_dd_pct intra-day with UTC rollover |
| `tests/test_s012_signal_audit.py` | 6 | `runtime_logs/signal_audit.jsonl` carries strategy attribution |
| **Total new** | **90** | |

Tests **updated** (B4 + scoped fixes during C-phase):
- `tests/test_s007_pipeline_rewire.py`, `test_s007_safe_model_loader.py`, `test_s007_signals_attribution.py`, `test_s007_validate_script.py`, `test_s007_bot_commands.py`
- `tests/test_s008_accounts.py`, `test_s008_dashboards.py`, `test_s008_strategies.py`, `test_s008_trading_school.py`, `test_s008_coordinator.py`
- `tests/test_s011_strategy_purity.py`, `test_unit_config.py`, `test_coordinator_flow.py`, `test_data_loaders.py`
- `tests/test_strategy_registry.py`, `test_vwap_strategy.py`

Tests **deleted** (C5 — strategy code went away with them):
- `tests/test_strategies_manager.py`, `test_turtle_soup_mtf.py`, `test_ict_signal_builder.py`
- `tests/test_runtime_ict.py`, `test_runtime_pipeline.py`, `test_multiplex_integration.py`

## Files deleted

Source:
- `strategies/turtle_soup_mtf_v1.py` (replaced by `src/units/strategies/turtle_soup.py`)
- `strategies/breakout_confirmation.py`, `strategies/vwap_signal_builder.py` (folded into vwap.py)
- `src/units/strategies/breakout_confirmation.py`, `ict.py`, `killzone.py`
- `src/runtime/strategies/ict.py` + `__init__.py`
- `src/strategies_manager.py`
- `src/core/automated_trading_loop.py`
- `run_trader.sh`, `scripts/start.sh`

Empty directories removed: `strategies/`, `src/runtime/strategies/`.

## Phantom service mystery — resolved

`ict-trader-bak` and `ict-trader-example` did **not** appear anywhere in the repo or its git history when the audit looked. The actual repo-side root cause turned out to be `data_loaders._load_env_accounts()`: it discovered `.env.example` (the repo's env template) and turned it into a phantom `account_id="example"` whose service field was generated as `ict-trader-example`. The same mechanism would generate `ict-trader-bak` from any `.env.bak` file.

Both phantoms are now blocked by:
1. **`_ENV_DISCOVERY_RESERVED`** in `src/bot/data_loaders.py` (PR D3) — filters reserved env-file names (`example`, `bak`, `template`, `sample`, `dist`, `default`, `backup`, `old`, `orig`, `save`, `test`, `tests`, `ci`, `local`, `development`, `dev`, `production`, `prod`, `staging`).
2. **`toggle_service` unit-file validation** in `src/bot/telegram_query_bot.py` (PR D3) — pre-validates against `deploy/*.service`; refuses to call `systemctl` for any service whose unit file doesn't exist.
3. **Default service set to `ict-trader-live`** everywhere (PRs C4 + D2) — `_load_yaml_accounts`, `_load_env_accounts`, `strategy_dashboard_data`, `strategy_registry.load_strategies` all default to the canonical service when an entry omits it.

Whether any other (non-repo) source on the VM still produces phantom names is left to PM § 4.5 diagnostic commands.

## Architecture decision

**Single-process, multi-strategy** (PM § 8 #1, confirmed). Every strategy in the active roster runs inside `ict-trader-live.service`, dispatched by `src/runtime/pipeline.py::multiplexed_signal_builder` through `src/core/coordinator.py::Coordinator.strategy_order_pkg`.

Per-strategy systemd units no longer exist and **must not be re-introduced** without an explicit sprint to author the unit files and refactor the dispatcher. The `service:` field has been dropped from `config/strategies.yaml` and `config/units.yaml`.

PM § 8 #1 guardrail audit (PR C4): no shared mutable state between strategy modules, no shared connector that could deadlock. Strategies are pure signal generators; per-account stateful objects (TradingAccount + RiskManager) are isolated by construction.

## Roster after sprint

```yaml
strategies:
  turtle_soup:    # NEW (S-012 PR C1)
    enabled: true
    risk_pct: 0.5
    timeframe: "15m"
    symbols: [BTCUSDT, ETHUSDT]
    # + ATR / sweep / TP / partial parameters ported from
    # the legacy TurtleSoupMTFv1 class

  vwap:           # KEPT, threshold preserved
    enabled: true
    risk_pct: 1.0
    timeframe: "15m"
    symbols: [BTCUSDT]
    threshold: 0.01
```

Per PM § 8 #2 final answer: turtle_soup ships `enabled: true`. Real order submission is gated by the **account-level** dry/live toggle (PM § 8 #4), not by the strategy flag. Each account starts in dry-run by default; flipping to live requires the explicit `/accounts live <id>` Telegram command.

## Test suite state (post-PR E4)

```
PYTHONPATH=. python3 -m pytest tests/ -q --ignore=tests/test_main_loop.py
→ 1153 passed, 17 failed, 2 skipped, 5 warnings (~106 s)
```

`secret_scan.py` — clean.
`repo_inventory.py` — no junk candidates.

### Pre-existing failures (deferred)

The 17 failures are **not introduced by S-012**. They fall into three classes, all carried since S-009 (per CP-2026-04-29-61):

| Count | Test file | Cause |
|---:|---|---|
| 15 | `test_runtime_validation.py` | Tests pass a `settings` dict to `validate_startup()`, but the production signature is `validate_startup() -> None` (reads env directly). Signature mismatch from a refactor that didn't update tests. |
| 1 | `test_runtime_smoke.py::test_runtime_smoke_path` | Same root cause as above. |
| 1 | `test_print_runtime_profile.py::test_print_runtime_profile_outputs_summary` | The `scripts/print_runtime_profile.py` shim still passes `os.environ` to `build_settings_from_env()`, which now takes 0 args. |

S-012 PR E1 added a new, focused live-mode test file (`tests/test_s012_live_mode.py`, 10 tests) covering exactly the interlock contract; the old broken file is left in place for a follow-up sprint to either rewrite or delete.

### Lessons learned

1. **Audit doc libraries beat one big audit doc.** Splitting `docs/audit/sprint-012/` into 9 focused chunks (one per audit section) plus an index made each follow-up PR small enough to write in one shot, and made the PM-decision document a clean single-source artefact (PRs B1 → E3a each cite specific § 8 items by number). Pattern recommended for future audit-style sprints.
2. **Treat audit hypotheses as hypotheses.** The phantom service investigation initially concluded the source must be VM-only; B3's verification accidentally surfaced the actual repo-side mechanism (`_load_env_accounts` discovering `.env.example`). Re-check claims when adjacent code touches them.
3. **Tightening defaults beats adding code paths.** PRs C4 + D2 didn't add new validation logic — they changed the default service value from `f"ict-trader-{name}"` to `"ict-trader-live"`. One-line edit per call site, whole-class phantom-service elimination without any runtime check. Same pattern with PR E1 (`dry_run != "true"` instead of `dry_run == "false"`) closed the silent-downgrade hole. **Default-tightening as a deliberate technique** for fail-closed systems.

### Suggested CLAUDE.md improvements for the next sprint

1. Add a "audit doc library" recipe to `docs/claude/session-workflow.md` so future sprints with a heavy audit phase reach for the multi-file pattern by default.
2. The 17 pre-existing `test_runtime_validation.py` failures should be a **first-task** of S-013 (rewrite or delete) so the suite is clean enough that future "pytest green" DoD items are unambiguous. Add an entry to the next sprint backlog.

## Deferred items

* **Pre-existing test failures (17):** rewrite or delete `test_runtime_validation.py` (15), `test_runtime_smoke.py` (1), and `test_print_runtime_profile.py` (1) to match the current signatures. Out of S-012 scope per audit B4.
* **Equity wiring for `max_dd_pct`:** PR E3a implements the cap; the orchestrator must call `RiskManager.update_equity(<usd>)` after each balance refresh for the cap to fire. Until that wiring lands (separate sprint), the drawdown check is silently skipped (preserving S-010 behaviour). PR F4's runbook flags this.
* **VM-side phantom investigation (PM § 4.5):** PM runs the four diagnostic commands separately to confirm no phantom call source remains outside the repo.
