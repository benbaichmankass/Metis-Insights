#!/usr/bin/env python3
"""READ-ONLY probe of Velotrade's DXtrade REST API (/dxsca-web).

Lane VELOTRADE-API-PROBE (2026-10-05, pipeline PI-20261005-APBY4NTV-0004).
Answers: does the trader login the web terminal accepts also work on the REST
API from this VM's egress, and what instrument specs does it return?

Safety, enforced in code (``_call``), not by convention:
  * the ONLY non-GET requests are POST ``/login`` and POST ``/logout``;
  * no path containing ``/orders`` (place / modify / cancel) is ever requested,
    and the GET set is a fixed allowlist of read resources;
  * nothing secret is printed: username, password, domain, session token and
    account codes are redacted from every printed string; accounts are counted,
    never named.

Exit codes: 0 = login accepted and reads done, 3 = login rejected / no usable
API (a MEASUREMENT, reported), 2 = no credentials, 1 = environment.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://dx.velotrade.com/dxsca-web"
SYMBOLS = ["ETHUSD", "SOLUSD", "XRPUSD", "BTCUSD"]
SPEC_FIELDS = (
    "symbol", "type", "lotSize", "minVolume", "maxVolume", "volumeStep",
    "pricePrecision", "quantityPrecision", "marginRate", "currency",
    "financingMode", "swapRateLong", "swapRateShort", "spreadType",
)
UA = "metis-insights-velotrade-api-probe/1 (read-only; contact: repo owner)"
_SECRETS: list[str] = []


def _redact(text: str) -> str:
    for s in _SECRETS:
        if s and len(s) >= 3:
            text = text.replace(s, "<redacted>")
            text = text.replace(urllib.parse.quote(s, safe=""), "<redacted>")
    return text


def _out(msg: str) -> None:
    print(_redact(msg))


def _call(method: str, path: str, token: str | None = None, body: dict | None = None):
    """The single HTTP choke point. Returns (status, parsed_json_or_None, text)."""
    p = path.split("?", 1)[0]
    if method == "POST":
        if p not in ("/login", "/logout"):
            raise RuntimeError(f"refused: POST {p} (only /login, /logout)")
    elif method != "GET":
        raise RuntimeError(f"refused method {method}")
    if "/orders" in p:
        raise RuntimeError("refused: orders resource is never touched by this probe")
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Accept": "application/json", "User-Agent": UA}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    if token:
        hdrs["Authorization"] = f"DXAPI {token}"
    req = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            status, text = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # network / TLS
        return 0, None, f"{type(e).__name__}: {e}"
    try:
        return status, json.loads(text), text
    except ValueError:
        return status, None, text


def _err(status, js, text) -> str:
    if isinstance(js, dict):
        return f"http={status} errorCode={js.get('errorCode')} description={str(js.get('description'))[:120]!r}"
    return f"http={status} body={text[:120]!r}"


def _specs(js) -> list[dict]:
    items = js.get("instruments") if isinstance(js, dict) else js
    return [i for i in (items or []) if isinstance(i, dict)]


def main() -> int:
    user = os.environ.get("VELOTRADE_DX_USERNAME", "")
    pw = os.environ.get("VELOTRADE_DX_PASSWORD", "")
    env_domain = os.environ.get("VELOTRADE_DX_DOMAIN", "")
    _SECRETS.extend([user, pw, env_domain])
    _out(f"credentials: username {'set' if user else 'MISSING'}, password {'set' if pw else 'MISSING'} (values never printed)")
    if not (user and pw):
        _out("feasibility: no_credentials")
        return 2

    # Domain is a required login field. Try a configured one, then the platform default.
    login_user, dom_from_user = (user.split("@", 1) + [""])[:2] if "@" in user else (user, "")
    domains = [d for d in dict.fromkeys([env_domain, dom_from_user, "default"]) if d]
    _SECRETS.extend([login_user, dom_from_user])
    token = None
    domain_used = None
    for i, dom in enumerate(domains):
        st, js, tx = _call("POST", "/login", body={"username": login_user, "domain": dom, "password": pw})
        _out(f"login attempt {i + 1}/{len(domains)} (domain source: {'env' if dom == env_domain else 'user-suffix' if dom == dom_from_user else 'default'}): {_err(st, js, tx) if not (isinstance(js, dict) and js.get('sessionToken')) else 'http=%d sessionToken present' % st}")
        if isinstance(js, dict) and js.get("sessionToken"):
            token, domain_used = js["sessionToken"], dom
            _SECRETS.append(token)
            _out(f"login: accepted (timeout={js.get('timeout')!r})")
            break
    if not token:
        _out("login: rejected / unusable")
        _out("feasibility: rest_login_failed")
        return 3

    try:
        full_user = f"{login_user}@{domain_used}"
        st, js, tx = _call("GET", "/users/" + urllib.parse.quote(full_user, safe=""), token)
        accounts = []
        if isinstance(js, dict):
            accounts = js.get("accounts") or []
        elif isinstance(js, list) and js:
            accounts = (js[0] or {}).get("accounts") or []
        _out(f"users: http={st} accounts={len(accounts)}")
        codes = [a.get("accountCode") or a.get("account") or a.get("code") for a in accounts if isinstance(a, dict)]
        codes = [c for c in codes if c]
        _SECRETS.extend(codes)
        if accounts and isinstance(accounts[0], dict):
            _out(f"account_keys: {sorted(accounts[0].keys())}")

        for n, code in enumerate(codes, 1):
            q = urllib.parse.quote(code, safe="")
            st, js, tx = _call("GET", f"/accounts/{q}/portfolio", token)
            if isinstance(js, dict):
                pf = js.get("portfolios", [js])
                pos = sum(len((p or {}).get("positions") or []) for p in pf)
                orders = sum(len((p or {}).get("orders") or []) for p in pf)
                _out(f"account#{n} portfolio: http={st} positions={pos} orders={orders}")
            else:
                _out(f"account#{n} portfolio: {_err(st, js, tx)}")
            st, js, tx = _call("GET", f"/accounts/{q}/metrics", token)
            if isinstance(js, dict):
                m = js.get("metrics", [js])
                m0 = (m[0] if m else {}) or {}
                keep = {k: m0[k] for k in ("balance", "equity", "availableFunds", "margin", "openPl") if k in m0}
                _out(f"account#{n} metrics: http={st} {json.dumps(keep)}")
            else:
                _out(f"account#{n} metrics: {_err(st, js, tx)}")

        # Reference data: global instruments, then the account-scoped view.
        for sym in SYMBOLS:
            qs = urllib.parse.quote(sym, safe="")
            for label, path in [("instruments", f"/instruments/query?symbols={qs}")] + [
                (f"acct#{n}-instruments", f"/accounts/{urllib.parse.quote(c, safe='')}/instruments/query?symbols={qs}")
                for n, c in enumerate(codes[:1], 1)
            ]:
                st, js, tx = _call("GET", path, token)
                rows = [i for i in _specs(js) if str(i.get("symbol", "")).upper() == sym] if js is not None else []
                if rows:
                    _out(f"spec[{label}]: " + json.dumps({k: rows[0][k] for k in SPEC_FIELDS if k in rows[0]}, sort_keys=True))
                else:
                    _out(f"spec[{label}] {sym}: no exact row ({_err(st, js, tx)})")
    finally:
        st, js, tx = _call("POST", "/logout", token)
        _out(f"logout: http={st}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
