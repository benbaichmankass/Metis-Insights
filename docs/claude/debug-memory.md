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

## Add new entries here

Use this format:

```md
### YYYY-MM-DD: symptom
- Cause:
- Fix:
- Check:
```
