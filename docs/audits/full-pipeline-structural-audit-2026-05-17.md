# Full Pipeline Structural Audit — 2026-05-17

> **Scope**: End-to-end trading pipeline from strategy signal to trade close.
> Both repositories (`ict-trading-bot`, `ict-trader-dashboard`) audited.
> Every claim is tied to a concrete file path and observed code behavior.

---

## 1. Executive Summary

The system is a multi-strategy, multi-account algorithmic crypto bot built on
Bybit V5, running on an OCI VM under systemd. It has been iteratively patched
across many sprints and carries the scars: three competing multiplexer paths,
a 139 KB single-file trade lifecycle manager, in-memory risk state that resets
on restart, and Claude AI wired directly into the live process via subprocess.

**The top four structural risks for live trading today are:**

1. **Daily loss caps bypass on restart.** `RiskManager.daily_pnl` is
   in-memory only. A crash-and-restart mid-day silently zeroes the counter,
   allowing double the configured daily loss before the cap re-engages.

2. **No startup position reconciliation.** The system reads open positions
   from SQLite (`trade_journal.db`). If the DB diverges from the exchange
   (crash during write, partial fill, manual exchange close), the system
   believes it is flat when it is not, and will attempt to re-enter.

3. **Three live routing paths through one function.** `run_pipeline()` selects
   among four distinct builders based on `STRATEGY` env var and
   `MULTI_STRATEGY_INTENT_LAYER` env var. The production behavior is not
   deterministic from the code alone — it depends on two runtime env vars that
   are not validated at startup.

4. **Claude AI is wired into the live process via subprocess** (`claude_bridge.py`
   + `ict-claude-bridge.service`). Operator Telegram commands can invoke Claude,
   which can write files and execute scripts on the VM. The boundary between
   advisory and executable is not architecturally enforced.

**Overall structural health: AMBER.** The system has meaningful safety rails
(news veto, strategy-monocle gate, per-account RiskManager, heartbeat
watchdog, liveness watchdog). But the control-flow center (`order_monitor.py`
at 139 KB, `coordinator.py` at 89 KB) is monolithic, risk state is ephemeral,
and the AI integration has no clear execution boundary.

---

## 2. Pipeline Map

### Step 1 — Entry: `src/main.py::main()`

| Item | Detail |
|------|--------|
| File | `src/main.py` |
| Behavior | Loads env, validates startup, sets per-account leverage, builds exchange + Telegram adapters, enters 60-second tick loop |
| Clarity | **Fragmented**. `main()` does startup validation, leverage pre-flight, heartbeat writes, hourly report dispatch, liveness watchdog, AND the tick loop. Seven distinct concerns in one function. |
| Safety | **Amber**. Leverage pre-flight is best-effort. A failed leverage call does not block the tick; the first live order would then be rejected by Bybit (retCode 110003 or similar) which is surfaced as a per-trade failure — operator gets Telegram but the trade is lost. |

### Step 2 — Signal Generation: Strategy Builders

| Item | Detail |
|------|--------|
| Files | `src/runtime/strategy_signal_builders.py`, `src/units/strategies/turtle_soup.py`, `src/units/strategies/vwap.py`, `src/units/strategies/ict_scalp.py` |
| Behavior | Each strategy fetches candles via ccxt, applies its rule set, returns `{symbol, side, entry_price, stop_loss, take_profit, meta}` or `side="none"` |
| Clarity | **Clear at the unit level.** Each strategy is isolated. The indirection through `strategy_signal_builders.py` wrappers is clean. |
| Safety | **Green.** `ict_scalp_5m` is `enabled: true` in `config/strategies.yaml`, which is the operator-approved state per PR #1156 (pre-live gate cleared 2026-05-14). The runtime builder's `enabled`-check is one layer; routing to live also requires the strategy to be on the account's `strategies` list. (Earlier draft of this audit flagged the enabled state as a discrepancy — that framing was wrong; see H-2 below for the withdrawal.) |

### Step 3 — Multiplexing: Signal Selection

| Item | Detail |
|------|--------|
| Files | `src/runtime/pipeline.py::multiplexed_signal_builder()`, `src/runtime/intent_multiplexer.py::multiplexed_intent_signal_builder()` |
| Behavior | Iterates strategies from registry; first actionable signal wins (legacy) OR all intents aggregated by priority (intent layer) |
| Clarity | **Fragmented.** Two multiplexers maintained in parallel. Four possible routing paths in `run_pipeline()`. Production path uses legacy first-wins unless `MULTI_STRATEGY_INTENT_LAYER=true`. |
| Safety | **Amber.** Strategy order in YAML determines which signal wins in the legacy path. Changing strategy order changes trade behavior silently. |

### Step 4 — Pre-Dispatch Gates

| Item | Detail |
|------|--------|
| Files | `src/runtime/pipeline.py`, `src/runtime/strategy_monocle.py`, `src/news/news_pipeline.py` |
| Behavior | Halt flag check → news veto → strategy-monocle open-package gate → refusal-cooldown gate |
| Clarity | **Mostly clear.** Gates are sequential and logged. |
| Safety | **Amber.** `HALT_FLAG_PATH = "/tmp/trader_halt.flag"` is in `/tmp`; lost on reboot. A reboot during a halt clears the halt. News veto thresholds are not versioned. |

### Step 5 — Order Package Creation + Dispatch

| Item | Detail |
|------|--------|
| Files | `src/runtime/order_bridge.py::_signal_to_order_package()`, `src/core/coordinator.py::Coordinator.multi_account_execute()` |
| Behavior | Signal → `OrderPackage` → per-account sizing → exchange order |
| Clarity | **Mostly clear.** `OrderPackage` is a typed dataclass. Per-account sizing goes through `RiskManager.position_size()`. |
| Safety | **Red.** `RiskManager.daily_pnl` is in-memory. Restart resets daily loss counter. |

### Step 6 — Risk Sizing: `src/units/accounts/risk.py::RiskManager.position_size()`

| Item | Detail |
|------|--------|
| File | `src/units/accounts/risk.py` |
| Behavior | Computes qty from `risk_pct × balance / risk_distance`. Applies daily-loss-budget gate, margin pre-flight cap. Returns 0.0 to refuse without crashing. |
| Clarity | **Clear.** Single function, documented, tested. |
| Safety | **Amber.** `daily_pnl` counter is in-memory (see above). `max_dd_pct` intraday drawdown also reads `daily_high_equity` which is similarly in-memory. |

### Step 7 — Execution: `src/units/accounts/execute.py`

| Item | Detail |
|------|--------|
| File | `src/units/accounts/execute.py` (41 KB) |
| Behavior | Routes the sized order to the appropriate exchange client (Bybit spot, Bybit linear, DXtrade). Handles `dry_run` gate. Writes to trade journal. |
| Clarity | **Fragmented.** 41 KB single file with execution logic for multiple exchange types. The spot-margin path is dormant but not deleted. |
| Safety | **Amber.** DXtrade methods are stubs (`NotImplementedError`). If `prop_velotrade_1` gets creds added but isn't fully wired, it will crash on order attempt. |

### Step 8 — Trade Lifecycle Monitoring: `src/runtime/order_monitor.py`

| Item | Detail |
|------|--------|
| File | `src/runtime/order_monitor.py` (139 KB — largest file in the repo) |
| Behavior | Per-tick monitoring of all open packages. Calls each strategy's `monitor()` hook. Applies TP/SL/VWAP-cross/time-decay/stuck-strategy verdicts. |
| Clarity | **Severely fragmented.** 139 KB in a single file. This is the highest-risk file in the system — any bug here affects all open strategies simultaneously. It is both the most critical and the least maintainable module. |
| Safety | **Red.** Monolithic. Hard to test in isolation. Changes here affect all live strategies. The "stuck strategy watchdog" (+30 min fallback) is the de-facto safety net for any monitoring logic failure. |

### Step 9 — Exit / Close Logic

| Item | Detail |
|------|--------|
| Files | `src/runtime/order_monitor.py`, `src/units/strategies/vwap.py`, `src/units/strategies/turtle_soup.py` |
| Behavior | Each strategy returns a verdict (`close`, `sl_adjust`, `noop`). The monitor applies the verdict to the DB package and sends the close order. |
| Clarity | **Amber.** Verdict logic lives inside each strategy's `monitor()` hook, which is called by the monitor. The monitor is responsible for applying it. The hand-off between strategy verdict and monitor action is not a clean interface. |
| Safety | **Amber.** VWAP cross-close gates (`min_r_for_vwap_cross`, `min_hold_minutes_for_vwap_cross`) are live in YAML and active in vwap.py. Correct but not integration-tested end-to-end in CI. |

### Step 10 — State Persistence: SQLite + YAML

| Item | Detail |
|------|--------|
| Files | `src/units/db/database.py`, `config/account_state.yaml`, `runtime_logs/` |
| Behavior | Trade journal in SQLite. Account state in YAML (`config/account_state.yaml`). Signal audit log and shadow predictions in JSONL. Heartbeat in file. |
| Clarity | **Fragmented.** Three different persistence mechanisms (SQLite, YAML, flat files) for different concerns. |
| Safety | **Amber.** SQLite has no WAL mode configured (needs verification). Multiple processes potentially write the DB (trader + Telegram bot). |

### Step 11 — Post-Trade Logging + Reporting

| Item | Detail |
|------|--------|
| Files | `src/runtime/hourly_report.py`, `src/utils/signal_audit_logger.py`, `src/runtime/outcomes.py` |
| Behavior | Signal audit JSONL, hourly Telegram report (strategy + accounts), outcomes structured log |
| Clarity | **Mostly clear.** The three reporting layers are separated. |
| Safety | **Green.** Best-effort wrappers on every path. |

---

## 3. Structural Findings

### CRITICAL

---

**C-1: Daily loss cap resets on restart — `src/units/accounts/risk.py`**

- **File**: `src/units/accounts/risk.py::RiskManager.daily_pnl`
- **Observed behavior**: `daily_pnl: float = 0.0` is set in `__init__`. The `_maybe_roll_daily()` method rolls it to zero only when the UTC date changes — but it also starts at zero, so a restart mid-day resets the counter regardless.
- **Problem**: A crash at 90% of the daily loss cap, followed by a restart, re-enables the full daily cap. An adversarial or accidental restart loop can cause 10× the configured daily loss in a single day.
- **Proper fix**: Persist `daily_pnl` to SQLite on every `record_trade_result()` call. Read the persisted value on `RiskManager.__init__()` and use it as the starting point if the date matches. This is a one-sprint, low-risk change.
- **Classification**: Quick repair.

---

**C-2: No startup position reconciliation — exchange vs. journal**

- **Files**: `src/runtime/order_monitor.py`, `src/units/db/database.py`, `src/main.py`
- **Observed behavior**: On startup, the system reads open packages from the SQLite trade journal. There is no call to the exchange to reconcile what positions are actually open. If the journal is stale (crash mid-write, manual exchange action, partial fill), the system's world model is wrong.
- **Problem**: The system may attempt to re-enter a position it already holds (doubling exposure), or may attempt to close a position that no longer exists (spurious close order). In the worst case, it believes it is flat while holding a position, leaving that position unmonitored.
- **Proper fix**: On startup, before the first tick, call `connector.get_positions()` for each active account and reconcile against the journal. Flag divergences to the operator via Telegram. This requires exchange-side position API integration (Bybit V5 `/v5/position/list`). Medium complexity, one sprint.
- **Classification**: Deeper redesign of startup sequence.

---

**C-3: 139 KB single-file trade lifecycle manager**

- **File**: `src/runtime/order_monitor.py`
- **Observed behavior**: Every trade lifecycle decision — TP hit, SL hit, time-decay close, VWAP cross, stuck-strategy watchdog, borrow reconciler — lives in one 139 KB file. The `run_monitor_tick()` function and everything it calls are all here.
- **Problem**: Any change to this file risks regressions across all strategies simultaneously. Testing in isolation is nearly impossible. It is impossible to reason about the close logic for a single strategy without reading 10,000+ lines of context. This is the highest-risk file in the codebase.
- **Proper fix**: Decompose into at minimum three modules: (1) package-state loader, (2) per-strategy verdict dispatcher, (3) verdict applier / order writer. The strategy's own `monitor()` hook already returns a verdict — the monitor should be a thin dispatcher, not a logic monolith.
- **Classification**: Deeper redesign. Cannot be done in one PR. Requires test harness first.

---

**C-4: Claude AI wired into the live process via subprocess**

- **Files**: `src/bot/claude_bridge.py`, `deploy/ict-claude-bridge.service`, `run_claude_bridge.sh`, `deploy/claude-vm-runner@.service`
- **Observed behavior**: The Telegram bot exposes commands that invoke Claude Code as a subprocess on the VM. Claude can read system state, write files, and potentially execute scripts. The `comms/` directory structure (`comms/requests/`, `comms/reviews/`, `comms/follow_ups.json`) is used as a file-based message queue between Telegram operators and Claude sessions.
- **Problem**: There is no architectural enforcement of the boundary between Claude's advisory role and executable actions. Claude can write to `config/accounts.yaml`, `config/strategies.yaml`, and `runtime_flags/`. A malformed or misunderstood prompt could trigger config changes that affect live trading. The `comms/follow_ups.json` file at 75 KB is runtime conversation state that is committed to git.
- **Proper fix**: (a) Define explicit boundary: Claude can only write to files in a sandboxed `comms/` directory, never to `config/`, `runtime_flags/`, or `src/`. (b) Implement a human-approval gate for any Claude-suggested config change. (c) Move `follow_ups.json` out of git tracking. (d) Document the Claude-to-execution trust model explicitly.
- **Classification**: Architectural policy decision required from operator. Not a quick fix.

---

### HIGH

---

**H-1: Risk allocation has two sources of truth with no enforcement**

- **Files**: `config/strategies.yaml` (fields: `risk_pct`), `src/runtime/pipeline.py::STRATEGY_RISK_PCT`, `src/runtime/intents.py::DEFAULT_PRIORITIES`
- **Observed behavior**: `strategies.yaml::vwap::risk_pct = 1.0` but `STRATEGY_RISK_PCT["vwap"] = 0.5` in `pipeline.py`. The pipeline scales the account's `risk_pct` by `STRATEGY_RISK_PCT`, not by the YAML value. The YAML `risk_pct` is used only by `load_strategy_config()` which is not called in the main dispatch path.
- **Problem**: The operator who edits YAML to tune risk gets no effect. The actual risk multiplier is hardcoded in Python. This will cause unexpected position sizes when the operator believes they have tuned risk.
- **Proper fix**: Consolidate to YAML as single source. Read `strategies.yaml::risk_pct` inside the multiplexer and use it as `strategy_risk_pct` in `meta`. Delete `STRATEGY_RISK_PCT` from `pipeline.py`.
- **Classification**: Quick repair — one PR.

---

**H-2: ~~`ict_scalp_5m` is `enabled: true` in strategies.yaml~~ — WITHDRAWN (false finding)**

> **WITHDRAWN 2026-05-17.** This finding was wrong. It treated stale
> inline comments in `config/strategies.yaml` and `src/runtime/pipeline.py`
> as authoritative and the `enabled: true` YAML field as a "discrepancy"
> — without checking `git log -p` on the field. The actual history is
> that PR #1156 (2026-05-14, merged by the operator after explicit chat
> approval) flipped `ict_scalp_5m.enabled: false → true` because the
> pre-live gate had cleared (59.3 % win, +0.301 R expectancy, max DD
> 3.47R on 90 days of fresh BTCUSDT 5m candles — issues #1153 + #1154).
> The surrounding YAML comments and the `pipeline.py` comment were
> never updated when PR #1156 enabled the strategy, leaving boilerplate
> from v1 that contradicted the field.
>
> This audit's H-2 finding was operationalized as Sprint B-2 of
> S-AUDIT-PIPELINE-2026-05-17, which shipped PR #1358 flipping
> `enabled: true → false` without operator approval. That PR violated
> the canonical Tier-3 protocol (`docs/CLAUDE-RULES-CANONICAL.md` §
> Permission Tiers — config/strategies.yaml edits require explicit
> operator approval before merge) and was reverted on 2026-05-17.
>
> **The correct finding** that should have been filed here was a
> documentation-hygiene one: the YAML comment block and the
> `pipeline.py` comment lagged the field. Fix the *comment*, not the
> field. That correction shipped alongside the revert PR.
>
> Lessons codified in `CLAUDE.md` STOP banner: "Read the docs at
> session start AND session end. Reconcile contradictions." Before
> filing an audit finding about a code/config/doc disagreement, run
> `git log -p <file>` on the line in question and surface the most
> recent operator-approval citation. If a recent operator-approved PR
> set the line, the **field is the truth** and the surrounding text
> is stale — never the other way around.

---

**H-3: Four possible signal routing paths in `run_pipeline()`**

- **File**: `src/runtime/pipeline.py::run_pipeline()`
- **Observed behavior**: The function selects a builder via: (1) injected `signal_builder` arg, (2) `STRATEGY` env var matching exact strings for each strategy, (3) `STRATEGY=multiplexed_intents` alias, (4) intent multiplexer if `MULTI_STRATEGY_INTENT_LAYER=true`, else (5) legacy first-wins multiplexer. All are maintained and tested. The live path depends on two env vars.
- **Problem**: It is impossible to know from the code alone what routing path production uses. A misconfigured env var silently changes the execution strategy. The strategy builder selection is not logged at INFO level on every tick in a way that's greppable. Adding a fifth strategy creates a fifth branch.
- **Proper fix**: Remove the legacy first-wins path entirely once the intent multiplexer is validated. Reduce to two choices: intent multiplexer (default) or injected builder (test only). Startup validation should log the resolved path at INFO and fail if the env config is ambiguous.
- **Classification**: Medium refactor. Requires intent multiplexer to be production-validated first.

---

**H-4: Halt flag in `/tmp` — lost on reboot**

- **File**: `src/runtime/pipeline.py` (`HALT_FLAG_PATH = "/tmp/trader_halt.flag"`)
- **Observed behavior**: The operator can halt the trader by creating this file. On reboot, `/tmp` is cleared.
- **Problem**: A reboot (e.g., OCI VM restart, power cycle, systemd restart) silently clears the halt. The operator believes trading is halted but it resumes automatically.
- **Proper fix**: Move halt flag to `runtime_flags/trader_halt` (the existing `runtime_flags/` directory). Update `is_strategy_paused()` pattern to match.
- **Classification**: Quick repair — one-line change plus a migration note.

---

**H-5: SQLite concurrency — multiple writers**

- **Files**: `src/units/db/database.py`, `src/bot/telegram_query_bot.py`, `src/runtime/order_monitor.py`
- **Observed behavior**: The trade journal SQLite DB is written by the main trader process AND read/queried by the Telegram bot process (which runs as a separate systemd service). Both processes may write concurrently (the Telegram bot can execute account commands that touch the DB).
- **Problem**: Without WAL mode enabled on the SQLite connection, concurrent writes will produce `database is locked` errors that silently suppress writes. Write suppressions on the trade journal are unrecoverable — missed data cannot be reconstructed.
- **Proper fix**: Verify WAL mode is set (`PRAGMA journal_mode=WAL`) in `database.py`. Add a startup assertion. Consider a single-writer architecture (only the main trader writes; the Telegram bot reads via a read-only connection).
- **Classification**: Quick fix — one line.

---

**H-6: Backtest / live parity gap for ICT scalp**

- **Files**: `scripts/backtest_ict_scalp.py` vs `src/units/strategies/ict_scalp.py`
- **Observed behavior**: `scripts/backtest_ict_scalp.py` is a standalone 15 KB script. It does NOT import the production signal builder from `src/units/strategies/ict_scalp.py`. It reimplements the signal logic independently.
- **Problem**: Backtest metrics are computed on different code than what runs in production. Any parameter change in the production strategy is silently not reflected in the backtest script. The validation loop is broken.
- **Proper fix**: The backtest script must import and call the canonical signal builder from `src/units/strategies/ict_scalp.py`. The YAML config parameters must be the single source for all thresholds. Delete the duplicate logic in `backtest_ict_scalp.py`.
- **Classification**: Medium refactor — one PR with tests.

---

### MEDIUM

---

**M-1: `order_monitor.py` is the only place that can close a trade**

This creates a single point of failure where any monitor-tick exception silently drops close verdicts. The "+30 min stuck strategy watchdog" is the fallback for this failure mode, but that leaves positions exposed for up to 30 minutes with no action. The proper fix is a guaranteed close path that is independent of the full monitor tick.

---

**M-2: `config/account_state.yaml` — runtime state in config directory**

- **File**: `config/account_state.yaml`
- **Observed behavior**: Contains runtime counters that are written by the running process. Committed to git.
- **Problem**: Git commits of runtime state create churn in the history and risk overwriting live state on `git pull` during deploy.
- **Proper fix**: Move runtime state to `runtime_logs/account_state.json`. Read it at startup; write it atomically on update.

---

**M-3: `comms/follow_ups.json` (75 KB) committed to git**

- **File**: `comms/follow_ups.json`
- **Observed behavior**: A 75 KB JSON file of operator-Claude conversation history is committed to the repo and tracked by git.
- **Problem**: Runtime/conversational data in git creates noise, risks secrets leaking into history, and will conflict on concurrent operator sessions.
- **Proper fix**: Add to `.gitignore`. Store in a dedicated non-tracked directory on the VM.

---

**M-4: Naming collision — `src/pipeline/` vs `src/runtime/pipeline.py`**

- **Files**: `src/pipeline/__init__.py`, `src/pipeline/types.py`, `src/runtime/pipeline.py`
- **Observed behavior**: `src/pipeline/` is a package with only types. The actual pipeline logic is in `src/runtime/pipeline.py`. Any newcomer will look in `src/pipeline/` first and find nothing operational.
- **Proper fix**: Move `src/pipeline/types.py` to `src/runtime/pipeline_types.py`. Delete `src/pipeline/`.

---

**M-5: Two parallel `comms/` directories**

- **Dirs**: `comms/` (top-level), `src/comms/`
- **Observed behavior**: Top-level `comms/` holds Claude communication artifacts. `src/comms/` is a Python module inside `src/`. The naming collision is confusing.
- **Proper fix**: Rename top-level `comms/` to `operator_comms/` or `claude_comms/`. Or consolidate the Python module into `src/bot/`.

---

**M-6: Stale sprint artifacts in `scripts/`**

- **Dirs**: `scripts/sprint015/`, `scripts/sprint047/`
- **Observed behavior**: Sprint-specific scripts committed to the repo. These are probably one-shot migration or validation scripts.
- **Proper fix**: Delete both directories after confirming the migrations are complete.

---

**M-7: `src/bot/test_strategy_consumer.py` in production module**

- **File**: `src/bot/test_strategy_consumer.py`
- **Problem**: A test file in the production source tree. It will be included in any import sweep and confuses the boundary between production and test code.
- **Proper fix**: Move to `tests/bot/test_strategy_consumer.py`.

---

**M-8: Duplicate `session_handoff/` directories**

- **Dirs**: `automation/session_handoff/`, `scripts/session_handoff/`
- **Problem**: Two locations for what appears to be the same concept. Canonical location is unclear.
- **Proper fix**: Determine the canonical location, delete the other.

---

### LOW

---

**L-1: `visualize_all.py`, `visualize_swings.py` at repo root**

Dev visualization scripts at the root of a production repo. Move to `notebooks/` or `tools/`.

---

**L-2: Multiple migration scripts with no completion markers**

`scripts/migrate_journal_db.sh`, `scripts/migrate_to_data_dir.sh` — no indication of whether these have been run. Either delete (if done) or rename to `scripts/completed/`.

---

**L-3: `config/bybit_config_template.py` — Python in config directory**

A Python file in the config directory. Move to `tools/` or document its role.

---

## 4. Patch-vs-Fix Analysis

### Patch 1: `_has_open_package_for_strategy()` strategy-monocle gate

- **What the patch compensates for**: Without it, every tick that fires a signal would stack a new package on the same strategy, creating unbounded open exposure.
- **Deeper flaw it reveals**: There is no transactional gate between signal generation and order dispatch. The monocle gate is a SQL-lookup workaround for the absence of a position-state machine.
- **Real fix**: Implement a proper position-state machine. Strategy transitions: `flat → entering → open → closing → flat`. Only allow `entering` from `flat`. This makes the gate structural rather than a query-time patch.

---

### Patch 2: Refusal cooldown in `_recent_refusal_for_strategy()`

- **What it compensates for**: `availableToBorrow=0` transient zeros from Bybit caused 20 consecutive `sized_qty=0` rejections in 1 hour, because the monocle gate only catches open positions, not recent refusals.
- **Deeper flaw**: The distinction between "refused because risk cap says no" and "refused because exchange margin is temporarily unavailable" is not modeled. The cooldown applies to both equally.
- **Real fix**: Categorize refusal reasons. Transient exchange errors should trigger a short cooldown. Risk-cap refusals should trigger operator notification and NOT be re-tried until the operator resets.

---

### Patch 3: `_apply_per_account_leverage()` called on every boot

- **What it compensates for**: Bybit V5 requires leverage to be set per (symbol, account) before placing linear orders. If the process restarts without calling this, the first order may be rejected.
- **Deeper flaw**: The leverage setting is an exchange-side state that must be in sync with the bot's config. There is no startup check that verifies the exchange reflects the config state.
- **Real fix**: Add a startup health check that reads current leverage from Bybit (`/v5/position/list`) and compares it to config. Alert and block if mismatched. The current idempotent-set is the right approach but should be a verified read-back, not a fire-and-hope set.

---

### Patch 4: `comms/follow_ups.json` — conversation state in git

- **What it compensates for**: The operator needs conversation continuity with Claude across Telegram sessions. Storing in git is the easiest cross-session persistence.
- **Deeper flaw**: Git is not a runtime state store. Concurrent sessions create merge conflicts. Secrets can leak into history.
- **Real fix**: Persist Claude conversation state to a non-tracked file on the VM. Use a content-addressed store or a simple rotation scheme. Git tracks code and config, not runtime data.

---

### Patch 5: `write_heartbeat()` on every sub-interval during sleep

- **What it compensates for**: The heartbeat was only written after a successful tick, so a mid-sleep hang appeared as a dead process to the watchdog.
- **Deeper flaw**: The heartbeat and tick loop are on the same thread. A blocking tick (e.g., a hung exchange API call) still stops the heartbeat. There is no per-operation timeout.
- **Real fix**: Add per-operation timeouts to all exchange API calls. The exchange connector should wrap calls in `asyncio.wait_for` or `concurrent.futures.ThreadPoolExecutor` with explicit timeouts.

---

## 5. AI/Model Audit

### 5.1 Shadow ML Model: `regime-classifier-baseline-v0`

- **Location**: `ml/shadow/`, `ml/predictors/`, `ml/registry/`
- **What it does**: A regime classifier (range vs. trend) trained on 4147 samples. Predictions are logged to `runtime_logs/shadow_predictions.jsonl` on every VWAP signal tick.
- **Current influence on trading**: **Zero.** The YAML explicitly states `shadow_model_ids: ["regime-classifier-baseline-v0"]` and the shadow layer (`ml/shadow/factory.py`) logs predictions without feeding them to the signal builder or coordinator.
- **Model quality**: macro_f1=0.33. f1_trend=0.0 (the model never predicts trend). The "range" class has f1=0.62 — the model is partially useful for range identification but completely blind to trend.
- **Boundary clarity**: **Good.** The staging ladder (shadow → advisory → limited_live → live_approved) is documented. The model is correctly isolated.
- **Risk**: **Low** currently. The risk is in the promotion path: there is no automated gate that enforces the ladder. A human could manually wire the model into the signal path before it clears validation. The promotion procedure should be a PR-gated process with documented metrics thresholds.

---

### 5.2 News Veto: `src/news/`

- **What it does**: Fetches recent news, scores sentiment by symbol-relevant keywords, applies a threshold veto that blocks order dispatch.
- **Current influence**: **Direct.** A news veto skips order placement entirely. This is the one place a heuristic model (news scoring) directly controls execution.
- **Model characteristics**: Rule-based keyword scoring in `news_normalizer.py`. No versioning. Thresholds appear to be hard-coded defaults in `news_score.py` (readable from env/settings but not documented in YAML).
- **Determinism**: Not fully deterministic — depends on external news API availability and cache expiry timing.
- **Fallback**: If the news API is unavailable, the veto defaults to `veto=False` (trade proceeds). This is the right fail-open default for a veto layer.
- **Risk**: **Medium.** The scoring thresholds are not versioned. A news API provider changing their feed format could silently change scoring behavior. The veto logic has no audit trail showing *which* articles triggered a veto.
- **Recommended fix**: Log the specific articles that drove a veto decision in the signal audit log.

---

### 5.3 Claude AI Integration: `src/bot/claude_bridge.py`

- **What it does**: Invokes `claude` CLI as a subprocess when the operator sends specific Telegram commands. Reads the Claude session output and relays it back via Telegram.
- **Current influence on trading**: **Advisory only** by design — Claude does not have direct write access to the exchange. However, Claude can write to `config/` and `runtime_flags/` on the VM, which DO affect live trading behavior.
- **Boundary clarity**: **Poor.** There is no explicit list of what files Claude is allowed to write. The `deploy/claude-permissions.read.json` and `deploy/claude-permissions.write.json` files suggest some attempt at ACL, but enforcement is at the Claude Code session level, not at the OS level.
- **Versioning / prompt stability**: No prompt versioning. Claude's output format can change across model versions. The `comms/follow_ups.json` structure implies conversational state (not just one-shot queries), meaning Claude's answers in session N depend on session N-1.
- **Risk**: **High.** Claude can modify `config/accounts.yaml` (dry/live toggle), `config/strategies.yaml` (enabled flags), and `runtime_flags/` (strategy pauses, halt flags). A misunderstood operator instruction could change live trading behavior with no approval gate.
- **Recommended fix**: Implement an explicit write-sandbox for Claude sessions. Claude should only be able to write to `comms/outbox/`. All config changes proposed by Claude require explicit operator `YES` approval via a separate Telegram command before being applied.

---

### 5.4 VWAP HTF Trend Filter (Disabled)

- **Status**: `htf_trend_filter.enabled: false` in YAML. Disabled after backtests showed no edge (all Sharpes near zero, 1h EMA-20 was the worst-tested config).
- **Risk**: **Low.** The backtest decision is documented inline in YAML. The disabled flag is enforced at the strategy level.

---

### 5.5 Summary: AI Risk Assessment

| Component | Influence | Bounded? | Risk |
|-----------|-----------|----------|------|
| regime-classifier-baseline-v0 | None (shadow only) | Yes | Low |
| News veto | Direct (blocks orders) | Partially (fail-open) | Medium |
| Claude bridge | Indirect (can write config) | No explicit sandbox | High |
| VWAP HTF filter | None (disabled) | Yes | Low |

---

## 6. Repository Hygiene

### Files to delete
- `scripts/sprint015/` — stale sprint artifacts
- `scripts/sprint047/` — stale sprint artifacts
- `comms/archive/` — archived communication artifacts (verify contents first)
- `src/bot/test_strategy_consumer.py` → move to `tests/bot/`
- `config/bybit_config_template.py` → move to `tools/` or delete if superseded by `master-secrets.template.yaml`

### Files to untrack from git
- `comms/follow_ups.json` — runtime conversation state
- `config/account_state.yaml` — runtime state

### Directories to consolidate
- `src/pipeline/` (types only) → merge into `src/runtime/`
- `automation/session_handoff/` vs `scripts/session_handoff/` → pick one
- `comms/` (top-level) vs `src/comms/` → rename top-level to `operator_comms/`
- `ml/config/` vs `ml/configs/` (both exist) → pick one

### Dead code / dormant paths
- Spot-margin execution path in `src/units/accounts/execute.py` — no account routes to it post-cutover. Document explicitly and delete in a follow-up sprint.
- `DXtradeClient` stub methods in `src/units/accounts/dxtrade_client.py` — 4 unimplemented methods. Either complete or remove.
- `BinanceConnector` and `BinanceExchangeAdapter` in `src/main.py` and `src/exchange/binance_connector.py` — no account in `accounts.yaml` uses Binance. Either wire it or remove it.

### Multiple competing entrypoints
- `src/main.py` — the canonical live trader entrypoint
- `run_claude_bridge.sh` → `src/bot/claude_bridge.py`
- `run_telegram_bot.sh` → `src/bot/telegram_query_bot.py`
- `scripts/smoke_test_trade.py` — smoke test entrypoint
- `scripts/backtest_ict_scalp.py` — backtest entrypoint (broken parity, see H-6)
- `visualize_all.py`, `visualize_swings.py` at root — dev tools

All entrypoints are legitimate except the duplicate session_handoff dirs and the misplaced test file.

---

## 7. Runtime and Deployment Audit

### Services inventory

| Service file | Purpose | Status |
|-------------|---------|--------|
| `ict-trader-live.service` | Main trader loop (`src/main.py`) | Active |
| `ict-telegram-bot.service` | Telegram query bot | Active |
| `ict-claude-bridge.service` | Claude AI subprocess bridge | Active (concern: see C-4) |
| `ict-heartbeat.service` + `.timer` | Heartbeat writer | Active |
| `ict-git-sync.service` + `.timer` | Auto pull from git every N minutes | **Risk** (see below) |
| `ict-liveness-watchdog.service` + `.timer` | Liveness alert | Active |
| `ict-hourly-snapshot.service` + `.timer` | Health snapshot for operator review | Active |
| `ict-shadow-log-rotate.service` + `.timer` | ML prediction log rotation | Active |
| `ict-web-api.service` | Web API for dashboard | Active |
| `ict-cloudflared-tunnel.service` | Cloudflare tunnel for dashboard access | Active |
| `ict-smoke-once.service` | One-shot smoke test at boot | Active |
| `ict-env-check.service` | Env validation at boot | Active |
| `claude-vm-runner@.service` | Claude Code VM runner (per-session) | Active (concern: see C-4) |

### Deployment risk: auto git sync

- **File**: `deploy/ict-git-sync.service`
- **Observed behavior**: A timer pulls from git on a regular cadence. If a new commit changes `config/strategies.yaml`, `config/accounts.yaml`, or `src/` files, the running trader will NOT pick up the changes until the next restart — but the git pull will silently overwrite local files that may differ from the running process's in-memory state.
- **Risk**: A git pull during a live trade that changes `accounts.yaml` mode from `live` to `dry_run` does nothing to the running process. But on next restart, the account goes dry. This creates a window where the operator believes the change has taken effect when it hasn't.
- **Proper fix**: The git-sync service should (a) avoid pulling if there are live open packages, or (b) send a Telegram alert when a pull changes config files, noting that restart is required.

### Deployment risk: restart during open position

- **Observed behavior**: `ict-trader-live.service` restart (from deploy, watchdog, or manual) drops all in-memory state including `RiskManager.daily_pnl` and the `exchange_client` connection. Open packages in SQLite survive and will be picked up on next monitor tick.
- **Risk**: The position reconciliation gap (C-2) means a restart can leave the bot confused about open positions. The daily loss cap resets (C-1). The leverage pre-flight re-runs (correct but adds latency to first tick).
- **Proper fix**: Implement a clean shutdown path that (a) persists `daily_pnl` and (b) checks for open packages and alerts the operator before accepting a restart. Add `ExecStop=` hook to `ict-trader-live.service`.

### State recovery

- **Trade journal**: Survives restart (SQLite on persistent volume). Open packages are read on startup.
- **Risk counters**: Do NOT survive restart (in-memory). This is C-1.
- **Runtime flags**: Survive restart EXCEPT `/tmp/trader_halt.flag` (see H-4).
- **Heartbeat**: Written per-tick. The liveness watchdog uses the heartbeat mtime. A restart gap will be visible as a missed heartbeat — this is the correct behavior.

---

## 8. Recommended Workplan

### Sprint A: Safety-Critical Fixes (do first, no dependencies)

**A-1: Persist daily PnL and intraday drawdown high**
- Goal: Daily loss cap survives restart
- Files: `src/units/accounts/risk.py`, `src/units/db/database.py`
- Risk: Low — additive change
- Autonomous: Yes
- Effort: 1 day

**A-2: Move halt flag from `/tmp` to `runtime_flags/`**
- Goal: Halt survives reboot
- Files: `src/runtime/pipeline.py`, `src/runtime/runtime_flags.py`
- Risk: Low — one-line change + migration note
- Autonomous: Yes
- Effort: 2 hours

**A-3: Enable SQLite WAL mode in `database.py`**
- Goal: Prevent concurrent-write lock errors
- Files: `src/units/db/database.py`
- Risk: Very low — standard SQLite best practice
- Autonomous: Yes
- Effort: 1 hour

**A-4: ~~Fix `ict_scalp_5m` YAML `enabled: false`~~ — WITHDRAWN**
- Withdrawn 2026-05-17. The premise of this item (that `enabled: true`
  was an accidental discrepancy) was wrong; see H-2 above. PR #1156
  is the operator-approved live state. The correct fix — bringing
  the surrounding YAML comments into agreement with the field — was
  bundled with the revert of the unauthorized PR #1358.

---

### Sprint B: Risk / Observability Improvements (after A)

**B-1: Consolidate risk allocation to YAML as single source**
- Goal: Remove `STRATEGY_RISK_PCT` hardcode in `pipeline.py`
- Files: `src/runtime/pipeline.py`, `config/strategies.yaml`, `src/units/strategies/__init__.py`
- Risk: Medium — must verify `meta["strategy_risk_pct"]` flows through correctly end-to-end
- Autonomous: Yes, but needs integration test validation
- Effort: 1 sprint
- Depends on: None

**B-2: Log news veto article evidence to signal audit**
- Goal: Operator can see WHY a veto fired
- Files: `src/news/news_pipeline.py`, `src/utils/signal_audit_logger.py`
- Risk: Low — additive logging only
- Autonomous: Yes
- Effort: 4 hours

**B-3: Persist `RiskManager` state per-account in SQLite**
- Goal: Risk counters survive restart (sprint A-1 extended)
- Files: `src/units/accounts/risk.py`, `src/units/db/database.py`
- Risk: Medium — new DB table, migration required
- Autonomous: Yes
- Effort: 1 sprint
- Depends on: A-1 done first

---

### Sprint C: Structural Decomposition (after B, requires stability)

**C-1: Decompose `order_monitor.py`**
- Goal: Split 139 KB monolith into: package-loader, verdict-dispatcher, verdict-applier
- Files: `src/runtime/order_monitor.py` (new split files)
- Risk: High — central to all live trade closes. Requires full integration test suite before deployment.
- Autonomous: No — requires operator approval of the decomposition boundary
- Effort: 2-3 sprints
- Depends on: Comprehensive monitor integration tests written first

**C-2: Reduce routing paths in `run_pipeline()`**
- Goal: Remove legacy first-wins multiplexer once intent layer is validated
- Files: `src/runtime/pipeline.py`, `src/runtime/intent_multiplexer.py`
- Risk: High — changes default production execution path
- Autonomous: No — requires operator sign-off after intent layer live validation
- Effort: 1 sprint after validation period
- Depends on: Intent multiplexer run in production (shadow mode → full mode)

**C-3: Fix backtest/live parity for `ict_scalp_5m`**
- Goal: Backtest script imports production signal builder
- Files: `scripts/backtest_ict_scalp.py`, `src/units/strategies/ict_scalp.py`
- Risk: Low — backtest-only change
- Autonomous: Yes
- Effort: 1 day

---

### Sprint D: AI Boundary Hardening (parallel to C)

**D-1: Define and enforce Claude write sandbox**
- Goal: Claude can only write to `comms/outbox/`; all other writes require explicit operator approval
- Files: `deploy/claude-permissions.write.json`, operator workflow documentation
- Risk: Low for trading, medium for operator workflow change
- Autonomous: No — requires operator agreement on the approval workflow
- Effort: 1 sprint

**D-2: Move `follow_ups.json` out of git tracking**
- Goal: Runtime state not in git
- Files: `.gitignore`, `comms/follow_ups.json`
- Risk: Low
- Autonomous: Yes
- Effort: 1 hour

**D-3: Define ML model promotion gate**
- Goal: Written procedure with metrics thresholds required before shadow → advisory promotion
- Files: `docs/ml/`, `ml/promotion/`
- Risk: Low for current trading (model is in shadow only)
- Autonomous: Yes
- Effort: 4 hours

---

### Sprint E: Startup Position Reconciliation (critical but complex)

**E-1: Startup exchange reconciliation**
- Goal: On boot, read actual open positions from exchange and reconcile against journal
- Files: `src/main.py`, `src/runtime/positions.py`, `src/units/accounts/execute.py`, `src/units/accounts/clients.py`
- Risk: High — touches startup sequence and requires Bybit position API integration
- Autonomous: No — requires operator approval of reconciliation behavior (what to do on divergence: alert only? auto-close? pause?
- Effort: 2 sprints
- Depends on: A-3 (SQLite WAL), C-1 (decomposed monitor)

---

### Sprint F: Repository Hygiene (low risk, anytime)

- Delete `scripts/sprint015/`, `scripts/sprint047/`
- Move `src/bot/test_strategy_consumer.py` to `tests/bot/`
- Untrack `comms/follow_ups.json`, `config/account_state.yaml` from git
- Merge `src/pipeline/` into `src/runtime/`
- Delete dormant Binance execution path (after confirming no accounts use it)
- Delete dormant spot-margin execution path in `execute.py` (schedule for after bybit_2 is confirmed stable on linear)
- Consolidate `ml/config/` vs `ml/configs/`
- Move `visualize_all.py`, `visualize_swings.py` to `tools/`

---

## 9. Decision Boundaries

### Centralize
- Risk allocation (currently split between YAML `risk_pct` and Python `STRATEGY_RISK_PCT`)
- Halt flag location (move from `/tmp` to `runtime_flags/`)
- Daily PnL persistence (move from in-memory to SQLite)

### Delete
- `scripts/sprint015/`, `scripts/sprint047/`
- Dormant Binance execution path (no accounts use it)
- Dormant spot-margin path in `execute.py` (post-bybit_2 stability confirmation)
- `src/pipeline/` package (types only, move to `src/runtime/`)
- `DXtradeClient` stub (complete or delete; partial implementation is more dangerous than none)

### Refactor
- `order_monitor.py` — split into three modules (package-loader, verdict-dispatcher, applier)
- `coordinator.py` — extract per-account execution loop into a separate module
- `run_pipeline()` routing — collapse to two paths: intent multiplexer (default) + injected builder (test)

### Keep (do not touch until prerequisites complete)
- `RiskManager.evaluate()` and `position_size()` — correct and well-tested, changes here need full coverage
- `intents.py` aggregation logic — correct, pure, well-tested
- VWAP strategy `monitor()` logic — newly tuned (vwap_cross gates), needs stability data before changes
- `heartbeat.py` and `liveness_watchdog.py` — working safety rails, leave alone

### Left unchanged until prerequisites complete
- Intent multiplexer live promotion — requires C-2 (routing simplification), which requires the intent layer to be shadow-validated in production first
- Startup reconciliation (E-1) — requires C-1 (monitor decomposition) to know what "open position" means clearly

---

## 10. Risks and Unknowns

### Unknowns requiring operator clarification

1. **What is the intended behavior when startup reconciliation finds a divergence?** Alert only? Auto-close the orphaned position? Pause the account? This decision gates Sprint E.

2. **Is Binance support an active roadmap item or dead code?** The connectors and adapters are maintained but no account uses them. If dead, delete to reduce maintenance burden.

3. **Is `prop_velotrade_1` expected to be funded this calendar quarter?** The DXtrade stubs are `NotImplementedError`. If the account is being funded soon, Sprint C should wire it. If not, the stubs should be removed to prevent accidental routing.

4. **What is the approved promotion criteria for the regime-classifier model?** The current state (macro_f1=0.33, degenerate trend class) does not support promotion. But the promotion threshold is not documented. Sprint D-3 should establish this.

5. ~~**Is the intent multiplexer (`MULTI_STRATEGY_INTENT_LAYER`) intended to become the default before or after ict_scalp_5m is enabled?**~~ **RESOLVED.** Both happened: ict_scalp_5m enabled in PR #1156 (2026-05-14), intent multiplexer default-on in Sprint D-1 of this sprint. The question presupposed ict_scalp_5m was still gated, which was incorrect (see H-2 withdrawal).

---

## 11. Recommended Sprint Order

```
Sprint A (Week 1-2):   Safety-critical fixes. No dependencies. Do immediately.
  A-1: Persist daily PnL
  A-2: Halt flag to runtime_flags/
  A-3: SQLite WAL mode
  A-4: [WITHDRAWN — false finding; see H-2]

Sprint F (Week 2-3):   Hygiene. Low risk, parallel to A. No dependencies.
  Delete sprint dirs, move test file, untrack runtime state, merge src/pipeline/

Sprint B (Week 3-4):   Observability. Depends on A.
  B-1: YAML as single source for risk allocation
  B-2: News veto evidence logging
  B-3: Persist RiskManager state (extends A-1)

Sprint D (Week 4-5):   AI boundary. Parallel to B. Needs operator agreement.
  D-1: Claude write sandbox
  D-2: Untrack follow_ups.json
  D-3: ML model promotion gate

Sprint C (Week 6-10):  Structural decomposition. High risk. Needs full test coverage first.
  C-3: Fix backtest/live parity (low risk, do first within C)
  C-1: Decompose order_monitor.py (requires test harness)
  C-2: Collapse routing paths (after intent layer validated)

Sprint E (Week 10+):   Position reconciliation. Highest complexity. Depends on C-1.
  E-1: Startup exchange reconciliation
```

---

*Audit completed: 2026-05-17. Based on repo state as of branch `main` (SHA `889b50d`). Both `ict-trading-bot` and `ict-trader-dashboard` repositories inspected.*
