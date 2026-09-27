"""PI-20260926-HJPL5ABP-0001 root fix: GTC entry-bracket legs.

``AlpacaClient.place`` sends a bracket/OTO as ``time_in_force: gtc`` so the
protective legs survive the RTH close. They were ``day`` and Alpaca cancelled
them at the close — alpaca_paper/SPY trade 6131's 8 shares went naked. A venue
refusal of the TIF falls back to ``day`` once.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.units.accounts.alpaca_client import AlpacaClient


def _client():
    return AlpacaClient(api_key="k", api_secret="s", env="paper")


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
