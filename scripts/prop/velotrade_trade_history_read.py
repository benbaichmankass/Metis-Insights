#!/usr/bin/env python3
"""READ-ONLY: can velotrade_1's closes be reconciled from the venue's own trade
history? (lane VELOTRADE-RECON, 2026-10-07; pipeline PI-20261006-ZRM27CBW-0001.)

Logs in over the DXtrade REST API, GETs ``/accounts/{a}/orders/history`` with a
few query variants, runs the same parser ``DXtradeApiAdapter.read_trade_history``
uses, and prints, per variant: the HTTP status, the number of orders and
executions in the raw body, the KEY NAMES of the first order / leg / execution
(shape only, never identifiers) and the parsed rows (time, symbol, side, effect,
volume, price, commission). Then logs out.

Safety, enforced in ``_get``: the ONLY requests are GET on that one path, plus
the adapter's own POST /login, GET /users and POST /logout. No order endpoint
is touched. Credentials, the session token and account codes are redacted.

Exit: 0 read done, 3 a variant was rejected / unreadable (a MEASUREMENT),
2 no credentials, 1 environment / login.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.prop.platform import load_platform_config  # noqa: E402
from src.prop.platform.base import FeasibilityError  # noqa: E402
from src.prop.platform.dxtrade_api import DXtradeApiAdapter, trade_history_rows  # noqa: E402

ACCOUNT = "velotrade_1"
VARIANTS = ("period=today", "period=yesterday", "")


def _get(a: DXtradeApiAdapter, query: str):
    path = f"/accounts/{a._acct()}/orders/history" + (f"?{query}" if query else "")
    return a._request("GET", path)


def _keys(d) -> list:
    return sorted(d.keys()) if isinstance(d, dict) else []


def read(a: DXtradeApiAdapter, out=print) -> int:
    rc = 0
    for q in VARIANTS:
        r = _get(a, q)
        orders = (r.body.get("orders") if isinstance(r.body, dict) else r.body) if r.ok else None
        orders = orders if isinstance(orders, list) else []
        first = orders[0] if orders and isinstance(orders[0], dict) else {}
        leg = (first.get("legs") or [{}])[0] if first else {}
        ex = (first.get("executions") or [{}])[0] if first else {}
        rows = trade_history_rows(r.body) if r.ok else []
        n_exec = sum(len(o.get("executions") or []) for o in orders if isinstance(o, dict))
        rec = {"step": "history", "query": q or "(none)", "http": r.status, "orders": len(orders),
               "executions": n_exec, "parsed_rows": len(rows),
               "order_keys": _keys(first), "leg_keys": _keys(leg), "execution_keys": _keys(ex),
               "rows": [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}
                        for row in rows[-20:]]}
        if not r.ok:
            rec["error"] = r.error()
            rc = 3
        out(a.redact(json.dumps(rec, sort_keys=True, default=str)))
    return rc


def main() -> int:
    cfg = load_platform_config(ACCOUNT)
    user = os.environ.get(cfg.get("username_env", ""), "")
    pw = os.environ.get(cfg.get("password_env", ""), "")
    print(json.dumps({"step": "start", "account": ACCOUNT, "credentials": "set" if user and pw else "MISSING"}))
    if not (user and pw):
        return 2
    a = DXtradeApiAdapter(domain=os.environ.get("VELOTRADE_DX_DOMAIN") or None)
    try:
        a.login(None, cfg["login_url"], user, pw)
    except FeasibilityError as fe:
        print(a.redact(json.dumps({"step": "login", "feasibility": fe.reason, "detail": fe.detail})))
        return 1
    try:
        return read(a)
    finally:
        print(json.dumps({"step": "logout", "http": a.logout()}))


if __name__ == "__main__":
    sys.exit(main())
