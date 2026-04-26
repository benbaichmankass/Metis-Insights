# Claude Code Work Plan — ICT Trading Bot: VWAP Platform Expansion

**Repo:** `the-lizardking/ict-trading-bot`  
**Date:** April 2026  
**Scope:** Full platform upgrade to multi-strategy architecture, VWAP implementation, automated training pipeline, HuggingFace integration, and Telegram bot redesign.

---

## How to Use This Plan with Claude Code

Before starting any session, paste this into Claude Code:

```
Read CLAUDE.md and docs/architecture.md before proceeding.
Do not write code until you have proposed a plan and listed affected files.
After each task, summarize what changed, what still needs testing, and any manual verification steps.
```

Each phase below is a standalone Claude Code session. Complete phases in order — later phases depend on earlier ones.

---

## Phase 0 — Repo Audit and Context Setup

**Estimated effort:** 1–2 sessions  
**Goal:** Give Claude a complete and accurate picture of the repo before any code changes. Establish the context files that all future sessions depend on.

### Session 0-A: Repo Audit

**Prompt for Claude Code:**
```
Inspect the full repository. Do not write any code yet.

Produce a report covering:
1. Current folder structure and what each top-level folder contains.
2. All places where ICT strategy logic is hardcoded (file name + line reference).
3. All places where paper/live mode is hardcoded rather than config-driven.
4. The current Telegram bot command tree — list every command and what it does.
5. The current backtest functions exposed in the bot — list them specifically.
6. How the current runtime/deployment is structured (services, scripts, Oracle setup).
7. Any broken imports, dead code, or obvious bugs found during inspection.
8. The top 5 architectural blockers to adding VWAP as a clean second strategy.

Format the output as a markdown report and save it to docs/audit/repo_audit.md.
```

**Acceptance criteria:** `docs/audit/repo_audit.md` exists and contains all 8 sections.

---

### Session 0-B: Create Context Files

**Prompt for Claude Code:**
```
Based on the audit in docs/audit/repo_audit.md, create the following context and documentation files. Do not modify any existing code.

1. CLAUDE.md (repo root) — concise project rules, architecture summary, testing commands, and pointers to key docs. Include the rule: "Always read CLAUDE.md and relevant docs/ files before coding."

2. docs/architecture.md — current and target architecture. Include the 5-layer model (strategy, model, execution, runtime, bot). Include the target folder structure.

3. docs/strategies/ict.md — document the existing ICT strategy: parameters, entry/exit rules, signals, what the current model does.

4. docs/strategies/vwap_mean_reversion.md — scaffold this file from the work plan. Mark all sections as [TO BE IMPLEMENTED]. Do not invent strategy rules.

5. docs/bot.md — document the current Telegram bot command tree. Then document the target registry-driven command tree per the redesign spec.

6. docs/deployment.md — document the current Colab → GitHub → Oracle deployment workflow.

Commit message suggestion: "docs: add CLAUDE.md and architecture context files"
```

**Acceptance criteria:** All 6 files exist with substantive content. No code changed.

---

## Phase 1 — Multi-Strategy Architecture Refactor

**Estimated effort:** 4–7 sessions  
**Goal:** Refactor the repo from ICT-specific to a proper multi-strategy platform. This is the highest-priority structural task and must be completed before any VWAP code is written.

### Session 1-A: Strategy Registry

**Prompt for Claude Code:**
```
Implement the strategy registry module. Do not touch any existing strategy logic.

Create src/strategies/registry.py with:
- A StrategyConfig dataclass with fields: strategy_id (str), display_name (str), enabled (bool), execution_venue (str), instruments (list[str]), model_attached (bool), model_path (str | None).
- A StrategyRegistry class that loads strategy configs from configs/strategies/*.yaml.
- Methods: get(strategy_id), list_all(), list_enabled(), is_registered(strategy_id).
- A module-level singleton: registry = StrategyRegistry().

Create configs/strategies/ict.yaml and configs/strategies/vwap.yaml with correct fields.

Create src/strategies/base.py with:
- A BaseStrategy abstract class.
- Abstract methods: generate_signal(), validate_config(), get_strategy_id().
- A standard signal output dataclass: StrategySignal with fields: strategy_id, timestamp, symbol, direction, entry, stop, target, score, metadata.

Write unit tests in tests/test_registry.py covering: load, get, list, and missing strategy error.

After implementing, list every file changed or created.
```

**Acceptance criteria:** Registry loads both strategies from YAML. Unit tests pass. No existing strategy behavior changed.

---

### Session 1-B: Repo Folder Refactor

**Prompt for Claude Code:**
```
Refactor the repo folder structure to match the multi-strategy platform layout. Preserve all existing logic — this is a move-and-rename task, not a rewrite.

Target structure:
  src/strategies/ict/          ← move existing ICT strategy code here
  src/strategies/vwap/         ← create empty scaffold (do not implement yet)
  src/models/ict/              ← move existing ICT model code here
  src/models/vwap/             ← create empty scaffold
  src/execution/               ← move broker/exchange connectors here
  src/execution/ninjatrader/   ← create empty scaffold
  src/runtime/                 ← move live trader / session management here
  src/bot/                     ← move Telegram bot code here
  configs/strategies/          ← already done in 1-A
  docs/strategies/             ← already done in Phase 0
  reports/backtests/           ← create empty with .gitkeep

Steps:
1. Propose the full file move mapping (old path → new path) for every file.
2. Wait for confirmation before executing.
3. After moving, fix all broken imports throughout the repo.
4. Verify the bot still starts without errors.
5. Verify existing ICT logic is untouched.

Commit message suggestion: "refactor: multi-strategy folder structure"
```

**Acceptance criteria:** Repo starts cleanly. All imports resolve. ICT strategy behavior unchanged. No VWAP logic written yet.

---

### Session 1-C: Shared Interfaces and Output Schema

**Prompt for Claude Code:**
```
Define the shared interfaces and output schemas that all strategies and the runtime will use.

Create src/strategies/interfaces.py with:
- StrategySignal dataclass (from Session 1-A if not already done).
- BacktestResult dataclass: strategy_id, run_id, start_date, end_date, total_trades, win_rate, profit_factor, sharpe, max_drawdown, net_pnl, parameters (dict), notes (str).
- TradeRecord dataclass: strategy_id, trade_id, symbol, direction, entry_price, exit_price, entry_time, exit_time, pnl, stop, target, model_score (float | None), tags (list[str]).
- LiveSessionState dataclass: strategy_id, mode (paper|live), status (running|stopped|error), active_positions (list), last_signal_time, errors (list[str]).

Create src/runtime/report_schema.py that provides:
- Functions to serialize any of the above to JSON and to dict.
- A function to load a BacktestResult from a saved JSON file.

Update the existing ICT reporting code to emit TradeRecord and BacktestResult objects using these schemas. Do not change the ICT strategy logic itself.

Write unit tests in tests/test_interfaces.py.

Commit message suggestion: "feat: shared strategy interfaces and output schema"
```

**Acceptance criteria:** Schemas defined and importable. ICT uses them. Tests pass.

---

### Session 1-D: Comprehensive Repo Debug

**Prompt for Claude Code:**
```
Perform a comprehensive debugging pass of the entire repo. This is a quality audit, not a feature task.

Check for and fix:
1. All broken or circular imports.
2. Any function or class that is imported but never defined.
3. Hardcoded file paths that should be config-driven (flag them).
4. Hardcoded secrets or API keys (flag immediately — do not commit fixes, report them).
5. Exception handling that silently swallows errors with bare `except: pass`.
6. Functions longer than 100 lines that have no unit tests (flag them).
7. Any `TODO` or `FIXME` comments — list them all with file and line number.
8. Dead code: functions defined but never called anywhere.
9. Inconsistent logging (some modules using print, some using logger — standardize to logger).
10. Any import of a library not in requirements.txt or pyproject.toml.

For each issue found:
- Categorize as: CRITICAL (fix now), WARN (flag for review), or INFO (note for later).
- Fix all CRITICAL issues in this session.
- Write a report to docs/audit/debug_report.md.

Commit message suggestion: "fix: comprehensive debug pass — broken imports, silent errors, logging"
```

**Acceptance criteria:** `docs/audit/debug_report.md` exists. All CRITICAL issues resolved. Bot starts cleanly.

---

## Phase 2 — VWAP Strategy Implementation

**Estimated effort:** 4–6 sessions  
**Goal:** Implement the rules-based VWAP mean reversion strategy as a clean, independently testable module.

### Session 2-A: VWAP Signal Generator

**Prompt for Claude Code:**
```
Implement the rules-based VWAP signal generator in src/strategies/vwap/.

Read docs/strategies/vwap_mean_reversion.md before coding. Do not invent any rules not documented there.

Create src/strategies/vwap/__init__.py
Create src/strategies/vwap/strategy.py with:
- VWAPStrategy class extending BaseStrategy.
- Method: compute_vwap(bars) → rolling VWAP with standard deviation bands.
- Method: detect_setup(bars, vwap, bands) → returns SetupCandidate or None.
- Method: generate_signal(bars) → returns StrategySignal or None.
- Regime filter: trend_day_exclusion() and volatility_gate() methods.
- Time filter: valid_session_window() using configurable session hours.
- All parameters loaded from configs/strategies/vwap.yaml.

Create src/strategies/vwap/params.py with:
- VWAPParams dataclass: session_start, session_end, deviation_multipliers (list[float]), min_stretch_atr, trigger_bar_type, stop_buffer_ticks, target_at_vwap (bool), partial_exits (bool), max_trades_per_session, volatility_gate_atr_threshold.

The strategy must run in a pure rules-only mode with no model required.

Write unit tests in tests/strategies/test_vwap_signal.py covering:
- Setup detection with a mock bar series.
- No-signal output on a trend day.
- No-signal output outside session hours.
- Valid signal output with correct fields.

Commit message suggestion: "feat: rules-based VWAP signal generator"
```

**Acceptance criteria:** Strategy instantiates from config. Tests pass. Rules-only mode confirmed with no model dependency.

---

### Session 2-B: VWAP Trade Lifecycle and Logging

**Prompt for Claude Code:**
```
Extend the VWAP strategy module with trade lifecycle management and trace logging.

Add to src/strategies/vwap/strategy.py:
- on_entry(signal, fill_price) → records TradeRecord start.
- on_exit(reason, fill_price) → completes TradeRecord with PnL.
- on_time_stop() → forced flatten at session end.
- Invalidation logic: if price moves through stop before entry, cancel setup.

Create src/strategies/vwap/logger.py with:
- Structured trace logging for every bar evaluated.
- Log fields: bar_time, vwap, deviation_band, close, setup_detected (bool), signal_issued (bool), skip_reason (str | None).
- Output: JSON lines format to reports/backtests/vwap/trace_{date}.jsonl.

This trace logging is required so every skipped or accepted setup can be explained in post-analysis.

Write unit tests in tests/strategies/test_vwap_lifecycle.py.

Commit message suggestion: "feat: VWAP trade lifecycle and trace logging"
```

**Acceptance criteria:** Full trade lifecycle tracked. Trace log written per session. Tests pass.

---

### Session 2-C: Backtest Interface for VWAP

**Prompt for Claude Code:**
```
Build the backtest simulation interface for the VWAP strategy.

Create src/backtests/vwap_backtest.py with:
- VWAPBacktester class that accepts a bar dataset and VWAPParams.
- run(start_date, end_date) → runs bar-by-bar simulation, returns BacktestResult.
- Realistic execution assumptions: configurable slippage (ticks), commission per side, no lookahead.
- Parameter sweep method: sweep(param_grid) → returns list[BacktestResult] for sensitivity testing.
- Outputs: BacktestResult object, trade log (list[TradeRecord]), and summary report saved to reports/backtests/vwap/.

The backtest must operate independently of any model layer.

Create a reference dataset fixture in tests/fixtures/vwap_test_bars.csv (synthetic bar data, at least 100 bars with a valid VWAP mean reversion setup).

Write integration test in tests/backtests/test_vwap_backtest.py that runs the backtest end-to-end on the fixture data and verifies the output schema is correct.

Commit message suggestion: "feat: VWAP backtest simulation interface"
```

**Acceptance criteria:** Backtest runs on fixture data. Output matches BacktestResult schema. No lookahead bias.

---

## Phase 3 — VWAP AI Model

**Estimated effort:** 3–5 sessions  
**Goal:** Build a strategy-specific VWAP model that acts as a quality filter, with all training assets hosted on HuggingFace where possible.

### Session 3-A: Model Design and Feature Schema

**Prompt for Claude Code:**
```
Define the VWAP model specification. Do not write training code yet.

Create docs/models/vwap_model_spec.md with:
- Modeling objective: classify whether a VWAP setup should be taken (binary label).
- Label definition: 1 if trade reaches VWAP before stop, 0 otherwise. Label is computed post-bar-close with no lookahead.
- Feature schema (freeze the first version):
    * vwap_distance_pct: (close - vwap) / vwap
    * vwap_slope_5bar: slope of VWAP over last 5 bars
    * atr_normalized: ATR(14) / close
    * opening_range_position: where price is relative to opening range high/low
    * time_of_day_minutes: minutes since session open
    * persistence_above_below: count of consecutive bars on same side of VWAP
    * rejection_wick_ratio: upper/lower wick as fraction of bar range
    * volume_ratio: bar volume / 20-bar average volume
    * (optional, if available) delta_imbalance: buy volume - sell volume
- Leakage checklist: for each feature, confirm it uses only data available at bar close.
- Train/validation/test split: time-based (no random shuffle). Use first 70% for train, next 15% for validation, last 15% for test.
- Model family: gradient boosting (XGBoost or LightGBM) as first candidate.
- Probability thresholding: only take a signal if model_score >= configurable threshold (default 0.60).
- How model score interacts with rules: rules must fire first; model is a filter only.

Create src/models/vwap/spec.py with:
- VWAP_FEATURE_COLS list (frozen feature names in order).
- LABEL_COL = "reached_target".
- ModelConfig dataclass: threshold (float), version (str), huggingface_repo (str), local_cache_path (str).

Commit message suggestion: "docs: VWAP model spec and feature schema"
```

**Acceptance criteria:** Spec doc complete. Leakage checklist covers all features. Feature list frozen in code.

---

### Session 3-B: Feature Engineering Pipeline

**Prompt for Claude Code:**
```
Implement the feature engineering pipeline for the VWAP model.

Create src/models/vwap/features.py with:
- build_features(bars_df, vwap_series, atr_series) → returns DataFrame with all VWAP_FEATURE_COLS.
- Each feature function isolated and independently testable.
- Label builder: build_labels(bars_df, trade_records) → attaches reached_target column to setup rows.
- Dataset builder: build_dataset(bars_df, trade_records, vwap_series, atr_series) → returns full labeled DataFrame ready for training.

Create src/models/vwap/dataset_io.py with:
- save_dataset(df, version, local_path) → saves as parquet locally.
- upload_to_huggingface(local_path, hf_repo, hf_token) → pushes dataset to HuggingFace Hub using the datasets library. Repo format: {username}/vwap-training-data.
- load_from_huggingface(hf_repo, version, hf_token) → pulls dataset from HuggingFace Hub for training.
- Dataset versioning: each dataset saved with a version tag (date + parameter hash).

Required libraries: pandas, numpy, pyarrow, datasets (huggingface). Add to requirements.txt.

Write unit tests in tests/models/test_vwap_features.py. Use the fixture bars from Phase 2.

Commit message suggestion: "feat: VWAP model feature engineering and HuggingFace dataset IO"
```

**Acceptance criteria:** Features build correctly on fixture data. Dataset uploads and downloads from HuggingFace without error. Tests pass.

---

### Session 3-C: Model Training and HuggingFace Artifact Storage

**Prompt for Claude Code:**
```
Implement the VWAP model training pipeline. All model artifacts should be stored on HuggingFace to minimize local compute usage.

Create src/models/vwap/trainer.py with:
- VWAPModelTrainer class.
- train(dataset_path_or_hf_repo, model_config) → trains XGBoost or LightGBM classifier.
- Evaluation: produces classification report, ROC-AUC, precision/recall, and calibration plot.
- Experiment tracking: saves each run's params, metrics, and feature importances to reports/training/vwap/{run_id}.json.
- save_model(model, run_id, hf_repo, hf_token) → saves model artifact to HuggingFace Hub using huggingface_hub library. Repo format: {username}/vwap-model.
- load_model(hf_repo, version, hf_token, local_cache) → pulls model from HuggingFace Hub and caches locally. If local cache is fresh (< 24 hours), skip download.
- Model versioning: each model tagged with run_id and timestamp.

Create src/models/vwap/scorer.py with:
- VWAPScorer class: loads model from local cache (with HF fallback).
- score(features_row) → returns float probability [0, 1].
- is_above_threshold(score, threshold) → bool.
- The VWAP strategy should call scorer.score() after a setup is detected and only emit a signal if is_above_threshold() returns True.

Wire VWAPScorer into VWAPStrategy.generate_signal() with a model_enabled flag. When model_enabled=False, all valid setups pass through (rules-only mode).

Commit message suggestion: "feat: VWAP model training pipeline and HuggingFace artifact storage"
```

**Acceptance criteria:** Model trains on fixture data. Saves to and loads from HuggingFace. Scorer integrates with strategy. Rules-only mode still works.

---

## Phase 4 — Automated Training Pipeline

**Estimated effort:** 3–5 sessions  
**Goal:** Build a scheduled, automated training pipeline that pulls new data from randomly sampled timeframes, trains, backtests the new model vs. the current production model, decides whether to promote, and sends Telegram alerts at start and completion.

### Session 4-A: Automated Training Scheduler

**Prompt for Claude Code:**
```
Build the automated training pipeline for the VWAP model.

Create src/training/vwap_training_pipeline.py with:
- VWAPTrainingPipeline class.
- run_session(trigger_reason="scheduled") → full pipeline execution:
    1. SEND_START_ALERT: Send Telegram alert with session ID, trigger reason, and timestamp (see Session 4-C for alert spec).
    2. SAMPLE_TIMEFRAME: Randomly sample a training timeframe from the available historical data. Sampling rules:
       - Window length drawn uniformly from [90, 365] days.
       - Start date drawn randomly from available history, subject to minimum lookback.
       - Log the selected window to the run record.
    3. PULL_DATASET: Fetch or regenerate the feature dataset for the sampled window.
    4. UPLOAD_DATASET: Upload versioned dataset to HuggingFace (src/models/vwap/dataset_io.py).
    5. TRAIN_MODEL: Train new model candidate on the sampled dataset.
    6. BACKTEST_NEW: Run VWAPBacktester on a held-out recent test period using the new model as filter.
    7. BACKTEST_CURRENT: Run VWAPBacktester on the same test period using the current production model.
    8. COMPARE: Compare BacktestResult objects on: net_pnl, win_rate, profit_factor, sharpe, max_drawdown.
    9. PROMOTE_DECISION: Promote new model to production if ALL of: net_pnl_new > net_pnl_current, profit_factor_new > profit_factor_current, max_drawdown_new <= max_drawdown_current * 1.10.
    10. IF PROMOTED: Upload new model to HuggingFace as production version. Update configs/models/vwap_production.yaml with new version tag.
    11. SEND_RESULT_ALERT: Send Telegram alert with full results (see Session 4-C).

- All pipeline steps wrapped in try/except with error logging. Pipeline failure sends an error alert, does not crash the system.
- Each pipeline run recorded in reports/training/vwap/pipeline_runs.jsonl with: run_id, trigger, sampled_window_start, sampled_window_end, new_model_metrics, current_model_metrics, promoted (bool), promotion_reason (str).

Commit message suggestion: "feat: automated VWAP training pipeline with random timeframe sampling"
```

**Acceptance criteria:** Pipeline runs end-to-end. Promotion logic tested with mock backtest results. Pipeline failure does not crash system.

---

### Session 4-B: Training Scheduler (Cron/Service)

**Prompt for Claude Code:**
```
Set up the automated scheduling layer for the training pipeline.

Create src/training/scheduler.py with:
- TrainingScheduler class.
- schedule_periodic(interval_hours=168) → runs training pipeline every N hours (default: weekly).
- Uses APScheduler or Python schedule library (add to requirements.txt).
- Scheduler runs as a background thread safe to run alongside the live trader.
- manual_trigger() → allows pipeline to be triggered on demand (wired to bot in Phase 5).
- Logs all schedule events to logs/training_scheduler.log.

Create scripts/run_training_scheduler.py — standalone entrypoint to run the scheduler as a process.

Create a systemd service file at deploy/services/training-scheduler.service for Oracle VM deployment.

Add the scheduler to docs/deployment.md as a new service.

Commit message suggestion: "feat: automated training scheduler with systemd service"
```

**Acceptance criteria:** Scheduler runs periodically. Can be triggered manually. Service file is valid systemd syntax.

---

### Session 4-C: Telegram Training Alerts

**Prompt for Claude Code:**
```
Implement the Telegram alert messages for training pipeline events.

Add to src/bot/alerts.py (create if not exists):

Function: send_training_start_alert(run_id, trigger_reason, sampled_window_start, sampled_window_end, bot_token, chat_id)
Message format:
  🤖 Training Session Started
  Run ID: {run_id}
  Trigger: {trigger_reason}
  Training window: {sampled_window_start} → {sampled_window_end}
  Strategy: VWAP
  Started at: {timestamp}

Function: send_training_result_alert(run_id, promoted, new_metrics, current_metrics, bot_token, chat_id)
Message format:
  ✅ Training Session Complete   (or ⚠️ Training Session Complete — Not Promoted)
  Run ID: {run_id}
  Promoted: Yes / No
  
  New model:
    Net PnL: {net_pnl}
    Win Rate: {win_rate}%
    Profit Factor: {profit_factor}
    Sharpe: {sharpe}
    Max DD: {max_drawdown}%
  
  Current model:
    Net PnL: {net_pnl}
    Win Rate: {win_rate}%
    Profit Factor: {profit_factor}
    Sharpe: {sharpe}
    Max DD: {max_drawdown}%
  
  Decision: {promotion_reason}

Function: send_training_error_alert(run_id, error_message, bot_token, chat_id)
Message format:
  🚨 Training Session Failed
  Run ID: {run_id}
  Error: {error_message}
  Time: {timestamp}

All functions use the python-telegram-bot library already in the repo.
Bot token and chat ID loaded from environment variables: TELEGRAM_BOT_TOKEN, TELEGRAM_ALERT_CHAT_ID.

Wire these into VWAPTrainingPipeline at the correct steps (1 and 11 from Session 4-A).

Write unit tests in tests/bot/test_training_alerts.py using mock Telegram client.

Commit message suggestion: "feat: Telegram alerts for training pipeline start and completion"
```

**Acceptance criteria:** Alerts fire at pipeline start, completion, and on error. Message format matches spec. Tests pass with mock client.

---

## Phase 5 — Telegram Bot Redesign

**Estimated effort:** 3–5 sessions  
**Goal:** Convert the bot from mode-centric to strategy-aware. Remove backtest commands. Add training trigger. Make the command tree registry-driven.

### Session 5-A: Remove Backtest Commands

**Prompt for Claude Code:**
```
Remove all backtest-related commands from the Telegram bot.

Find all backtest commands in src/bot/ — reference docs/bot.md and the audit from Phase 0.
Expected commands to remove include but may not be limited to: /latest_backtest, /backtest, /run_backtest, or any variant.

For each command:
1. Remove the command handler registration.
2. Remove the handler function.
3. Remove any backtest-specific keyboard buttons.
4. Update the /help or /start command text to not mention backtest commands.
5. If the command writes to or reads from a database table used only by that command, flag it but do not drop the table yet.

After removal:
- Confirm the bot starts without errors.
- Confirm no remaining references to the removed commands exist in the codebase.
- Update docs/bot.md to reflect the removal.

Commit message suggestion: "feat: remove backtest commands from Telegram bot"
```

**Acceptance criteria:** No backtest commands exist in bot. Bot starts cleanly. Docs updated.

---

### Session 5-B: Strategy-Aware Command Tree

**Prompt for Claude Code:**
```
Redesign the Telegram bot command tree to be strategy-aware and registry-driven.

Read docs/bot.md for the target design before coding.

The new interaction model:
  /start → Show strategy selection menu: [ICT] [VWAP]
  
  After strategy selected (strategy_id stored in user session):
    Main menu for selected strategy:
      [▶ Start Paper]   [▶ Start Live]
      [⏹ Stop]          [📊 Status]
      [📍 Positions]    [🔔 Signals]
      [🤖 Model Info]   [📋 Logs]
      [⬅ Back]

  /status → shows status for all active strategies
  /stop_all → emergency stop all running strategies

Implementation requirements:
- Replace all hardcoded strategy references with strategy_id from the registry.
- All callback payloads include: {"strategy_id": "ict"|"vwap", "action": "...", "mode": "paper"|"live"}.
- Command handlers use strategy_id to dispatch to the correct strategy module.
- Adding a new strategy in the future must require only: (a) registering it in configs/strategies/ and (b) no changes to command handler code.
- Strategy submenu options load from registry.list_enabled() dynamically.
- Add [🔁 Trigger Training] button in VWAP model info submenu → calls scheduler.manual_trigger().

Update docs/bot.md with example conversation flows for both ICT and VWAP.

Commit message suggestion: "feat: registry-driven strategy-aware Telegram bot"
```

**Acceptance criteria:** Bot dynamically loads strategies from registry. Both ICT and VWAP submenus work. Training can be manually triggered from bot. Adding a third strategy requires only a YAML config entry.

---

## Phase 6 — NinjaTrader Integration

**Estimated effort:** 4–7 sessions  
**Goal:** Define and implement the integration path for VWAP execution in NinjaTrader.

### Session 6-A: Integration Design

**Prompt for Claude Code:**
```
Create the NinjaTrader integration design document. Do not write any NinjaScript code yet.

Create docs/execution/ninjatrader_integration.md covering:
1. Decision: does NinjaTrader host full execution logic, or does it receive decisions from the Python platform? Document the chosen approach and rationale.
2. How strategy_id maps to NinjaTrader strategy instances and templates.
3. Data handoff specification: what data moves from Python → NinjaTrader (signals, parameters) and what moves back (fills, errors, position state).
4. Parameter mapping: VWAPParams fields → NinjaTrader strategy parameters.
5. Paper trading activation workflow step by step.
6. Error handling: what happens if NinjaTrader disconnects, misses a signal, or sends an unexpected fill.
7. State reconciliation: how to detect and resolve mismatches between Python-side expected positions and NinjaTrader actual positions.
8. Log flow: how fills and errors from NinjaTrader are written back to the shared platform log.
9. First rollout: chart-attached strategy or Control Center strategy — which and why.

Also create src/execution/ninjatrader/__init__.py and src/execution/ninjatrader/adapter.py as a scaffold with stubs for:
- send_signal(signal: StrategySignal) → None
- get_positions() → list[dict]
- get_fills(since: datetime) → list[dict]
- flatten_all() → None

Commit message suggestion: "docs: NinjaTrader integration design and execution adapter scaffold"
```

**Acceptance criteria:** Design doc complete. Adapter scaffold in place with stubs.

---

### Session 6-B: NinjaTrader Execution Adapter

**Prompt for Claude Code:**
```
Implement the NinjaTrader execution adapter based on the design in docs/execution/ninjatrader_integration.md.

Implement src/execution/ninjatrader/adapter.py:
- Connection via the chosen integration method (TCP socket, file-based signal, or HTTP — follow the design doc).
- send_signal(signal): serialize StrategySignal and transmit to NinjaTrader.
- get_positions(): query current open positions.
- get_fills(since): retrieve fill records since a given timestamp.
- flatten_all(): send flatten/close-all command.
- Reconnect logic: if connection drops, attempt reconnect up to 3 times with backoff before raising.
- All actions logged with strategy_id preserved.

Create src/execution/ninjatrader/state_reconciler.py with:
- reconcile(expected_positions, actual_positions) → list of discrepancies.
- heal(discrepancies) → attempt to correct mismatches (flatten unexpected positions).

Wire the adapter into the VWAP strategy's execution path. When a StrategySignal is emitted and mode=paper or mode=live, it routes through this adapter.

Add environment variables: NINJATRADER_HOST, NINJATRADER_PORT to .env.example.

Commit message suggestion: "feat: NinjaTrader execution adapter"
```

**Acceptance criteria:** Adapter connects and sends signals in paper mode. Reconnect logic tested. State reconciler identifies discrepancies correctly.

---

## Phase 7 — Live Trader Framework

**Estimated effort:** 4–7 sessions  
**Goal:** Extend the runtime so it can manage multiple strategies safely, with per-strategy risk controls and kill switches.

### Session 7-A: Multi-Strategy Runtime

**Prompt for Claude Code:**
```
Extend the live trader runtime to support multiple strategies running simultaneously.

Replace any hardcoded ICT assumptions in src/runtime/ with strategy-aware equivalents.

Create or update src/runtime/orchestrator.py with:
- StrategySession dataclass: strategy_id, mode, process/thread handle, config, state (LiveSessionState).
- start_session(strategy_id, mode) → validates config, launches strategy session, registers in session table.
- stop_session(strategy_id) → sends stop signal, waits for clean shutdown, updates state.
- stop_all() → emergency stop for all sessions.
- get_status(strategy_id) → returns LiveSessionState.
- get_all_status() → returns all active sessions.
- Only approved (strategy_id, instrument, mode) combinations can be launched (validated from registry and config).
- Restart-safe: session state persisted to disk so crashes can be detected and recovered.

Create src/runtime/risk_manager.py with:
- Per-strategy risk limits loaded from configs/strategies/{strategy_id}.yaml.
- check_entry(signal, session_state) → bool: blocks entry if daily loss limit or max positions reached.
- on_fill(fill, session_state) → updates running PnL tracker.
- on_session_end(session_state) → writes final session risk report.
- kill_switch(strategy_id) → immediately blocks all new entries for that strategy.

Commit message suggestion: "feat: multi-strategy runtime orchestrator and risk manager"
```

**Acceptance criteria:** Two strategy sessions can run simultaneously. Risk manager blocks entries correctly. Kill switch works. Session state persists across restart.

---

## Phase 8 — Deployment and Operations

**Estimated effort:** 2–4 sessions  

### Session 8-A: Deployment Update

**Prompt for Claude Code:**
```
Update deployment configuration for the multi-strategy platform.

Update deploy/ with:
- deploy/services/ict-strategy.service — systemd unit for ICT strategy session.
- deploy/services/vwap-strategy.service — systemd unit for VWAP strategy session.
- deploy/services/telegram-bot.service — updated unit for the redesigned bot.
- deploy/services/training-scheduler.service — from Phase 4-B.
- deploy/services/runtime-orchestrator.service — for the shared orchestrator.

Update deploy/scripts/deploy.sh to:
- Accept a --strategy flag so individual strategies can be deployed/restarted independently.
- Reload systemd and restart only the affected service.
- Run a post-deploy health check that confirms the bot responds to /status within 30 seconds.

Update docs/deployment.md with:
- The full multi-strategy service architecture diagram (text/ASCII).
- Start/stop/restart runbook for each service.
- Rollback procedure: how to revert to the previous model version on HuggingFace if a promoted model underperforms.
- Health check commands for each service.

Commit message suggestion: "ops: multi-strategy deployment scripts and service files"
```

**Acceptance criteria:** All service files are valid systemd syntax. Deploy script handles --strategy flag. Docs include rollback procedure.

---

## Phase 9 — Validation and Rollout

**Estimated effort:** 3–5 sessions + 1–3 weeks calendar

### Session 9-A: Paper Trading Shakeout Checklist

**Prompt for Claude Code:**
```
Create the paper trading validation checklist and observability setup.

Create docs/ops/paper_trading_checklist.md with sign-off criteria for:
- VWAP signals firing at the correct times (checked against manual chart review).
- NinjaTrader receiving signals and placing paper orders correctly.
- Bot commands returning correct status, position, and signal data for VWAP.
- Training pipeline running on schedule and sending correct Telegram alerts.
- No cross-strategy interference in logs, risk state, or runtime state.
- ICT and VWAP running simultaneously without session conflicts.
- Kill switch confirmed for each strategy.
- Restart recovery confirmed (kill the process, restart, verify state is recovered).

Create src/runtime/health_monitor.py with:
- check_strategy_health(strategy_id) → returns a HealthReport with: last_heartbeat, signal_count_today, fill_count_today, error_count_today, model_last_loaded.
- Expose health summary to the bot's /status command.

Commit message suggestion: "ops: paper trading checklist and health monitor"
```

**Acceptance criteria:** Checklist document complete. Health monitor returns data for both strategies.

---

## Completion Criteria Summary

| Milestone | Done When |
|-----------|-----------|
| Architecture | New strategy registered via YAML only, no code changes required |
| VWAP spec | Another developer can implement it without guessing rule details |
| Repo refactor | ICT and VWAP are independent modules, no cross-imports |
| Debug pass | No silent exceptions, no broken imports, unified logging |
| VWAP engine | Rules-only backtest produces reproducible results |
| Model pipeline | Training runs automatically, artifacts on HuggingFace |
| Automated training | Pipeline runs on schedule, promotes correctly, alerts fire |
| Bot redesign | Adding a third strategy requires only a YAML config file |
| NinjaTrader | Paper fills, logs, and shutdown verified end-to-end |
| Live rollout | System can start, stop, monitor, and recover all strategies safely |

---

## Key Rules for Every Claude Code Session

1. Always read `CLAUDE.md` before starting.
2. Always propose a plan and list affected files before writing code.
3. Never hardcode secrets or credentials.
4. Never invent strategy rules not documented in the spec.
5. Never remove a rules-only mode from a strategy.
6. Prefer registry/config-driven patterns over `if strategy == "ict"` branching.
7. After every session: summarize what changed, what needs testing, and any manual steps required.
8. For model code: explicitly state assumptions, leakage risks, and split methodology.
9. For execution and risk code: require explicit human review before any live deployment.
10. Keep changes small and reviewable — prefer multiple focused commits over one giant change.
