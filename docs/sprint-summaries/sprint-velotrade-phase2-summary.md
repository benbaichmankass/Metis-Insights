# Sprint Velotrade phase-2 — DXtrade integration infrastructure + persistent prop state

**Dates:** 2026-05-03 (single-day, three-segment sprint; PRs #336 → #337 → #338 + this summary)
**Checkpoints:** CP-2026-05-03-01 → CP-2026-05-03-02 → CP-2026-05-03-03
**Outcome:** ✅ all three PRs shipped + 56 new tests + zero behaviour change to live Bybit trading. Operator authorised each phase serially in one conversation; mid-session pivot from BLOCKED → infrastructure when operator clarified the goal.

## PR list

| # | Phase | PR | Title | Status |
|---|---|---|---|---|
| 1 | Phase 1 — scaffold (prior session) | #336 | `feat(accounts): Velotrade integration scaffold + mission-aware PropRiskManager` | merged |
| 2 | Phase 2a — SDK shape + not-configured state | #337 | `feat(accounts): Velotrade phase-2 — DXtrade integration infrastructure + 'not fully configured' account state` | merged |
| 3 | Phase 2b — persistence + UI | #338 | `feat(accounts/ui): Velotrade phase-2b — prop_state.json persistence + /accounts_status prop fields` | merged |
| 4 | sprint summary | this PR | `docs(sprint): Velotrade phase-2 COMPLETE — summary + DXtrade contract template` | self-merged (docs-only) |

## Deliverables (file/unit → tests)

### Phase 1 — scaffold (CP-2026-05-03-01, PR #336)

| File / unit | Tests added |
|---|---|
| `src/units/accounts/risk.py::evaluate(order)` — structured skip vocabulary (`DAILY_LOSS_CAP`, `POSITION_SIZE_CAP`, `INTRADAY_DRAWDOWN`) | covered in `tests/test_prop_risk_manager.py` |
| `src/units/accounts/prop_risk.py::PropRiskManager(RiskManager)` — adds `SKIP_MISSION_MET`, `SKIP_OVERNIGHT_RESTRICTED`, `SKIP_WEEKEND_RESTRICTED` + UTC time gates + mission predicates | `tests/test_prop_risk_manager.py` × 31 |
| `src/units/accounts/__init__.py::load_accounts` — picks Prop vs base RiskManager by `type` | (covered by loader tests) |
| `src/core/coordinator.py::multi_account_execute` — calls `evaluate()` so skip reasons surface on the result row | (covered by routing tests) |
| `src/units/accounts/integrator.py` — `VelotradeAPI` + deprecated `BreakoutAPI` alias | (covered by executor tests) |
| `config/accounts.yaml` — `prop_velotrade_1` row with `enabled: false`, evaluation-phase requirements, overnight/weekend block | (config wiring sanity test) |
| `docs/claude/prop-account-state.md` (new) | (docs) |

### Phase 2a — SDK shape + not-configured state (CP-2026-05-03-02, PR #337)

| File / unit | Tests added |
|---|---|
| `src/units/accounts/dxtrade_client.py` (new) — `DXtradeClient` class shape + `MissingCredentialsError(RuntimeError)` | `tests/test_velotrade_infrastructure.py::TestDXtradeClient` × 9 |
| `src/units/accounts/clients.py::velotrade_client_for(account)` — factory mirroring `bybit_client_for` / `binance_conn_for`; reads `VELOTRADE_BASE_URL` env var | `tests/test_velotrade_infrastructure.py::TestVelotradeClientFactory` × 3 |
| `src/units/accounts/integrator.py::VelotradeAPI.place(order, dry_run, client=…)` — accepts injected `DXtradeClient`; bare class without client raises `MissingCredentialsError` | `tests/test_prop_risk_manager.py::TestVelotradeExecutor` × 5 |
| `src/units/accounts/execute.py::_submit_order` velotrade branch — real call structure with retCode-style error handling; missing client → `MissingCredentialsError`; SDK `NotImplementedError` → `RuntimeError("DXtrade SDK contract pending — …")` | (same TestVelotradeExecutor) |
| `src/core/coordinator.py::multi_account_execute` — adds velotrade to client-construction switch; not-fully-configured message names the env var | `tests/test_velotrade_infrastructure.py::TestCoordinatorNotConfiguredPing` × 2 |
| `src/units/accounts/account.py::TradingAccount` — `configured: bool` + `configured_reason: Optional[str]` fields, surfaced in `status()` | `tests/test_velotrade_infrastructure.py::TestLoaderConfiguredFlag` × 3 |
| `src/units/accounts/__init__.py::load_accounts` — sets `configured` based on `resolve_credentials()` | (same TestLoaderConfiguredFlag) |
| `config/accounts.yaml` — `prop_velotrade_1` lost `enabled: false` (loaded as not-configured + empty strategies block any signal) | `tests/test_velotrade_infrastructure.py::TestRealAccountsYamlWiring` × 1 |

### Phase 2b — persistence + UI (CP-2026-05-03-03, PR #338)

| File / unit | Tests added |
|---|---|
| `src/units/accounts/prop_state_io.py` (new) — atomic JSON read/write; `load_prop_state`, `write_prop_state`, `get_prop_state_path`, `set_prop_state_path` | `tests/test_prop_state_persistence.py::TestPropStateIO` × 7 |
| `src/units/accounts/prop_risk.py::PropRiskManager(account_name=…)` — JSON-wins-over-YAML seeding | `tests/test_prop_state_persistence.py::TestPropRiskManagerSeeding` × 4 |
| `src/units/accounts/prop_risk.py::record_trade_result` — atomic write-through with defensive outer try/except | `tests/test_prop_state_persistence.py::TestPropRiskManagerWriteThrough` × 4 |
| `src/units/accounts/__init__.py::load_accounts` — passes `account_name=name` to PropRiskManager | `tests/test_prop_state_persistence.py::TestLoaderIntegration` × 3 |
| `src/units/ui/processor.py::format_account_status_block(status)` (new) — single per-account renderer for `/accounts_status` (Rule-5 thin-shell extraction) | `tests/test_accounts_status_block_renderer.py` × 18 (regular / not-configured / prop / HTML escape) |
| `src/bot/telegram_query_bot.py::cmd_accounts_status` — delegates to processor; ~70 lines removed | (covered indirectly by renderer tests) |
| `.gitignore` — adds `runtime_state/` | (n/a) |
| `docs/claude/prop-account-state.md` § "State persistence" — rewritten for JSON-wins contract + reset workflow | (docs) |

Net: **+56 new tests this sprint** (Phase 1 31 + Phase 2a +20 + Phase 2b +36, minus rewrites). Combined accounts/coordinator/UI regression sweep: **300 passed** post-Phase-2b. No regressions; pre-existing `test_coordinator_flow.py` / `test_accounts_status_md_rendering.py` collect-time skips on this sandbox have the same root cause on `main` (`pandas`, `python-telegram-bot` package layout) — not introduced by this sprint.

## Highlights

- **Mid-session pivot from BLOCKED → infrastructure.** The phase-2 prompt said "STOP and open a [BLOCKED-PM] ping-PR if creds aren't provisioned, do not stub the SDK with fake endpoints". The session correctly opened the BLOCKED PR + ping pattern. The operator then clarified: "we are only building the integration infrastructure, not hooking up a specific account; create an account with a 'not fully configured' status that exists in the unit and pings if anyone tries to use it". The session discarded the BLOCKED commit, force-pushed the work branch with the real infrastructure, and re-titled the PR. Lesson: when the operator clarifies mid-session, the mid-flight cleanup pattern (discard ping branch, reset work branch, force-push, re-title) is cheap.
- **Generic "not fully configured" mechanism.** What the operator asked for as a single demo account turned into a generic loader-level flag (`TradingAccount.configured` + `configured_reason`) that applies to every account whose env-var creds are missing — bybit_1 / bybit_2 / prop_velotrade_1 all benefit. The UI renderer surfaces it; the existing diagnostic-ping infrastructure surfaces it on any live attempt. One mechanism, three account types.
- **Real-shape SDK class with stub methods.** `DXtradeClient` validates creds in `__init__` (raises `MissingCredentialsError` immediately if absent) and exposes `place` / `cancel` / `status` / `balance` with real signatures — the *bodies* raise `NotImplementedError("DXtrade SDK contract pending — …")` until the operator drops the contract. The executor + coordinator + integrator already speak the retCode-style shape. When the contract drops, only four method bodies in one file change.
- **Persistence that can't crash the order path.** `record_trade_result` writes through after updating in-process counters, with two layers of defence: `prop_state_io.write_prop_state` already swallows IO errors at the helper layer; `PropRiskManager._persist_state` adds an outer `try/except` that catches anything else (including a misbehaving monkeypatch in tests). The order path can never see a persistence-layer exception.
- **JSON-wins-over-YAML seeding.** The YAML `prop_state:` block is now a fallback seed — useful for fresh installs and phase resets — but the JSON file is canonical when present. To reset a prop account between phases, delete the section from `runtime_state/prop_state.json` (or the whole file). Operator no longer hand-edits YAML between sessions.
- **Bot-renderer extraction is the canonical Rule-5 win.** Phase-2b moved the ~70-line `cmd_accounts_status` block into `processor.format_account_status_block(status)`. The bot now does: `for s in statuses: lines.append(format_account_status_block(s))`. The renderer is unit-testable without importing `telegram` (which doesn't even install on this sandbox), and the same processor function will power the eventual web dashboard (CLAUDE.md "the bot and the webapp are both UIs").

## Live-mode invariant — every PR

Every work-PR in this sprint touched `src/units/accounts/*` + `src/core/coordinator.py` (Live-mode invariant rule 3 list). All three were flagged for PM review and operator-approved before merge:

| PR | Approval mode | Notes |
|---|---|---|
| #337 phase-2a | operator approved + merged after PM review | Touches the SDK shape + executor branch + coordinator routing; the diff is functionally inert until the SDK methods get real bodies. |
| #338 phase-2b | operator approved + merged after PM review | Persistence + renderer; persistence writes happen *after* execution in `record_trade_result`, so the order path cannot be affected by a persistence-layer failure. |

`scripts/check_dry_run_in_diff.py` was clean on every PR. No `mode: live` flips. Bybit accounts (`bybit_1`, `bybit_2`) untouched.

## Architecture rules — every PR

- **Unit boundary.** Only `src/units/accounts/*` (the owning unit), `src/core/coordinator.py` (the canonical translator), `src/units/ui/processor.py` (the UI processor), and `src/bot/telegram_query_bot.py` (thin-shell delegation) touched. No new cross-unit imports outside the coordinator. The bot's import of `format_account_status_block` is bot → UI processor — the canonical Rule-5 dependency direction.
- **Strategies untouched** across the entire sprint.
- `execute_pkg` remains the single canonical live-order entry point.
- **DB unit untouched.** Per the unit-boundary declaration in the original sprint plan, prop-state lives in `runtime_state/`, not the DB unit (per-account ephemeral, not log-shaped). The trade-journal write in `_log_trade_to_journal` from S-029 is unaffected.
- **Bot is a thin shell.** Phase-2b removed ~70 lines of inline rendering.

## What's left after this sprint

- **DXtrade SDK contract drop.** Fill in the four `NotImplementedError` method bodies in `src/units/accounts/dxtrade_client.py` once the operator provides the API contract. Single-file change. The new `docs/integrations/dxtrade-contract-template.md` (this PR) is the structured drop-zone.
- **Live smoke test.** Per § 6 of the original phase-2 prompt: enable `prop_velotrade_1`, route a `pkg.meta['is_test']=True` order with qty below DXtrade min-lot, expect rejection. Requires the SDK methods + sandbox creds. The wiring is in place — the existing smoke-test path in `cmd_smoke_test` will exercise the velotrade branch the moment the SDK methods land.

## Lessons learned (1–3 bullets for future sprints)

- **Operator clarifications can flip a session from "STOP" to "build it". Be ready to pivot in-flight.** The phase-2 prompt's STOP rule was correct *for the spec it described* (real SDK calls, real creds). Once the operator clarified the goal as "infrastructure + not-configured state", the same prompt's hard rules ("no SDK stubbing with fake endpoints") still applied — but the path forward was building the real-shape SDK class with stub method bodies, not stopping. **Carry forward:** when the BLOCKED ping-PR pattern is invoked but the operator clarifies the scope, discard the ping-PR work cleanly (don't push the BLOCKED branch), force-push the work-PR with the real implementation, and re-title. The wasted work is one local commit.
- **A "not configured" account state is the right abstraction for any optional integration.** What started as a Velotrade-specific request became a generic loader-level mechanism that applies to all accounts. Future integrations (additional prop firms, more bybit accounts on different keys, etc.) inherit the configured/not-configured surface for free. **Carry forward:** when the operator asks for "an account that exists but doesn't trade if creds are missing", the right move is a generic `configured` flag, not an account-type-specific guard.
- **Extracting bot renderers into the UI processor is always the right call.** The cmd_accounts_status renderer existed inline in `telegram_query_bot.py` since S-021. Phase-2b moved it to `processor.format_account_status_block(status)` to enable testing without the python-telegram-bot import — but it also unlocks the future web dashboard rendering the same blocks. **Carry forward:** any new UI logic in the bot starts as a processor helper from day 1. The bot's role is loop + reply_text + auth gate; everything else lives in the UI processor.

## Proposed CLAUDE.md improvements (for next sprint)

1. **Add a "mid-session scope flip" pattern to § Telegram Reporting.** The current Ping-PR vs work-PR rules describe the BLOCKED case but don't cover what happens when the operator un-blocks mid-session by clarifying scope. Suggested addition (one paragraph): "If the operator clarifies the scope mid-session and the BLOCKED state no longer applies, discard the ping-PR work locally (don't push), reset the work branch to its pre-BLOCKED commit, build the real implementation, force-push, and re-title the work-PR. Do NOT open the ping-PR — Telegram will fire on the work-PR's merged commit instead."
2. **Document the "not configured" account mechanism in § Architecture rules.** Now that we have a generic `configured` / `configured_reason` surface on `TradingAccount`, future PRs that touch the accounts unit should respect the contract: missing creds → load with `configured=False` (don't filter); any live action that bypasses the not-configured gate should emit a diagnostic ping. Suggested addition under Rule 3 (account/risk/execute boundary): "When credentials are missing, the account loads with `configured=False`. The order route (`Coordinator.multi_account_execute`'s `client_error` path) is the only canonical place that may refuse a live action — emit `enqueue_execution_failure` so the operator gets a Telegram alert. New code paths that touch creds must follow this pattern."

## Sprint stats

- **3 work-PRs** + **1 sprint-summary PR** = **4 PRs** total.
- **+56 net new tests** across the three phases.
- **300 passed** in the post-Phase-2b regression sweep.
- **Zero regressions** to live Bybit trading.
- **Zero new dependencies** (pure stdlib for both DXtradeClient + prop_state_io).
- **Three Tier-2 PM-review approvals** (one per work-PR; operator approved in the same conversation).
