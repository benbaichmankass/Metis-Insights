# Velotrade: place orders through the DXtrade REST API instead of the browser? (lane VELOTRADE-WIRE, 2026-10-05)

> **Doc status:** `unknown` · category `evidence` · last verified `2026-10-05` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)

Lane VELOTRADE-WIRE (session_01WfdESzsCPZbuawkqiWjzWX), task from manager session_01MM8o5js6TcDFeNAPBY4Ntv at 12:54Z on 2026-10-05: *"should velotrade_1's executor place orders through the REST API instead of the browser ticket?"* The answer is read-only. **No order was placed and no `/orders` call was made.** `config/accounts.yaml` and the roster were not touched; the roster comes from lane PROP-ROSTER-EXPAND.

Builds on [`velotrade-api-probe-2026-10-05.md`](velotrade-api-probe-2026-10-05.md) (#16617). That probe showed the REST login works from the VM, every read works, and the contract specs are measured.

## 0. Answer

**Recommendation: yes, build an API executor for `velotrade_1`, behind the existing platform-adapter interface.** The browser executor stays as it is for `breakout_1` and `tradeify_1`. The one fact it depended on is now measured (below): the account is position-based.

| question | answer | status |
|---|---|---|
| Is `velotrade_1` position-based? An IF-THEN bracket in one request requires it. | **YES — MEASURED 2026-10-05 14:40Z.** Probe v4 (#16619 at 42e15364), issue #16631, run 37326456086: `account#1 type: {"accountStatus": "FULL_TRADING", "baseCurrency": "USD", "positionBased": true}`. The server's key is `positionBased`; the spec (`rest/types/account-details.md`) names it `isPositionBased`. | measured |
| Do Velotrade's rules allow placing orders through the API? | **Yes, verbatim** (§ 3). | read live 2026-10-05 |
| Can the API do everything the browser executor does? | **Yes.** The five calls are in § 2. Every one the browser does through the DOM has a REST call. | from the spec, not exercised |

- **It is true (measured), so:** place an entry with SL and TP in **one** request (IF-THEN group). There is no naked window between fill and protection.
- **(Not applicable now) if it were false (net-based):** order groups are refused. The fallback is the spec's two-step: a MARKET entry, read the position, then POST the SL and TP as separate orders. That leaves a naked window of one round trip, which is roughly the same window the browser executor has today. The API path still removes every DOM-fragility failure, so I would still recommend it, but with the window stated.

## 1. Why the browser path is the weaker one (measured on `tradeify_1` and `breakout_1`)

Every one of these is a recorded failure of the DOM executor that a REST call cannot have:

- **LIMIT price field drift.** The terminal kept its own price: typed 2679.725, read back 2,679.719 (TRADEIFY-PRICE-FILL, #15975). That needed a key-by-key fill workaround.
- **Order-type default.** The form opened in MARKET mode, so `place_bracket` could not find the LIMIT price field. Two SOL tickets were lost (BREAKOUT-ATTRITION, #14737).
- **Ticket opener.** Tradeify has no "New Order" control. The only measured opener is a guarded click on the watchlist Ask button (#15743, #15827).
- **Submit hit-test.** SOLUSD's Buy submit was disabled at 0.01 lots with every field read back exactly (PI-20261003-ILZMCTFQ-0002).
- **Symbol display and search.** Tradeify shows `ETH/USD`, not `ETHUSD`, and the watchlist search needed a slash query (#15444).
- **Velotrade's own terminal served only BTCUSD to the passive capture.** ETH/SOL/XRP came back bare, and `instrument-probe` refused on two Symbol/Bid/Ask tables (#16577, #16596). The REST API returned all four (#16616).

None of these says the browser path cannot work on Velotrade. They say each new terminal costs a run of DOM measurements, and Velotrade's terminal has not had any.

## 2. The calls an API executor needs (DXtrade REST spec, `dx.velotrade.com/developers`, read 2026-10-05)

All calls use `Authorization: DXAPI <token>` from `POST /login`, the same login probe v3 already uses. The token has a 30-minute timeout; `POST /ping` keeps it alive.

| executor need | REST call | notes from the spec |
|---|---|---|
| **place a bracket** | `POST /accounts/{account}/orders` with an Order Group: `contingencyType: "IF-THEN"`, parent `type: MARKET` or `LIMIT`, `positionEffect: OPEN`, nonzero `quantity`; children `positionEffect: CLOSE`, opposite `side`, quantity absent ("position attached"), `tif: GTC`, one `STOP` (SL) and one `LIMIT` (TP) | *"Order groups can be issued only for Position-based accounts."* POST is **not idempotent**: send a client-unique order id; a duplicate returns `409`, error `100` (*"Order with this id already exists"*). That gives the executor a natural idempotency key. |
| **protect an already-filled position** (net-based fallback, or repair) | `POST /accounts/{account}/orders`, single order with `positionCode` = the opening order's id and `positionEffect: CLOSE` | the spec's own Example 1: a filled MARKET order *"cannot be modified via PUT"*, so protections are new orders against the position |
| **modify SL / TP** | `PUT /accounts/{account}/orders` with the whole order (or the whole group) | **conditional:** requires `If-Match: <ETag>` from a prior GET. A missing header returns `403`; a stale ETag returns `412`. The spec shows one PUT that changes parent and both children at once. |
| **cancel** | `DELETE /accounts/{account}/orders/{order code}` or `.../orders/group?order-codes=...&contingency-type=IF-THEN` | also conditional (`If-Match`). The order code may be the client order id. |
| **read fills / state** | `GET /accounts/{account}/portfolio` (positions and working orders), `GET .../orders/history` (working and final orders, with linked orders), `GET .../metrics` (balance, equity), `GET .../events` (liquidations, margin calls) | the probe already reads portfolio and metrics; history and events are new reads |

**Rate limits** (standard defaults; the platform operator may differ): login 1/s per IP, reads 10/s, trading 10/s per session, bulk data 1/s. The executor runs one cycle per ~5 minutes, so it sits far inside these.

### What the browser executor already has (`src/prop/platform/base.py` interface, `dxtrade.py` implementation)

`login`, `read_account`, `read_positions`, `read_orders`, `place_bracket` (form fill, read-back of every field, then `arm=True` clicks submit), `modify_bracket` (the measured docked edit surface), `cancel_order` (one order by terminal id), and `flatten` (close one symbol's position). Fills come from the Trade History table (`read_history`, PROP-EXIT-READ).

Above the adapter sit:
- the intake, guard and sizing layer (`prop_executor.py`: kill switch, structure guard, static-DD and daily-loss guards, leverage caps, retry_pending, partial-fill halt, AUTO-REVERT latch);
- trailing (`prop_trail.py`);
- the account_status feed.

**None of that layer touches the DOM.** It talks to the adapter.

**So an API executor is a new adapter, not a new executor.** A `dxtrade_api` platform would implement the same eight methods over REST, with `page` unused. `config/prop_platforms.yaml` already selects the adapter per account with one line (`platform:`), and `src/prop/platform/__init__.py` raises on an unknown value. Every guard, the ledger, retries and alerts carry over unchanged.

Size (INFERRED, not measured): about 300 to 500 lines plus tests, against more than 8,600 lines in `dxtrade.py`, most of which is DOM handling.

## 3. Velotrade's rules on API order placement (`velotrade.com/rules`, read live 2026-10-05)

> *"Velotrade permits the use of Expert Advisors (EAs) and other automated trading systems. We recognise that algorithmic and automated strategies form a legitimate part of many traders' approaches."*

> *"Full programmatic API access is available on all accounts, evaluation and funded, at no additional cost and without a separate approval process. Connect any execution engine, algo trading system, trading bot, or signal automation directly to your account via the DXtrade REST or WebSocket API."*

> *"Your system must monitor live daily P&L and equity and halt before any limit is breached. Automated systems that breach account limits are closed on the same basis as manual breaches."*

> *"Automation is prohibited when it is used to exploit latency, stale pricing, execution delays, platform errors, or other technical inefficiencies. Systems that place or cancel orders at extreme speeds to gain an unfair technical advantage are also prohibited."*

A 1h trend leg placing a handful of brackets a week is nowhere near the speed clause. The "must monitor live daily P&L and equity" clause is what the existing static-DD and daily-loss guards do. They carry over unchanged, and the daily guard reads max(balance, equity) at the reset (#16500).

## 4. Tier-2 test plan (NOT run; needs an operator OK in chat)

**Before the test:**
1. Probe v4 (#16619) merged and dispatched once. Record `isPositionBased`.
2. The adapter PR is merged with the API path **dry by default**. A dry call builds and logs the exact request body and never sends it. The `velotrade_1` kill switch `PROP_EXECUTOR_MODE_VELOTRADE_1` stays unset.
3. `velotrade_1` stays `mode: dry_run` with an empty roster. The test needs neither: it is a direct action, like `round-trip-dry` / `round-trip-live` on the browser path.

**The test: one min-size round trip on ETHUSD.**
- Size: 0.01 ETH (the measured minimum; about $25 notional at about $2,500).
- SL and TP set far from the market, so neither can trigger during the test. The SL risk at 0.01 ETH is cents.

| step | call | pass condition |
|---|---|---|
| 1. dry | build the IF-THEN body; no send | logged body has 0.01 qty, CLOSE children, opposite side, GTC, client id |
| 2. live place | `POST .../orders` (group if position-based, else entry plus a protect POST) | `200`; portfolio shows 1 position of 0.01 and 2 working children at the typed SL/TP, read back exactly |
| 3. modify | `PUT` the SL one tick tighter, with `If-Match` | history shows the new SL; the ETag changed |
| 4. close | `POST /accounts/{account}/close` (Bulk Close) or a CLOSE market order against `positionCode` | portfolio: 0 positions, 0 working orders (children cancelled with the group) |
| 5. reconcile | `GET .../orders/history`, `.../metrics` | fills recorded; balance change equals fill PnL minus commission (0.03% per side); nothing left open |

**Stop rule:** any non-`200` on steps 2 to 4, or a read-back mismatch, means flatten (`POST .../close`), alert, and do not retry in the same run. That is the browser path's fail-closed posture.

**Cost:** two commissions on about $25 of notional (cents) plus the spread. **Rollback:** nothing to roll back after step 4. The adapter stays dry until a separate go-live PR.

## 5. What this memo did not establish

- That any order call works on this account. Nothing was sent. The spec warns *"API is not permitted for the user"* returns `404` on `/orders`, and that per-user permission is only visible by trying. Step 2 of the test plan is the first time it is exercised.
- Velotrade-specific rate limits. The defaults above are the spec's; the platform operator may set others.
- Whether `/orders/history` carries execution prices and commission per fill on this deployment. The spec says it returns linked orders; the field set is not measured here.

## 6. Filed

`PI-20261005-KQIWJZWX-0002`: decide on and build the `dxtrade_api` adapter (dry by default), gated on `isPositionBased`. Then the § 4 round trip, only with an operator OK.
