"""Wiring tests for `IBClient._sweep_stray_oca_groups`.

Includes a POSITIVE CONTROL that the shipped keyed pre-cancel genuinely leaves
the strays behind — without it these tests could pass against code that never
had the defect.
"""

import types

import pytest

from src.units.accounts import ib_client as ibc


class FakeOrder:
    def __init__(self, order_id, oca_group, order_type):
        self.orderId = order_id
        self.ocaGroup = oca_group
        self.orderType = order_type
        self.permId = 900000 + order_id


class FakeContract:
    def __init__(self, symbol):
        self.symbol = symbol


class FakeTrade:
    def __init__(self, order_id, oca_group, order_type, symbol="MHG"):
        self.order = FakeOrder(order_id, oca_group, order_type)
        self.contract = FakeContract(symbol)


class FakeIB:
    """Cancels are recorded; the book shrinks so a re-read reflects the effect."""

    def __init__(self, trades):
        self._trades = list(trades)
        self.cancelled = []
        self.errorEvent = types.SimpleNamespace(
            __iadd__=lambda *a: None, __isub__=lambda *a: None)

    def cancelOrder(self, order):
        self.cancelled.append(order.orderId)
        self._trades = [t for t in self._trades if t.order.orderId != order.orderId]

    def openTrades(self):
        return list(self._trades)

    def reqAllOpenOrders(self):
        return list(self._trades)


# the live 2026-08-26 ib_paper/MHG book: keyed group + two legacy strays
def live_mhg_book():
    return [
        FakeTrade(493, "oca-protect-t4796", "STP"),
        FakeTrade(494, "oca-protect-t4796", "LMT"),
        FakeTrade(447, "oca-protect-446", "STP"),
        FakeTrade(448, "oca-protect-446", "LMT"),
        FakeTrade(466, "oca-protect-465", "STP"),
        FakeTrade(467, "oca-protect-465", "LMT"),
    ]


@pytest.fixture
def client():
    return ibc.IBClient.__new__(ibc.IBClient)


def _stub_verify(client, monkeypatch):
    monkeypatch.setattr(client, "_verify_cancel_effect",
                        lambda *a, **k: {"verify_state": "verified",
                                         "still_resting": [],
                                         "confirmed_gone": [],
                                         "account_wide_seen": None},
                        raising=False)
    monkeypatch.setattr(client, "_cancel_error_capture",
                        lambda ib: ({}, {}, lambda: None), raising=False)
    monkeypatch.setattr(client, "_log_cancel_verdict",
                        lambda *a, **k: None, raising=False)
    monkeypatch.setattr(client, "_leg_descriptor",
                        lambda trade: {"id": trade.order.orderId}, raising=False)


# ── POSITIVE CONTROL ─────────────────────────────────────────────────────────

def test_positive_control_keyed_precancel_alone_leaves_the_strays(client, monkeypatch):
    """The SHIPPED keyed pre-cancel touches only its own group by name.

    This is the defect. If this test ever fails, the pre-cancel changed and the
    sweep below may be redundant — do not just delete it, find out which.
    """
    _stub_verify(client, monkeypatch)
    ib = FakeIB(live_mhg_book())
    client._cancel_oca_group_for_symbol(ib, "MHG", "oca-protect-t4796")
    assert sorted(ib.cancelled) == [493, 494]
    survivors = {t.order.ocaGroup for t in ib.openTrades()}
    assert survivors == {"oca-protect-446", "oca-protect-465"}


# ── annotate (the shipped default) ───────────────────────────────────────────

def test_annotate_default_cancels_nothing(client, monkeypatch):
    monkeypatch.delenv("PROTECTION_STRAY_GROUP_MODE", raising=False)
    _stub_verify(client, monkeypatch)
    ib = FakeIB(live_mhg_book())
    plan = client._sweep_stray_oca_groups(ib, "MHG", "oca-protect-t4796")
    assert plan["mode"] == "annotate"
    assert plan["acted"] is False
    assert ib.cancelled == []
    assert sorted(plan["stray_groups"]) == ["oca-protect-446", "oca-protect-465"]


def test_off_does_not_even_read(client, monkeypatch):
    monkeypatch.setenv("PROTECTION_STRAY_GROUP_MODE", "off")
    ib = FakeIB(live_mhg_book())
    plan = client._sweep_stray_oca_groups(ib, "MHG", "oca-protect-t4796")
    assert plan == {"mode": "off", "acted": False}
    assert ib.cancelled == []


# ── apply ────────────────────────────────────────────────────────────────────

def test_apply_cancels_only_the_stray_legs(client, monkeypatch):
    monkeypatch.setenv("PROTECTION_STRAY_GROUP_MODE", "apply")
    _stub_verify(client, monkeypatch)
    ib = FakeIB(live_mhg_book())
    plan = client._sweep_stray_oca_groups(ib, "MHG", "oca-protect-t4796")
    assert plan["acted"] is True
    assert sorted(ib.cancelled) == [447, 448, 466, 467]
    # the trade's OWN keyed legs are untouched by this sweep
    assert 493 not in ib.cancelled and 494 not in ib.cancelled


def test_apply_never_touches_a_siblings_keyed_group(client, monkeypatch):
    """The BL-20260814 guard, at the wiring level."""
    monkeypatch.setenv("PROTECTION_STRAY_GROUP_MODE", "apply")
    _stub_verify(client, monkeypatch)
    ib = FakeIB([
        FakeTrade(493, "oca-protect-t4796", "STP"),
        FakeTrade(600, "oca-protect-t5150", "STP"),
        FakeTrade(601, "oca-protect-t5150", "LMT"),
    ])
    plan = client._sweep_stray_oca_groups(ib, "MHG", "oca-protect-t4796")
    assert ib.cancelled == []
    assert plan["preserved_groups"] == ["oca-protect-t5150"]


def test_apply_ignores_other_symbols(client, monkeypatch):
    monkeypatch.setenv("PROTECTION_STRAY_GROUP_MODE", "apply")
    _stub_verify(client, monkeypatch)
    ib = FakeIB([
        FakeTrade(493, "oca-protect-t4796", "STP", symbol="MHG"),
        FakeTrade(423, "834864174", "STP", symbol="MGC"),
    ])
    client._sweep_stray_oca_groups(ib, "MHG", "oca-protect-t4796")
    assert ib.cancelled == []


def test_read_failure_is_not_evidence_of_no_strays(client, monkeypatch):
    monkeypatch.setenv("PROTECTION_STRAY_GROUP_MODE", "apply")
    _stub_verify(client, monkeypatch)

    class Boom(FakeIB):
        def openTrades(self):
            raise RuntimeError("gateway wedged")

    plan = client._sweep_stray_oca_groups(Boom([]), "MHG", "oca-protect-t4796")
    assert plan["read_state"] == "could_not_look"
    assert plan["acted"] is False


def test_returned_plan_carries_no_live_handles(client, monkeypatch):
    monkeypatch.setenv("PROTECTION_STRAY_GROUP_MODE", "apply")
    _stub_verify(client, monkeypatch)
    plan = client._sweep_stray_oca_groups(
        FakeIB(live_mhg_book()), "MHG", "oca-protect-t4796")
    for leg in plan["cancel"]:
        assert not any(k.startswith("_") for k in leg)
