"""Tests for the account-scoped options-expression overlay (Slice-3b).

Pure parts (the gate + the contracts/snapshot join) are tested directly; the live
`place_options_expression` path is tested with INJECTED fake data/exec clients —
no network, no real Alpaca.
"""
from __future__ import annotations

import datetime as _dt
from types import SimpleNamespace

from src.units.accounts.options_overlay import (
    account_expresses_options,
    build_chain_from_responses,
    place_options_expression,
)

TODAY = _dt.date(2026, 6, 27)
EXP = "2026-07-31"  # ~34 DTE


# ---------------------------------------------------------------- the gate
def test_gate_returns_block_when_expressing():
    cfg = {"options": {"express_as": "debit_vertical", "max_loss_per_trade_usd": 60}}
    assert account_expresses_options(cfg) == cfg["options"]


def test_gate_none_without_block_or_disabled_or_other_structure():
    assert account_expresses_options({}) is None
    assert account_expresses_options({"options": {"express_as": "debit_vertical", "enabled": False}}) is None
    assert account_expresses_options({"options": {"express_as": "iron_condor"}}) is None
    assert account_expresses_options({"options": "yes"}) is None


# ---------------------------------------------------- contracts/snapshot join
def test_build_chain_join():
    contracts = {"option_contracts": [
        {"symbol": "GDX260731C00082000", "strike_price": "82", "expiration_date": EXP, "type": "call", "open_interest": 100},
        {"symbol": "GDX260731C00083000", "strike_price": "83", "expiration_date": EXP, "type": "call"},
        {"symbol": "GDX260731C09999000", "strike_price": "9999", "expiration_date": EXP, "type": "call"},  # no snapshot
    ]}
    snapshots = {"snapshots": {
        "GDX260731C00082000": {"latestQuote": {"bp": 0.88, "ap": 0.92}, "greeks": {"delta": 0.52}, "impliedVolatility": 0.30},
        "GDX260731C00083000": {"latestQuote": {"bp": 0.43, "ap": 0.47}, "greeks": {"delta": 0.40}, "impliedVolatility": 0.31},
    }}
    chain = build_chain_from_responses(contracts, snapshots)
    assert len(chain) == 3
    c82 = next(c for c in chain if c.strike == 82)
    assert c82.mid == 0.90 and c82.delta == 0.52 and c82.iv == 0.30 and c82.open_interest == 100
    c_noquote = next(c for c in chain if c.strike == 9999)
    assert c_noquote.mid is None  # no snapshot → unquotable → selector will skip


# ------------------------------------------------ live path with fake clients
class _FakeData:
    def __init__(self, contracts, snapshots):
        self._contracts, self._snapshots = contracts, snapshots
        self.calls = []

    def list_option_contracts(self, underlying, **kw):
        self.calls.append(("contracts", underlying, kw))
        return {"retCode": 0, "result": self._contracts}

    def snapshots(self, underlying, **kw):
        return {"retCode": 0, "result": self._snapshots}


class _FakeExec:
    def __init__(self):
        self.placed = None

    def place_spread(self, legs, **kw):
        # AlpacaOptionsExecutor.place_spread returns the envelope with `orderId`
        # (it maps the raw API `id` internally), so the fake mirrors that shape.
        self.placed = {"legs": legs, **kw}
        return {"retCode": 0, "result": {"orderId": "opt-order-1"}}


def _gdx_chain():
    contracts = {"option_contracts": [
        {"symbol": "GDX260731C00081000", "strike_price": "81", "expiration_date": EXP, "type": "call"},
        {"symbol": "GDX260731C00082000", "strike_price": "82", "expiration_date": EXP, "type": "call"},
        {"symbol": "GDX260731C00083000", "strike_price": "83", "expiration_date": EXP, "type": "call"},
    ]}
    snapshots = {"snapshots": {
        "GDX260731C00081000": {"latestQuote": {"bp": 1.38, "ap": 1.42}},
        "GDX260731C00082000": {"latestQuote": {"bp": 0.88, "ap": 0.92}},  # mid 0.90
        "GDX260731C00083000": {"latestQuote": {"bp": 0.43, "ap": 0.47}},  # mid 0.45
    }}
    return contracts, snapshots


CFG = {"express_as": "debit_vertical", "max_loss_per_trade_usd": 60,
       "target_dte": 35, "min_dte": 21, "max_dte": 60}


def test_place_expression_happy_path():
    data = _FakeData(*_gdx_chain())
    ex = _FakeExec()
    pkg = SimpleNamespace(symbol="GDX", direction="long", entry=82.0, sl=80.0, tp=85.0)
    res = place_options_expression(pkg, CFG, data_client=data, exec_client=ex, today=TODAY)
    assert not res.refused
    assert res.trade_id == "opt-order-1"
    # 82/83 bull call: net debit 0.45 → $45/contract; $60 budget → 1 contract.
    assert res.contracts == 1
    assert res.net_debit == 0.45
    assert res.max_loss_usd == 45.0
    # The executor received a 2-leg mleg (buy 82, sell 83) at the net-debit limit.
    assert [leg.side for leg in ex.placed["legs"]] == ["buy", "sell"]
    assert ex.placed["legs"][0].symbol == "GDX260731C00082000"
    assert ex.placed["qty"] == 1
    assert ex.placed["limit_price"] == 0.45


def test_place_expression_dry_run_places_nothing():
    data = _FakeData(*_gdx_chain())
    ex = _FakeExec()
    pkg = SimpleNamespace(symbol="GDX", direction="long", entry=82.0, sl=80.0, tp=85.0)
    res = place_options_expression(pkg, CFG, data_client=data, exec_client=ex, today=TODAY, is_dry=True)
    assert not res.refused and res.contracts == 1 and ex.placed is None  # selected+sized, not placed


def test_place_expression_refuses_bad_package():
    data = _FakeData(*_gdx_chain())
    ex = _FakeExec()
    pkg = SimpleNamespace(symbol="GDX", direction="long", entry=0.0)
    res = place_options_expression(pkg, CFG, data_client=data, exec_client=ex, today=TODAY)
    assert res.refused and ex.placed is None


def test_place_expression_refuses_empty_chain():
    data = _FakeData({"option_contracts": []}, {"snapshots": {}})
    ex = _FakeExec()
    pkg = SimpleNamespace(symbol="GDX", direction="long", entry=82.0, sl=80.0, tp=85.0)
    res = place_options_expression(pkg, CFG, data_client=data, exec_client=ex, today=TODAY)
    assert res.refused and res.reason == "empty_chain" and ex.placed is None


# ---------------------------------------------------- Slice-5 structure dict
from src.units.accounts.options_overlay import (  # noqa: E402
    OptionsExpressionResult,
    options_structure_dict,
)
from src.units.accounts.options_selector import (  # noqa: E402
    ChainContract,
    DebitVertical,
)
from src.units.accounts.alpaca_options_exec import OptionLeg  # noqa: E402


def test_options_structure_dict_captures_legs_and_defined_risk():
    long_cc = ChainContract(
        symbol="SLV260116C00025000", type="call", strike=25.0, expiration="2026-01-16",
    )
    short_cc = ChainContract(
        symbol="SLV260116C00027000", type="call", strike=27.0, expiration="2026-01-16",
    )
    vertical = DebitVertical(
        True, long_leg=long_cc, short_leg=short_cc, width=2.0, net_debit=0.60,
        max_loss_usd=120.0, max_gain_usd=280.0, breakeven=25.60, expiration="2026-01-16",
    )
    legs = [
        OptionLeg("SLV260116C00025000", "buy", "buy_to_open", 1),
        OptionLeg("SLV260116C00027000", "sell", "sell_to_open", 1),
    ]
    res = OptionsExpressionResult(
        False, contracts=2, net_debit=0.60, max_loss_usd=120.0,
        vertical=vertical, legs=legs,
    )
    d = options_structure_dict(res)
    assert d["structure"] == "debit_vertical"
    assert d["contracts"] == 2
    assert d["net_debit"] == 0.60
    assert d["max_loss_usd"] == 120.0
    assert d["width"] == 2.0
    assert d["max_gain_usd"] == 280.0
    assert d["breakeven"] == 25.60
    assert d["expiration"] == "2026-01-16"
    assert len(d["legs"]) == 2
    assert d["legs"][0] == {
        "symbol": "SLV260116C00025000", "side": "buy", "intent": "buy_to_open",
        "ratio": 1, "strike": 25.0, "type": "call",
    }
    assert d["legs"][1]["side"] == "sell"
    assert d["legs"][1]["strike"] == 27.0


def test_options_structure_dict_degrades_without_vertical():
    res = OptionsExpressionResult(False, contracts=1, net_debit=0.4, max_loss_usd=40.0)
    d = options_structure_dict(res)
    assert d["structure"] == "debit_vertical"
    assert d["contracts"] == 1
    assert d["legs"] == []
    assert "expiration" not in d  # no vertical → no geometry fields
