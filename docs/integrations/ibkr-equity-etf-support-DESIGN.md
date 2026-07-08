# IBKR equity/ETF (STK) contract support — DESIGN

**Status (updated 2026-07-08):** Steps 1-6 of §6 BUILT and MERGED (#5871 steps
1-5; #5914 step 6 — Tier-3, operator-authorized). Only **step 7 (live paper
verification)** remains open — no `ib_paper` SPY/QQQ/IWM/TLT fill has occurred
yet as of 2026-07-08 (the 4 wired cells are daily-cadence and haven't hit a
candle close since wiring; see the backlog item's latest update). §5's open
questions were answered by the operator 2026-07-07 (recorded below and in the
backlog) — reuse `ib_paper` (not a new `ib_equity_paper` account, contra the
§4.6b/§6-step-6 recommendation below), all 10 Alpaca ETFs, keep the existing
Alpaca/yfinance signal-candle source, and real-money IBKR ETF is the eventual
goal. Tier-3 (`accounts.yaml` wiring) already merged with that approval.
**Backlog:** `PB-20260707-IBKR-STK-ETF-SUPPORT` (approved-to-scope 2026-07-07).
**Author:** Claude, 2026-07-07.

## 1. Why

The operator asked: *"can anything trading on alpaca paper also trade on ibkr
paper?"* Answer today: **no.** The IB integration knows **only three futures
contracts** — `IBClient._build_contract` (`src/units/accounts/ib_client.py:738`)
maps `{MES: CME, MGC: COMEX, MHG: COMEX}` and **raises `ValueError` for any
other symbol**. There is zero equity/ETF (`secType='STK'`) support anywhere in
the IB path (no `Stock()` construction, no STK branch).

Meanwhile `alpaca_paper` trades a basket of US equity ETFs (SPY, QQQ, GLD, IWM,
TLT, IEF, SLV, USO, GDX, TQQQ, QLD). IBKR can trade all of those as US equities;
the code just can't build the contract.

**Value of building it:**
- A **second paper venue** for the ETF strategies → cross-broker validation
  (same strategy, two brokers, compare fills/slippage).
- A path to an eventual **real-money IBKR ETF route** (IBKR as an alternative to
  Alpaca for equities).

**Not urgent:** those ETFs already soak live on `alpaca_paper`, so nothing is
stranded. This is redundancy / optionality, deliberately sequenced behind a
design review.

## 2. Scope — what "done" means

`ib_paper` (or a new `ib_equity_paper` account) can trade a chosen set of US
equity ETFs end-to-end: signal → order package → IB `Stock` order → journaled
fill → monitored exit → closed trade with realised PnL, with the mandatory
`account_compat_matrix` run before any routing. **No futures behaviour changes.**

Out of scope (explicit non-goals): options on equities (that's the Alpaca
options overlay), non-US equities, fractional shares (IB bracket orders reject
them — same constraint as Alpaca), and any real-money wire (separate Tier-3).

## 3. Current state (what exists to build on)

| Concern | Futures today | Reusable for equities? |
|---|---|---|
| Contract build | `_build_contract` → `ContFuture`→`Future` via the `ib_exchanges` map, `ValueError` otherwise (`ib_client.py:724-769`) | **Extend** with an STK branch |
| Whole-unit qty | `RiskManager.position_size` sizes **whole contracts** for futures; the equity analogue already exists — `risk.WHOLE_UNIT_QTY_EXCHANGES = {alpaca}` → `requires_whole_unit_qty` → `whole_units` flag → whole **shares** (`IB_PLACE_CONFIRM_S` note in CLAUDE.md; BL-20260622-ALPACA-FRACTIONAL-SIZE) | **Reuse** — add `interactive_brokers` (equity mode) to the whole-share path |
| PnL | prefer-broker-else-local, keyed on `contract_value_usd` (`config/instruments.yaml`); IB realised PnL swept by `order_monitor._sweep_local_pnl_for_unpriced` | **Reuse** — equities are `contract_value_usd = 1 × price`, multiplier 1 |
| Market hours | the Alpaca ETF strategies already gate on the US cash session (`market_hours us_equity`, reason `us_market_closed`) so closed-market candles don't produce entries | **Reuse the gate**; confirm it fires on the IB routing path |
| Asset class | `_asset_class.asset_class_for_symbol` already buckets SPY/QQQ/… as `equity` (config-driven, `config/instruments.yaml`) | already works |
| Candles / market data | `market_data.connector_for_symbol` routes MES/MGC/MHG → IBKR; equity ETFs currently route to Alpaca/yfinance | **Decide** the IB-equity candle source (see §5 open Q) |

## 4. Design

### 4.1 Per-symbol instrument-type resolver
Introduce a small authoritative map (config-driven, mirroring the `_asset_class`
pattern) that answers **"is this symbol a FUT or a STK on IBKR, and on which
exchange?"** — e.g. in `config/instruments.yaml` add an `ib` block per symbol:

```yaml
MES:  { ib: { sec_type: FUT, exchange: CME } }
MGC:  { ib: { sec_type: FUT, exchange: COMEX } }
MHG:  { ib: { sec_type: FUT, exchange: COMEX } }
SPY:  { ib: { sec_type: STK, exchange: SMART, primary_exchange: ARCA, currency: USD } }
QQQ:  { ib: { sec_type: STK, exchange: SMART, primary_exchange: NASDAQ } }
# … GLD/IWM/TLT/IEF/SLV/USO/GDX/TQQQ/QLD
```

`_build_contract` resolves the symbol through this map instead of the hardcoded
`ib_exchanges` dict. **Back-compat:** if a symbol is absent from the map, fall
back to the current futures behaviour for MES/MGC/MHG (or keep the existing dict
as the FUT default) so the change is purely additive. This map is the **single
source of truth** — the same place the `ibkr_offvm` historical-pull adapter's
`_SYMBOL_EXCHANGE` should eventually read from (today it duplicates the FUT map;
see the 2026-07-07 COMEX fix, #5853).

### 4.2 STK branch in `_build_contract`
```python
spec = ib_instrument_spec(sym)          # {sec_type, exchange, primary_exchange, currency}
if spec.sec_type == "STK":
    from ib_insync import Stock
    contract = Stock(sym, spec.exchange or "SMART", spec.currency or "USD",
                     primaryExchange=spec.primary_exchange)
    ib.qualifyContracts(contract)       # resolves conId; raises if ambiguous/unknown
    self._contract = contract
    return contract
# else: existing ContFuture→Future path
```
`SMART` routing + a `primaryExchange` hint is the IBKR-recommended way to avoid
ambiguous-contract errors for US equities. Cache per-symbol (the existing cache
guard already keys on `sym`).

### 4.3 Sizing (whole shares)
Add IBKR-equity to the whole-unit-qty path so `position_size` floors to whole
shares (IB bracket orders reject fractional shares, same as Alpaca). The
`RiskManager` is built from only the `risk` sub-block and never sees the
exchange, so the `whole_units` flag must be resolved from the account/symbol
before sizing — exactly the mechanism `requires_whole_unit_qty` already uses for
Alpaca. A sub-1-share order is a per-trade refusal (logged cause), never a
fractional order.

### 4.4 PnL + `contract_value_usd`
Add an equity entry per ETF in `config/instruments.yaml`
(`contract_value_usd = last_price × 1`, multiplier 1). Realised PnL flows through
the existing prefer-broker-else-local sweep — no new PnL path. (IB does report
equity PnL; confirm `BROKER_PNL_READER_EXCHANGES` handling or let the local sweep
fill it, as it does for futures today.)

### 4.5 Market-hours gate
The ETF strategies already refuse outside the US cash session. Confirm that gate
is strategy-side (not Alpaca-adapter-side) so it fires regardless of routing
venue; if it's adapter-side, lift it to the strategy/coordinator so the IB route
inherits it. A closed-market equity candle must never produce an IB entry.

### 4.6 Account wiring
Either (a) add the ETF symbols to `ib_paper.symbols` + route the strategies
there in `accounts.yaml`, or (b) create a dedicated `ib_equity_paper` account
(cleaner separation: futures vs equities on IBKR, distinct clientId). **(b) is
recommended** — it isolates the new instrument class, keeps the futures account's
contract cache/clientId clean, and mirrors how Alpaca options got its own account
(`alpaca_options_paper`). Both are **Tier-3** (accounts.yaml).

## 5. Open questions for the operator (ANSWERED 2026-07-07, see §0 status)
1. **Which ETFs?** ANSWERED: all 10 Alpaca ones. (§6 step 6 note: only 4 of the
   13 evaluable cells actually cleared `ib_paper`'s compat-matrix gate and got
   wired — the other 6 stayed off, an evidence-driven outcome, not a scope walk-back.)
2. **New account or reuse `ib_paper`?** ANSWERED: reuse `ib_paper` (contra this
   doc's §4.6b recommendation of a new `ib_equity_paper`).
3. **Candle source for IB equities** — ANSWERED: kept the existing Alpaca/yfinance
   signal-candle source, routed only execution to IB, per this doc's recommendation.
4. **Real-money IBKR ETF** — ANSWERED: yes, that is the eventual goal; the build
   hardens toward `ib_live` (mandatory compat-matrix gate before any real-money wire).

## 6. Build plan (once approved)
1. **DONE (#5871).** Add the per-symbol `ib` instrument-type map to
   `config/instruments.yaml` + an `ib_instrument_spec()` resolver (Tier-1, tested).
2. **DONE (#5871).** STK branch in `_build_contract` + unit tests (monkeypatched
   `qualifyContracts`, like the existing futures tests) (Tier-2, order-path code).
3. **DONE (#5871).** Extend the whole-share qty path to IBKR-equity (Tier-2).
4. **DONE (#5871).** `config/instruments.yaml` equity `contract_value_usd` entries
   (Tier-1) — already present from the alpaca-ETF entries, no new rows needed.
5. **DONE (#5871).** Confirm/lift the market-hours gate (Tier-2) — confirmed
   already strategy-side, no code change needed.
6. **DONE (#5914), with a scope change.** Strategy routing in `accounts.yaml`
   (Tier-3, operator-approved) — wired onto the **existing `ib_paper`** account
   (operator chose reuse over a new `ib_equity_paper`, §5 Q2), **after** the
   mandatory `scripts/prop/account_compat_matrix.py` run scored 4 of 13 evaluable
   cells ROUTE at `ib_paper`'s own risk_pct (spy_trend_long_1d, qqq_trend_long_1d,
   iwm_trend_long_1d, tlt_pullback_1d — only those 4 wired, not the full 10-ETF
   set from §5 Q1; see the accounts.yaml comment + backlog for the per-cell
   numbers).
7. **OPEN.** Live paper verification: place → journal → monitor → close a real IB
   paper ETF trade end-to-end, confirm PnL resolves. Checked 2026-07-08 (issue
   #5926) — no fill yet on any of the 4 wired cells (daily-cadence, hasn't hit a
   candle close since wiring).

## 7. Risk / guardrails
- **No futures regression** — the change is additive; MES/MGC/MHG keep their exact
  path (the FUT map is the default for absent symbols).
- **Fractional-share reject** — the whole-share floor is mandatory before any IB
  equity order (§4.3), or IBKR rejects the bracket outright.
- **Market-data entitlement** — the IB paper account needs US-equity market-data
  permission for `qualifyContracts` to resolve conIds; if absent the strategy
  logs-but-is-inert (same failure shape as the metals COMEX-entitlement guard in
  `_build_contract:759-765`), never a bad order.
- **Tier-3 gates intact** — the live wire (accounts.yaml + real-money) stays
  operator-approved; this doc changes nothing live on its own.
