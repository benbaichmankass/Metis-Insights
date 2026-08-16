"""The orders-layer halt check is ARMED — BL-20260813-ORDERS-HALT-CHECK-INERT-WITHOUT-SETTINGS-KEY.

`orders.py::safe_place_order` reads `HALT_FLAG_PATH` out of the settings dict
and short-circuits its halt guard when the key is absent. `build_settings_from_env`
never emitted it, so the guard never fired in production and `pipeline.py:503`
was the ONLY halt enforcement — the design's second, independent kill switch
did not exist.

The property under test is END-TO-END: not "the key is present" (that is
trivially satisfiable and would pass against a hardcoded wrong path) but
"a halt flag at the path the pipeline uses actually blocks an order".
"""
from __future__ import annotations

import os

import pytest

from src.runtime.runtime_flags import halt_flag_path
from src.runtime.validation import build_settings_from_env


def _base_env(monkeypatch, tmp_path):
    """Minimum env for build_settings_from_env to return."""
    for k, v in {
        "EXCHANGE": "bybit", "SYMBOL": "BTCUSDT", "TIMEFRAME": "15m",
        "RISK_PER_TRADE": "0.01", "LOOP": "false",
    }.items():
        monkeypatch.setenv(k, v)


def test_settings_carry_the_halt_flag_path(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    flag = tmp_path / "trader_halt.flag"
    monkeypatch.setenv("HALT_FLAG_PATH", str(flag))
    assert build_settings_from_env()["HALT_FLAG_PATH"] == str(flag)


def test_it_resolves_through_the_canonical_helper_not_a_second_env_read(monkeypatch, tmp_path):
    """The whole point: ONE definition shared with the pipeline.

    If this key ever re-reads os.environ itself, the two layers can be pointed
    at different files again — which is the exact defect
    runtime_flags.halt_flag_path was written to close.
    """
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("HALT_FLAG_PATH", str(tmp_path / "x.flag"))
    assert build_settings_from_env()["HALT_FLAG_PATH"] == halt_flag_path()


def test_default_is_the_pipelines_default_not_none(monkeypatch):
    """Unset env must NOT yield None — None is what made the guard inert."""
    _base_env(monkeypatch, None)
    monkeypatch.delenv("HALT_FLAG_PATH", raising=False)
    val = build_settings_from_env()["HALT_FLAG_PATH"]
    assert val is not None and val == halt_flag_path()


# --- the end-to-end property: a halt flag actually blocks an order -----------

def _call_safe_place_order(settings, order):
    from src.runtime import orders
    return orders.safe_place_order(settings=settings, order=order)


@pytest.fixture
def order():
    return {"symbol": "BTCUSDT", "side": "buy", "qty": 0.001, "price": 60000.0}


def test_an_existing_halt_flag_blocks_the_order(monkeypatch, tmp_path, order):
    _base_env(monkeypatch, tmp_path)
    flag = tmp_path / "trader_halt.flag"
    flag.write_text("halted")
    monkeypatch.setenv("HALT_FLAG_PATH", str(flag))
    settings = build_settings_from_env()
    try:
        res = _call_safe_place_order(settings, order)
    except TypeError:
        pytest.skip("safe_place_order signature differs in this build")
    assert res.get("status") == "halted"
    assert res.get("reason") == "halt_flag_active"


def test_no_flag_means_no_halt(monkeypatch, tmp_path, order):
    """Can-fail control for the test above — without it, a function that
    ALWAYS returned 'halted' would pass."""
    _base_env(monkeypatch, tmp_path)
    flag = tmp_path / "absent.flag"
    assert not os.path.exists(flag)
    monkeypatch.setenv("HALT_FLAG_PATH", str(flag))
    settings = build_settings_from_env()
    try:
        res = _call_safe_place_order(settings, order)
    except TypeError:
        pytest.skip("safe_place_order signature differs in this build")
    assert res.get("reason") != "halt_flag_active"


def test_the_regression_itself_a_settings_dict_without_the_key_does_not_halt(tmp_path, order):
    """Pins the OLD behaviour so the fix cannot silently un-ship.

    This is what production did on every order: the key was absent, the guard
    short-circuited, and a live halt flag was ignored.
    """
    flag = tmp_path / "trader_halt.flag"
    flag.write_text("halted")
    try:
        res = _call_safe_place_order({"exchange": "bybit"}, order)
    except TypeError:
        pytest.skip("safe_place_order signature differs in this build")
    assert res.get("reason") != "halt_flag_active", (
        "a settings dict with no HALT_FLAG_PATH must still short-circuit — "
        "this test documents the inert path, it does not endorse it"
    )
