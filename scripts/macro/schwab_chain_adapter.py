#!/usr/bin/env python3
"""M31 Track B — Schwab option-chain → normalized-rows adapter.

The credential-free **parser** that flattens a Schwab Trader API
``GET /marketdata/v1/chains`` payload into the vendor-neutral rows
``iv_skew_probe.skew_features`` consumes. Keeping the Schwab-specific
normalization in its own module preserves ``iv_skew_probe`` as a pure,
vendor-agnostic feature layer (Schwab today, another options vendor tomorrow —
only this adapter changes).

**Schwab payload shape** (verified against the Trader API docs):
  ``{"symbol","status","underlyingPrice",
     "callExpDateMap": {"YYYY-MM-DD:DTE": {"<strike>": [ {contract}, ... ]}},
     "putExpDateMap":  {... same shape ...}}``
  each ``{contract}`` carries ``putCall``, ``strikePrice``, ``volatility``
  (**percent points**, e.g. ``18.3`` = 18.3% → divided by 100 here to match the
  fraction convention), ``delta`` (signed; puts negative), ``daysToExpiration``.
  Illiquid contracts come back with ``volatility``/``delta`` = **-999.0** — the
  documented sentinel, dropped here so it never corrupts a nearest-delta lookup.

**Split of concerns.** `parse_schwab_chain` is pure + fully tested offline against
a synthetic Schwab-shaped payload. `fetch_chain` (the live HTTPS GET + OAuth
bearer) is **credential-gated** — it needs the operator's Schwab app key/secret +
an access token — so it is a thin, documented shell that raises a clear error
until a token is supplied; it is NOT exercised here (no credential, no network).

Import-pure: no ``src.*`` import, no order path. Parser is stdlib-only.
"""
from __future__ import annotations

import json
import sys
from typing import Optional

SCHWAB_IV_SENTINEL = -999.0   # Schwab's illiquid-contract marker on volatility/delta
_CHAINS_PATH = "/marketdata/v1/chains"


def _num(x) -> Optional[float]:
    try:
        f = float(x)
        return f
    except (TypeError, ValueError):
        return None


def _rows_from_exp_map(exp_map: Optional[dict], put_call: str) -> list:
    """Flatten one of the two nested ``{expKey: {strike: [contracts]}}`` maps."""
    typ = "call" if put_call.upper() == "CALL" else "put"
    out: list = []
    for exp_key, strikes in (exp_map or {}).items():
        parts = str(exp_key).split(":", 1)            # "YYYY-MM-DD:DTE"
        date_part = parts[0]
        key_dte = None
        if len(parts) == 2:
            try:
                key_dte = int(parts[1])
            except ValueError:
                key_dte = None
        for _strike, contracts in (strikes or {}).items():
            for c in contracts or []:
                iv = _num(c.get("volatility"))
                delta = _num(c.get("delta"))
                if iv is None or delta is None:
                    continue
                if iv == SCHWAB_IV_SENTINEL or delta == SCHWAB_IV_SENTINEL:
                    continue
                dte = c.get("daysToExpiration")
                if not isinstance(dte, (int, float)):
                    dte = key_dte
                out.append({
                    "expiration": date_part,                 # clean YYYY-MM-DD from the map key
                    "dte": dte,
                    "type": typ,
                    "strike": _num(c.get("strikePrice")),
                    "iv": iv / 100.0,                        # percent points → fraction
                    "delta": delta,
                })
    return out


def parse_schwab_chain(payload: dict) -> dict:
    """Schwab ``/chains`` payload → ``{underlying, rows, symbol, status}``.

    ``rows`` are the normalized dicts ``iv_skew_probe`` consumes. Best-effort:
    a missing map yields no rows for that side; a `-999` sentinel row is dropped."""
    if not isinstance(payload, dict):
        return {"underlying": None, "rows": [], "symbol": None, "status": None}
    rows = (_rows_from_exp_map(payload.get("callExpDateMap"), "CALL")
            + _rows_from_exp_map(payload.get("putExpDateMap"), "PUT"))
    return {
        "underlying": _num(payload.get("underlyingPrice")),
        "rows": rows,
        "symbol": payload.get("symbol"),
        "status": payload.get("status"),
    }


def fetch_chain(symbol: str, *, access_token: Optional[str] = None,
                base_url: str = "https://api.schwabapi.com", http_get=None,
                **params) -> dict:
    """Credential-gated live fetch → parsed chain. **Operator hand-off required.**

    Needs a Schwab OAuth **access_token** (from the app key/secret the operator
    registers). ``http_get`` is an injectable ``get(url, headers, params) -> dict``
    so the live path can be unit-tested later without a real credential; when it is
    ``None`` this raises rather than reaching for the network — there is no live
    call in this module today. Returns ``parse_schwab_chain`` of the JSON body."""
    if not access_token:
        raise RuntimeError(
            "Schwab fetch is credential-gated: register the Schwab developer app "
            "(Trader API product) and supply an OAuth access_token. Until then the "
            "parser (parse_schwab_chain) is the usable, credential-free path.")
    if http_get is None:
        raise RuntimeError("no http_get injected — the live HTTPS path is a "
                           "deliberate follow-up; inject a getter to exercise it.")
    url = base_url.rstrip("/") + _CHAINS_PATH
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    body = http_get(url, headers=headers, params={"symbol": symbol, **params})
    return parse_schwab_chain(body)


def main() -> int:
    """Offline: parse a raw Schwab ``/chains`` JSON (file arg or stdin) → normalized."""
    raw = open(sys.argv[1]).read() if len(sys.argv) > 1 else sys.stdin.read()
    if not raw.strip():
        print("Pipe a raw Schwab /chains JSON payload to normalize it. "
              "Live fetch is credential-gated (see fetch_chain).")
        return 0
    parsed = parse_schwab_chain(json.loads(raw))
    print(json.dumps({"underlying": parsed["underlying"], "symbol": parsed["symbol"],
                      "status": parsed["status"], "n_rows": len(parsed["rows"]),
                      "rows_sample": parsed["rows"][:4]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
