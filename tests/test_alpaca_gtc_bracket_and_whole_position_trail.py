"""PI-20260926-HJPL5ABP-0001 root fix — two halves.

(i) ``AlpacaClient.place`` sends a bracket/OTO as ``time_in_force: gtc`` so the
    protective legs survive the RTH close (they were ``day`` and Alpaca
    cancelled them at the close — alpaca_paper/SPY trade 6131, 8 of 19 shares
    naked). A venue refusal of the TIF falls back to ``day`` once.
(ii) ``AlpacaClient.modify_protective(..., sole_open_row=True)`` may trail a
    single OCO sized to the WHOLE netted position when the trade is the
    symbol's only open journal row — and refuses every shape that would move a
    sibling's protection (BL-20260908) or guess (BL-20260820).
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from src.runtime import order_monitor as om
from src.units.accounts import execute
from src.units.accounts.alpaca_client import AlpacaClient


def _client():
    return AlpacaClient(api_key="k", api_secret="s", env="paper")


# ───────────────────────────────────────────── (i) GTC entry bracket
def _capture_posts(monkeypatch, cli, responses=None):
    posts: List[Dict[str, Any]] = []
    responses = list(responses or [])

    def fake(method, path, json_body=None):
        if method == "POST":
            posts.append(dict(json_body))
            if responses:
                return responses.pop(0)
            return {"retCode": 0, "result": {"id": "o1"}}
        return {"retCode": 0, "result": {"status": "filled"}}

    monkeypatch.setattr(cli, "_request", fake)
    monkeypatch.setenv("ALPACA_PLACE_CONFIRM_S", "0")
    return posts


def test_bracket_entry_is_gtc(monkeypatch):
    cli = _client()
    posts = _capture_posts(monkeypatch, cli)
    out = cli.place({"symbol": "SPY", "side": "Buy", "qty": 8, "sl": 760.64, "tp": 840.65})
    assert out["retCode"] == 0
    assert posts[0]["order_class"] == "bracket"
    assert posts[0]["time_in_force"] == "gtc"


def test_oto_entry_is_gtc(monkeypatch):
    cli = _client()
    posts = _capture_posts(monkeypatch, cli)
    cli.place({"symbol": "SPY", "side": "Buy", "qty": 8, "sl": 760.64})
    assert posts[0]["order_class"] == "oto" and posts[0]["time_in_force"] == "gtc"


def test_plain_market_without_legs_stays_day(monkeypatch):
    cli = _client()
    posts = _capture_posts(monkeypatch, cli)
    cli.place({"symbol": "SPY", "side": "Buy", "qty": 8})
    assert "order_class" not in posts[0] and posts[0]["time_in_force"] == "day"


def test_tif_refusal_falls_back_to_day_once(monkeypatch):
    cli = _client()
    posts = _capture_posts(monkeypatch, cli, responses=[
        {"retCode": 422, "retMsg": "invalid time_in_force for bracket orders"},
        {"retCode": 0, "result": {"id": "o2"}},
    ])
    out = cli.place({"symbol": "SPY", "side": "Buy", "qty": 8, "sl": 760.64, "tp": 840.65})
    assert out["retCode"] == 0
    assert [p["time_in_force"] for p in posts] == ["gtc", "day"]


def test_non_tif_refusal_is_not_retried(monkeypatch):
    cli = _client()
    posts = _capture_posts(monkeypatch, cli, responses=[
        {"retCode": 403, "retMsg": "insufficient buying power"}])
    out = cli.place({"symbol": "SPY", "side": "Buy", "qty": 8, "sl": 760.64, "tp": 840.65})
    assert out["retCode"] == 403 and len(posts) == 1


# ───────────────────────────────────────────── (ii) whole-position trail
def _oco(qty="19", side="sell"):
    return [
        {"id": "lim", "symbol": "SPY", "side": side, "type": "limit",
         "qty": qty, "limit_price": "840.65"},
        {"id": "stp", "symbol": "SPY", "side": side, "type": "stop",
         "qty": qty, "stop_price": "760.64"},
    ]


def _venue(monkeypatch, cli, *, orders, position=None):
    patches: List[tuple] = []
    monkeypatch.setattr(cli, "_open_orders_for_symbol", lambda s: [dict(o) for o in orders])
    monkeypatch.setattr(cli, "_position_raw", lambda s: position)

    def fake(method, path, json_body=None):
        assert method == "PATCH", (method, path)
        patches.append((path, json_body))
        return {"retCode": 0, "result": {}}

    monkeypatch.setattr(cli, "_request", fake)
    return patches


LONG_19 = {"qty": "19", "side": "long"}


def test_sole_row_trails_the_whole_position_oco(monkeypatch):
    cli = _client()
    patches = _venue(monkeypatch, cli, orders=_oco(), position=LONG_19)
    out = cli.modify_protective("SPY", sl=763.69, qty=8, sole_open_row=True)
    assert out["retCode"] == 0, out
    assert out["result"]["scope"] == "whole_position_sole_open_row"
    assert patches == [("/v2/orders/stp", {"stop_price": "763.69"})]


def test_without_sole_row_the_exact_qty_refusal_is_unchanged(monkeypatch):
    cli = _client()
    patches = _venue(monkeypatch, cli, orders=_oco(), position=LONG_19)
    out = cli.modify_protective("SPY", sl=763.69, qty=8)
    assert out["retCode"] == 1 and "may protect another open trade" in out["retMsg"]
    assert patches == []


def test_exact_qty_match_still_wins_and_reports_trade_scope(monkeypatch):
    cli = _client()
    patches = _venue(monkeypatch, cli, orders=_oco(qty="8"), position=LONG_19)
    out = cli.modify_protective("SPY", sl=763.69, qty=8, sole_open_row=True)
    assert out["result"]["scope"] == "trade_qty" and len(patches) == 1


def test_stale_partial_oco_is_not_trailed(monkeypatch):
    """The pre-repair SPY state: an 11-share OCO against a 19 net. It does
    not cover the book, so it is not attributable — refuse."""
    cli = _client()
    patches = _venue(monkeypatch, cli, orders=_oco(qty="11"), position=LONG_19)
    out = cli.modify_protective("SPY", sl=763.69, qty=8, sole_open_row=True)
    assert out["retCode"] == 1 and "whole-position match did not apply" in out["retMsg"]
    assert patches == []


def test_non_protective_order_blocks_the_whole_position_match(monkeypatch):
    cli = _client()
    stray = {"id": "entry", "symbol": "SPY", "side": "buy", "type": "limit", "qty": "19"}
    patches = _venue(monkeypatch, cli, orders=_oco() + [stray], position=LONG_19)
    out = cli.modify_protective("SPY", sl=763.69, qty=8, sole_open_row=True)
    assert out["retCode"] == 1 and patches == []


def test_a_leg_listed_twice_counts_once(monkeypatch):
    cli = _client()
    patches = _venue(monkeypatch, cli, orders=_oco() + [_oco()[1]], position=LONG_19)
    out = cli.modify_protective("SPY", sl=763.69, qty=8, sole_open_row=True)
    assert out["retCode"] == 0 and len(patches) == 1


def test_two_stop_legs_are_not_chosen_between(monkeypatch):
    cli = _client()
    extra = dict(_oco()[1], id="stp2")
    patches = _venue(monkeypatch, cli, orders=_oco() + [extra], position=LONG_19)
    out = cli.modify_protective("SPY", sl=763.69, qty=8, sole_open_row=True)
    assert out["retCode"] == 1 and patches == []


def test_unreadable_position_refuses(monkeypatch):
    cli = _client()
    patches = _venue(monkeypatch, cli, orders=_oco(), position=None)
    out = cli.modify_protective("SPY", sl=763.69, qty=8, sole_open_row=True)
    assert out["retCode"] == 1 and "unreadable" in out["retMsg"] and patches == []


def test_net_smaller_than_trade_refuses(monkeypatch):
    cli = _client()
    patches = _venue(monkeypatch, cli, orders=_oco(qty="5"),
                     position={"qty": "5", "side": "long"})
    out = cli.modify_protective("SPY", sl=763.69, qty=8, sole_open_row=True)
    assert out["retCode"] == 1 and patches == []


# ───────────────────────────────────────────── wiring
class _DB:
    def __init__(self, rows=None, boom=False):
        self.rows, self.boom = rows or [], boom

    def get_trades(self, filters=None, limit=None):
        if self.boom:
            raise RuntimeError("db locked")
        return [r for r in self.rows
                if all(r.get(k) == v for k, v in (filters or {}).items())]


LEG = {"id": 6131, "account_id": "alpaca_paper", "symbol": "SPY", "status": "open"}


def test_is_sole_open_row_true_for_one_row():
    assert om._is_sole_open_row(_DB([dict(LEG)]), LEG) is True


def test_is_sole_open_row_false_with_a_case_drifted_sibling():
    sib = dict(LEG, id=6000, symbol="spy")
    assert om._is_sole_open_row(_DB([dict(LEG), sib]), LEG) is False


def test_is_sole_open_row_ignores_backtest_rows():
    bt = dict(LEG, id=1, is_backtest=1)
    assert om._is_sole_open_row(_DB([dict(LEG), bt]), LEG) is True


def test_is_sole_open_row_fails_closed_on_read_error():
    assert om._is_sole_open_row(_DB(boom=True), LEG) is False


def test_modify_open_order_forwards_sole_open_row(monkeypatch):
    cli = _client()
    seen = {}

    def fake_modify(symbol, sl=None, tp=None, qty=None, sole_open_row=False):
        seen.update(symbol=symbol, qty=qty, sole=sole_open_row)
        return {"retCode": 0, "result": {"patched": ["stp"]}}

    monkeypatch.setattr(cli, "modify_protective", fake_modify)
    out = execute.modify_open_order(
        cli, {"exchange": "alpaca", "account_id": "alpaca_paper"},
        symbol="SPY", sl=763.69, qty=8, sole_open_row=True)
    assert out["ok"] is True and seen == {"symbol": "SPY", "qty": 8, "sole": True}


@pytest.mark.parametrize("sole", [True, False])
def test_send_modify_passes_sole_open_row_through(monkeypatch, sole):
    seen = {}
    monkeypatch.setattr(om, "_build_account_client",
                        lambda a: (object(), {"exchange": "alpaca", "mode": "live"}))
    import src.units.accounts.clients as clients
    monkeypatch.setattr(clients, "account_supports_management", lambda cfg, op: True)

    def fake_modify_open_order(client, cfg, **kw):
        seen.update(kw)
        return {"ok": True}

    monkeypatch.setattr(execute, "modify_open_order", fake_modify_open_order)
    om._send_modify_to_exchange(dict(LEG), sl=763.69, qty=8.0, sole_open_row=sole)
    assert seen["sole_open_row"] is sole
