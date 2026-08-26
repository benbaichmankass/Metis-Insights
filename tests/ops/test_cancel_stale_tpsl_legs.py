"""The cleanup script selects by OWNERSHIP, and refuses rather than guessing.

The load-bearing case is `test_does_not_cancel_the_live_trades_leg`: it drives
`cancel_stale_legs` with the leg table measured on the live book at
2026-08-26T00:10Z, where the NEWEST leg belongs to a closed trade. The shipped
newest-wins rule would have kept that leg and cancelled the live trade's.
"""
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
mod = importlib.import_module("scripts.ops.cancel_stale_tpsl_legs")


class _Client:
    def __init__(self):
        self.cancelled = []

    def cancel_order(self, **kw):
        self.cancelled.append(kw.get("orderId"))
        return {"retCode": 0}


def _bybit_leg(order_id, qty, created, kind="StopLoss"):
    return {"orderId": order_id, "qty": str(qty), "stopOrderType": kind,
            "createdTime": str(created), "triggerPrice": "1", "orderStatus": "Untriggered"}


def _row(tid, status, sl, qty):
    return {"id": tid, "status": status, "sl_order_id": sl,
            "tp_order_id": None, "position_size": qty}


# Measured shape: position 5.59; open rows 4921 (1.18) + 4903 (4.41).
_LEGS = [
    _bybit_leg("leg-5003", 0.19, 1_756_083_900_000),   # NEWEST
    _bybit_leg("leg-4987", 0.21, 1_756_000_000_000),
    _bybit_leg("leg-4960", 0.30, 1_755_900_000_000),
    _bybit_leg("leg-4941", 0.22, 1_755_800_000_000),
    _bybit_leg("leg-4921", 1.18, 1_755_759_900_000),   # OPEN trade, OLDER
    _bybit_leg("leg-4903", 4.41, 1_755_700_000_000),   # OPEN trade, OLDEST
]
_ROWS = [
    _row(5003, "closed", "leg-5003", 0.19),
    _row(4987, "closed", "leg-4987", 0.21),
    _row(4960, "closed", "leg-4960", 0.30),
    _row(4941, "closed", "leg-4941", 0.22),
    _row(4921, "open", "leg-4921", 1.18),
    _row(4903, "open", "leg-4903", 4.41),
]


@pytest.fixture
def wired(monkeypatch):
    client = _Client()
    monkeypatch.setattr(mod, "_load_account", lambda a: {"account_id": a, "exchange": "bybit"})
    monkeypatch.setattr(mod, "_live_position_size", lambda cfg, sym: 5.59)
    monkeypatch.setattr(mod, "_build_client", lambda cfg: client)
    monkeypatch.setattr(mod, "_category", lambda cfg: "linear")
    monkeypatch.setattr(mod, "_stop_orders", lambda c, cat, sym: list(_LEGS))
    monkeypatch.setattr(mod, "_journal_leg_rows", lambda a, s: list(_ROWS))
    return client


def test_does_not_cancel_the_live_trades_leg(wired):
    out = mod.cancel_stale_legs("bybit_1", "ETHUSDT", apply=False)
    assert out["action"] == "dry_run", out
    cancel = {r["orderId"] for r in out["plan"]["cancel"]}
    keep = {r["orderId"] for r in out["plan"]["keep"]}
    assert keep == {"leg-4921", "leg-4903"}
    assert "leg-5003" in cancel                     # newest, and CLOSED
    assert cancel.isdisjoint({"leg-4921", "leg-4903"})
    assert wired.cancelled == []                    # dry-run touched nothing


def test_apply_cancels_exactly_the_closed_owned_legs(monkeypatch, wired):
    # After the cancels, only the two open rows' legs rest.
    remaining = [l for l in _LEGS if l["orderId"] in ("leg-4921", "leg-4903")]
    calls = {"n": 0}

    def _stop_orders(c, cat, sym):
        calls["n"] += 1
        return list(_LEGS) if calls["n"] == 1 else remaining

    monkeypatch.setattr(mod, "_stop_orders", _stop_orders)
    out = mod.cancel_stale_legs("bybit_1", "ETHUSDT", apply=True)
    assert out["action"] == "cancelled", out
    assert set(wired.cancelled) == {"leg-5003", "leg-4987", "leg-4960", "leg-4941"}
    assert out["post_state"]["sl_count"] == 2


def test_unreadable_journal_refuses_and_does_not_report_clean(monkeypatch, wired):
    monkeypatch.setattr(mod, "_journal_leg_rows", lambda a, s: None)
    out = mod.cancel_stale_legs("bybit_1", "ETHUSDT", apply=True)
    assert out["action"] == "abort_not_graded", out
    assert out["ok"] is False
    assert wired.cancelled == []


def test_a_leg_no_row_claims_refuses(monkeypatch, wired):
    monkeypatch.setattr(mod, "_journal_leg_rows", lambda a, s: _ROWS[4:])  # only the open rows
    out = mod.cancel_stale_legs("bybit_1", "ETHUSDT", apply=True)
    assert out["action"] == "abort_unattributable_legs", out
    assert wired.cancelled == []


def test_all_legs_live_is_a_clean_noop_not_a_refusal(monkeypatch, wired):
    monkeypatch.setattr(mod, "_stop_orders", lambda c, cat, sym: [
        _bybit_leg("leg-4921", 1.18, 1), _bybit_leg("leg-4903", 4.41, 2)])
    monkeypatch.setattr(mod, "_journal_leg_rows", lambda a, s: _ROWS[4:])
    out = mod.cancel_stale_legs("bybit_1", "ETHUSDT", apply=True)
    assert out["action"] == "noop_already_clean"
    assert out["ok"] is True
    assert wired.cancelled == []


def test_refuses_when_the_cancel_would_undercover(monkeypatch, wired):
    monkeypatch.setattr(mod, "_stop_orders", lambda c, cat, sym: [
        _bybit_leg("live", 1.0, 2), _bybit_leg("dead", 4.59, 1)])
    monkeypatch.setattr(mod, "_journal_leg_rows", lambda a, s: [
        _row(1, "open", "live", 1.0), _row(2, "closed", "dead", 4.59)])
    out = mod.cancel_stale_legs("bybit_1", "ETHUSDT", apply=True)
    assert out["action"] == "abort_would_undercover", out
    assert wired.cancelled == []
