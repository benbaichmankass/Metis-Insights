# Velotrade DXtrade REST API probe — read access and contract specs (lane VELOTRADE-API-PROBE, 2026-10-05)

> **Doc status:** `unknown` · category `evidence` · last verified `2026-10-05` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)

Lane VELOTRADE-API-PROBE (session_01JzDpamQRqFT7p3tS1ChgfQ), dispatched by manager session_01MM8o5js6TcDFeNAPBY4Ntv. The operator's words, 2026-10-05 ~07:40Z: *"Ok, let's go that"* — web terminal first, then test the DXtrade REST API as a follow-up. Closes pipeline item `PI-20261005-APBY4NTV-0004` and supplies the measurements `PI-20261004-KQIWJZWX-0001` was waiting on.

**Read-only throughout.** Every request the probe can make is GET, `POST /login` or `POST /logout`; `scripts/prop/velotrade_api_probe.py` refuses everything else in code and never touches `/orders`. No order, position-close or modify call was made. Credentials, session token and the account code are redacted from output; the account was counted, never named.

## 0. Answers

| question | answer | evidence |
|---|---|---|
| Does the REST API accept the trader login from the VM's egress? | **Yes.** `POST /dxsca-web/login` → HTTP 200 with a session token (30-minute timeout), using the `VELOTRADE_DX_*` credentials and domain `default`. | issues #16590, #16595, #16608, #16612, #16616 |
| Which reads work? | users, account portfolio, account metrics, global instruments (list and by symbol), account-scoped instruments (list and by symbol), logout. | run 37311474412 (issue #16616) |
| Do they agree with the web terminal? | **Yes where both were read.** REST balance 5000 / equity 5000 / 0 positions / 0 orders = the web login check (#16577, 07:45Z). REST BTCUSD specs match the web terminal's own BTCUSD row (§ 2). | #16577 vs #16616 |
| Contract specs for ETHUSD / SOLUSD / XRPUSD? | **Measured** (§ 1). | #16616 |
| Is a closed API the answer, as it was for Tradeify (`/specs` → 409 on 2026-09-30)? | **No** — Velotrade's REST API is open for this account. | #16616 |

## 1. Measured contract specs (REST, account-scoped, 2026-10-05 12:43Z)

Population: the one `velotrade_1` account (CLASSIC 1-Step $5k), `tradingStatus: FULL` on all four symbols, `type: FOREX`, `assetClass: Crypto`, `lotSize` 1, `multiplier` 1, `maxOrderSize` 1e12 LOTS (effectively unlimited). Quantity in the REST order request is **in units, not lots**; with `lotSize` 1 the two are the same number.

| symbol | `minOrderSize` | `minOrderSizeIncrement` | `quantityIncrement` | `priceIncrement` | `pipSize` | `marginRate` (flat) |
|---|---|---|---|---|---|---|
| ETHUSD | 0.01 | 0.01 | 0.01 | 0.01 | 0.01 | 0.16666 |
| SOLUSD | 0.1 | 0.1 | 0.1 | 0.001 | 0.001 | 0.16666 |
| XRPUSD | 10 | 10 | 10 | 0.00001 | 0.00001 | 0.33333 |
| BTCUSD | 0.001 | 0.001 | 0.001 | 0.01 | 0.001 | 0.1 |

Two things that differ from what the repo assumed, both from this measurement:

- **SOLUSD minimum is 0.1 units on Velotrade**, not the 0.01 Tradeify/Breakout use. A `lot_step: 0.01` copied from `tradeify_1` would be rejected or clamped.
- **XRPUSD minimum is 10 units** (step 10), so the smallest XRP order is 10 XRP. Its notional depends on the price at the time; no price was read here.

`marginRate` reads as 1/leverage (6×, 6×, 3×, 10×). **INFERRED** from the numbers, not stated by the API; nothing here depends on it.

## 2. Cross-check against the web terminal (BTCUSD, the one symbol the terminal ever served)

Web terminal, login check #16577 (passive capture): `pricePrecision 2, lotSize 1, minVolume 0.001, volumeStep 0.001, quantityPrecision 3`.

| web terminal field | REST field | web | REST | match |
|---|---|---|---|---|
| `pricePrecision` 2 | `priceIncrement` | 2 decimals | 0.01 | yes |
| `lotSize` | `lotSize` | 1 | 1.0 | yes |
| `minVolume` | `minOrderSize` | 0.001 | 0.001 | yes |
| `volumeStep` | `minOrderSizeIncrement` | 0.001 | 0.001 | yes |
| `quantityPrecision` 3 | `quantityIncrement` | 3 decimals | 0.001 | yes |

So the REST field names map one-to-one onto the web terminal's. The web-terminal route could not give ETH/SOL/XRP: the existing `instrument-probe` (issue #16596) refused with *"2 Symbol/Bid/Ask tables (need exactly 1)"*, and the passive capture in #16577 returned bare `{"symbol": ...}` for those three (the terminal had not loaded them).

## 3. Proposed `velotrade_1.executor.lots` entries (PROPOSAL — config NOT edited)

Same shape as the existing `tradeify_1` entries (`lot_units`, `lot_step`, `min_lots`, `price_step`):

```yaml
lots:
  ETHUSD: {lot_units: 1, lot_step: 0.01,  min_lots: 0.01,  price_step: 0.01}
  SOLUSD: {lot_units: 1, lot_step: 0.1,   min_lots: 0.1,   price_step: 0.001}
  XRPUSD: {lot_units: 1, lot_step: 10,    min_lots: 10,    price_step: 0.00001}
  BTCUSD: {lot_units: 1, lot_step: 0.001, min_lots: 0.001, price_step: 0.01}
```

What these values are and are not:

- **MEASURED**: every number above, on the live account, over REST.
- **NOT measured**: that the web terminal's order ticket accepts those sizes in its own lots field (the Tradeify lesson: the ticket's quantity box has its own rounding and clamp). `min_lots` should still pass the dry round trip (`round-trip-dry`) on the browser path before any symbol is added to `enabled_venue_symbols`.
- `watched_click_max_lots` and `enabled_venue_symbols` stay empty until a separate go-live PR; this memo arms nothing.

## 4. What placing a bracket via the API would require (DOCS ONLY — never called)

Source: the Velotrade developer portal's REST spec (`dx.velotrade.com/developers`), read 2026-10-05.

- Auth: the same `POST /login` token, `Authorization: DXAPI <token>`, renewed within the 30-minute timeout (or `POST /ping`).
- A bracket in one request: `POST /accounts/{account code}/orders` with an **Order Group** `contingencyType: "IF-THEN"`: the parent order `type: MARKET`, `positionEffect: OPEN`, nonzero `quantity`, `side`; the `THEN` children are `positionEffect: CLOSE`, opposite `side`, absent or zero `quantity` (attached to the position when the parent fills), `tif: GTC`, one `STOP` (stop loss) and one `LIMIT` (take profit) with `stopPrice` / `limitPrice`. Each order needs a unique client `orderCode`.
- **Order groups are only valid on a Position-based account** (spec: *"Order groups can be issued only for Position-based accounts."*). The probe printed the account's key names (`positionBased` is one) but not its value, so **whether `velotrade_1` is position-based is NOT measured.** If it is not, the fallback is the two-step the spec also describes: MARKET fill, read back the position's `positionCode`, then post the protection orders.
- Reads: 10 per second per session by default; order placement is limited separately.
- Velotrade's own rules page says programmatic API access is available on all accounts at no cost (deep-dive § 1.4, quoted there; not re-verified today).

This would be a new execution path (REST client instead of the Playwright DOM executor). It removes the DOM-fragility class (ticket opener, limit-price fill, quote display) and the naked window between fill and bracket if the account is position-based. It is a design decision for the manager/operator, not part of this lane.

## 5. What was not established

- Whether `velotrade_1` is position-based (see § 4).
- Whether order endpoints are permitted for this user: only reads were made. The spec's *"API is not permitted for the user"* (404) would only show on an order call.
- Any live price, so no notional minimums (especially XRP's 10-unit minimum).
- Trading hours and holidays per symbol (the instrument rows carry them; the probe's allowlist did not print them).
- `realized_today` on the account (the web login check's `account_unparsed: realized_today` is unchanged by this probe).

## 6. How this was measured, and what went wrong on the way

Six dispatches of the read-only actions, five probe versions. Runs 1–5 returned only `lotSize` / `currency` / `type` (or less) because of two separate defects of mine, not API limits. The output-allowlist defect (run 5's cause) was present from run 1, so it also hid the global view's `priceIncrement` / `pipSize` / `quantityIncrement` throughout:

| run | issue | outcome | cause |
|---|---|---|---|
| 1 | #16590 | login accepted; 0 accounts; only `lotSize` | `/users` parsed as a flat dict |
| 2 | #16595 | same | v2 still assumed the shape |
| 3 | #16596 | web `instrument-probe` refused | 2 watchlist tables in the terminal |
| 4 | #16608 | same as 1–2 (VM confirmed on 4a6cc92d7) | `userDetails` is a list, not a dict |
| 5 | #16612 | account, balance, portfolio read; specs dropped | output allowlist used web-terminal names (`minVolume`) not REST names (`minOrderSize`) |
| 6 | #16616 | **everything read** | fixed (v5, #16613, VM checkout d48b69634) |

Merged ≠ deployed ≠ observed: the probe changes merged as #16581, #16591, #16597, #16609, #16613; the VM checkout carried v5 (`git_sha_on_disk d48b69634`, `/api/diag/version`) before dispatch; the observation is run 37311474412 (issue #16616). The system-action `velotrade-api-probe` remains on the allowlist; it is read-only by construction (§ top).
