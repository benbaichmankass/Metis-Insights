# ICT Bot Next Sprint Plan: VWAP Stabilization + Google AI Studio Workflow

Date: 2026-04-27
Owner: Ben Baichman-Kass
Project: `the-lizardking/ict-trading-bot`

## Sprint Objective

Stabilize the VWAP dry-run path, harden logging around secrets, and formalize how Google AI Studio, Colab, Claude Code, and Oracle work together without wasting paid compute.

The previous sprint proved that:

- `vwap_btcusd_dry_run` renders successfully from the encrypted Google Drive master secrets file.
- VWAP runtime support was merged in PR #17.
- An isolated Oracle staging checkout exists at `/home/ubuntu/ict-trading-bot-vwap-staging`.
- A separate `ict-vwap-dry-run.service` can start from staging without touching the existing live trader.
- Dry-run safety flags are correct: `DRY_RUN=true`, `ALLOW_LIVE_TRADING=false`, `STRATEGY=vwap`.
- The one-shot smoke test generated a simulated order, not a live order.
- The continuous VWAP service then failed on zero-volume candle data, so it should remain stopped until fixed.
- Telegram tokens appeared in HTTP logs during one smoke test, so token rotation was required and logging hardening is needed.

The Google AI Studio thread also proved that Studio is useful for offline analytical prototypes. The ICT Signals Analyzer handled 1,361 rows from April 20-27, 2026 and detected approximately 244 FVGs plus 20 order blocks with an interactive Plotly candlestick overlay. This should become a repeatable research/backtest workflow, not runtime trading code yet.

## Sprint Guardrails

Do not do these until explicitly approved:

- Do not reset the VM.
- Do not stop the existing live trader.
- Do not overwrite `/home/ubuntu/ict-trading-bot`.
- Do not place live orders.
- Do not paste secrets into Claude, Studio, GitHub, notebooks, or chat.
- Do not run long training/backtests inside Claude Code.

Default execution model:

- Claude Code: small focused repo PRs, tests, docs, safety gates.
- Google AI Studio: offline prototypes, analysis notebooks, prompt-to-notebook generation, strategy research.
- Colab: run notebooks, generate CSV reports, execute safe VM/SSH cells, run offline simulations.
- Oracle: staging-only runtime validation, then separate dry-run services.
- Hugging Face: later for datasets/models, not needed in this sprint unless a dataset archive becomes useful.

## Milestones

### Milestone 1: Stop or keep stopped the broken VWAP service

Goal: Ensure `ict-vwap-dry-run.service` is not repeatedly throwing zero-volume errors every 15 minutes.

Owner: Colab / Oracle

Status: Should be done before further runtime testing.

Copy-ready Colab/SSH task:

```text
Stop only `ict-vwap-dry-run.service` if it is still running. Do not stop `ict-trader-live.service`, `ict-telegram-bot.service`, or `ict-git-sync.service`. Confirm the existing live services remain active. Do not reset the VM.
```

Success criteria:

- `ict-vwap-dry-run.service` is inactive.
- Existing live services remain active.
- No VM reset.

### Milestone 2: Claude PR 1, VWAP invalid-data no-trade fix

Goal: Make zero-volume, missing-volume, and empty candle data produce safe no-trade instead of an unhandled exception.

Owner: Claude Code

Why first: This is the direct blocker found in Oracle staging logs.

Claude-sized prompt:

```text
Focused stabilization PR for repo:

https://github.com/the-lizardking/ict-trading-bot

Context:
A separate VWAP dry-run staging service ran from `/home/ubuntu/ict-trading-bot-vwap-staging` with:

DRY_RUN=true
ALLOW_LIVE_TRADING=false
STRATEGY=vwap
MODE=PAPER
BYBIT_TESTNET=false
SYMBOL=BTCUSD
TIMEFRAME=1m

The service started, but the first continuous tick failed with:

ValueError: VWAP cannot be computed: total volume across all candles is zero or negative. Check that the candle data contains valid volume information.

Stack:
- src.runtime.pipeline.vwap_signal_builder
- strategies.vwap_signal_builder.build_vwap_signal
- strategies.vwap_signal_builder.compute_vwap

Google AI Studio offline simulation confirmed:
- Normal Data -> simulated -> PASS
- Zero Volume -> no_trade -> PASS when wrapped safely
- Missing Volume Column -> no_trade -> PASS when wrapped safely
- Empty Candles -> no_trade -> PASS when wrapped safely

Goal:
Create a small PR that makes VWAP invalid candle data fail safe as no-trade instead of raising an unhandled exception.

Strict scope:
- Do not call Bybit.
- Do not call Telegram.
- Do not SSH.
- Do not place orders.
- Do not reset the VM.
- Do not change env rendering.
- Do not change systemd.
- Do not implement Telegram logging hardening in this PR.
- Do not solve BTCUSD vs BTCUSDT mapping in this PR unless required for tests.
- Keep this PR focused.

Implementation requirement:
When VWAP cannot be computed because candle data is invalid, empty, missing volume, or has total volume <= 0, the strategy should return a safe no-trade result and allow the tick to complete.

Preferred behavior:
- Reason text: `VWAP skipped: total candle volume is zero or negative` or `VWAP skipped: candle data is empty or invalid`.
- No unhandled exception for expected bad market data.
- No order placement for no-trade.
- Preserve current behavior for normal valid candles.

Files likely involved:
- strategies/vwap_signal_builder.py
- src/runtime/pipeline.py if needed
- tests/test_vwap_strategy.py
- maybe tests/test_runtime_pipeline.py if no-trade handling needs coverage

Please inspect the repo’s existing no-trade signal/result format before implementing. Match existing style.

Tests:
Add focused tests for:
1. zero-volume candles return no-trade and do not raise
2. missing volume column returns no-trade and does not raise
3. empty DataFrame returns no-trade and does not raise
4. normal nonzero-volume candles still produce the existing expected VWAP behavior
5. if easy, pipeline no-trade path does not call order placement

Checks:
- python -m py_compile on changed Python files
- python scripts/secret_scan.py
- focused pytest for VWAP/runtime tests touched

Deliverable:
Open a PR titled: `Handle invalid VWAP candle data as no-trade`

PR body:
- What changed
- Why it is safe
- Tests run
- What was intentionally not done
```

Success criteria:

- PR opened and manually merged.
- Zero-volume VWAP test passes.
- No Bybit/Telegram calls in tests.

### Milestone 3: Claude PR 2, logging hardening for Telegram/httpx

Goal: Prevent Telegram bot tokens and tokenized URLs from appearing in logs.

Owner: Claude Code

Why second: Token was exposed in smoke-test output; user rotated it, but code should prevent recurrence.

Claude-sized prompt:

```text
Security hardening task for repo:

https://github.com/the-lizardking/ict-trading-bot

Context:
During a staging one-shot VWAP dry-run smoke test, the app logged Telegram API URLs containing the bot token. The log lines came from httpx and included URLs like:

https://api.telegram.org/bot<TOKEN>/getMe
https://api.telegram.org/bot<TOKEN>/sendMessage

The token has been rotated manually by the user.

Your task:
Create a focused security hardening PR to prevent Telegram tokens and other secrets from appearing in logs.

Requirements:
1. Do not call Telegram.
2. Do not call Bybit.
3. Do not place orders.
4. Do not SSH.
5. Do not reset VM.
6. Do not print or inspect secret values.
7. Do not change trading logic except notification/logging safety.

Implementation goals:
1. Suppress or sanitize noisy httpx/httpcore logs so full Telegram URLs are not logged.
2. Add a log redaction utility if needed.
3. Ensure any logged URL matching `https://api.telegram.org/bot...` is redacted to something like `https://api.telegram.org/bot<REDACTED>/sendMessage`.
4. Make sure env/token values are never printed by notification code.
5. Add tests proving Telegram bot tokens are redacted from logs.
6. Add/update docs/claude/security-secrets.md and docs/claude/debug-memory.md with this lesson:
   - httpx can log Telegram bot tokens inside request URLs
   - tokens exposed in logs must be rotated
   - runtime tests should avoid verbose third-party HTTP logging

Suggested areas to inspect:
- src/runtime/notify.py
- src/runtime/signal_notifications.py
- src/main.py
- logging setup
- any Telegram bot/client code
- tests around notifications/logging

Checks to run:
- python -m py_compile on changed Python files
- python scripts/secret_scan.py
- focused pytest for new logging/redaction tests

Deliverable:
Open a PR titled: `Harden logging redaction for Telegram tokens`

PR body should include:
- What was changed
- How token leakage is prevented
- Tests run
- What was intentionally not done
```

Success criteria:

- PR opened and manually merged.
- Unit test proves redaction.
- Later smoke-test journal has no `api.telegram.org/bot` token URL.

### Milestone 4: Claude PR 3, clarify Bybit and mode logging

Goal: Remove confusing `LIVE BYBIT` wording and make runtime logs distinguish market-data environment from trading safety.

Owner: Claude Code

Prompt:

```text
Focused logging cleanup task for repo:

https://github.com/the-lizardking/ict-trading-bot

Context:
VWAP dry-run logs printed `LIVE BYBIT`, which is confusing because the service was safely dry-run:

DRY_RUN=true
ALLOW_LIVE_TRADING=false
BYBIT_TESTNET=false
MODE=PAPER

The phrase appears to mean Bybit mainnet market data, not live order placement.

Goal:
Replace ambiguous runtime log/print messages like `LIVE BYBIT` with explicit, safe wording.

Requirements:
- Do not change trading logic.
- Do not call Bybit or Telegram.
- Do not SSH.
- Do not reset VM.
- Do not place orders.

Preferred log wording:
- `Bybit market data environment: mainnet`
- `Trading execution mode: dry-run`
- `Live order placement allowed: false`

Add/adjust tests if there are existing logging tests. Otherwise keep the PR tiny and document manual verification.

Checks:
- python -m py_compile on changed Python files
- python scripts/secret_scan.py
- focused pytest if relevant

Deliverable:
Open a PR titled: `Clarify Bybit dry-run runtime logging`
```

Success criteria:

- No `LIVE BYBIT` ambiguity in new runtime logs.
- Dry-run safety messages are explicit.

### Milestone 5: Google AI Studio task, ICT Signals Analyzer follow-up

Goal: Turn the successful Studio analysis of 1,361 rows into a reusable offline research notebook for FVG/order-block validation and candidate backtests.

Owner: Google AI Studio

Context from previous Studio thread:

- Dataset spans April 20-27, 2026.
- 1,361 rows.
- Likely EURUSD or similar, prices around 1.168-1.179.
- 244 FVGs detected: about 120 bullish, 124 bearish.
- 20 order blocks detected: 10 bullish, 10 bearish.
- Interactive Plotly candlestick chart generated successfully.
- All gaps/unfilled and OBs untested, ready for backtesting.

Studio prompt:

```text
Create a Google Colab notebook for offline ICT signal validation and simple backtesting.

Context:
A previous ICT Signals Analyzer successfully processed 1,361 rows of OHLCV-style data from April 20-27, 2026. It detected approximately 244 FVGs and 20 order blocks and generated an interactive Plotly candlestick chart.

Goal:
Build a copy-ready Colab notebook that turns this into a repeatable offline research workflow.

Important safety constraints:
- Do not call Bybit.
- Do not call Telegram.
- Do not require API keys.
- Do not place orders.
- Do not SSH.
- Do not push to GitHub.
- This is research only.

Notebook requirements:
1. Upload or load a CSV with OHLCV/time columns.
2. Normalize column names.
3. Detect bullish/bearish FVGs.
4. Detect bullish/bearish order blocks using a simple configurable strong-body rule.
5. Mark whether FVGs are filled later.
6. Mark whether OBs are retested later.
7. Produce an interactive Plotly chart with candlesticks, FVG overlays, and OB overlays.
8. Produce summary tables:
   - FVG count by side
   - FVG fill rate by side
   - average bars-to-fill
   - OB count by side
   - OB retest rate by side
9. Add a very simple backtest section:
   - entry on FVG fill or OB retest
   - configurable stop/target in R multiples
   - win rate, average R, max drawdown approximation
10. Export results to CSV files:
   - ict_signals_summary.csv
   - fvg_events.csv
   - order_block_events.csv
   - simple_backtest_results.csv

Output style:
- Give complete Colab notebook cells.
- Use markdown headings.
- Keep explanations short.
- No secrets or API calls.

Final cell:
Print a `Recommendation for Claude PR` section explaining what parts, if any, are worth moving into the repo later.
```

Success criteria:

- Notebook runs offline.
- Outputs CSVs and Plotly chart.
- Produces a recommendation for Claude about repo-worthy code.

### Milestone 6: Colab task, post-fix VWAP staging redeploy and smoke test

Goal: After Milestones 2 and 3 are merged, update staging, rerun smoke test, then decide whether to restart `ict-vwap-dry-run.service`.

Owner: Colab / Oracle

Steps:

1. Pull latest `origin/main` into `/home/ubuntu/ict-trading-bot-vwap-staging`.
2. Re-render `vwap_btcusd_dry_run` env from encrypted Drive secrets.
3. Copy env to staging.
4. Run one-shot `LOOP=false` test.
5. Confirm invalid-volume candles produce no-trade, not exception.
6. Confirm no Telegram token URL appears in logs.
7. Restart `ict-vwap-dry-run.service` only if the one-shot test is clean.

Success criteria:

- One-shot test exits 0.
- No unhandled exception.
- No token leakage.
- Order result is either `simulated` or `no_trade`, never live.
- Existing live services remain active.

### Milestone 7: Live repo reconciliation, later

Goal: Resolve the local hotfix blocking pulls in `/home/ubuntu/ict-trading-bot`.

Current live repo state:

```diff
- candles = exchange.fetch_ohlcv(symbol, "1m", limit=100)
+ candles = exchange.get_ohlcv(symbol, "1m", limit=100)
```

Do not do this until VWAP staging is stable.

Claude prompt later:

```text
Inspect whether the VM-only local hotfix `fetch_ohlcv -> get_ohlcv` in `src/runtime/pipeline.py` is already represented in current main or still needed. Create a PR if needed, or document that it is obsolete. Do not touch the VM. Do not stop services. Do not place orders.
```

## Parallel Work Plan

Run these in parallel after Claude reset:

1. Claude Code PR 1: invalid VWAP data -> no-trade.
2. Google AI Studio: offline ICT backtest notebook.
3. Colab: keep artifacts organized and avoid running Oracle VWAP service until fix is merged.

Then:

1. Claude Code PR 2: Telegram/httpx log redaction.
2. Colab: staging redeploy and one-shot smoke test.
3. Oracle: restart `ict-vwap-dry-run.service` only after staging test passes.

## Definition of Done for Next Sprint

The sprint is complete when:

- VWAP zero-volume candles no longer crash runtime.
- Telegram/httpx logs no longer leak bot tokens.
- `LIVE BYBIT` wording is clarified or scheduled as the next tiny cleanup.
- Studio-generated ICT research notebook exports reusable CSVs.
- Oracle staging VWAP dry-run service can run at least two cycles without unhandled exceptions.
- Existing live trader remains untouched unless separately approved.

## Notes for Ben

The VM reset is no longer the best immediate goal. The safer architecture is now:

- Existing live service remains on `/home/ubuntu/ict-trading-bot`.
- VWAP dry-run runs separately from `/home/ubuntu/ict-trading-bot-vwap-staging`.
- After stability is proven, decide whether VWAP stays as a separate service or becomes part of the main deployment process.
