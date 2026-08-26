"""place_protective must never arm a bracket over legs it failed to cancel.

BL-20260825-PLACE-PROTECTIVE-COUNTS-THE-CANCEL-CALL-NOT-ITS-EFFECT.

MEASURED LIVE 2026-08-25T14:52:34Z on ``ib_paper``/MHG. ``place_protective``'s
pre-cancel issued ``cancelOrder`` on four resting legs; IBKR refused the two
owned by a retired clientId with ``Error 10147 — OrderId 417 that needs to be
cancelled is not found``; ``ib.cancelOrder`` returns ``None`` and never raises,
so the refusal was unobservable, and a fresh bracket was armed in a THIRD OCA
group. ``ocaType=1`` cancels only WITHIN a group, so the account then held 87
lots of resting stop against a 29-lot position: one stop firing flattens it and
the survivors sell the same size again, into an un-hedged reverse.

These tests drive the defect through a **simulated IBKR error event**, not a
return-value stub, because the whole point is that the return value carried no
information. A fake whose ``cancelOrder`` merely returned False would pass
against the broken code.
"""
from __future__ import annotations

import sys
import types

import pytest

from src.units.accounts.ib_client import get_ib_client


class _Event:
    """Minimal ib_insync Event: supports ``+=`` / ``-=`` and calling."""

    def __init__(self):
        self._handlers = []

    def __iadd__(self, fn):
        self._handlers.append(fn)
        return self

    def __isub__(self, fn):
        if fn in self._handlers:
            self._handlers.remove(fn)
        return self

    def emit(self, *args):
        for fn in list(self._handlers):
            fn(*args)

    def __len__(self):
        return len(self._handlers)


class _Contract:
    def __init__(self, symbol="MHG", conId=1):
        self.symbol = symbol
        self.conId = conId
        self.exchange = "COMEX"
        self.currency = "USD"


class _Order:
    def __init__(self, orderId, permId, clientId, ocaGroup, orderType="STP",
                 totalQuantity=29.0):
        self.orderId = orderId
        self.permId = permId
        self.clientId = clientId
        self.ocaGroup = ocaGroup
        self.ocaType = 1
        self.orderType = orderType
        self.totalQuantity = totalQuantity
        self.transmit = True
        self.tif = "GTC"
        self.account = None


class _Trade:
    def __init__(self, order, contract):
        self.order = order
        self.contract = contract


class _Client:
    def __init__(self):
        self._next = 900

    def getReqId(self):
        self._next += 1
        return self._next


class FakeIB:
    """An IB whose cancels behave like the real venue.

    ``owning_client_id`` models IBKR's rule that a cancel is honoured only for
    the client that SUBMITTED the order. A cancel for any other client's order
    is answered on ``errorEvent`` with 10147 and the leg keeps resting — which
    is exactly what the live trader observed.
    """

    def __init__(self, resting, *, session_client_id=597,
                 reqallopenorders_raises=False):
        self._resting = list(resting)
        self.session_client_id = session_client_id
        self.client = _Client()
        self.errorEvent = _Event()
        self.placed = []
        self.cancel_calls = []
        self._reqall_raises = reqallopenorders_raises
        self._connected = False

    # --- connection ---------------------------------------------------
    def connect(self, host, port, clientId, timeout=10.0, readonly=False):
        self._connected = True

    def isConnected(self):
        return self._connected

    def disconnect(self):
        self._connected = False

    def qualifyContracts(self, *cs):
        return list(cs)

    def sleep(self, _t=0):
        return None

    # --- orders -------------------------------------------------------
    def openTrades(self):
        return list(self._resting)

    def trades(self):
        return list(self._resting)

    def reqAllOpenOrders(self):
        if self._reqall_raises:
            raise RuntimeError("gateway wedged mid-read")

    def cancelOrder(self, order):
        self.cancel_calls.append(order.orderId)
        if order.clientId != self.session_client_id:
            # IBKR's actual answer. Note it is delivered on the error event,
            # NOT as a return value or an exception.
            self.errorEvent.emit(
                order.orderId, 10147,
                f"OrderId {order.orderId} that needs to be cancelled is not found.",
                None,
            )
            return None
        self._resting = [t for t in self._resting
                         if t.order.orderId != order.orderId]
        return None

    def placeOrder(self, contract, order):
        self.placed.append((contract, order))
        return _Trade(order, contract)


@pytest.fixture(autouse=True)
def _fake_ib_module(monkeypatch):
    mod = types.ModuleType("ib_insync")
    mod.IB = FakeIB
    mod.Future = lambda **kw: _Contract(symbol=kw.get("symbol", "MHG"))
    mod.ContFuture = lambda symbol, exchange, currency=None: _Contract(symbol=symbol)
    mod.MarketOrder = lambda a, q: _Order(0, 0, 597, "", "MKT", q)
    mod.LimitOrder = lambda a, q, p: _Order(0, 0, 597, "", "LMT", q)
    mod.StopOrder = lambda a, q, p: _Order(0, 0, 597, "", "STP", q)
    monkeypatch.setitem(sys.modules, "ib_insync", mod)
    return mod


_SEQ = {"n": 0}


def _client_for(fake_ib, symbol="MHG"):
    _SEQ["n"] += 1
    c = get_ib_client(host="127.0.0.1", port=7497, client_id=7000 + _SEQ["n"],
                      account="DUQ1", symbol=symbol,
                      _ib_factory=lambda: fake_ib)
    c._build_contract = lambda sym=None: _Contract(  # type: ignore[method-assign]
        symbol=str(sym or symbol).upper())
    return c


def _mhg_legs(client_id, group, base_oid, base_perm):
    ct = _Contract("MHG")
    return [
        _Trade(_Order(base_oid, base_perm, client_id, group, "STP"), ct),
        _Trade(_Order(base_oid + 1, base_perm + 1, client_id, group, "LMT"), ct),
    ]


# ---------------------------------------------------------------------------
# The verification itself
# ---------------------------------------------------------------------------

def test_refused_cancel_is_reported_as_still_resting_not_as_cancelled():
    """The exact live shape: legs owned by a RETIRED clientId (497) while the
    session runs on the rotated id (597). IBKR answers 10147 and the legs stay."""
    fake = FakeIB(_mhg_legs(497, "oca-protect-416", 417, 1179890976),
                  session_client_id=597)
    client = _client_for(fake)

    out = client._cancel_resting_orders_for_symbol(fake, "MHG")

    assert out["cancelled"] == 2, out            # two CALLS were made ...
    assert out["verify_state"] == "verified", out
    assert len(out["still_resting"]) == 2, out   # ... and nothing was removed
    assert out["confirmed_gone"] == [], out
    codes = {(leg.get("refusal") or {}).get("code") for leg in out["still_resting"]}
    assert codes == {10147}, out


def test_cancel_that_works_reports_confirmed_gone():
    """The control. Without it, a test that only pins the failure would also
    pass against code that refuses everything."""
    fake = FakeIB(_mhg_legs(597, "oca-protect-432", 433, 1649238173),
                  session_client_id=597)
    client = _client_for(fake)

    out = client._cancel_resting_orders_for_symbol(fake, "MHG")

    assert out["verify_state"] == "verified", out
    assert out["still_resting"] == [], out
    assert len(out["confirmed_gone"]) == 2, out


def test_unverified_is_not_collapsed_into_a_clean_cancel():
    """A failed re-read means 'we did not look'. An empty still_resting there
    carries no information and must not read as success."""
    fake = FakeIB(_mhg_legs(497, "oca-protect-416", 417, 1179890976),
                  session_client_id=597, reqallopenorders_raises=True)
    client = _client_for(fake)

    out = client._cancel_resting_orders_for_symbol(fake, "MHG")

    assert out["verify_state"] == "unverified", out
    assert out["still_resting"] == [], out
    assert out["account_wide_seen"] is None, out


def test_nothing_resting_is_not_attempted_not_verified():
    """Distinct from 'verified, nothing left': no cancel was issued, so the
    position may be genuinely naked and the caller must still arm."""
    fake = FakeIB([], session_client_id=597)
    client = _client_for(fake)

    out = client._cancel_resting_orders_for_symbol(fake, "MHG")

    assert out["verify_state"] == "not_attempted", out
    assert out["seen"] == 0, out


def test_error_handler_is_detached_after_the_batch():
    """A leaked handler would accumulate one subscriber per re-arm on a
    long-lived client and attribute later errors to a finished cancel."""
    fake = FakeIB(_mhg_legs(597, "oca-protect-432", 433, 1649238173),
                  session_client_id=597)
    client = _client_for(fake)

    client._cancel_resting_orders_for_symbol(fake, "MHG")
    client._cancel_resting_orders_for_symbol(fake, "MHG")

    assert len(fake.errorEvent) == 0


# ---------------------------------------------------------------------------
# The invariant: never arm over a surviving leg
# ---------------------------------------------------------------------------

def test_place_protective_joins_the_surviving_group_instead_of_minting_one():
    """THE REGRESSION. Before this, place_protective armed a SECOND OCA group
    here, and ocaType=1 does not cancel across groups — so one stop could
    flatten the position and the other sell the same size again into an
    un-hedged reverse."""
    fake = FakeIB(_mhg_legs(497, "oca-protect-416", 417, 1179890976),
                  session_client_id=597)
    client = _client_for(fake)

    resp = client.place_protective(
        {"symbol": "MHG", "direction": "long", "qty": 29, "sl": 6.255,
         "tp": 7.1415})

    assert resp["retCode"] == 0, resp
    assert len(fake.placed) == 2, fake.placed
    # Every leg on the symbol now lives in ONE group, so they are mutually
    # exclusive: whichever fills, IBKR cancels the rest.
    groups = {o.ocaGroup for _c, o in fake.placed}
    assert groups == {"oca-protect-416"}, groups
    assert resp["result"]["ocaGroup"] == "oca-protect-416", resp


def test_the_join_does_not_fire_when_the_precancel_worked():
    """The control. A clean pre-cancel must still mint its own group — joining
    a group whose legs are GONE would be joining nothing."""
    fake = FakeIB(_mhg_legs(597, "oca-protect-432", 433, 1649238173),
                  session_client_id=597)
    client = _client_for(fake)

    resp = client.place_protective(
        {"symbol": "MHG", "direction": "long", "qty": 29, "sl": 6.255,
         "tp": 7.1415})

    assert resp["retCode"] == 0, resp
    groups = {o.ocaGroup for _c, o in fake.placed}
    assert groups != {"oca-protect-432"}, "joined a group it had just emptied"


def test_the_join_target_is_the_group_holding_the_most_stop_quantity():
    """With survivors in several groups, joining one still strictly improves the
    position — the fresh legs stop being an (N+1)th independent group. The
    STOP quantity decides, because a stop firing is what creates the reverse."""
    small = _mhg_legs(597, "oca-protect-100", 101, 1000001)
    for t in small:
        t.order.totalQuantity = 5.0
    big = _mhg_legs(597, "oca-protect-200", 201, 2000001)
    fake = FakeIB(small + big, session_client_id=999)   # both foreign
    client = _client_for(fake)

    resp = client.place_protective(
        {"symbol": "MHG", "direction": "long", "qty": 29, "sl": 6.255,
         "tp": 7.1415})

    assert resp["retCode"] == 0, resp
    groups = {o.ocaGroup for _c, o in fake.placed}
    assert groups == {"oca-protect-200"}, groups


def test_a_survivor_with_no_oca_group_is_armed_around_not_refused():
    """A bare protective order cannot be joined. Arming anyway is the lesser
    evil — a protected position beats an unprotected one — and the caller says
    so loudly rather than silently recreating the hazard."""
    legs = _mhg_legs(597, "oca-protect-777", 778, 7770001)
    for t in legs:
        t.order.ocaGroup = ""
    fake = FakeIB(legs, session_client_id=999)
    client = _client_for(fake)

    resp = client.place_protective(
        {"symbol": "MHG", "direction": "long", "qty": 29, "sl": 6.255,
         "tp": 7.1415})

    assert resp["retCode"] == 0, resp
    assert len(fake.placed) == 2, fake.placed


def test_join_selection_prefers_stops_and_reads_stop_limit_as_a_stop():
    """`"STP LMT"` contains `"LMT"`, so a limit-first test would file every
    stop-limit as a target and pick the wrong group to join — the classifier
    bug from BL-20260816-COVERAGE-IS-ONE-SIDED, in a new place."""
    from src.units.accounts.ib_client import IBClient
    survivors = [
        {"oca_group": "g-target", "order_type": "LMT", "total_quantity": 99.0},
        {"oca_group": "g-stop", "order_type": "STP LMT", "total_quantity": 10.0},
    ]
    group, count = IBClient._select_join_group(survivors)
    assert group == "g-stop", (group, count)
    assert count == 2


def test_place_protective_arms_when_the_precancel_actually_cleared():
    """The control for the invariant: a clean pre-cancel must still arm."""
    fake = FakeIB(_mhg_legs(597, "oca-protect-432", 433, 1649238173),
                  session_client_id=597)
    client = _client_for(fake)

    resp = client.place_protective(
        {"symbol": "MHG", "direction": "long", "qty": 29, "sl": 6.255,
         "tp": 7.1415})

    assert resp["retCode"] == 0, resp
    assert len(fake.placed) == 2, fake.placed


def test_place_protective_arms_a_naked_position():
    """Nothing was resting, so nothing survived. A position with no stop is the
    one state the system must always correct — refusing here would be the bug."""
    fake = FakeIB([], session_client_id=597)
    client = _client_for(fake)

    resp = client.place_protective(
        {"symbol": "MHG", "direction": "long", "qty": 29, "sl": 6.255,
         "tp": 7.1415})

    assert resp["retCode"] == 0, resp
    assert len(fake.placed) == 2, fake.placed


def test_place_protective_arms_when_the_effect_could_not_be_verified():
    """'We could not look' must still ARM, and must not try to join. With no
    confirmed read there may be no surviving leg to join at all, and the
    position may be genuinely naked — the one state CLAUDE.md says the system
    must always correct."""
    fake = FakeIB(_mhg_legs(497, "oca-protect-416", 417, 1179890976),
                  session_client_id=597, reqallopenorders_raises=True)
    client = _client_for(fake)

    resp = client.place_protective(
        {"symbol": "MHG", "direction": "long", "qty": 29, "sl": 6.255,
         "tp": 7.1415})

    assert resp["retCode"] == 0, resp
    assert len(fake.placed) == 2, fake.placed


def test_scoped_oca_precancel_also_verifies():
    """The scoped path (oca_key supplied) must carry the same guarantee — it is
    the path criterion 3 will route the trailing amend through. Here the scoped
    name and the survivor's group coincide, which is exactly the point: a
    deterministic per-trade group makes the join a no-op."""
    legs = _mhg_legs(497, "oca-protect-t4796", 417, 1179890976)
    fake = FakeIB(legs, session_client_id=597)
    client = _client_for(fake)

    resp = client.place_protective(
        {"symbol": "MHG", "direction": "long", "qty": 29, "sl": 6.255,
         "tp": 7.1415, "oca_key": "4796"})

    assert resp["retCode"] == 0, resp
    groups = {o.ocaGroup for _c, o in fake.placed}
    assert groups == {"oca-protect-t4796"}, groups


def test_a_sibling_group_is_not_counted_as_a_survivor_of_a_scoped_cancel():
    """The scoped cancel targets ONE group; a different trade's legs were never
    attempted, so they must not block this trade's re-arm."""
    mine = _mhg_legs(597, "oca-protect-t4796", 433, 1649238173)
    sibling = _mhg_legs(597, "oca-protect-t9999", 501, 1700000001)
    fake = FakeIB(mine + sibling, session_client_id=597)
    client = _client_for(fake)

    resp = client.place_protective(
        {"symbol": "MHG", "direction": "long", "qty": 29, "sl": 6.255,
         "tp": 7.1415, "oca_key": "4796"})

    assert resp["retCode"] == 0, resp
    assert len(fake.placed) == 2, fake.placed
    remaining = {t.order.ocaGroup for t in fake._resting}
    assert "oca-protect-t9999" in remaining, "sibling protection was destroyed"


# ---------------------------------------------------------------------------
# IBClient.cancel — the ops wire. A refusal must not read as "OK".
# BL-20260825-CANCEL-IB-ORDER-REPORTS-RETMSG-OK-WHILE-IBKR-REFUSED
# ---------------------------------------------------------------------------

def test_cancel_reports_the_venue_refusal_instead_of_ok():
    """The live shape: system-action cancel-ib-order emitted
    `{'retCode': 0, 'retMsg': 'OK'}` for a cancel IBKR had refused, and only a
    separate read-back revealed it did nothing (issue #10280)."""
    fake = FakeIB(_mhg_legs(497, "oca-protect-416", 417, 1179890976),
                  session_client_id=597)
    client = _client_for(fake)

    out = client.cancel("417")

    assert out["retCode"] == 1, out
    assert out["refusal"]["code"] == 10147, out
    assert "REFUSED" in out["retMsg"], out


def test_cancel_still_reports_ok_when_the_venue_accepts():
    """Control: an accepted cancel must not be mislabelled as a refusal. Its
    retMsg says 'accepted', not 'confirmed' — acceptance is not confirmation."""
    fake = FakeIB(_mhg_legs(597, "oca-protect-432", 433, 1649238173),
                  session_client_id=597)
    client = _client_for(fake)

    out = client.cancel("433")

    assert out["retCode"] == 0, out
    assert "refusal" not in out, out
    assert "accepted" in out["retMsg"], out


def test_cancel_does_not_attribute_another_orders_refusal():
    """The error event is keyed on reqId. A refusal for a DIFFERENT order must
    not be reported against this one — that would invent a failure."""
    legs = (_mhg_legs(597, "oca-protect-432", 433, 1649238173)
            + _mhg_legs(497, "oca-protect-416", 417, 1179890976))
    fake = FakeIB(legs, session_client_id=597)
    client = _client_for(fake)

    # Cancel the foreign leg first so a 10147 for 417 is on the wire, then the
    # session's own leg. The second call must come back clean.
    assert client.cancel("417")["retCode"] == 1
    out = client.cancel("433")

    assert out["retCode"] == 0, out
    assert "refusal" not in out, out


# ---------------------------------------------------------------------------
# BL-20260826-CANCEL-IB-ORDER-READS-ERROR-202-AS-A-REFUSAL
#
# The fix above made the venue's answer READABLE. It then filed EVERY answer as
# a refusal — and IBKR sends acceptance down the same channel. Error 202,
# "Order Canceled - reason:...", is the venue confirming the cancel LANDED.
#
# Measured live 2026-08-26 on `ib_paper` MHG order 466: `cancel-ib-order`
# returned `action: refused_by_venue` / `verify_state: still_present` with a
# note telling the operator a retry would not help, while a re-run from a fresh
# process read `lookup_state: not_found` and the account's order count had gone
# 8 -> 6. The cancel had worked. Reporting a successful repair on a live
# position's protection as a failure is the worse direction of the two.
# ---------------------------------------------------------------------------

class ConfirmingIB(FakeIB):
    """A venue that ACKNOWLEDGES a successful cancel on the error channel.

    This is the real behaviour the original FakeIB did not model: it emitted an
    event only on the refusal path, so a capture that filed every event as a
    refusal looked correct against it.
    """

    def cancelOrder(self, order):
        if order.clientId != self.session_client_id:
            return super().cancelOrder(order)
        self.cancel_calls.append(order.orderId)
        self._resting = [t for t in self._resting
                         if t.order.orderId != order.orderId]
        self.errorEvent.emit(order.orderId, 202, "Order Canceled - reason:", None)
        return None


def test_event_classifier_keeps_confirmation_and_refusal_apart():
    from src.units.accounts.ib_client import _classify_ib_cancel_event

    assert _classify_ib_cancel_event(202) == "confirmed"
    assert _classify_ib_cancel_event("202") == "confirmed"
    # An ALLOWLIST: anything unrecognised stays a refusal, so a rejection this
    # repo has never seen is reported loudly rather than swallowed as success.
    assert _classify_ib_cancel_event(10147) == "refused"
    assert _classify_ib_cancel_event(201) == "refused"
    assert _classify_ib_cancel_event(None) == "refused"
    assert _classify_ib_cancel_event("nonsense") == "refused"


def test_venue_confirmation_is_not_reported_as_a_refusal():
    """The live shape: cancel succeeds, IBKR answers 202."""
    fake = ConfirmingIB(_mhg_legs(597, "oca-protect-465", 466, 743292101),
                        session_client_id=597)
    client = _client_for(fake)
    out = client.cancel("466")

    assert out["retCode"] == 0, out
    assert "refusal" not in out
    assert out["confirmation"]["code"] == 202
    assert "CONFIRMED" in out["retMsg"]


def test_a_real_refusal_still_reports_retcode_1():
    """The fix must not blind the tool to a genuine rejection."""
    fake = ConfirmingIB(_mhg_legs(497, "oca-protect-416", 417, 1179890976),
                        session_client_id=597)
    client = _client_for(fake)
    out = client.cancel("417")

    assert out["retCode"] == 1, out
    assert out["refusal"]["code"] == 10147
    assert "confirmation" not in out


def test_a_confirmed_leg_is_not_counted_as_still_resting_with_a_refusal():
    """The batch path shares the capture — a 202 must not poison it either.

    `_verify_cancel_effect` stamps `refusal` on any leg still resting. If a
    confirmation were filed as a refusal, a leg that a DIFFERENT client's 10147
    left resting could be reported under the wrong code.
    """
    ct = _Contract("MHG")
    legs = [
        _Trade(_Order(466, 743292101, 597, "oca-protect-465", "STP"), ct),
        _Trade(_Order(417, 1179890976, 497, "oca-protect-416", "STP"), ct),
    ]
    fake = ConfirmingIB(legs, session_client_id=597)
    client = _client_for(fake)
    ib = client.connect()
    result = client._cancel_resting_orders_for_symbol(ib, "MHG")

    still = {int(r["order_id"]): r for r in result["still_resting"]}
    assert 466 not in still, "the confirmed cancel actually removed the leg"
    assert still[417]["refusal"]["code"] == 10147
