"""Tests for the prop screenshot → report vision parser.

The LLM call (:func:`src.prop.screenshot_parse._call_vision`) is the only impure
seam; everything else — shaping the model's JSON into ingest-ready reports,
number coercion, honest-null omission — is pure and locked here. The one
transport test monkeypatches ``_call_vision`` so no API key / network is needed.
"""
from __future__ import annotations

import pytest

from src.prop import screenshot_parse as sp


def test_position_screen_yields_a_single_fill() -> None:
    """A Position detail screen (no balance) → exactly one fill, no account row."""
    model = {
        "reports": [{
            "type": "fill",
            "symbol": "ETHUSD",
            "direction": "buy",
            "status": "filled",
            "entry_price": 1812.04,
            "qty": 1,
            "sl": 1795.0,
            "tp": 1992.0,
            "external_order_id": "501509517",
        }]
    }
    reports = sp._reports_from_model_json(model, default_account="breakout_1")
    assert len(reports) == 1
    r = reports[0]
    assert r["account_id"] == "breakout_1"
    assert r["symbol"] == "ETHUSD"
    assert r["direction"] == "buy"
    assert r["status"] == "filled"
    assert r["entry_price"] == pytest.approx(1812.04)
    assert r["external_order_id"] == "501509517"
    assert "kind" not in r  # a fill, not an account_status


def test_account_screen_yields_account_status() -> None:
    model = {"reports": [{
        "type": "account_status", "balance": 5116, "equity": 5118,
        "realized_today": 0, "unrealized": 1.9,
    }]}
    reports = sp._reports_from_model_json(model, default_account="breakout_1")
    assert len(reports) == 1
    r = reports[0]
    assert r["kind"] == "account_status"
    assert r["balance"] == pytest.approx(5116.0)
    assert r["equity"] == pytest.approx(5118.0)


def test_both_a_fill_and_a_balance_from_one_screen() -> None:
    model = {"reports": [
        {"type": "fill", "symbol": "ETHUSD", "direction": "buy", "entry_price": 1812},
        {"type": "account_status", "balance": 5116, "equity": 5118},
    ]}
    reports = sp._reports_from_model_json(model, default_account="breakout_1")
    kinds = [("account_status" if r.get("kind") == "account_status" else "fill")
             for r in reports]
    assert sorted(kinds) == ["account_status", "fill"]


def test_number_coercion_handles_commas_and_currency() -> None:
    model = {"reports": [{
        "type": "account_status", "balance": "5,116.00", "equity": "$5,118 USD",
    }]}
    r = sp._reports_from_model_json(model, default_account="breakout_1")[0]
    assert r["balance"] == pytest.approx(5116.0)
    assert r["equity"] == pytest.approx(5118.0)


def test_missing_fields_are_omitted_not_zeroed() -> None:
    """A field the model can't read stays absent — never a fabricated 0."""
    model = {"reports": [{"type": "fill", "symbol": "ETHUSD", "entry_price": 1812}]}
    r = sp._reports_from_model_json(model, default_account="breakout_1")[0]
    assert "sl" not in r and "tp" not in r and "pnl" not in r
    assert "exit_price" not in r


def test_account_row_with_no_numbers_is_dropped() -> None:
    """An account report the model emitted with neither balance nor equity is junk."""
    model = {"reports": [{"type": "account_status", "realized_today": 0}]}
    assert sp._reports_from_model_json(model, default_account="breakout_1") == []


def test_fill_without_symbol_is_dropped() -> None:
    model = {"reports": [{"type": "fill", "entry_price": 1812}]}
    assert sp._reports_from_model_json(model, default_account="breakout_1") == []


def test_exit_price_infers_closed_status() -> None:
    model = {"reports": [{
        "symbol": "ETHUSD", "direction": "sell", "exit_price": 1850, "pnl": 38,
    }]}
    r = sp._reports_from_model_json(model, default_account="breakout_1")[0]
    assert r["status"] == "closed"
    assert r["exit_price"] == pytest.approx(1850.0)


def test_empty_reports_list() -> None:
    assert sp._reports_from_model_json({"reports": []}, default_account="x") == []


def test_bare_object_tolerated() -> None:
    """A model that returns a single report object (no 'reports' wrapper) works."""
    model = {"type": "fill", "symbol": "ETHUSD", "entry_price": 1812}
    reports = sp._reports_from_model_json(model, default_account="breakout_1")
    assert len(reports) == 1 and reports[0]["symbol"] == "ETHUSD"


def test_parse_screenshot_monkeypatched(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end shape with the LLM seam stubbed — no API key needed."""
    canned = (
        '{"reports": [{"type":"fill","symbol":"ETHUSD","direction":"buy",'
        '"status":"filled","entry_price":1812.04,"qty":1,'
        '"external_order_id":"501509517"}]}'
    )
    monkeypatch.setattr(sp, "_call_vision", lambda b64, mt: canned)
    reports = sp.parse_screenshot(b"\x89PNG fake", "image/png",
                                  default_account="breakout_1")
    assert len(reports) == 1
    assert reports[0]["symbol"] == "ETHUSD"
    assert reports[0]["external_order_id"] == "501509517"


def test_parse_screenshot_fenced_json(monkeypatch: pytest.MonkeyPatch) -> None:
    canned = '```json\n{"reports": [{"type":"account_status","balance":5116}]}\n```'
    monkeypatch.setattr(sp, "_call_vision", lambda b64, mt: canned)
    reports = sp.parse_screenshot(b"img", "image/jpeg", default_account="breakout_1")
    assert reports[0]["kind"] == "account_status"


def test_parse_screenshot_bad_json_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sp, "_call_vision", lambda b64, mt: "sorry, I can't")
    with pytest.raises(sp.ScreenshotParseError):
        sp.parse_screenshot(b"img", "image/png", default_account="breakout_1")


def test_parse_screenshot_empty_bytes_raises() -> None:
    with pytest.raises(sp.ScreenshotParseError):
        sp.parse_screenshot(b"", "image/png", default_account="breakout_1")


def test_call_vision_without_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(sp.ScreenshotParseError):
        sp._call_vision("Zm9v", "image/png")
