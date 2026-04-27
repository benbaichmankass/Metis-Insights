# Debug memory

Use this file for recurring bugs so Claude does not rediscover them.

## Known patterns

- Use `PYTHONPATH=.` when running tests from repo root.
- Runtime validation tests may fail if function signatures drift from tests.
- Telegram-related tests need the `telegram` package or must be skipped/mocked.
- `.env` loading tests need `python-dotenv`.
- Never test live exchange behavior without explicit paper/live-mode instructions.

## Anti-patterns

- Running full backtests to verify a docs or config change.
- Hardcoding API keys in smoke scripts.
- Creating one-off notebooks without saving outputs to Drive/HF.

## Durable findings

### Bybit subaccount routing
- The Bybit REST API does **not** support routing a request to a subaccount via a
  parent-account API key. There is no per-call subaccount selector.
- To trade on a specific subaccount, use API keys created **inside that subaccount**.
- In the master secrets file, the `vwap_strategy` subaccount keys live under
  `bybit.vwap_strategy.api_key` / `api_secret`. The renderer maps these to
  `BYBIT_API_KEY` / `BYBIT_API_SECRET` for the `vwap_btcusd_*` profiles.
- Do not try to derive subaccount credentials from `bybit.live.*` parent-account keys.

### 2026-04-27: STRATEGY=vwap routing and offline VWAP signal builder
- Cause: Pipeline had no handler for `STRATEGY=vwap`; it fell through to killzone.
- Fix: Added `vwap_signal_builder` to `src/runtime/pipeline.py` and routed `STRATEGY=vwap`.
  Pure computation lives in `strategies/vwap_signal_builder.py` — no exchange calls,
  no ML dependency, offline-safe.
- Check: `PYTHONPATH=. pytest tests/test_vwap_strategy.py -q`

### 2026-04-27: MODE=LIVE without ALLOW_LIVE_TRADING passes validate_startup
- Cause: `validate_startup` only checked `DRY_RUN=false` requires `ALLOW_LIVE_TRADING=true`.
  A config with `MODE=LIVE` + `DRY_RUN=true` + `ALLOW_LIVE_TRADING=false` passed validation
  even though the intent was clearly live.
- Fix: Added a second interlock: `MODE=LIVE` requires `ALLOW_LIVE_TRADING=true` at startup,
  regardless of `DRY_RUN`.
- Check: `PYTHONPATH=. pytest tests/test_vwap_strategy.py::TestLiveSafetyGate -q`

### 2026-04-27: Telegram bot token leaked into logs via httpx

- Cause: `python-telegram-bot` uses `httpx` internally. At `INFO` level, httpx logs full request URLs including the bot token (`https://api.telegram.org/bot<TOKEN>/sendMessage`). Triggered during a VWAP dry-run smoke test.
- Fix: Added `src/utils/log_redact.py` with `RedactingFilter` (installed on root logger at startup) and `suppress_httpx_logging()` (raises httpx/httpcore to WARNING). `alert_manager.py` `print()` calls replaced with `logger`. See `docs/claude/security-secrets.md` for full details.
- Check: `PYTHONPATH=. pytest tests/test_log_redaction.py -q`
- Lesson: Never run smoke tests at INFO log level without first suppressing httpx/httpcore. Any new Telegram client code must call `suppress_httpx_logging()` before sending.

### 2026-04-27: deploy_pull_restart.sh restarted ict-bot.service, not ict-trader-live.service
- Cause: Script was written when `ict-bot.service` was the primary trading unit. `ict-trader-live.service` was added later but the deploy script was never updated.
- Effect: Every `ict-git-sync` auto-deploy left `ict-trader-live.service` running stale code; restarts had to be done manually.
- Fix: Changed `scripts/deploy_pull_restart.sh` and `deploy/ict-telegram-bot.service` to reference `ict-trader-live.service` instead of `ict-bot.service`.
- Check: Confirm `sudo systemctl status ict-trader-live.service` shows the new code after the next git-sync run.

## Add new entries here

Use this format:

```md
### YYYY-MM-DD: symptom
- Cause:
- Fix:
- Check:
```
