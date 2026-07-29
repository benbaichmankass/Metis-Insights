# Broker-truth IB fills — Flex Web Service design (rec #7 PR c)

**Date:** 2026-07-29 · **Milestone:** roadmap-toolbox assessment rec #7 (broker-truth
cost coverage) · **Status:** design landed; the build is **gated on an operator-provided
Flex token** (a genuine human-at-broker step) + a real Flex-XML capture (verify-before-build).

## Where rec #7 stands

The "3/8 accounts have broker-truth" gap decomposed into an **automatable set** and an
**operator-gated tail**:

| Account | Exchange | Path | Status |
|---|---|---|---|
| `bybit_1` / `bybit_2` / `bybit_portfolio` | Bybit | `fetch_my_trades` (ccxt), daily timer | ✅ **shipped** — multi-account (#7891) |
| `alpaca_paper` / `alpaca_portfolio` / `alpaca_options_paper` | Alpaca | `/v2/account/activities` FILL, daily timer | ✅ **shipped** — adapter (#7895) |
| `ib_paper` | IBKR | **IB Flex Web Service** (this doc) | ⛔ **operator-gated** — needs a Flex token |
| `bybit_2` lifetime wallet-truth | Bybit | UM CSV export (netting stitch) | ⛔ operator-gated (separate) |

So the **6 API-automatable accounts are done**. IB is the third genuine operator-secret
case rec #7 itself predicted.

## Why not `reqExecutions` (the verified wall)

IB is fundamentally unlike Bybit/Alpaca, which expose **account-wide** fills to any
authenticated key. IB's execution feed is **clientId-scoped**: a client only receives
executions for orders **it** placed, unless it is the gateway's configured *Master API
client ID*. The trader holds `clientId 497` (`ib_paper`); a separate daily pull process
must use a *different* clientId (a collision on 497 is refused), and a high PID-salted
read-only clientId (the `ib_read_client_for` pattern) would therefore see **zero** of the
trader's fills. There is no Master-API-client configured, and the read would also ride the
gateway breaker/pacing wedge class (BL-20260609 family). Building a `reqExecutions` parser
would ship something that returns nothing — the FMP-403 anti-pattern the verify-before-build
invariant exists to prevent.

## The chosen path: IB Flex Web Service

An **Activity Flex Query** (Trades section) fetched over the **Flex Web Service** (a pure
HTTPS reporting API, **no gateway involvement**) is the standard IB broker-truth path:
account-wide, historical (not current-day-only), and it carries the **real IB commission**
per trade (unlike Alpaca's honest-0 equity fills).

### Operator step (human-at-broker — the only hand-off)

In **IB Account Management → Reporting → Flex Queries**:
1. Create an **Activity Flex Query**. Include the **Trades** section with (at minimum):
   `tradeID / execID`, `symbol` (+ `assetCategory`/`conid`), `buySell`, `quantity`,
   `tradePrice`, `ibCommission` + `ibCommissionCurrency`, `dateTime` (or `tradeDate` +
   `tradeTime`), `orderID`, `accountId`. Format **XML**, period **Last N Days** (e.g. 7),
   date format `yyyyMMdd`, time `HHmmss`. Note the **Query ID**.
2. Under **Reporting → Settings → Flex Web Service**, **enable** it and generate a
   **token** (valid ~1 year).
3. Paste the two values into the pre-created Actions secret slots (issue #7896):
   - `IB_FLEX_TOKEN` = the Flex Web Service token
   - `IB_FLEX_QUERY_ID` = the Activity Flex Query id
   (Empty placeholders are minted by `init-actions-secrets`; you paste via
   Settings → Secrets → Update, never "New secret".)

No API can create a Flex Query definition — this is genuinely a human-at-broker action,
like originating an exchange key.

### Adapter build (after the token exists — a follow-up session)

1. **Capture a real Flex XML** first (verify-before-build): a one-shot runner/VM fetch with
   the token → commit a sanitized fixture under `tests/fixtures/`. Only then write the parser.
2. **`scripts/pull_ib_flex_fills.py`** — the 2-step Flex Web Service protocol:
   `SendRequest` (token + query id) → a reference code → poll `GetStatement` until the XML is
   ready → parse the `<Trade>` rows.
3. **`src/runtime/exchange_fills_ib_flex.py`** — a pure `flex_trade_to_row(trade, account_id)`
   mapper → the `exchange_fills` schema: `exec_id` = `execID`/`tradeID`, `symbol`, `side`
   (`buySell` BUY/SELL), `price` = `tradePrice`, `qty` = `|quantity|`, **`fee` = `|ibCommission|`**,
   `fee_currency` = `ibCommissionCurrency`, `exec_time` = `dateTime`, `order_id` = `orderID`,
   `is_maker` = 0. Futures contract multipliers: `tradePrice` × `quantity` is per-contract;
   the store keeps price+qty as reported (the FIFO cost sweep already handles multipliers via
   `config/instruments.yaml`). Injectable fetch → unit-testable against the captured fixture.
4. **Wiring:** add `IB_FLEX_TOKEN` / `IB_FLEX_QUERY_ID` to `sync-vm-secrets.yml`
   `OPTIONAL_SECRETS` (+ the env block) so they reach the VM `.env`, then a **third
   `ExecStart`** on `ict-exchange-fills-pull.service` (`pull_ib_flex_fills.py`, fail-soft:
   a no-token run is a clean no-op, not a unit failure) — the same daily 00:20 UTC timer.
   The Flex Web Service is HTTPS, so it runs on the VM (writes the local `exchange_fills.sqlite`)
   with no gateway dependency.
5. `exec_id` PRIMARY KEY makes the daily overlap idempotent, exactly like Bybit/Alpaca.

### Notes / caveats

- Flex Web Service rate-limits a given query to **~once every few minutes**; a daily pull is
  well within it. A `GetStatement` returns `ErrorCode`/`Warning` envelopes — parse defensively.
- The report reflects settled/booked trades; T+0 same-day fills may lag vs the real-time feed.
  For continuous cost-truth that's fine (the sweep tolerates a day's lag).
- Unlike the Alpaca `fee=0`, IB Flex gives the **true commission**, so the broker-truth cost
  sweep (`backfill_broker_truth_costs.py`) upgrades `ib_paper` fees from estimate → broker
  the same way it does Bybit.

## Coverage after this lands

`bybit trio + alpaca trio + ib_paper` = **7/8** exchange-truth-covered on a daily cadence;
the remaining item (`bybit_2` lifetime wallet-truth via the UM netting-stitch export) is the
one true operator-export tail. No further code is needed for the automatable set.
