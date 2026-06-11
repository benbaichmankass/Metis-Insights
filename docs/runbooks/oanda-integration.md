# OANDA v20 Integration — Runbook (M15 Phase 2)

> First new-market wire of the M15 migration
> ([`m15-phase0-results-2026-06-10.md`](../research/m15-phase0-results-2026-06-10.md):
> XAU/USD positive train+OOS across four strategy families). Practice
> account first; live is a separate, operator-gated flip.

> **STATUS 2026-06-11: LIVE-ON-PRACTICE.** Creds synced (#3333), deployed (#3337), account flipped live (#3338); `xauusd_trend_1h` executes practice orders. The §"After the ping" sequence below is the historical record; remaining verification (first-fill check, balance probe, Dukascopy-vs-OANDA candle fidelity) is tracked in the M15 soak handoff (`docs/research/session-handoff-m15-soak-2026-06-11.md`).


## What is wired (Claude-side, S-M15-PHASE2-OANDA)

| Touch point | File |
|---|---|
| Execution client (market order + broker-side `stopLossOnFill`/`takeProfitOnFill`, balance, positions, idempotent close) | `src/units/accounts/oanda_client.py` |
| Factory (`None` when creds unset → account loads `configured: False`) | `src/units/accounts/clients.py::oanda_client_for` |
| Integrator | `src/units/accounts/integrator.py::OandaAPI` + `EXCHANGE_MAP["oanda"]` |
| Executor branch (retCode contract, MissingCredentialsError naming env vars) | `src/units/accounts/execute.py::_submit_order` |
| Coordinator client dispatch | `src/core/coordinator.py::multi_account_execute` |
| Market data (candles, practice host) | `src/exchange/oanda_connector.py` (Phase 1) + `market_data.py::_build_exchange_client` |
| Session gate (FX weekend close — wired to strategies at assignment time) | `src/runtime/market_hours.py` (Phase 1) |
| Inert account entry | `config/accounts.yaml::oanda_practice` (`mode: dry_run` + `strategies: []` + creds unset — independent gates) |
| Secrets propagation | `sync-vm-secrets.yml` `OPTIONAL_SECRETS` (`OANDA_API_TOKEN`, `OANDA_ACCOUNT_ID`) |
| Tests | `tests/test_oanda_wiring.py` |

Auth model: **one bearer token + account id** (no key+secret pair).
`OANDA_ENV` selects the host — `practice` (code default) →
`api-fxpractice.oanda.com`; `live` requires an explicit env flip AND the
account-mode/Tier-3 gates. Order units are signed integers of the base
instrument; the executor floors qty at 1 unit.

## Operator steps (exactly three)

1. **Originate at OANDA**: open the practice (demo) account on the US
   division, then in the account portal ("Manage API Access") generate a
   **personal access token** and note the **account id**
   (`xxx-xxx-xxxxxxx-xxx`).
2. **Paste values into the pre-created GitHub Actions secrets**
   (`OANDA_API_TOKEN`, `OANDA_ACCOUNT_ID` — empty slots created by
   issue #3302; Settings → Secrets → Actions → Update).
3. **Ping Claude in chat** that the secrets are in.

Everything else is Claude-side, via workflows.

## After the ping (Claude-side, autonomous unless marked)

1. Dispatch `sync-vm-secrets` (Tier-2 ack rides in the issue body) →
   token lands in the live VM `.env`.
2. Smoke test over the diag relay: factory builds, `balance()` returns
   the practice NAV, `get_ohlcv("XAU_USD", "1h")` returns candles, one
   1-unit practice round-trip (`place` → `positions` → `close`) — paper
   money, practice host.
3. Cross-check OANDA candles vs the Phase-0 Dukascopy series (sanity on
   the backtest's data fidelity).
4. **Tier-3 (operator approval to merge):** the strategy-assignment PR —
   a gold strategy clone (first candidate: `xauusd_trend_1h`, the
   sweep's strongest cell) with `execution: shadow`, routed to
   `oanda_practice` only, plus the market-hours weekend gate wired for
   FX symbols.
5. Shadow soak → `set-account-mode`/execution flips per the normal
   promotion ladder (each step operator-gated).

## Failure vocabulary

- Factory returns `None` / `MissingCredentialsError` naming
  `OANDA_API_TOKEN` / `OANDA_ACCOUNT_ID` (names only, never values) →
  creds not propagated; re-run step 1 of "After the ping".
- `retCode != 0` envelopes carry OANDA's `errorMessage` / cancel
  `reason` (e.g. `INSUFFICIENT_MARGIN`, `MARKET_HALTED` over the
  weekend) — surfaced through the coordinator's diagnostic ping.
- FOK market orders can be created-then-cancelled while HTTP returns
  201; the client maps that to `retCode -3` so it is never mistaken
  for a fill.
