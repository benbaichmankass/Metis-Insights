#!/usr/bin/env python3
"""Re-arm ONE GTC OCO protective sized to an Alpaca position's LIVE net qty.

Why this exists
---------------
``alpaca_paper``/SPY, diagnosed 2026-09-26 (pipeline record
``PI-20260926-HJPL5ABP-0001``): the broker holds a netted **19**-share long
(journal trade 6131 = 8, plus an untracked earlier 11). The only resting
protection is a stale GTC OCO sized **11**. Trade 6131's own bracket was
``time_in_force: day`` (``AlpacaClient.place``) and Alpaca cancelled it at the
close, so 8 shares are naked. There was no sanctioned way to repair it: the only
Alpaca system action was ``flatten-alpaca-position``, and the only code path
that places an Alpaca protective leg (``AlpacaClient.place_protective``) cancels
EVERY order on the symbol first with no check of what it is cancelling
(``BL-20260908-ALPACA-PLACE-PROTECTIVE-IGNORES-OCA-KEY-…``). The operator
approved this remedy on 2026-09-27 (MANAGER-CHECKLIST row R4, decision 2).

What it does
------------
1. Reads the LIVE position (``GET /v2/positions/{sym}``) and the resting orders
   (``GET /v2/orders?status=open&nested=true``). Three-state: an unreadable read
   is ``could_not_look``, never "flat" and never "no orders".
2. Reads the OPEN journal row(s) for (account, symbol), READ-ONLY. The SL/TP are
   taken from the journal — **never from the caller**.
3. DRY-RUN by default: prints the plan. ``--apply`` executes:
   cancel the resting protective legs → wait until every cancelled order reads
   terminal AND ``qty_available`` has released the full net qty → place ONE
   GTC OCO for the net qty → confirm it was not rejected → re-read and verify
   coverage.

The level rule (stated, because a netted position can carry several rows)
-------------------------------------------------------------------------
* exactly one open row → that row's ``stop_loss`` / ``take_profit_1``;
* several open rows whose levels agree at the venue's 2dp → those levels;
* several open rows whose levels DIFFER → **refused** (``ambiguous_levels``).
  Picking one (e.g. the newest) would move protection another strategy chose,
  which is BL-20260908-ALPACA-TRAILING-AMEND-PATCHED-A-SIBLING-TRADES-STOP in a
  different shape. The rule is echoed in the output as ``level_rule``.

Shares the journal does not account for (the untracked 11 on SPY) are covered
at the journal row's levels. That is reported (``untracked_qty``), not refused:
covering them is the point. A journal that claims MORE than the broker holds is
refused — that geometry is not understood.

Refusals
--------
``refused_non_protective_order`` (a resting order that does not REDUCE the
position, or is not a stop/limit — it could fill after the new OCO flattens the
book and open a reverse), ``refused_direction_mismatch``, ``refused_no_open_row``
/ ``refused_levels_missing``, ``ambiguous_levels``, ``refused_levels_wrong_side``
(a stop at or through the current price would trigger on placement),
``refused_journal_exceeds_position``, ``refused_fractional_position``,
``refused_unreadable_leg`` and ``could_not_look``. Each refusal happens BEFORE
anything is cancelled.

The cancel→place gap, and what happens when the place fails
-----------------------------------------------------------
Alpaca will not accept a sell OCO for 19 while 11 shares are
``held_for_orders`` by the old OCO, so this cannot be place-then-cancel. The gap
is bounded to the cancel settle (polled, not slept — ``ALPACA_REARM_SETTLE_S``,
default 15s) plus one POST. If the place is refused it is retried once; if it
is refused again the OLD protection is re-placed at its own qty and prices
(``restored_old_protection``, exit 1). If that also fails the result is
``NAKED`` — exit 5, which ``notify_run.sh`` pages as urgent. If the cancel does
not settle in the window (e.g. an order sits ``pending_cancel``) nothing is
placed and the result is ``cancel_unsettled`` (exit 5): the shares are still
held, so a place would only be rejected.

Outside RTH
-----------
Alpaca accepts OCO/bracket orders with ``time_in_force`` ``day`` or ``gtc``, and
an order submitted after the close "is queued and submitted the following
trading day" (docs.alpaca.markets/docs/orders-at-alpaca, read 2026-09-27). So
a GTC OCO can be placed on a weekend. The same page states no restriction on
CANCELS outside market hours, and that is NOT verified live by this change —
which is exactly why the apply waits for every cancelled order to read
terminal before placing, and refuses to place if it does not. ``/v2/clock`` is
read and reported as ``market_open`` so the outcome can be read against it.

Usage (on the live VM, via the ``rearm-alpaca-protective`` system-action):
    python3 scripts/ops/rearm_alpaca_protective.py --account alpaca_paper --symbol SPY
    python3 scripts/ops/rearm_alpaca_protective.py --account alpaca_paper --symbol SPY --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

#: Exit codes. Distinct on purpose — notify_run.sh maps each to a priority.
EXIT_OK = 0            # dry-run ready, already armed, or re-armed + verified
EXIT_FAILED_RESTORED = 1  # new OCO refused; the OLD protection was re-placed
EXIT_COULD_NOT_LOOK = 2   # a read failed — NOT evidence either way
EXIT_UNCONFIRMED = 3      # placed, but the verification read failed/disagreed
EXIT_REFUSED = 4          # a safety guard refused BEFORE anything was cancelled
EXIT_NAKED = 5            # cancelled, and could not place new OR old protection

_TERMINAL = ("canceled", "cancelled", "expired", "filled", "rejected",
             "replaced", "done_for_day")
_QTY_EPS = 1e-9


# ─────────────────────────────────────────────────────────── seams (patched in tests)
def _load_account(account_id: str) -> Optional[Dict[str, Any]]:
    from src.units.ui.data_loaders import list_accounts

    for acc in list_accounts() or []:
        if (acc or {}).get("account_id") == account_id or (acc or {}).get("name") == account_id:
            return acc
    return None


def _build_client(account_cfg: Dict[str, Any]):
    """Mode-agnostic place-capable client (same builder as flatten-alpaca-position)."""
    from src.units.accounts.clients import alpaca_client_for

    return alpaca_client_for(account_cfg)


def _open_rows(account_id: str, symbol: str) -> Optional[List[Dict[str, Any]]]:
    """OPEN, non-backtest journal rows for (account, symbol). READ-ONLY.

    ``None`` = the journal could not be read (not "no rows"). Exact symbol
    match — a ``LIKE 'SPY%'`` would also take SPYG.
    """
    from src.utils.paths import trade_journal_db_path

    try:
        conn = sqlite3.connect(f"file:{trade_journal_db_path()}?mode=ro", uri=True)
    except Exception:  # noqa: BLE001
        return None
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, symbol, direction, position_size, stop_loss, take_profit_1, "
            "strategy_name, created_at FROM trades WHERE status='open' "
            "AND account_id=? AND UPPER(symbol)=? AND COALESCE(is_backtest,0)=0 "
            "ORDER BY id ASC",
            (account_id, symbol.upper()),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:  # noqa: BLE001
        return None
    finally:
        conn.close()


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


# ─────────────────────────────────────────────────────────── venue reads
def _read_position(client, sym: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """``("open", row)`` / ``("flat", None)`` / ``("could_not_look", None)``."""
    env = client._request("GET", f"/v2/positions/{sym}")
    rc = env.get("retCode")
    if rc == 404:
        return "flat", None
    res = env.get("result")
    if rc != 0 or not isinstance(res, dict):
        return "could_not_look", None
    try:
        qty = abs(float(res.get("qty")))
    except (TypeError, ValueError):
        return "could_not_look", None
    if qty <= 0:
        return "flat", None
    side = str(res.get("side") or "").lower()
    if side not in ("long", "short"):
        return "could_not_look", None

    def _f(key):
        try:
            v = float(res.get(key))
            return v if v == v else None
        except (TypeError, ValueError):
            return None

    return "open", {"qty": qty, "side": side,
                    "qty_available": _f("qty_available"),
                    "current_price": _f("current_price"),
                    "avg_entry_price": _f("avg_entry_price")}


def _read_orders(client, sym: str) -> Optional[List[Dict[str, Any]]]:
    """Open orders on *sym*, parents + nested legs, de-duplicated by id."""
    rows = client._open_orders_for_symbol(sym)
    if rows is None:
        return None
    seen, out = set(), []
    for o in rows:
        oid = o.get("id")
        if oid and oid in seen:
            continue
        seen.add(oid)
        out.append(o)
    return out


def _market_open(client) -> Optional[bool]:
    env = client._request("GET", "/v2/clock")
    if env.get("retCode") != 0 or not isinstance(env.get("result"), dict):
        return None
    return bool(env["result"].get("is_open"))


def _leg_kind(order: Dict[str, Any]) -> str:
    """``stop`` / ``target`` / ``""``. STOP FAMILY FIRST (a stop_limit contains
    "limit"); mirrors ``AlpacaClient._leg_protective_side``."""
    t = str(order.get("type") or order.get("order_type") or "").lower()
    if "stop" in t or "trail" in t:
        return "stop"
    if "limit" in t:
        return "target"
    return ""


def _leg_qty(order: Dict[str, Any]) -> Optional[float]:
    try:
        q = float(order.get("qty"))
    except (TypeError, ValueError):
        return None
    return q if q > 0 else None  # NOT abs(): a non-positive qty is anomalous


def _leg_price(order: Dict[str, Any], kind: str) -> Optional[float]:
    key = "stop_price" if kind == "stop" else "limit_price"
    try:
        v = float(order.get(key))
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _summarize(o: Dict[str, Any]) -> Dict[str, Any]:
    keep = ("id", "side", "type", "order_class", "status", "qty", "limit_price",
            "stop_price", "time_in_force", "submitted_at", "created_at")
    return {k: o.get(k) for k in keep if k in o}


def _grade_orders(orders: List[Dict[str, Any]], reducing: str) -> Dict[str, Any]:
    """Split resting orders into protective legs and blockers.

    A leg is protective only if it REDUCES the position (Alpaca has no hedge
    mode, so that is a function of the position side) AND is a stop/limit.
    """
    protective, blockers, unreadable = [], [], []
    for o in orders:
        kind = _leg_kind(o)
        side = str(o.get("side") or "").lower()
        if not kind or side != reducing:
            blockers.append(o)
            continue
        if _leg_qty(o) is None:
            unreadable.append(o)
            continue
        protective.append(o)
    stop_qty = sum(_leg_qty(o) for o in protective if _leg_kind(o) == "stop")
    target_qty = sum(_leg_qty(o) for o in protective if _leg_kind(o) == "target")
    return {"protective": protective, "blockers": blockers,
            "unreadable": unreadable, "stop_qty": stop_qty,
            "target_qty": target_qty}


def _restore_plan(protective: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The old protection, as ONE OCO that can be re-placed — or None.

    Only the plain OCO shape (exactly one stop + one limit of the same qty,
    both priced) is restorable exactly. Anything else is reported as
    not restorable, so a failed place is honestly labelled NAKED rather than
    "restored" to a shape that never existed.
    """
    stops = [o for o in protective if _leg_kind(o) == "stop"]
    tgts = [o for o in protective if _leg_kind(o) == "target"]
    if len(stops) != 1 or len(tgts) != 1:
        return None
    s, t = stops[0], tgts[0]
    if "trail" in str(s.get("type") or "").lower():
        return None
    qs, qt = _leg_qty(s), _leg_qty(t)
    sp, tp = _leg_price(s, "stop"), _leg_price(t, "target")
    if None in (qs, qt, sp, tp) or abs(qs - qt) > _QTY_EPS:
        return None
    return {"qty": qs, "sl": sp, "tp": tp}


def _oco_body(sym: str, close_side: str, qty: int, sl: float, tp: float) -> Dict[str, Any]:
    """Same request schema ``AlpacaClient.place_protective`` uses (verified live
    2026-06-29) — built here so the place does NOT run that method's blanket
    pre-cancel of every order on the symbol."""
    return {
        "symbol": sym, "qty": str(int(qty)), "side": close_side,
        "type": "limit", "time_in_force": "gtc", "order_class": "oco",
        "take_profit": {"limit_price": f"{float(tp):.2f}"},
        "stop_loss": {"stop_price": f"{float(sl):.2f}"},
    }


def _place_oco(client, body: Dict[str, Any], confirm_s: float) -> Dict[str, Any]:
    """POST the OCO, then poll briefly for an async reject (mirrors
    ``AlpacaClient.place``'s post-accept confirmation)."""
    env = client._request("POST", "/v2/orders", body)
    if env.get("retCode") != 0:
        return {"ok": False, "retCode": env.get("retCode"), "retMsg": env.get("retMsg")}
    oid = str((env.get("result") or {}).get("id") or "")
    status = str((env.get("result") or {}).get("status") or "")
    deadline = time.monotonic() + confirm_s
    while oid and confirm_s > 0:
        st_env = client._request("GET", f"/v2/orders/{oid}")
        if st_env.get("retCode") == 0:
            status = str((st_env.get("result") or {}).get("status") or "").lower()
            if status in ("rejected", "canceled", "cancelled", "expired"):
                return {"ok": False, "order_id": oid, "retMsg": f"accepted then {status}"}
            if status:
                break
        if time.monotonic() >= deadline:
            break
        _sleep(0.25)
    return {"ok": True, "order_id": oid, "status": status}


def _await_settle(client, sym: str, cancelled_ids: List[str], net: float,
                  settle_s: float) -> Dict[str, Any]:
    """Wait until every cancelled order reads TERMINAL and qty_available >= net.

    Polled, not slept. Reports the last-seen status per order so a
    ``pending_cancel`` is visible rather than inferred.
    """
    deadline = time.monotonic() + settle_s
    last: Dict[str, str] = {}
    qty_avail: Optional[float] = None
    filled: List[str] = []
    while True:
        pending = []
        for oid in cancelled_ids:
            env = client._request("GET", f"/v2/orders/{oid}")
            st = (str((env.get("result") or {}).get("status") or "").lower()
                  if env.get("retCode") == 0 else "could_not_look")
            last[oid] = st
            if st == "filled":
                filled.append(oid)
            if st not in _TERMINAL:
                pending.append(oid)
        state, pos = _read_position(client, sym)
        qty_avail = pos.get("qty_available") if pos else None
        if filled:
            return {"settled": False, "why": "an old protective leg FILLED during "
                    "the re-arm — the position changed under us",
                    "order_status": last, "qty_available": qty_avail,
                    "position_state": state}
        if not pending and qty_avail is not None and qty_avail + _QTY_EPS >= net:
            return {"settled": True, "order_status": last, "qty_available": qty_avail}
        if time.monotonic() >= deadline:
            return {"settled": False, "why": "cancel did not settle in "
                    f"{settle_s:.0f}s (pending={pending}, qty_available={qty_avail}, "
                    f"need {net})", "order_status": last,
                    "qty_available": qty_avail, "position_state": state}
        _sleep(0.4)


# ─────────────────────────────────────────────────────────── core
def rearm(account_id: str, symbol: str, *, apply: bool,
          out: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
    """Core routine. *out* may be passed in so a caller that catches an
    unexpected exception can still see how far the run got (cancel issued?)."""
    sym = symbol.upper()
    if out is None:
        out = {}
    out.update({"account": account_id, "symbol": sym, "apply": bool(apply)})

    def done(code: int, **kw) -> Tuple[int, Dict[str, Any]]:
        out.update(kw)
        out["exit_code"] = code
        return code, out

    cfg = _load_account(account_id)
    if cfg is None:
        return done(EXIT_COULD_NOT_LOOK, state="could_not_look",
                    detail=f"account {account_id!r} not found in accounts.yaml")
    if str(cfg.get("exchange") or "").lower() != "alpaca":
        return done(EXIT_REFUSED, state="refused_not_alpaca",
                    detail=f"account {account_id!r} is exchange={cfg.get('exchange')!r}")
    client = _build_client(cfg)
    if client is None:
        return done(EXIT_COULD_NOT_LOOK, state="could_not_look",
                    detail="alpaca_client_for returned None (creds unset) — NOT evidence "
                           "the position is flat or unprotected")

    # (1) position — the denominator
    pstate, pos = _read_position(client, sym)
    if pstate == "could_not_look":
        return done(EXIT_COULD_NOT_LOOK, state="could_not_look",
                    detail="position read failed — refusing to act blind")
    orders = _read_orders(client, sym)
    if orders is None:
        return done(EXIT_COULD_NOT_LOOK, state="could_not_look",
                    detail="open-orders read failed — this is NOT 'no orders rest'")
    out["resting_orders"] = [_summarize(o) for o in orders]
    if pstate == "flat":
        return done(EXIT_REFUSED if orders else EXIT_OK,
                    state="refused_flat_with_orders" if orders else "noop_flat",
                    detail=("broker reads FLAT but orders rest on the symbol — a "
                            "re-arm protects a position; this is a different repair "
                            "(the orders could open one)") if orders else
                           "broker reads FLAT — nothing to protect")
    net, side = pos["qty"], pos["side"]
    out["live_position"] = pos
    if abs(net - round(net)) > _QTY_EPS:
        return done(EXIT_REFUSED, state="refused_fractional_position",
                    detail=f"net qty {net} is fractional; an OCO is whole shares, so "
                           "it would under- or over-cover. Not guessed.")
    net_int = int(round(net))
    reducing = "sell" if side == "long" else "buy"

    # (2) resting orders — only reducing stop/limit legs may be touched
    g = _grade_orders(orders, reducing)
    out["existing_coverage"] = {"stop_qty": g["stop_qty"], "target_qty": g["target_qty"],
                                "net_qty": net}
    if g["blockers"]:
        return done(EXIT_REFUSED, state="refused_non_protective_order",
                    blockers=[_summarize(o) for o in g["blockers"]],
                    detail=f"{len(g['blockers'])} resting order(s) on {sym} are not "
                           f"{reducing}-side stop/limit legs. A re-arm cancels every "
                           "protective leg on the symbol and one of these could fill "
                           "after the new OCO flattens the book, opening a reverse. "
                           "Nothing was cancelled.")
    if g["unreadable"]:
        return done(EXIT_REFUSED, state="refused_unreadable_leg",
                    legs=[_summarize(o) for o in g["unreadable"]],
                    detail="a protective leg's qty could not be read — coverage is "
                           "ungradeable, not zero. Nothing was cancelled.")

    # (3) journal levels — never caller-supplied
    rows = _open_rows(account_id, sym)
    if rows is None:
        return done(EXIT_COULD_NOT_LOOK, state="could_not_look",
                    detail="journal read failed — cannot establish declared levels")
    out["journal_rows"] = rows
    if not rows:
        return done(EXIT_REFUSED, state="refused_no_open_row",
                    detail="no OPEN journal row for this (account, symbol) — there are "
                           "no declared levels to arm, and levels are never invented")
    want_dir = side  # long / short
    bad_dir = [r["id"] for r in rows
               if {"buy": "long", "sell": "short"}.get(str(r.get("direction") or "").lower(),
                                                       str(r.get("direction") or "").lower()) != want_dir]
    if bad_dir:
        return done(EXIT_REFUSED, state="refused_direction_mismatch",
                    detail=f"journal row(s) {bad_dir} disagree with the broker's {side} "
                           "position — the geometry is not understood")
    missing = [r["id"] for r in rows
               if not (r.get("stop_loss") or 0) > 0 or not (r.get("take_profit_1") or 0) > 0]
    if missing:
        return done(EXIT_REFUSED, state="refused_levels_missing",
                    detail=f"journal row(s) {missing} declare no stop_loss and/or "
                           "take_profit_1 — an OCO needs both, and neither is invented")
    levels = sorted({(round(float(r["stop_loss"]), 2), round(float(r["take_profit_1"]), 2))
                     for r in rows})
    if len(levels) != 1:
        return done(EXIT_REFUSED, state="ambiguous_levels",
                    level_rule="refuse: open rows declare different SL/TP",
                    levels_by_row={r["id"]: [r["stop_loss"], r["take_profit_1"]] for r in rows},
                    detail="the open rows on this netted position declare DIFFERENT "
                           "levels; choosing one would move protection another "
                           "strategy chose. Refused rather than picked.")
    sl, tp = levels[0]
    out["level_rule"] = ("single open row → its own SL/TP" if len(rows) == 1 else
                         f"{len(rows)} open rows, all declaring the same SL/TP at 2dp")
    out["levels"] = {"sl": sl, "tp": tp, "source_rows": [r["id"] for r in rows]}

    journal_qty = sum(float(r.get("position_size") or 0) for r in rows)
    out["journal_qty"] = journal_qty
    out["untracked_qty"] = round(net - journal_qty, 9)
    if journal_qty > net + _QTY_EPS:
        return done(EXIT_REFUSED, state="refused_journal_exceeds_position",
                    detail=f"journal open qty {journal_qty} > broker net {net}")

    long_ = side == "long"
    if (long_ and not sl < tp) or (not long_ and not sl > tp):
        return done(EXIT_REFUSED, state="refused_levels_wrong_side",
                    detail=f"sl={sl} / tp={tp} are inverted for a {side} position")
    px = pos.get("current_price")
    if px is not None and ((long_ and not sl < px < tp) or (not long_ and not tp < px < sl)):
        return done(EXIT_REFUSED, state="refused_levels_wrong_side",
                    detail=f"current price {px} is not between sl={sl} and tp={tp}: "
                           "the OCO would trigger on placement. That is an exit "
                           "decision, not a repair.")

    # Already armed exactly as intended?
    restore = _restore_plan(g["protective"])
    out["restore_plan"] = restore
    if (restore and abs(restore["qty"] - net) <= _QTY_EPS
            and round(restore["sl"], 2) == sl and round(restore["tp"], 2) == tp):
        return done(EXIT_OK, state="already_armed",
                    detail=f"one OCO for the full {net_int} at sl={sl}/tp={tp} already rests")

    out["market_open"] = _market_open(client)
    close_side = reducing
    body = _oco_body(sym, close_side, net_int, sl, tp)
    to_cancel = [str(o["id"]) for o in g["protective"] if o.get("id")]
    out["plan"] = {"cancel": to_cancel, "place": body,
                   "restore_if_place_fails": restore or "NOT restorable exactly "
                   "(old protection is not a single stop+limit OCO) — a failed place "
                   "would leave the position NAKED and exit 5"}
    if out["untracked_qty"] > 0:
        out["note_untracked"] = (f"{out['untracked_qty']} share(s) have no open journal "
                                 "row; they are covered at the journal row's levels")

    if not apply:
        return done(EXIT_OK, state="ready", action="dry_run",
                    detail=f"DRY-RUN — would cancel {len(to_cancel)} protective leg(s) "
                           f"and place ONE GTC OCO {close_side} {net_int} {sym} "
                           f"sl={sl} tp={tp}. Re-run with apply: true to execute.")

    # ── APPLY ────────────────────────────────────────────────────────────────
    # TOCTOU: re-read immediately before cancelling; the set must be unchanged.
    again = _read_orders(client, sym)
    if again is None:
        return done(EXIT_COULD_NOT_LOOK, state="could_not_look",
                    detail="pre-cancel re-read failed — nothing was cancelled")
    if sorted(str(o.get("id")) for o in again) != sorted(str(o.get("id")) for o in orders):
        return done(EXIT_REFUSED, state="refused_orders_changed",
                    detail="the resting orders changed between grading and cancel — "
                           "nothing was cancelled; re-run the dry-run")

    t0 = time.monotonic()
    cancels = {}
    for oid in to_cancel:
        env = client._request("DELETE", f"/v2/orders/{oid}")
        cancels[oid] = env.get("retCode")
    out["cancel_results"] = cancels
    settle = _await_settle(client, sym, to_cancel, net,
                           _env_float("ALPACA_REARM_SETTLE_S", 15.0))
    out["settle"] = settle
    if not settle.get("settled"):
        return done(EXIT_NAKED, state="cancel_unsettled",
                    detail="cancels were issued but did not settle — NOTHING was placed "
                           "(the shares are still held, so a place would be rejected). "
                           "The old legs may still be live or pending_cancel: read "
                           "/api/diag/alpaca_open_orders NOW and do not re-run blind.")

    confirm_s = _env_float("ALPACA_PLACE_CONFIRM_S", 3.0)
    attempts = []
    placed = _place_oco(client, body, confirm_s)
    attempts.append(placed)
    if not placed["ok"]:
        _sleep(1.0)
        placed = _place_oco(client, body, confirm_s)
        attempts.append(placed)
    out["place_attempts"] = attempts
    out["unprotected_window_s"] = round(time.monotonic() - t0, 2)

    if not placed["ok"]:
        if restore is None:
            return done(EXIT_NAKED, state="NAKED",
                        detail="the new OCO was refused twice and the old protection "
                               "was not restorable exactly — the position is NAKED. "
                               "Act now (flatten-alpaca-position, or re-run in RTH).")
        rbody = _oco_body(sym, close_side, int(round(restore["qty"])),
                          restore["sl"], restore["tp"])
        rest = _place_oco(client, rbody, confirm_s)
        out["restore_attempt"] = {"body": rbody, "result": rest}
        if rest["ok"]:
            return done(EXIT_FAILED_RESTORED, state="restored_old_protection",
                        detail=f"new OCO refused ({placed.get('retMsg')}); the OLD "
                               f"protection ({restore['qty']} @ sl={restore['sl']} "
                               f"tp={restore['tp']}) was re-placed. Back to the "
                               "pre-run state: still partially naked.")
        return done(EXIT_NAKED, state="NAKED",
                    detail="the new OCO AND the restore of the old protection were both "
                           "refused — the position is NAKED. Act now.")

    # (4) verify from the venue, not from the POST's 2xx
    after = _read_orders(client, sym)
    if after is None:
        return done(EXIT_UNCONFIRMED, state="placed_unconfirmed",
                    detail="the OCO was accepted but the verification read failed — "
                           "accepted is not confirmed; re-run the dry-run")
    ga = _grade_orders(after, reducing)
    out["coverage_after"] = {"stop_qty": ga["stop_qty"], "target_qty": ga["target_qty"],
                             "net_qty": net, "orders": [_summarize(o) for o in after]}
    ra = _restore_plan(ga["protective"])
    if (ra and abs(ra["qty"] - net) <= _QTY_EPS and round(ra["sl"], 2) == sl
            and round(ra["tp"], 2) == tp and not ga["blockers"]):
        return done(EXIT_OK, state="rearmed",
                    detail=f"ONE GTC OCO {close_side} {net_int} {sym} sl={sl} tp={tp} "
                           "rests and covers the full net position (verified by re-read)")
    return done(EXIT_UNCONFIRMED, state="rearm_unverified",
                detail="the OCO was accepted but the re-read does not show exactly one "
                       f"OCO covering {net_int} at the journal levels — inspect "
                       "coverage_after before doing anything else")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Re-arm one GTC OCO sized to the live net qty.")
    ap.add_argument("--account", required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    out: Dict[str, Any] = {}
    try:
        code, out = rearm(args.account, args.symbol, apply=args.apply, out=out)
    except Exception as exc:  # noqa: BLE001 — report, never a bare traceback
        # Once a cancel has been issued, an unexpected error can have left the
        # position naked — say so at the urgent exit, never as could-not-look.
        mid_apply = "cancel_results" in out and "coverage_after" not in out
        code = EXIT_NAKED if mid_apply else EXIT_COULD_NOT_LOOK
        out.update(state="error_after_cancel_possibly_NAKED" if mid_apply else "error",
                   detail=f"{type(exc).__name__}: {exc}", exit_code=code)
    print(json.dumps(out, indent=2, default=str))
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
