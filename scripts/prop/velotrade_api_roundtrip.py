#!/usr/bin/env python3
"""ONE minimum-size round trip on velotrade_1 over the DXtrade REST API.

Lane VELOTRADE-API-EXEC (2026-10-05). Operator decision relayed by manager
session_01MM8o5js6TcDFeNAPBY4Ntv, verbatim: "Build it + pre-approve test".
Runs EXACTLY the test plan in docs/research/velotrade-api-executor-design-2026-10-05.md § 4:

  0. preflight: login; the account must read 0 positions and 0 orders
  1. dry: build the IF-THEN body (0.01 ETHUSD long, SL/TP ~20% away)
  2. place: ONE POST of the group                    (--live only)
  3. read back: 1 position of 0.01, SL and TP exactly as typed
  4. modify: SL one tick (0.01) tighter, conditional PUT with If-Match
  5. close: Bulk Close on ETHUSD (positions + working orders)
  6. reconcile: 0 positions, 0 orders; fills from order history; balance delta

STOP RULE: any non-200 on steps 2-5, or a read-back mismatch, means Bulk Close
ETHUSD, verify flat, and STOP. Nothing is ever retried in the same run (a 429
backoff and the spec's one 412 re-read are the only repeats, both inside the
adapter, and a repeated POST carries the same client ids so it cannot double).

ONE-SHOT: a live run writes a latch before its first send and refuses if the
latch already exists. Without --live it sends nothing that changes the account.

Prints JSON lines of shapes, statuses, prices and fills only. Credentials, the
session token and the account code are redacted from every printed line.

Exit: 0 passed and flat · 3 stop rule tripped, verified flat · 4 stop rule
tripped and NOT verified flat (an operator must look) · 5 refused before any
send (preflight / latch / not armed) · 2 no credentials · 1 environment.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.prop.platform import load_platform_config  # noqa: E402
from src.prop.platform.base import BracketSpec, FeasibilityError  # noqa: E402
from src.prop.platform.dxtrade_api import (  # noqa: E402
    VENUE_SPECS, DXtradeApiAdapter, client_ids, round_to_step)

ACCOUNT = "velotrade_1"
SYMBOL = "ETHUSD"
QTY = 0.01                     # the measured minimum (memo § 1)
SL_TP_DISTANCE = 0.20          # 20% from the ask: neither can trigger in a one-minute test
COMMISSION_RATE = 0.0003       # 0.03% per side (memo § 4), for the expected-cost line only
EXIT_OK, EXIT_ENV, EXIT_NOCREDS, EXIT_STOPPED_FLAT, EXIT_NOT_FLAT, EXIT_REFUSED = 0, 1, 2, 3, 4, 5
DEFAULT_LATCH = Path.home() / ".cache" / "metis-prop-api" / ACCOUNT / "roundtrip.latch"


class Run:
    def __init__(self, adapter: DXtradeApiAdapter, out=print):
        self.a = adapter
        self.out = out

    def emit(self, step: str, **kw: Any) -> None:
        self.out(self.a.redact(json.dumps({"step": step, **kw}, sort_keys=True, default=str)))


def _flat(a: DXtradeApiAdapter, reads: int, sleep) -> Dict[str, Any]:
    last: Dict[str, Any] = {}
    for i in range(reads):
        try:
            pos = [p for p in a.read_positions() if p.symbol.upper() == SYMBOL]
            orders = [o for o in a.read_orders() if o.symbol.upper() == SYMBOL]
            last = {"positions": len(pos), "orders": len(orders), "reads": i + 1}
            if not pos and not orders:
                return {**last, "flat": True}
        except Exception as exc:
            last = {"error": type(exc).__name__, "reads": i + 1}
        sleep(2.0)
    return {**last, "flat": False}


def stop_and_flatten(run: Run, why: str, sleep) -> int:
    run.emit("stop_rule", why=why)
    r = run.a.flatten(None, SYMBOL, arm=True)
    run.emit("flatten", http_ok=r.get("ok"), why=r.get("why"))
    f = _flat(run.a, 6, sleep)
    run.emit("verify_flat", **f)
    return EXIT_STOPPED_FLAT if f.get("flat") else EXIT_NOT_FLAT


def round_trip(a: DXtradeApiAdapter, *, live: bool, latch: Path, out=print, sleep=time.sleep,
               now: Optional[datetime] = None) -> int:
    run = Run(a, out)
    now = now or datetime.now(timezone.utc)
    vs = VENUE_SPECS[SYMBOL]
    # 0. preflight (reads only)
    acct0 = a.read_account()
    pos0, ord0 = a.read_positions(), a.read_orders()
    run.emit("preflight", balance=acct0.balance, equity=acct0.equity, positions=len(pos0), orders=len(ord0))
    if pos0 or ord0:
        run.emit("refused", why="the account is not flat; this test only runs on an empty account")
        return EXIT_REFUSED
    q = a.read_quote(None, SYMBOL)
    run.emit("quote", symbol=SYMBOL, **(q or {"bid": None, "ask": None}))
    if not q:
        run.emit("refused", why="no quote (could not look); nothing sent")
        return EXIT_REFUSED
    ask = q["ask"]
    sl = round_to_step(ask * (1 - SL_TP_DISTANCE), vs["price_step"])
    tp = round_to_step(ask * (1 + SL_TP_DISTANCE), vs["price_step"])
    tid = "vtrt-" + now.strftime("%Y%m%dT%H%M%S")
    spec = BracketSpec(ticket_id=tid, venue_symbol=SYMBOL, side="long", quantity=QTY,
                       stop_loss=sl, take_profit=tp, order_type="market", price_step=vs["price_step"])
    # 1. dry
    dry = a.place_bracket(None, spec, arm=False)
    run.emit("dry_body", stage=dry.stage, detail=dry.detail, request=dry.form.get("request"),
             expected_cost_usd=round(2 * COMMISSION_RATE * QTY * ask, 4))
    if dry.stage != "form_verified":
        run.emit("refused", why=dry.detail)
        return EXIT_REFUSED
    if not live:
        run.emit("done", result="dry only (no --live): nothing sent")
        return EXIT_OK
    if latch.exists():
        run.emit("refused", why="one-shot latch present: this round trip already ran once")
        return EXIT_REFUSED
    latch.parent.mkdir(parents=True, exist_ok=True)
    latch.write_text(json.dumps({"ticket_id": tid, "at": now.isoformat()}) + "\n")

    ids = client_ids(tid)
    try:
        # 2. place
        att = a.place_bracket(None, spec, arm=True)
        run.emit("place", stage=att.stage, submitted=att.submitted, detail=att.detail,
                 response=att.form.get("response"))
        if att.detail != "http 200":
            return stop_and_flatten(run, f"place: {att.detail}", sleep)
        # 3. read back
        got = None
        for _ in range(6):
            sleep(2.0)
            pos = [p for p in a.read_positions() if p.symbol.upper() == SYMBOL]
            orders = [o for o in a.read_orders() if o.symbol.upper() == SYMBOL]
            if len(pos) == 1 and pos[0].quantity and len(orders) >= 2:
                got = (pos[0], orders)
                break
        if not got:
            return stop_and_flatten(run, "read-back: no single filled position with two working children", sleep)
        p, orders = got
        stops = [o for o in orders if o.order_type == "stop"]
        limits = [o for o in orders if o.order_type == "limit"]
        rb = {"position_qty": p.quantity, "side": p.side, "fill_price": p.entry_price,
              "position_sl": p.stop_loss, "position_tp": p.take_profit,
              "stop_orders": [(o.price, o.raw.get("positionEffect"), o.order_id == ids["sl"]) for o in stops],
              "limit_orders": [(o.price, o.raw.get("positionEffect"), o.order_id == ids["tp"]) for o in limits]}
        run.emit("read_back", **rb)
        ok = (p.side == "long" and abs((p.quantity or 0) - QTY) < 1e-9
              and len(stops) == 1 and len(limits) == 1
              and stops[0].price is not None and abs(stops[0].price - sl) < 1e-9
              and limits[0].price is not None and abs(limits[0].price - tp) < 1e-9
              and stops[0].raw.get("positionEffect") == "CLOSE" and limits[0].raw.get("positionEffect") == "CLOSE")
        if not ok:
            return stop_and_flatten(run, f"read-back mismatch (typed sl={sl} tp={tp} qty={QTY})", sleep)
        # 4. modify SL one tick tighter (a long's stop moves UP)
        new_sl = round_to_step(sl + vs["price_step"], vs["price_step"])
        m = a.modify_bracket(None, p, new_sl, None, arm=True)
        run.emit("modify", ok=m.get("ok"), why=m.get("why"), legs=m.get("legs"), new_sl=new_sl)
        if not m.get("ok"):
            return stop_and_flatten(run, f"modify: {m.get('why')}", sleep)
        sleep(2.0)
        stops2 = [o for o in a.read_orders() if o.symbol.upper() == SYMBOL and o.order_type == "stop"]
        run.emit("modify_read_back", stop_prices=[o.price for o in stops2])
        if len(stops2) != 1 or stops2[0].price is None or abs(stops2[0].price - new_sl) > 1e-9:
            return stop_and_flatten(run, f"modify read-back mismatch (want sl={new_sl})", sleep)
        # 5. close
        c = a.flatten(None, SYMBOL, arm=True, side="long", quantity=QTY)
        run.emit("close", ok=c.get("ok"), why=c.get("why"))
        if not c.get("ok"):
            return stop_and_flatten(run, f"close: {c.get('why')}", sleep)
        # 6. reconcile
        f = _flat(a, 6, sleep)
        run.emit("reconcile_flat", **f)
        if not f.get("flat"):
            return stop_and_flatten(run, "not flat after close", sleep)
        hist: List[Dict[str, Any]] = []
        try:
            hist = [h for h in a.read_history_today(SYMBOL)
                    if str(h.get("issue_time") or "") >= now.strftime("%Y-%m-%dT%H:%M")]
        except Exception as exc:
            run.emit("history", error=type(exc).__name__)
        for h in hist:
            run.emit("history_order", **{k: h.get(k) for k in ("client_id", "type", "side", "status",
                                                                 "position_effect", "avg_price", "filled_qty", "fills")})
        acct1 = a.read_account()
        delta = (acct1.balance - acct0.balance) if acct1.balance is not None and acct0.balance is not None else None
        run.emit("result", passed=True, balance_before=acct0.balance, balance_after=acct1.balance,
                 cost_usd=(None if delta is None else round(-delta, 4)), fill_entry=p.entry_price)
        return EXIT_OK
    except Exception as exc:  # anything unexpected after the latch: flatten, never leave it open
        return stop_and_flatten(run, f"exception {type(exc).__name__}: {a.redact(str(exc))[:160]}", sleep)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--live", action="store_true", help="send the orders (default: dry, nothing sent)")
    ap.add_argument("--latch", default=str(DEFAULT_LATCH))
    args = ap.parse_args(argv)
    cfg = load_platform_config(ACCOUNT)
    if cfg["platform"] != "dxtrade_api":
        print(json.dumps({"step": "refused", "why": f"{ACCOUNT} platform is {cfg['platform']!r}, not dxtrade_api"}))
        return EXIT_REFUSED
    user = os.environ.get(cfg.get("username_env", ""), "")
    pw = os.environ.get(cfg.get("password_env", ""), "")
    print(json.dumps({"step": "start", "account": ACCOUNT, "live": args.live,
                      "credentials": "set" if user and pw else "MISSING"}))
    if not (user and pw):
        return EXIT_NOCREDS
    a = DXtradeApiAdapter(domain=os.environ.get("VELOTRADE_DX_DOMAIN") or None)
    try:
        a.login(None, cfg["login_url"], user, pw)
    except FeasibilityError as fe:
        print(a.redact(json.dumps({"step": "login", "feasibility": fe.reason, "detail": fe.detail})))
        return EXIT_ENV
    try:
        rc = round_trip(a, live=args.live, latch=Path(args.latch))
    finally:
        st = a.logout()
        print(json.dumps({"step": "logout", "http": st}))
        print(a.redact(json.dumps({"step": "calls", "calls": a.calls})))
    return rc


if __name__ == "__main__":
    sys.exit(main())
