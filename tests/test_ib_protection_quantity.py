"""IB protection is a QUANTITY, not a boolean — and a re-arm keeps siblings'.

Regression guard for BL-20260814-IB-PROTECTION-BOOLEAN-NOT-QUANTITY.

IB nets per contract per account: three strategies trading MGC share ONE broker
position whose size is the SUM of their journal rows, while each trade's
protective OCA is sized to its OWN qty. Two defects compounded on that:

1. DETECTION was a boolean — ``has_protective_orders`` returned ``True`` on the
   FIRST matching STP/LMT, so one surviving leg made a one-third-covered
   position read exactly like a fully covered one.
2. RE-ARM was destructive — ``place_protective`` cancelled EVERY resting order
   on the symbol root before arming one bracket sized to the calling trade,
   silently deleting the sibling trades' take-profit legs.

Together: a trade's TP is cancelled by a sibling's re-arm, and the boolean check
then reports PROTECTED because the sibling's new leg exists. That is the
mechanism behind a take-profit that was reached and never executed.

PR #8000 fixed exactly this for Bybit and did not fix it for IB, because at the
time IB was believed not to net.

These assert the PROPERTIES (coverage is measured; a sibling's legs survive), so
they still hold if the implementation changes.
"""
from __future__ import annotations

import sys
import types


class _Contract:
    def __init__(self, symbol):
        self.symbol = symbol


class _Order:
    def __init__(self, order_type, qty, oca_group="", order_id=0):
        self.orderType = order_type
        self.totalQuantity = qty
        self.ocaGroup = oca_group
        self.orderId = order_id


class _Trade:
    def __init__(self, symbol, order_type, qty, oca_group="", order_id=0):
        self.contract = _Contract(symbol)
        self.order = _Order(order_type, qty, oca_group, order_id)


class _Position:
    def __init__(self, symbol, position):
        self.contract = _Contract(symbol)
        self.position = position


class _IB:
    """Minimal ib_insync stub: a netted position plus its resting legs."""

    def __init__(self, positions, trades):
        self._positions = positions
        self._trades = list(trades)
        self.cancelled = []

    def positions(self):
        return list(self._positions)

    def openTrades(self):
        return list(self._trades)

    def reqAllOpenOrders(self):
        return None

    def cancelOrder(self, order):
        self.cancelled.append(order)
        self._trades = [t for t in self._trades if t.order is not order]

    def sleep(self, _s):
        return None


def _client(monkeypatch, ib):
    from src.units.accounts import ib_client as mod

    c = mod.IBClient(host="h", port=1, client_id=1, account="A", symbol="MGC")
    monkeypatch.setattr(c, "connect", lambda: ib)
    monkeypatch.setattr(c, "_req_positions_snapshot", lambda _ib: ib.positions())
    return c


# --- 1. coverage is a quantity ------------------------------------------------

def test_partial_coverage_is_visible_not_reported_as_protected(monkeypatch):
    """3 contracts held, ONE leg covering 1 → covered 1 of 3, not "protected".

    Under the old boolean this returned True and the sweep skipped, leaving two
    contracts unprotected and invisible to every layer.
    """
    ib = _IB([_Position("MGC", 3)], [_Trade("MGC", "STP", 1, "oca-protect-t10")])
    cov = _client(monkeypatch, ib).protection_coverage("MGC")
    assert cov is not None
    assert cov["size"] == 3
    assert cov["covered_qty"] == 1
    assert cov["covered_qty"] < cov["size"]


def test_boolean_view_disagrees_with_coverage_on_a_partial_position(monkeypatch):
    """The defect, stated as a live comparison rather than as prose.

    On the SAME fixture the retained boolean says "protected" while coverage
    shows 1 of 3 contracts covered. This is why the boolean must never be the
    input to a naked-detection decision on a netted contract, and it fails
    loudly if anyone wires ``has_protective_orders`` back into that path.
    """
    ib = _IB([_Position("MGC", 3)], [_Trade("MGC", "STP", 1, "oca-protect-t10")])
    c = _client(monkeypatch, ib)
    assert c.has_protective_orders("MGC") is True, "boolean view changed"
    cov = c.protection_coverage("MGC")
    assert cov["covered_qty"] == 1 and cov["size"] == 3
    assert cov["covered_qty"] < cov["size"], (
        "coverage agreed with the boolean — the quantity signal is gone"
    )


def test_full_coverage_sums_across_sibling_groups(monkeypatch):
    """Three trades, each with its own OCA group → fully covered."""
    ib = _IB(
        [_Position("MGC", 3)],
        [
            _Trade("MGC", "STP", 1, "oca-protect-t10"),
            _Trade("MGC", "STP", 1, "oca-protect-t11"),
            _Trade("MGC", "STP", 1, "oca-protect-t12"),
        ],
    )
    cov = _client(monkeypatch, ib).protection_coverage("MGC")
    assert cov["covered_qty"] == 3 == cov["size"]


def test_oca_pair_counts_once_not_twice(monkeypatch):
    """A STOP and a LIMIT in ONE group protect the SAME qty.

    Counting both would double the coverage and hide a genuinely naked
    remainder — a 1-contract bracket on a 2-contract position would read as
    fully covered.
    """
    ib = _IB(
        [_Position("MGC", 2)],
        [
            _Trade("MGC", "STP", 1, "oca-protect-t10"),
            _Trade("MGC", "LMT", 1, "oca-protect-t10"),
        ],
    )
    cov = _client(monkeypatch, ib).protection_coverage("MGC")
    assert cov["covered_qty"] == 1, "OCA pair double-counted"
    assert cov["covered_qty"] < cov["size"]


def test_unparseable_leg_qty_is_counted_not_assumed(monkeypatch):
    """An unknown qty makes coverage UNGRADEABLE — never silently full."""
    t = _Trade("MGC", "STP", 1, "oca-protect-t10")
    t.order.totalQuantity = None
    t.order.quantity = "not-a-number"
    ib = _IB([_Position("MGC", 3)], [t])
    cov = _client(monkeypatch, ib).protection_coverage("MGC")
    assert cov["unknown_qty_legs"] == 1
    assert cov["covered_qty"] == 0


def test_flat_position_is_flat_not_naked(monkeypatch):
    ib = _IB([], [])
    cov = _client(monkeypatch, ib).protection_coverage("MGC")
    assert cov["size"] == 0 and cov["source"] == "flat"


def test_read_failure_returns_none_so_caller_skips(monkeypatch):
    """Could-not-look must never be reported as zero coverage."""
    from src.units.accounts import ib_client as mod

    c = mod.IBClient(host="h", port=1, client_id=1, account="A", symbol="MGC")

    def _boom():
        raise RuntimeError("breaker open")

    monkeypatch.setattr(c, "connect", _boom)
    assert c.protection_coverage("MGC") is None


# --- 2. a re-arm no longer strands a sibling's take-profit --------------------

def _place_client(monkeypatch, ib):
    # place_protective imports LimitOrder/StopOrder inside the method; inject a
    # fake ib_insync so the import resolves without the real dependency.
    mod = types.ModuleType("ib_insync")
    mod.LimitOrder = lambda action, qty, price: _Order("LMT", qty)
    mod.StopOrder = lambda action, qty, price: _Order("STP", qty)
    monkeypatch.setitem(sys.modules, "ib_insync", mod)

    c = _client(monkeypatch, ib)
    c.readonly = False

    class _C:
        @staticmethod
        def getReqId():
            return 999

    ib.client = _C()
    monkeypatch.setattr(c, "_build_contract", lambda _s: _Contract("MGC"))

    placed = []

    def _fake_place(order):
        placed.append(order)
        order.orderId = 500 + len(placed)

        class _T:
            pass

        t = _T()
        t.order = order
        return t

    ib.placeOrder = lambda _c, o: _fake_place(o)
    return c, placed


def test_rearm_keeps_sibling_take_profit(monkeypatch):
    """THE bug. Trade 11's re-arm must not cancel trade 10's TP.

    Fails without the fix: the symbol-wide pre-cancel removes every MGC leg, so
    trade 10's take-profit is gone and can never fire.
    """
    sibling_tp = _Trade("MGC", "LMT", 1, "oca-protect-t10", order_id=10)
    sibling_sl = _Trade("MGC", "STP", 1, "oca-protect-t10", order_id=11)
    ib = _IB([_Position("MGC", 2)], [sibling_tp, sibling_sl])
    c, _placed = _place_client(monkeypatch, ib)

    resp = c.place_protective(
        {"symbol": "MGC", "direction": "long", "qty": 1,
         "sl": 4200.0, "tp": 4400.0, "oca_key": "11"}
    )
    assert resp["retCode"] == 0

    survivors = {t.order.ocaGroup for t in ib.openTrades()}
    assert "oca-protect-t10" in survivors, (
        "the re-arm cancelled a SIBLING trade's protective legs — this is the "
        "take-profit-never-fired mechanism"
    )
    assert sibling_tp.order not in ib.cancelled


def test_rearm_replaces_its_own_group(monkeypatch):
    """Re-arming the SAME trade must not stack a second live bracket.

    This is what the symbol-wide pre-cancel originally existed to prevent
    (BL-20260624-MHG-FLIP: stacked OCAs firing together and flipping a flat
    position into a reverse orphan). A deterministic per-trade group serves that
    purpose without touching siblings.
    """
    own_sl = _Trade("MGC", "STP", 1, "oca-protect-t11", order_id=11)
    ib = _IB([_Position("MGC", 1)], [own_sl])
    c, _placed = _place_client(monkeypatch, ib)

    c.place_protective(
        {"symbol": "MGC", "direction": "long", "qty": 1,
         "sl": 4200.0, "tp": 4400.0, "oca_key": "11"}
    )
    assert own_sl.order in ib.cancelled, "stale leg of the same trade not replaced"


def test_group_name_is_deterministic_per_trade(monkeypatch):
    """Same trade → same group across re-arms, so the scoped cancel can find it."""
    ib = _IB([_Position("MGC", 1)], [])
    c, placed = _place_client(monkeypatch, ib)
    for _ in range(2):
        c.place_protective(
            {"symbol": "MGC", "direction": "long", "qty": 1,
             "sl": 4200.0, "tp": 4400.0, "oca_key": "77"}
        )
    groups = {o.ocaGroup for o in placed}
    assert groups == {"oca-protect-t77"}, groups


def test_keyless_caller_still_works(monkeypatch):
    """Back-compat: no oca_key → legacy reqId group, still arms protection."""
    ib = _IB([_Position("MGC", 1)], [])
    c, placed = _place_client(monkeypatch, ib)
    resp = c.place_protective(
        {"symbol": "MGC", "direction": "long", "qty": 1, "sl": 4200.0, "tp": 4400.0}
    )
    assert resp["retCode"] == 0
    assert placed and placed[0].ocaGroup.startswith("oca-protect-")
