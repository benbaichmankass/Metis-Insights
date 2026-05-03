# Debug memory

Use this file for recurring bugs so Claude does not rediscover them.

## Known patterns

- Use `PYTHONPATH=.` when running tests from repo root.
- Runtime validation tests may fail if function signatures drift from tests.
- Telegram-related tests need the `telegram` package or must be skipped/mocked.
- `.env` loading tests need `python-dotenv`.
- Never test live exchange behavior without explicit live-mode/dry-run instructions. (There is no paper-trading mode.)

## Anti-patterns

- Running full backtests to verify a docs or config change.
- Hardcoding API keys in smoke scripts.
- Creating one-off notebooks without saving outputs to Drive/HF.

## Durable findings

### 2026-05-01: Telegram parse modes — use HTML for any handler with dynamic identifiers

Telegram's bot API has three parse modes that disagree on escaping:

| Mode | Bold | Italic | Code | Escape mechanism |
|---|---|---|---|---|
| `parse_mode="Markdown"` (legacy v1) | `*bold*` | `_italic_` | `` `code` `` | **None** — backslash escapes are NOT processed; appear literally |
| `parse_mode="MarkdownV2"` | `*bold*` | `_italic_` | `` `code` `` | `\` escapes any of ``_*[]()~`>#+-=|{}.!`` |
| `parse_mode="HTML"` | `<b>bold</b>` | `<i>italic</i>` | `<code>code</code>` | Escape `&`, `<`, `>` only |

**Trap:** `parse_mode="Markdown"` silently strips unmatched `_` as italic markers. So `BYBIT_API_KEY_1` renders as `BYBITAPIKEY1`. Backslash-escaping (`BYBIT\_API\_KEY\_1`) does NOT help — legacy Markdown renders the backslash literally as `\_`.

**Rule:** any Telegram handler whose output contains user-visible identifiers (env var names, account names, file paths, error strings) must use `parse_mode="HTML"`. Two-line helper:
```python
def _h(s):
    return str(s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
```

Canonical pattern: `cmd_accounts_status` in `src/bot/telegram_query_bot.py`. See BUG-027 + BUG-028.

### 2026-05-01: Multi-process restart awareness

The bot has multiple systemd units. Each reads `os.environ` once at startup. When the operator rotates env vars, **every** unit that reads them must restart, not just the trader:

| systemd unit | Surface affected |
|---|---|
| `ict-trader-live.service` | trade loop, signal generation |
| `ict-telegram-bot.service` | `/accounts_status`, `/balance`, `/smoke_test`, every `cmd_*` handler |
| `ict-web-api.service` | dashboard `/api/*` endpoints |
| `ict-heartbeat.service` | daily heartbeat ping |

The Colab key-rotation notebook restarts BOTH `ict-trader-live.service` and `ict-telegram-bot.service` after writing `.env`. See BUG-029.

### 2026-05-01: `.env` vs `.env.live` divergence

Multiple code paths look at multiple files for the same data:

| Path | What it reads |
|---|---|
| systemd `EnvironmentFile=` (most units) | `/home/<user>/ict-trading-bot/.env` |
| `src/main.py::load_dotenv()` (no arg) | `.env` from CWD |
| `src/runtime/pipeline.py` line 11 | `.env.live` if exists |
| `scripts/render_env_from_master.py` (default `--out`) | writes `.env.live` |
| Colab key-rotation notebook (post-#252) | writes BOTH `.env` and `.env.live` |
| `deploy/ict-heartbeat.service` | `.env.live` |
| `deploy/ict-smoke-once.service` | `.env` + `-.env.bybit_<id>` |

**Rule:** when wiring a new env-loading path, always check the systemd unit. If they disagree, write the same content to both files. Long-term fix is to standardize via `EnvironmentFile=-/home/.../.env.live` (deploy/ change → PM review). See BUG-026.

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
> **Historical** — `MODE`, `DRY_RUN`, and `ALLOW_LIVE_TRADING` were removed by BUG-039
> (2026-05-03). `src/runtime/trading_mode.py` was deleted. The single toggle is now
> `mode: live | dry_run` per account in `config/accounts.yaml`. This entry is kept as
> context only; the fix described below no longer exists in the codebase.
- Cause: `validate_startup` only checked `DRY_RUN=false` requires `ALLOW_LIVE_TRADING=true`.
  A config with `MODE=LIVE` + `DRY_RUN=true` + `ALLOW_LIVE_TRADING=false` passed validation
  even though the intent was clearly live.
- Fix (historical): Added a second interlock: `MODE=LIVE` requires `ALLOW_LIVE_TRADING=true`
  at startup, regardless of `DRY_RUN`. Superseded by BUG-039 removal of all three flags.
- Check: `PYTHONPATH=. pytest tests/test_vwap_strategy.py::TestLiveSafetyGate -q`
  (test class deleted in BUG-039 sprint)

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
