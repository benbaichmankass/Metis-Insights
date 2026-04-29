# § 9 — Recommended PR sequence (Phases B–F)

Ordered, ≤ 400 LOC each, one concern per PR. Self-merge per `CLAUDE.md`
rules unless flagged "PM review" (E1, F4).

After every two merged PRs Claude pauses, re-reads the sprint prompt and
the audit, and rechecks the DoD before the next PR.

## Phase B — Config reconciliation

### PR B1 — Rewrite `config/strategies.yaml`
- **Files:** `config/strategies.yaml`.
- **Change:** strategies dict contains only `turtle_soup` and `vwap`. Both
  `enabled: true`. Drop `service:` field per § 8 #1 (default action).
  Port any turtle_soup-specific params from
  `strategies/turtle_soup_mtf_v1.py` constants. VWAP threshold preserved.
- **Acceptance:** YAML loads via `load_strategy_config`; new tests assert
  the keyset is `{"turtle_soup", "vwap"}`. Existing
  `tests/test_s007_strategy_registry` ordering assertion is updated to
  the new roster (or removed).
- **Depends on:** none.

### PR B2 — Rewrite `config/units.yaml` strategies section
- **Files:** `config/units.yaml`.
- **Change:** `units.strategies` list contains only `turtle_soup` and
  `vwap`, `enabled: true`, `service:` field dropped. The `accounts.live`
  block is left for PR B3.
- **Acceptance:** matches B1 exactly; `Coordinator.list_strategies()`
  returns two entries.
- **Depends on:** B1.

### PR B3 — Reconcile account ID space
- **Files:** `config/accounts.yaml` (add `strategies: [turtle_soup, vwap]`
  to each account), `config/units.yaml` (remove the `accounts` block or
  collapse it to point at `accounts.yaml`), `src/units/accounts/__init__.py`,
  `src/core/coordinator.py::list_accounts()`.
- **Change per § 8 #3 default:** single source of truth in
  `accounts.yaml`. Risk caps already non-zero; verify and add an explicit
  `strategies:` field per account.
- **Acceptance:** `Coordinator.list_accounts()` returns the three accounts
  from `accounts.yaml`; each has `strategies == ["turtle_soup", "vwap"]`.
- **PM decision required:** § 8 item 3 default applies; PM may veto.
- **Depends on:** B2.

### PR B4 — Update tests with hard-coded production roster
- **Files:** `tests/test_s007_strategy_registry.py`,
  `tests/test_s008_strategies.py`, `tests/test_s011_strategy_purity.py`,
  any other suite asserting `[ict, vwap, breakout_confirmation, killzone]`
  as the production roster.
- **Change:** assertions updated to `[turtle_soup, vwap]`. Tests using
  synthetic YAML fixtures (registry behaviour tests, parsing tests) keep
  their fixtures unchanged.
- **Acceptance:** `pytest --collect-only` is clean; suite green except
  for the 23 pre-existing `test_runtime_validation.py` failures.
- **Depends on:** B1, B2, B3.

**Pause and re-read prompt after B2 and after B4.**

## Phase C — Code reconciliation

### PR C1 — Port Turtle Soup into `src/units/strategies/`
- **Files (new):** `src/units/strategies/turtle_soup.py`.
- **Change:** thin adapter exporting `order_package(cfg, candles_df)`,
  delegating to the logic in `strategies/turtle_soup_mtf_v1.py`. Conform
  to the `_base.py` contract. Old file untouched (deleted in C5).
- **Acceptance:** `Coordinator.strategy_order_pkg("turtle_soup", "BTCUSDT",
  df)` returns a valid `OrderPackage` shape on a happy-path fixture.
- **Depends on:** B1, B2.

### PR C2 — Unit tests for new turtle_soup module
- **Files (new):** `tests/test_s012_turtle_soup.py`.
- **Coverage:** signal generation on a synthetic happy-path fixture; no
  signal on flat market; edge cases (empty df, single candle, all-zero
  volume). Mirror the structure of `tests/test_s008_strategies.py:201-250`
  (vwap tests).
- **Acceptance:** ≥ 6 new passing tests.
- **Depends on:** C1.

### PR C3 — Wire turtle_soup into the runtime pipeline
- **Files:** `src/runtime/pipeline.py`, `src/core/coordinator.py` (only
  if needed — Coordinator is dynamic-import based and may not need
  changes).
- **Change:** the pipeline dispatch loop iterates enabled strategies from
  `units.yaml` and invokes `Coordinator.strategy_order_pkg(strategy, ...)`
  for each. Confirm signals are written to the same DB and routed
  through the same `RiskManager` → order layer as VWAP. Remove the
  hardcoded `strategies.vwap_signal_builder` import at line 119 if it
  exists post-C5.
- **Acceptance:** integration test (in `tests/test_s012_pipeline.py`,
  new) drives one tick and verifies both vwap and turtle_soup emit
  signal-audit log lines.
- **Depends on:** C1, C2, B2.

### PR C4 — Drop `service:` mapping from registry consumers
- **Files:** `src/strategy_registry.py` (remove `service_name()` and any
  callers), grep-cleanup of any other `service:` reads.
- **Change:** post-decision-#1, single-process is confirmed. Strip the
  field's machinery.
- **Acceptance:** `grep -rn "service_name\|service:" src/ config/` returns
  only the systemd `.service` files in `deploy/` and the bot's own
  service handling for `ict-trader-live` / `ict-telegram-bot`.
- **PM decision required:** § 8 item 1.
- **Depends on:** B1, B2.

### PR C5 — Delete out-of-scope strategies and the legacy registry
- **Files (deleted):**
  - `strategies/turtle_soup_mtf_v1.py` (replaced by C1)
  - `strategies/breakout_confirmation.py`
  - `strategies/vwap_signal_builder.py` *if* `compute_vwap`/
    `build_vwap_signal` were folded into `src/units/strategies/vwap.py`
    in this PR; otherwise keep and flag as a follow-up
  - `src/units/strategies/breakout_confirmation.py`
  - `src/units/strategies/ict.py`
  - `src/units/strategies/killzone.py`
  - `src/runtime/strategies/ict.py`
  - `src/runtime/strategies/__init__.py` (if dir is now empty)
  - `src/strategies_manager.py`
  - `tests/test_strategies_manager.py`
  - `tests/test_turtle_soup_mtf.py` (covered by C2 now)
- **Change:** for each deletion, run `grep -rn` and prove no live import
  remains. If any tests still import a deleted name, fix in this PR.
- **Acceptance:** suite green; `python scripts/repo_inventory.py` clean.
- **PM decision required:** § 8 item 7.
- **Depends on:** C1, C2, C3, C4, B4.

### PR C6 — Reconcile entrypoints + delete `automated_trading_loop.py`
- **Files:**
  - `run_trader.sh` — edit to `python -m src.main` *or* delete (recommend
    delete; the systemd unit is canonical).
  - `check_bots.sh` — fix process-name greps to `src.main` /
    `src.bot.telegram_query_bot` *or* delete.
  - `src/core/automated_trading_loop.py` — delete.
  - `docs/claude/deployment-ops.md` — add the canonical-entrypoint
    paragraph from § 5.5.
- **Acceptance:** no consumer of `automated_trading_loop` remains;
  `git grep "automated_trading_loop"` returns 0.
- **Depends on:** C5.

**Pause after C2 and after C5.**

## Phase D — Service reconciliation

### PR D1 — (only if § 8 #1 vetoed in favour of per-strategy services)
- **Files:** new `deploy/ict-trader-vwap.service`,
  `deploy/ict-trader-turtle-soup.service`, install steps in
  `docs/claude/deployment-ops.md`.
- **Default expectation:** **NOT EXECUTED** — single-process is the
  default action.

### PR D2 — Single-process consolidation (default)
- **Files:** verify no consumer remains of per-strategy `service` fields
  (covered by C4 already). Add a structural check to the regression test
  suite that asserts the only trader-side unit in `deploy/` is
  `ict-trader-live.service`.
- **Acceptance:** test passes; `ls deploy/*.service` matches the expected
  set (`ict-env-check`, `ict-git-sync`, `ict-heartbeat`,
  `ict-telegram-bot`, `ict-trader-live`).
- **Depends on:** C4, C5.

### PR D3 — Harden Telegram start-services + phantom regression test
- **Files:** `src/bot/telegram_query_bot.py::cmd_toggle`,
  `tests/test_s012_telegram_services.py` (new).
- **Change:**
  - Before issuing `systemctl start <svc>`, assert `<svc>` is one of
    `{name for f in deploy/*.service}` (parse at startup, cache).
    If a configured service name has no matching unit, fail loudly with
    a Telegram-friendly error rather than silently surfacing the
    systemctl miss.
  - Optionally fail at process startup if any configured service has no
    matching unit file (defense in depth).
- **Acceptance:** new test exercises:
  (a) configured service whose `.service` exists → toggles cleanly;
  (b) configured service whose `.service` is missing → returns explicit
  "config drift" error and does **not** invoke `systemctl`.
- **Depends on:** D2.

## Phase E — Live-mode hardening

### PR E1 — Hard live-mode interlock
- **Files:** `src/main.py` (startup assertion), `src/runtime/validation.py`
  (consolidate the existing interlock into a single explicit
  `assert_live_mode_or_dry_run()` helper).
- **Change:** at startup, if `DRY_RUN!=true` and `ALLOW_LIVE_TRADING!=true`,
  exit non-zero with a clear error. No silent downgrade. Existing
  `DRY_RUN`-respecting paths in `src/runtime/orders.py` and
  `src/units/accounts/execute.py` are unchanged (they're the legitimate
  staging path).
- **Acceptance:** new tests in `tests/test_s012_live_mode.py` cover the
  four start-up combos in § 6.5.
- **PM review (light):** flagged because this changes startup behaviour;
  PM should know.
- **Depends on:** none directly; can run in parallel with B/C if desired,
  recommended after C6.

### PR E2 — Confirm `/accounts` toggle stays + document defaults
- **Files:** `docs/claude/deployment-ops.md`.
- **Change:** documentation only, no code change. Records that the
  per-account toggle remains and that the **default** state of every
  configured account is `live`.
- **PM decision required:** § 8 item 4.
- **Depends on:** none.

### PR E3 — Risk-cap firing tests for both strategies
- **Files (new):** `tests/test_s012_risk_caps.py`.
- **Coverage:** the five tests in § 7.5 minus E3a's drawdown test (added
  separately).
- **Acceptance:** all green; uses the existing `RiskManager` API; no
  changes to risk.py in this PR.
- **Depends on:** B3, C1, C3.

### PR E3a — Implement `max_dd_pct` (intra-day default)
- **Files:** `src/units/accounts/risk.py`,
  `tests/test_s012_risk_caps.py` (extend).
- **Change per § 8 #6 default:** `RiskManager.approve()` rejects when the
  intra-day equity drawdown ≥ `max_dd_pct`. UTC-midnight reset.
- **Acceptance:** new test fires the rejection.
- **PM decision required:** § 8 item 6 (semantic). PM may veto and pick
  inception-to-date or rolling.
- **Depends on:** E3.

### PR E4 — Strategy-attributed signal audit log
- **Files:** verify `src/runtime/pipeline.py` writes
  `runtime_logs/signal_audit.jsonl` with a `strategy` field. Add a test
  if missing.
- **Acceptance:** `tail -1 runtime_logs/signal_audit.jsonl` after a tick
  contains `"strategy": "turtle_soup"` or `"vwap"`.
- **Depends on:** C3.

**Pause after E1 and after E3.**

## Phase F — Verification & deployment

### PR F1 — Full-suite + secret scan + repo inventory
- **Files:** `docs/sprint-summaries/sprint-012-summary.md` (initial draft;
  finalised in F5).
- **Change:** record `pytest tests/ -q --ignore=tests/test_main_loop.py`
  output, `python scripts/secret_scan.py`, `python scripts/repo_inventory.py`.
- **Acceptance:** suite green except the 23 pre-existing
  `test_runtime_validation.py` failures (carried since S-009 — will be
  reduced in this sprint by E1's new file). Secret scan clean.
- **Depends on:** all prior PRs in this list except F4.

### PR F4 — Deployment runbook
- **Files (new):** `docs/audit/sprint-012-deployment-runbook.md`.
- **Content:** the six items from sprint prompt § F4 — pre-flight, pull
  + reload, restart sequence (live LAST), verification commands, rollback.
- **PM review:** flagged because PM (or Colab SSH) executes it on the VM.
- **Depends on:** D2, D3, E1.

### PR F5 — Sprint summary
- **Files:** `docs/sprint-summaries/sprint-012-summary.md`,
  `docs/claude/checkpoints/CHECKPOINT_LOG.md` (final S-012 checkpoint).
- **Content:** sprint completion checklist per `CLAUDE.md`. Includes
  PR list, tests added, files deleted, phantom-service resolution
  status (per § 4 / § 8 #5), architecture decision, lessons learned.
- **Acceptance:** docs only, self-merge per the docs-only rule.
- **Depends on:** F1, F4.

**End of S-012 PR sequence.**

## DoD coverage map

| DoD checkbox | Closed by |
|---|---|
| `strategies.yaml` lists exactly `turtle_soup` + `vwap`, both enabled | B1 |
| `units.yaml` strategies match | B2 |
| `accounts.yaml` references only the new roster, caps non-zero | B3 |
| Exactly one strategy directory | C5 |
| Exactly one strategy registry | C5 (deletes `strategies_manager.py`) |
| Every `.service` corresponds to a real need; bidirectional | D2 |
| Phantom services gone + regression test | D3 (+ § 8 #5 PM step for VM) |
| No `DRY_RUN=true` reachable for production strategies; startup hard-fails when `ALLOW_LIVE_TRADING≠true` | E1 |
| Risk caps fire for both strategies | E3 (+ E3a for max_dd_pct) |
| `pytest tests/ -q` green; `secret_scan.py` clean | F1 |
| Deployment runbook exists, followed on VM | F4 (+ PM/Colab VM step) |
| `systemctl status ict-trader-live` active; both strategies producing signals | F4 verification step |
| Telegram `/strategies` returns the new roster, both live, both with non-zero recent signals | F4 verification step |
| Live trader uptime preserved across sprint | guardrail #1; F4 places live restart LAST |
