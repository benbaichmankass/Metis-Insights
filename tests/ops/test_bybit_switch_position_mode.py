"""The venue position-mode switch decides, refuses, and VERIFIES.

`scripts/ops/bybit_switch_position_mode.py` is the venue half of T.2
(`BL-20260821-PAIRS-SOL-ETH-STRANDS-ON-EVERY-OPEN`). It mutates a live account's
book structure, so the decision has to be arguable HERE rather than against a
real position — the lesson of
`BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG`.

The load-bearing assertions, in order of what they stop:
  * a mode we could NOT read is never treated as one-way (the whole point of the
    four-state read; defaulting an unread mode to the netting value is what
    would make a hedge account look safe to treat as netted);
  * a switch is refused unless the symbol is flat AND has no resting orders;
  * an accepted-but-ineffective switch reports FAILURE, because the venue enum
    (3 = Both Sides, not 1) is verified by re-reading rather than asserted.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

_SRC = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "ops" / "bybit_switch_position_mode.py"
_spec = importlib.util.spec_from_file_location("bybit_switch_position_mode", _SRC)
mod = importlib.util.module_from_spec(_spec)
sys.modules["bybit_switch_position_mode"] = mod
_spec.loader.exec_module(mod)


def _rows(*specs):
    return {"result": {"list": [{"positionIdx": i, "side": s, "size": z, "avgPrice": "1"}
                                for i, s, z in specs]}}


class FakeClient:
    """Minimal pybit stand-in. `switched` records what the venue was sent."""

    def __init__(self, positions, orders=None, after=None, raise_positions=False,
                 raise_orders=False, raise_switch=False):
        self._positions = positions
        self._after = after
        self._orders = orders if orders is not None else {"result": {"list": []}}
        self._raise_positions = raise_positions
        self._raise_orders = raise_orders
        self._raise_switch = raise_switch
        self.switched = []

    def get_positions(self, **kw):
        if self._raise_positions:
            raise RuntimeError("venue unreachable")
        if self.switched and self._after is not None:
            return self._after
        return self._positions

    def get_open_orders(self, **kw):
        if self._raise_orders:
            raise RuntimeError("venue unreachable")
        return self._orders

    def switch_position_mode(self, **kw):
        if self._raise_switch:
            raise RuntimeError("rejected")
        self.switched.append(kw)
        return {"retCode": 0, "retMsg": "OK"}


# --------------------------- read_mode: four states ---------------------------

def test_one_way_is_a_single_positionidx_zero_row():
    r = mod.read_mode(FakeClient(_rows((0, "None", "0"))), "linear", "SOLUSDT")
    assert (r["read_state"], r["mode"], r["flat"]) == ("read", mod.ONE_WAY, True)


def test_hedge_is_the_idx_1_and_2_pair_and_is_readable_while_flat():
    # The whole reason this script can verify a switch: size 0 rows still carry
    # positionIdx, so the mode survives the symbol being flat.
    r = mod.read_mode(FakeClient(_rows((1, "Buy", "0"), (2, "Sell", "0"))), "linear", "SOLUSDT")
    assert (r["read_state"], r["mode"], r["flat"]) == ("read", mod.HEDGE, True)


def test_absent_is_not_one_way():
    r = mod.read_mode(FakeClient({"result": {"list": []}}), "linear", "NEWCOIN")
    assert r["read_state"] == "absent" and r["mode"] is None


def test_error_is_not_one_way():
    r = mod.read_mode(FakeClient(None, raise_positions=True), "linear", "SOLUSDT")
    assert r["read_state"] == "error" and r["mode"] is None


def test_unrecognised_idx_set_is_ambiguous_never_guessed():
    r = mod.read_mode(FakeClient(_rows((0, "Buy", "0"), (2, "Sell", "0"))), "linear", "SOLUSDT")
    assert r["read_state"] == "ambiguous" and r["mode"] is None


def test_a_held_position_is_not_flat():
    r = mod.read_mode(FakeClient(_rows((0, "Sell", "4.5"))), "linear", "SOLUSDT")
    assert r["mode"] == mod.ONE_WAY and r["flat"] is False


# ------------------------------ apply refusals -------------------------------

def _run(monkeypatch, client, argv):
    monkeypatch.setattr(mod, "_account_cfg", lambda a: {"id": a, "exchange": "bybit"})
    monkeypatch.setitem(sys.modules, "src.units.accounts.clients",
                        type(sys)("src.units.accounts.clients"))
    sys.modules["src.units.accounts.clients"].bybit_client_for = lambda cfg: client
    return mod.main(argv)


BASE = ["--account", "bybit_1", "--symbol", "SOLUSDT"]
APPLY = BASE + ["--mode", "hedge", "--confirm-account", "bybit_1", "--apply"]


def test_report_only_never_switches(monkeypatch, capsys):
    c = FakeClient(_rows((0, "None", "0")))
    assert _run(monkeypatch, c, BASE) == 0
    assert c.switched == [], "report-only must not touch the venue"


def test_refuses_when_not_flat(monkeypatch):
    c = FakeClient(_rows((0, "Sell", "4.5")))
    assert _run(monkeypatch, c, APPLY) == 3
    assert c.switched == []


def test_refuses_on_resting_orders(monkeypatch):
    c = FakeClient(_rows((0, "None", "0")), orders={"result": {"list": [{"orderId": "x"}]}})
    assert _run(monkeypatch, c, APPLY) == 3
    assert c.switched == []


def test_refuses_when_orders_unreadable(monkeypatch):
    # Could-not-look is not "no resting orders".
    c = FakeClient(_rows((0, "None", "0")), raise_orders=True)
    assert _run(monkeypatch, c, APPLY) == 3
    assert c.switched == []


def test_refuses_when_mode_unreadable(monkeypatch):
    c = FakeClient(None, raise_positions=True)
    assert _run(monkeypatch, c, APPLY) == 3
    assert c.switched == []


def test_refuses_without_the_account_echo(monkeypatch):
    c = FakeClient(_rows((0, "None", "0")))
    argv = BASE + ["--mode", "hedge", "--confirm-account", "bybit_2", "--apply"]
    assert _run(monkeypatch, c, argv) == 2
    assert c.switched == []


def test_already_in_mode_is_a_clean_noop(monkeypatch):
    c = FakeClient(_rows((1, "Buy", "0"), (2, "Sell", "0")))
    assert _run(monkeypatch, c, APPLY) == 0
    assert c.switched == []


# ------------------------- the verification contract -------------------------

def test_switch_sends_both_sides_three_not_one(monkeypatch):
    c = FakeClient(_rows((0, "None", "0")), after=_rows((1, "Buy", "0"), (2, "Sell", "0")))
    assert _run(monkeypatch, c, APPLY) == 0
    assert c.switched and c.switched[0]["mode"] == mod.MODE_BOTH_SIDES == 3


def test_accepted_but_ineffective_switch_reports_failure(monkeypatch, capsys):
    # The venue says OK and the mode does not move. That must NOT read as success:
    # arming the allowlist against an unchanged venue refuses every order.
    c = FakeClient(_rows((0, "None", "0")), after=_rows((0, "None", "0")))
    rc = _run(monkeypatch, c, APPLY)
    assert rc == 5
    assert '"switch_verified": false' in capsys.readouterr().out


def test_switch_raising_is_reported_not_swallowed(monkeypatch):
    c = FakeClient(_rows((0, "None", "0")), raise_switch=True)
    assert _run(monkeypatch, c, APPLY) == 4
