# Alpaca Integration — Runbook (M15 Phase 2b)

> Second platform of the M15 migration
> ([`m15-phase0-results-2026-06-10.md`](../research/m15-phase0-results-2026-06-10.md)):
> the daily ETF futures-replacements (trend1d QQQ/SPY ≈
> `mes_trend_long_1d`, pullback1d GLD ≈ `mgc_pullback_1d`) and the SPY
> intraday candidates. Paper account first; live is a separate,
> operator-gated `ALPACA_ENV` flip.

## What is wired (Claude-side, S-M15-PHASE2B-ALPACA)

| Touch point | File |
|---|---|
| Execution client (**bracket** market orders — entry + TP limit + SL stop atomic; equity 2dp; whole-share qty; balance; positions; 404-idempotent close) | `src/units/accounts/alpaca_client.py` |
| Factory (`None` when creds unset) | `src/units/accounts/clients.py::alpaca_client_for` |
| Integrator | `integrator.py::AlpacaAPI` + `EXCHANGE_MAP["alpaca"]` |
| Executor branch (retCode contract) | `execute.py::_submit_order` |
| Coordinator dispatch | `src/core/coordinator.py` |
| Market data (free IEX feed) | `src/exchange/alpaca_connector.py` (Phase 1) |
| Session gate (`us_equity` RTH) | `src/runtime/market_hours.py` (Phase 1; wired per strategy at assignment) |
| Inert account | `config/accounts.yaml::alpaca_paper` (dry_run + `strategies: []` + creds unset) |
| Secrets propagation | already in `sync-vm-secrets.yml` `OPTIONAL_SECRETS` (Phase 2) |
| Tests | `tests/test_alpaca_wiring.py` |

Auth: key pair `ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY` (free paper
keys; same pair the data connector uses). `ALPACA_ENV=paper` is the code
default → paper-api.alpaca.markets; `live` is an explicit flip behind
the usual gates.

## Operator steps (exactly three)

1. **Originate at Alpaca**: open the individual account (US setup per
   memo §0.5), enable paper trading, generate the **API key id +
   secret** from the dashboard.
2. **Paste values into the pre-created GitHub Actions secrets**
   (`ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY` — slots created by
   issue #3302).
3. **Ping Claude in chat.**

## After the ping (Claude-side)

1. Dispatch `sync-vm-secrets` → keys land in the live VM `.env`.
2. Smoke test via the diag relay: factory builds, `balance()` returns
   paper equity, `get_ohlcv("SPY","1d")` returns bars, one 1-share
   paper bracket round-trip (`place` → `positions` → `close`).
3. **Tier-3 (operator approval):** strategy-assignment PR — the daily
   ETF clones (`spy_trend_long_1d` / `qqq_trend_long_1d` ≈
   mes_trend_long_1d params; `gld_pullback_1d` ≈ mgc params) in
   `execution: shadow`, routed to `alpaca_paper` only, with the
   `us_equity` session gate in their builders. SPY intraday and any QQQ
   work stay research/shadow per the Phase-0 verdict.
4. Shadow soak → live-on-paper flips per the normal promotion ladder.

## Failure vocabulary

- `MissingCredentialsError` naming the env vars → creds not propagated.
- `retCode != 0` envelopes carry Alpaca's `message` (e.g. "insufficient
  buying power", "market is closed" — bracket orders are day-TIF and
  queue/reject outside RTH; the session gate prevents builders signaling
  off-hours).
- Bracket orders require whole shares — the client floors qty at 1; at
  SPY ~$600 the 0.5%-risk sizing on a small paper balance can round to
  1 share (notional ~$600); verify margin headroom in the smoke test.
