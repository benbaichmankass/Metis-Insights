# Bybit AI Trading Skill

Source: <https://github.com/bybit-exchange/skills>  
Version: 1.3.0 (MIT)

This document captures the authoritative Bybit V5 API integration rules
extracted from the upstream SKILL.md. It is the reference for any AI-assisted
order logic in this codebase.

---

## Authentication

| Method | Sign-Type Header | Output |
|--------|-----------------|--------|
| HMAC-SHA256 | `1` (or omit) | Hex |
| RSA-SHA256 PKCS\#1 v1.5 | `2` | Base64 |

Required request headers:
- `X-BAPI-API-KEY`
- `X-BAPI-TIMESTAMP`
- `X-BAPI-SIGN`
- `X-BAPI-RECV-WINDOW: 5000`
- `X-BAPI-SIGN-TYPE` (2 for RSA; omit or 1 for HMAC)

Param string: `{timestamp}{apiKey}{recvWindow}{queryString|jsonBody}`

---

## Environments

| Mode | Base URL |
|------|---------|
| Mainnet | `https://api.bybit.com` |
| Testnet | `https://api-testnet.bybit.com` |

---

## Critical Order Parameters

| Parameter | Notes |
|-----------|-------|
| `category` | `spot`, `linear`, `inverse`, `option` |
| `symbol` | Uppercase pair, e.g. `BTCUSDT` |
| `side` | `Buy` or `Sell` |
| `orderType` | `Market` or `Limit` |
| `qty` | **String**, not number |
| `price` | **String**, required for Limit |
| `timeInForce` | `GTC`, `IOC`, `FOK`, `PostOnly`, `RPI` |
| `stopLoss` | Trigger price string, must align to `priceFilter.tickSize` |
| `takeProfit` | Trigger price string, must align to `priceFilter.tickSize` |
| `tpslMode` | `Full` or `Partial`; pass `"0"` to cancel |
| `marketUnit` | `baseCoin` (qty = coin) or `quoteCoin` (qty = USDT) for spot market orders |

---

## Precision & Validation Rules

> **CRITICAL — always call `/v5/market/instruments-info` before placing
> orders to retrieve the live `priceFilter.tickSize`, `lotSizeFilter`,
> and `minNotionalValue`. Cache the result for up to 2 hours.**

Violations return specific error codes:

| Code | Meaning |
|------|---------|
| `170134` | Order price has too many decimals (price not aligned to `tickSize`) |
| `110003` | Price out of range |
| `170140` | Order value below `minNotionalValue` |

### How to quantize prices

All SL, TP, and limit prices must be rounded to the nearest multiple of
`priceFilter.tickSize` **before** serialising to the request body:

```python
from decimal import Decimal, ROUND_HALF_UP

def quantize_price(value: float, tick: Decimal) -> str:
    d = Decimal(str(value))
    quotient = (d / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return str((quotient * tick).quantize(tick))
```

### Tick-size lookup order (this codebase)

See `src/units/accounts/precision.py::get_tick_size`:

1. **Process cache** (2-hour TTL) — populated by previous live lookups
2. **Live `GET /v5/market/instruments-info`** — authoritative
3. **Static map** (`_STATIC_TICK_SIZE`) — fallback when live API is unavailable
4. **0.01 default** — last resort

On any `170134` rejection, call `invalidate_tick_cache(symbol, category)` to
evict the cached value so the next order re-queries the live API.

---

## Rate Limiting

- GET requests: **100 ms** minimum interval
- POST (write): **300 ms** minimum interval
- On `retCode=10006`: wait 500–1500 ms random, retry up to 3×
- After 3 consecutive rate-limit hits: pause 10 s, then resume at 400 ms

---

## Spot Market Buy

Use `marketUnit=quoteCoin` when `qty` represents USDT amount (recommended
for buys). Use `marketUnit=baseCoin` when `qty` is the coin quantity (used
for sells, and for all base-coin-sized buys in this bot).

---

## Security Checklist

- Never enable **Withdraw** permission on AI-used API keys
- Use dedicated sub-accounts with limited balance
- Bind IP address where possible
- Rotate keys every 30–90 days
- API Key display: first 5 + last 4 chars only
- API Secret display: last 5 chars only
