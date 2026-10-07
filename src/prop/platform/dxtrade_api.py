"""DXtrade REST order path (``platform: dxtrade_api``) — lane VELOTRADE-API-EXEC.

Design: ``docs/research/velotrade-api-executor-design-2026-10-05.md``. Measured
specs: ``docs/research/velotrade-api-probe-2026-10-05.md``. REST spec read
2026-10-05 at ``dx.velotrade.com/developers`` (DXtrade-REST-API.md + models).

The same :class:`PropPlatformAdapter` methods as the browser adapter, over the
REST API instead of the DOM. ``page`` is accepted and IGNORED everywhere, so
every guard, the ledger, the retries and the alerts above the adapter
(``src/prop/prop_executor.py``, ``prop_trail.py``) are unchanged.

DRY BY DEFAULT, exactly like the browser adapter: every call that would change
the account takes ``arm`` and, with ``arm=False`` (the default), builds the
exact request and returns it WITHOUT sending it.

What the spec says, and what this module does about it:

* **Bracket = one IF-THEN order group** (``POST /accounts/{a}/orders``): parent
  ``positionEffect: OPEN`` with a nonzero quantity, children ``CLOSE``, opposite
  side, quantity absent, ``tif: GTC``. *"Order groups can be issued only for
  Position-based accounts"* — velotrade_1 is ``positionBased: true`` (MEASURED,
  issue #16631), so there is no naked window between fill and protection.
* **POST is not idempotent**: every order carries a client ``orderCode``. A
  duplicate returns ``409`` error ``100`` — read as ALREADY PLACED, never as a
  reason to place again.
* **PUT / DELETE are conditional**: ``If-Match`` with the ``ETag`` of
  ``GET /accounts/{a}/orders`` (List Open Orders; the spec says it sends one).
  No header -> ``403``; stale -> ``412``: re-read and retry ONCE.
* **Rate limits** (defaults; the operator may differ): reads 10/s, trading
  10/s, login 1/s. A ``429`` is backed off (1 s, 2 s, 4 s) and retried; a
  repeated POST is safe because the same ``orderCode`` makes it a ``409/100``.
* **Flatten** = Bulk Close (``POST /accounts/{a}/close``) filtered to ONE
  instrument, closing positions AND cancelling its working orders. Never an
  unfiltered close-all.

Nothing secret leaves this module: results carry no credential, token or
account code; :meth:`DXtradeApiAdapter.redact` scrubs any text a caller prints.
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.prop.platform.base import (
    AccountSnapshot,
    BracketSpec,
    FeasibilityError,
    PlaceAttempt,
    Position,
    PropPlatformAdapter,
    WorkingOrder,
)

UA = "metis-insights-dxtrade-api/1"
# 429 backoff schedule, seconds. Three retries, then the 429 is returned.
BACKOFF_S = (1.0, 2.0, 4.0)

# MEASURED 2026-10-05 12:43Z over REST on velotrade_1 (account-scoped
# instruments, issue #16616, run 37311474412; memo § 1). Quantity is in UNITS
# (lotSize 1). A symbol absent here is refused: sizing an unmeasured contract
# would send a number whose risk nobody computed.
VENUE_SPECS: Dict[str, Dict[str, float]] = {
    "ETHUSD": {"min_qty": 0.01, "qty_step": 0.01, "price_step": 0.01},
    "SOLUSD": {"min_qty": 0.1, "qty_step": 0.1, "price_step": 0.001},
    "XRPUSD": {"min_qty": 10.0, "qty_step": 10.0, "price_step": 0.00001},
    "BTCUSD": {"min_qty": 0.001, "qty_step": 0.001, "price_step": 0.01},
}

# (method, url, body bytes | None, headers, timeout_s) -> (status, headers, text)
Transport = Callable[[str, str, Optional[bytes], Dict[str, str], float], Tuple[int, Dict[str, str], str]]


def urllib_transport(method: str, url: str, data: Optional[bytes], headers: Dict[str, str],
                     timeout: float) -> Tuple[int, Dict[str, str], str]:
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        hdrs = {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}
        return e.code, hdrs, e.read().decode("utf-8", "replace")
    except Exception as e:  # network / TLS / timeout: status 0, never raised
        return 0, {}, f"{type(e).__name__}: {e}"


@dataclass
class ApiResponse:
    status: int
    body: Any = None
    text: str = ""
    etag: Optional[str] = None
    retries_429: int = 0

    @property
    def ok(self) -> bool:
        return self.status == 200

    def error(self) -> Dict[str, Any]:
        """Status plus the spec's error envelope (``errorCode``, ``description``)."""
        out: Dict[str, Any] = {"http": self.status}
        if isinstance(self.body, dict):
            if "errorCode" in self.body:
                out["errorCode"] = self.body.get("errorCode")
            if self.body.get("description"):
                out["description"] = str(self.body.get("description"))[:160]
        elif self.text and self.status != 200:
            out["body"] = self.text[:160]
        return out


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _on_step(x: float, step: float) -> bool:
    n = x / step
    return abs(n - round(n)) < 1e-6


def round_to_step(x: float, step: float) -> float:
    """Nearest multiple of ``step``, printed without float noise."""
    decimals = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return round(round(x / step) * step, decimals + 2)


def _num(x: float) -> str:
    """Numbers as strings, the form the spec's own examples send."""
    return format(x, ".10f").rstrip("0").rstrip(".") if x != int(x) else str(int(x))


def _side_for(direction: str) -> str:
    return "BUY" if direction == "long" else "SELL"


def _opposite(side: str) -> str:
    return "SELL" if side == "BUY" else "BUY"


def check_spec(spec: BracketSpec) -> List[str]:
    """Refusals for a bracket the venue would reject or we could not protect."""
    bad: List[str] = []
    sym = (spec.venue_symbol or "").upper()
    vs = VENUE_SPECS.get(sym)
    if vs is None:
        return [f"{sym or '?'}: no measured venue spec (refused, never guessed)"]
    if spec.side not in ("long", "short"):
        bad.append(f"side {spec.side!r} is not long/short")
    q = _f(spec.quantity)
    if q is None or q <= 0:
        bad.append("quantity must be > 0")
    else:
        if q < vs["min_qty"] - 1e-12:
            bad.append(f"quantity {q} below the venue minimum {vs['min_qty']}")
        if not _on_step(q, vs["qty_step"]):
            bad.append(f"quantity {q} not a multiple of the step {vs['qty_step']}")
    sl, tp = _f(spec.stop_loss), _f(spec.take_profit)
    if sl is None or tp is None or sl <= 0 or tp <= 0:
        bad.append("stop_loss and take_profit are both required (no unprotected entry)")
    elif spec.side == "long" and not sl < tp:
        bad.append("long: stop_loss must be below take_profit")
    elif spec.side == "short" and not sl > tp:
        bad.append("short: stop_loss must be above take_profit")
    if spec.order_type not in ("market", "limit"):
        bad.append(f"order_type {spec.order_type!r} is not market/limit")
    if spec.order_type == "limit":
        lp = _f(spec.limit_price)
        if lp is None or lp <= 0:
            bad.append("limit order without a limit_price")
        elif sl is not None and tp is not None:
            if spec.side == "long" and not (sl < lp < tp):
                bad.append("long limit: need stop_loss < limit < take_profit")
            if spec.side == "short" and not (tp < lp < sl):
                bad.append("short limit: need take_profit < limit < stop_loss")
    return bad


def client_ids(ticket_id: str) -> Dict[str, str]:
    """Deterministic client order ids for one ticket's bracket: the SAME ticket
    always produces the SAME ids, so a re-POST after an unknown outcome is
    answered 409/100 instead of opening a second position. The spec allows
    latin alphanumerics and ``-_.``; max 64 chars."""
    base = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in str(ticket_id))[:56] or "ticket"
    return {"entry": f"{base}-E", "sl": f"{base}-S", "tp": f"{base}-T"}


def build_bracket_body(spec: BracketSpec) -> Dict[str, Any]:
    """The IF-THEN Order Group Request for one bracket (spec: Place Order,
    Example 1 step 3). Pure: no I/O, no rounding of the quantity (the executor
    sized it; :func:`check_spec` refuses an off-step one)."""
    sym = spec.venue_symbol.upper()
    step = (VENUE_SPECS.get(sym) or {}).get("price_step") or spec.price_step
    px = (lambda v: _num(round_to_step(float(v), step))) if step else (lambda v: _num(float(v)))
    ids = client_ids(spec.ticket_id)
    side = _side_for(spec.side)
    parent: Dict[str, Any] = {
        "orderCode": ids["entry"], "type": "MARKET" if spec.order_type == "market" else "LIMIT",
        "instrument": sym, "quantity": _num(float(spec.quantity)), "positionEffect": "OPEN",
        "side": side, "tif": "GTC",
    }
    if spec.order_type == "limit":
        parent["limitPrice"] = px(spec.limit_price)
    child = {"instrument": sym, "positionEffect": "CLOSE", "side": _opposite(side), "tif": "GTC"}
    return {
        "orders": [
            parent,
            {"orderCode": ids["sl"], "type": "STOP", **child, "stopPrice": px(spec.stop_loss)},
            {"orderCode": ids["tp"], "type": "LIMIT", **child, "limitPrice": px(spec.take_profit)},
        ],
        "contingencyType": "IF-THEN",
    }


@dataclass
class DXtradeApiAdapter(PropPlatformAdapter):
    """One DXtrade REST session on one account. ``login_url`` is the REST base
    (``https://<host>/dxsca-web``)."""

    platform: str = "dxtrade_api"
    # TP doctrine B1: modify_bracket PUTs the TP child's limitPrice (one
    # conditional PUT per leg, spec'd and built for VELOTRADE-GOLIVE).
    TP_AMEND_SUPPORTED = True
    transport: Transport = field(default=urllib_transport)
    sleep: Callable[[float], None] = field(default=time.sleep)
    timeout_s: float = 25.0
    domain: Optional[str] = None          # None: the username's @suffix, else "default"
    base_url: str = ""
    token: Optional[str] = None
    account: Optional[str] = None
    orders_etag: Optional[str] = None
    _secrets: List[str] = field(default_factory=list)
    calls: List[Dict[str, Any]] = field(default_factory=list)   # method/path-shape/status, no secrets

    # ── plumbing ────────────────────────────────────────────────────────
    def redact(self, text: Any) -> str:
        s = str(text)
        for sec in sorted({x for x in self._secrets if x and len(x) >= 3}, key=len, reverse=True):
            s = s.replace(sec, "<redacted>").replace(urllib.parse.quote(sec, safe=""), "<redacted>")
        return s

    def _acct(self) -> str:
        if not self.account:
            raise FeasibilityError("no_session", "login first")
        return urllib.parse.quote(self.account, safe="")

    def _request(self, method: str, path: str, body: Any = None,
                 headers: Optional[Dict[str, str]] = None, *, auth: bool = True) -> ApiResponse:
        hdrs = {"Accept": "application/json", "User-Agent": UA}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        if auth and self.token:
            hdrs["Authorization"] = f"DXAPI {self.token}"
        hdrs.update(headers or {})
        retries = 0
        while True:
            status, rh, text = self.transport(method, self.base_url + path, data, hdrs, self.timeout_s)
            if status == 429 and retries < len(BACKOFF_S):
                self.sleep(BACKOFF_S[retries])
                retries += 1
                continue
            break
        try:
            parsed = json.loads(text) if text else None
        except ValueError:
            parsed = None
        # The account code is a path segment: log the path's SHAPE only.
        shape = path.split("?", 1)[0]
        if self.account:
            shape = shape.replace(urllib.parse.quote(self.account, safe=""), "{a}")
        self.calls.append({"method": method, "path": shape, "status": status, "retries_429": retries})
        return ApiResponse(status=status, body=parsed, text=text, etag=(rh or {}).get("etag"),
                           retries_429=retries)

    # ── session ─────────────────────────────────────────────────────────
    def login(self, page: Any, login_url: str, username: str, password: str) -> None:
        if not username or not password:
            raise FeasibilityError("no_credentials", "username/password env vars are empty")
        self.base_url = login_url.rstrip("/")
        user, _, suffix = username.partition("@")
        domain = self.domain or suffix or "default"
        self._secrets.extend([username, user, password, domain if domain != "default" else ""])
        r = self._request("POST", "/login", {"username": user, "domain": domain, "password": password},
                          auth=False)
        tok = r.body.get("sessionToken") if isinstance(r.body, dict) else None
        if not tok:
            raise FeasibilityError("login_rejected", self.redact(json.dumps(r.error())))
        self.token = tok
        self._secrets.append(tok)
        r = self._request("GET", "/users/" + urllib.parse.quote(f"{user}@{domain}", safe=""))
        accounts = _find_accounts(r.body)
        codes = [c for c in (_account_code(a) for a in accounts) if c]
        self._secrets.extend(codes)
        if len(codes) != 1:
            # Never guess which account an order goes to.
            raise FeasibilityError("ambiguous_account", f"{len(codes)} accounts on this login (need exactly 1)")
        self.account = codes[0]

    def resume_session(self, page: Any, login_url: str, login_form_grace_ms: int = 0) -> str:
        return "logged_in" if self.token and self.account else "login_form"

    def wait_ready(self, page: Any, timeout_ms: Optional[int] = None) -> bool:
        return bool(self.token and self.account)

    def logout(self) -> int:
        if not self.token:
            return 0
        r = self._request("POST", "/logout")
        self.token = None
        return r.status

    # ── reads ───────────────────────────────────────────────────────────
    def read_account(self, page: Any = None) -> AccountSnapshot:
        r = self._request("GET", f"/accounts/{self._acct()}/metrics")
        m = r.body if isinstance(r.body, dict) else {}
        if isinstance(m.get("metrics"), list):
            m = (m["metrics"][0] if m["metrics"] else {}) or {}
        snap = AccountSnapshot(balance=_f(m.get("balance")), equity=_f(m.get("equity")),
                               unrealized=_f(m.get("openPl")), margin_used=_f(m.get("margin")),
                               available=_f(m.get("availableFunds")))
        snap.unparsed = [k for k, v in (("balance", snap.balance), ("equity", snap.equity),
                                        ("unrealized", snap.unrealized)) if v is None]
        snap.unparsed.append("realized_today")   # not a REST metrics field: "could not look"
        return snap

    def _portfolio(self) -> Dict[str, Any]:
        r = self._request("GET", f"/accounts/{self._acct()}/portfolio")
        if not r.ok:
            raise RuntimeError(f"portfolio read failed {r.error()}")
        js = r.body if isinstance(r.body, dict) else {}
        pf = js.get("portfolios")
        return (pf[0] if isinstance(pf, list) and pf else js) or {}

    def read_positions(self, page: Any = None) -> List[Position]:
        out = []
        for p in self._portfolio().get("positions") or []:
            out.append(Position(
                symbol=str(p.get("symbol") or ""), side="long" if p.get("side") == "BUY" else "short",
                quantity=_f(p.get("quantity")), entry_price=_f(p.get("openPrice")),
                stop_loss=_f(p.get("stopLossPrice")), take_profit=_f(p.get("takeProfitPrice")),
                raw={"positionCode": str(p.get("positionCode") or "")}))
        return out

    def read_orders(self, page: Any = None) -> List[WorkingOrder]:
        """List Open Orders. Also keeps the response's ETag: it is what a
        modify / cancel must send as ``If-Match``."""
        r = self._request("GET", f"/accounts/{self._acct()}/orders")
        if not r.ok:
            raise RuntimeError(f"open-orders read failed {r.error()}")
        self.orders_etag = r.etag
        rows = r.body if isinstance(r.body, list) else (r.body or {}).get("orders") or []
        out = []
        for o in rows:
            if not isinstance(o, dict) or o.get("finalStatus"):
                continue
            leg = (o.get("legs") or [{}])[0] or {}
            out.append(WorkingOrder(
                symbol=str(o.get("instrument") or ""), side="long" if o.get("side") == "BUY" else "short",
                order_type=str(o.get("type") or "").lower(), quantity=_f(leg.get("quantity")),
                price=_f(leg.get("price")), order_id=str(o.get("clientOrderId") or o.get("orderCode") or ""),
                raw={"positionEffect": str(leg.get("positionEffect") or ""),
                     "positionCode": str(leg.get("positionCode") or ""),
                     "status": str(o.get("status") or ""), "side": str(o.get("side") or "")}))
        return out

    def read_quote(self, page: Any, venue_symbol: str) -> Optional[Dict[str, float]]:
        r = self._request("POST", "/marketdata", {"eventTypes": [{"type": "Quote", "format": "COMPACT"}],
                                                   "symbols": [venue_symbol.upper()]})
        for ev in (r.body or {}).get("events") or [] if isinstance(r.body, dict) else []:
            if str(ev.get("symbol", "")).upper() == venue_symbol.upper():
                bid, ask = _f(ev.get("bid")), _f(ev.get("ask"))
                if bid is not None and ask is not None:
                    return {"bid": bid, "ask": ask}
        return None

    def read_history(self, client_order_ids: List[str]) -> List[Dict[str, Any]]:
        """Final + working orders for these client ids, with their TRADE fills
        (``lastPrice`` x ``lastQuantity``). No account code in the output."""
        q = urllib.parse.quote(",".join(client_order_ids), safe=",")
        r = self._request("GET", f"/accounts/{self._acct()}/orders/history?with-client-id={q}")
        return _history_rows(r.body)

    def read_history_today(self, instrument: str) -> List[Dict[str, Any]]:
        q = urllib.parse.quote(instrument, safe="")
        r = self._request("GET", f"/accounts/{self._acct()}/orders/history?period=today&for-instrument={q}")
        return _history_rows(r.body)

    def read_trade_history(self, page: Any = None, restore: Optional[str] = None,
                           query: str = "period=today") -> List[Dict[str, Any]]:
        """READ-ONLY (``GET .../orders/history``): executed trades in the shape
        ``prop_executor.match_exit`` consumes from the browser adapter's Trade
        History read (:func:`trade_history_rows`). ``page`` / ``restore`` are
        accepted and ignored. Raises ``RuntimeError`` on a non-200 (the caller
        then keeps the close unread, never guessed); ``[]`` means the read
        succeeded and found no executions."""
        r = self._request("GET", f"/accounts/{self._acct()}/orders/history?{query}")
        if not r.ok:
            raise RuntimeError(f"trade history read failed {self.redact(json.dumps(r.error()))}")
        return trade_history_rows(r.body)

    # ── order controls (dry unless arm=True) ────────────────────────────
    def place_bracket(self, page: Any, spec: BracketSpec, *, arm: bool = False) -> PlaceAttempt:
        bad = check_spec(spec)
        if bad:
            return PlaceAttempt(stage="refused", detail="; ".join(bad))
        body = build_bracket_body(spec)
        if not arm:
            return PlaceAttempt(stage="form_verified", detail="dry: IF-THEN body built, NOT sent",
                                form={"request": body})
        r = self._request("POST", f"/accounts/{self._acct()}/orders", body)
        form = {"request": body, "response": r.error() if not r.ok else r.body}
        if r.ok:
            return PlaceAttempt(stage="submitted", submitted=True, detail="http 200", form=form)
        if r.status == 409 and isinstance(r.body, dict) and str(r.body.get("errorCode")) == "100":
            # The client ids already exist: an earlier POST of THIS ticket got
            # through. Already placed; the re-read confirms it.
            return PlaceAttempt(stage="submitted", submitted=True,
                                detail="already_placed (409/100 duplicate client order id)", form=form)
        if r.status == 0 or r.status >= 500:
            # Outcome unknown (timeout / server error): it MAY exist. Reported
            # as submitted so the caller confirms by re-read instead of
            # assuming nothing happened. A re-POST would be a 409/100.
            return PlaceAttempt(stage="submitted", submitted=True,
                                detail=f"outcome_unknown {self.redact(json.dumps(r.error()))}", form=form)
        return PlaceAttempt(stage="refused", detail=f"rejected {self.redact(json.dumps(r.error()))}", form=form)

    def _protection_orders(self, position: Position) -> Tuple[Optional[WorkingOrder], Optional[WorkingOrder]]:
        """The working CLOSE STOP (SL) and CLOSE LIMIT (TP) attached to this
        position. Refreshes ``orders_etag`` as a side effect."""
        code = (position.raw or {}).get("positionCode") or ""
        sl = tp = None
        for o in self.read_orders():
            if o.symbol.upper() != position.symbol.upper() or o.raw.get("positionEffect") != "CLOSE":
                continue
            if code and o.raw.get("positionCode") and o.raw["positionCode"] != code:
                continue
            if o.order_type == "stop":
                sl = o
            elif o.order_type == "limit":
                tp = o
        return sl, tp

    def _put_with_etag(self, body: Dict[str, Any]) -> Tuple[ApiResponse, int]:
        """PUT with If-Match; on 412 re-read (fresh ETag) and retry ONCE."""
        attempts = 0
        while True:
            attempts += 1
            hdr = {"If-Match": self.orders_etag} if self.orders_etag else {}
            r = self._request("PUT", f"/accounts/{self._acct()}/orders", body, hdr)
            if r.status == 412 and attempts == 1:
                self.read_orders()
                continue
            return r, attempts

    def modify_bracket(self, page: Any, position: Position, stop_loss: Optional[float],
                       take_profit: Optional[float], *, arm: bool = False, rollout: Any = None) -> Dict[str, Any]:
        """Re-price the position's SL and/or TP: one conditional PUT per leg
        (a single-order Modify Order request on the child's own client id).
        The filled MARKET parent is final, so the group itself cannot be PUT
        (spec: error 1005, "Reference order is closed")."""
        sym = position.symbol.upper()
        step = (VENUE_SPECS.get(sym) or {}).get("price_step")
        try:
            sl_o, tp_o = self._protection_orders(position)
        except Exception as exc:
            return {"ok": False, "clicked": False, "why": f"orders read failed ({type(exc).__name__})"}
        bodies = []
        for want, o, kind, key in ((stop_loss, sl_o, "sl", "stopPrice"), (take_profit, tp_o, "tp", "limitPrice")):
            if want is None:
                continue
            if o is None or not o.order_id:
                return {"ok": False, "clicked": False, "why": f"no working {kind} order for {sym}"}
            px = round_to_step(float(want), step) if step else float(want)
            b = {"orderCode": o.order_id, "instrument": sym, "positionEffect": "CLOSE", "side": o.raw.get("side"),
                 key: _num(px), "tif": "GTC"}
            if o.raw.get("positionCode"):
                b["positionCode"] = o.raw["positionCode"]
            bodies.append((kind, b))
        if not bodies:
            return {"ok": False, "clicked": False, "why": "nothing to modify"}
        if not arm:
            return {"ok": True, "clicked": False, "why": "dry: PUT body built, NOT sent",
                    "would_send": [b for _, b in bodies]}
        legs = []
        for kind, b in bodies:
            etag_before = self.orders_etag
            r, attempts = self._put_with_etag(b)
            legs.append({"leg": kind, "http": r.status, "attempts": attempts,
                         "etag_changed": bool(r.etag) and r.etag != etag_before, **({} if r.ok else r.error())})
            if r.ok and r.etag:
                self.orders_etag = r.etag
            if not r.ok:
                return {"ok": False, "clicked": True, "why": self.redact(f"{kind} modify {r.error()}"), "legs": legs}
        return {"ok": True, "clicked": True, "why": "http 200", "legs": legs}

    def cancel_order(self, page: Any, order: WorkingOrder, *, arm: bool = False) -> Dict[str, Any]:
        if not order or not order.order_id:
            return {"ok": False, "clicked": False, "why": "no order_id: refusing to guess which order"}
        path = f"/accounts/{self._acct()}/orders/{urllib.parse.quote(order.order_id, safe='')}"
        if not arm:
            return {"ok": True, "clicked": False, "why": "dry: DELETE built, NOT sent"}
        if self.orders_etag is None:
            self.read_orders()
        for attempt in (1, 2):
            r = self._request("DELETE", path, None, {"If-Match": self.orders_etag} if self.orders_etag else {})
            if r.status == 412 and attempt == 1:
                self.read_orders()
                continue
            break
        return {"ok": r.ok, "clicked": True, "why": "http 200" if r.ok else self.redact(str(r.error()))}

    def flatten(self, page: Any, symbol: Optional[str] = None, *, arm: bool = False,
                side: Optional[str] = None, quantity: Optional[float] = None,
                entry_price: Optional[float] = None, rel_tol: float = 0.02) -> Dict[str, Any]:
        """Close ONE instrument: its positions closed and its working orders
        cancelled in one Bulk Close. A symbol is required (never close-all).
        When the caller states side / size / fill, the open position must
        match them first (same posture as the browser adapter)."""
        if not symbol:
            return {"ok": False, "clicked": False, "why": "a symbol is required (never close-all)"}
        sym = symbol.upper()
        if side or quantity or entry_price:
            try:
                rows = [p for p in self.read_positions() if p.symbol.upper() == sym]
            except Exception as exc:
                return {"ok": False, "clicked": False, "why": f"positions read failed ({type(exc).__name__})"}
            if len(rows) != 1:
                return {"ok": False, "clicked": False, "why": f"{len(rows)} {sym} positions (need exactly 1)"}
            p = rows[0]
            for want, got, name in ((quantity, p.quantity, "quantity"), (entry_price, p.entry_price, "entry")):
                if want and got and abs(got - want) > rel_tol * abs(want):
                    return {"ok": False, "clicked": False, "why": f"{name} mismatch ({got} vs {want})"}
            if side and p.side != side:
                return {"ok": False, "clicked": False, "why": f"side mismatch ({p.side} vs {side})"}
        body = {"instrument": sym, "closePositions": True, "cancelOrders": True,
                "comment": "metis flatten"}
        if not arm:
            return {"ok": True, "clicked": False, "why": "dry: Bulk Close built, NOT sent", "would_send": body}
        r = self._request("POST", f"/accounts/{self._acct()}/close", body)
        return {"ok": r.ok, "clicked": True, "why": "http 200" if r.ok else self.redact(str(r.error()))}


def _find_accounts(o: Any) -> List[Any]:
    """``/users`` wraps the accounts list differently per deployment (MEASURED
    on Velotrade: ``userDetails`` is a LIST, probe v4 #16608) — search for it."""
    if isinstance(o, dict):
        if isinstance(o.get("accounts"), list):
            return o["accounts"]
        for v in o.values():
            r = _find_accounts(v)
            if r:
                return r
    elif isinstance(o, list):
        for v in o:
            r = _find_accounts(v)
            if r:
                return r
    return []


def _account_code(a: Any) -> Optional[str]:
    if isinstance(a, dict):
        c = a.get("account") or a.get("accountCode") or a.get("code")
        return c if isinstance(c, str) and c else None
    return a if isinstance(a, str) and a else None


def _history_rows(js: Any) -> List[Dict[str, Any]]:
    orders = js.get("orders") if isinstance(js, dict) else js
    out = []
    for o in orders or []:
        if not isinstance(o, dict):
            continue
        fills = [{"price": _f(e.get("lastPrice")), "qty": _f(e.get("lastQuantity"))}
                 for e in o.get("executions") or []
                 if isinstance(e, dict) and _f(e.get("lastPrice")) is not None and _f(e.get("lastQuantity"))]
        leg = (o.get("legs") or [{}])[0] or {}
        out.append({"client_id": o.get("clientOrderId"), "type": o.get("type"), "side": o.get("side"),
                    "status": o.get("status"), "final": o.get("finalStatus"),
                    "position_effect": leg.get("positionEffect"), "avg_price": _f(leg.get("averagePrice")),
                    "filled_qty": _f(leg.get("filledQuantity")), "fills": fills,
                    "issue_time": o.get("issueTime")})
    return out


def _iso(text: Any) -> Optional[datetime]:
    """An ISO-8601 instant (``2026-10-06T10:18:03.123Z`` / ``+00:00``) as an
    aware UTC datetime; None when it does not parse (never guessed)."""
    t = str(text or "").strip()
    if not t:
        return None
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        return None
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


_EXEC_TIME_KEYS = ("executionTime", "time", "transactionTime", "timestamp")


def trade_history_rows(js: Any) -> List[Dict[str, Any]]:
    """One row per execution (TRADE fill) in an Order History body, the shape of
    ``dxtrade.trade_history_from_tables``: ``time`` (raw), ``ts`` (UTC datetime
    or None), ``symbol``, ``side`` (long = BUY / short = SELL), ``effect``
    ("opening" / "closing" / None, from the leg's positionEffect), ``volume``
    (``lastQuantity``), ``price`` (``lastPrice``), ``commission``,
    ``closed_pnl`` and ``net_closed_pnl`` (None unless the venue sent them: the
    REST order model is not known to carry realized P&L, and an absent figure is
    never filled in). Order and account identifiers are NOT carried. An
    execution without a usable price or quantity is skipped, not defaulted."""
    orders = js.get("orders") if isinstance(js, dict) else js
    out: List[Dict[str, Any]] = []
    for o in orders or []:
        if not isinstance(o, dict):
            continue
        leg = (o.get("legs") or [{}])[0] or {}
        eff = str(leg.get("positionEffect") or "").upper()
        side = str(o.get("side") or leg.get("side") or "").upper()
        for e in o.get("executions") or []:
            if not isinstance(e, dict):
                continue
            px, qty = _f(e.get("lastPrice")), _f(e.get("lastQuantity"))
            if px is None or not qty:
                continue
            raw = next((e[k] for k in _EXEC_TIME_KEYS if e.get(k)), None) or o.get("transactionTime") \
                or o.get("issueTime") or ""
            out.append({
                "time": str(raw), "ts": _iso(raw),
                "symbol": str(o.get("instrument") or leg.get("instrument") or "").upper(),
                "side": "long" if side == "BUY" else "short" if side == "SELL" else None,
                "effect": "opening" if eff == "OPEN" else "closing" if eff == "CLOSE" else None,
                "volume": abs(qty), "price": px,
                "commission": _f(e.get("commission")),
                "closed_pnl": _f(e.get("closedPnl") if e.get("closedPnl") is not None else e.get("closedPL")),
                "net_closed_pnl": _f(e.get("netClosedPnl")),
            })
    out.sort(key=lambda r: (r["ts"] is None, r["ts"] or datetime.min.replace(tzinfo=timezone.utc)))
    return out
