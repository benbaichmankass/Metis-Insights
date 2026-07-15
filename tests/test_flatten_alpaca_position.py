"""Tests for scripts/ops/flatten_alpaca_position.py — the one-shot guarded
Alpaca flatten (the Alpaca sibling of flatten_bybit_position.py /
flatten_ib_position.py).

All broker I/O is monkeypatched: these verify the script's decision logic
(dry-run vs apply, already-flat noop, unreadable abort, non-Alpaca refusal,
side derivation, post-flatten verify) without touching Alpaca.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "flatten_alpaca_position",
    Path(__file__).resolve().parents[1] / "scripts" / "ops" / "flatten_alpaca_position.py",
)
flat = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(flat)  # type: ignore


_ALPACA_ACCT = {"account_id": "alpaca_live", "exchange": "alpaca",
                "alpaca_env": "live", "api_key_env": "ALPACA_API_KEY_ID_LIVE"}


@pytest.fixture(autouse=True)
def _patch_account(monkeypatch):
    monkeypatch.setattr(flat, "_load_account",
                        lambda aid: _ALPACA_ACCT if aid == "alpaca_live" else None)


def _patch_live(monkeypatch, sequence):
    """sequence: list of return values for successive _live_position calls."""
    calls = {"i": 0}

    def _lp(cfg, symbol):
        i = min(calls["i"], len(sequence) - 1)
        calls["i"] += 1
        return sequence[i]

    monkeypatch.setattr(flat, "_live_position", _lp)


def test_unknown_account_aborts():
    r = flat.flatten("nope", "IEF", apply=False)
    assert r["ok"] is False and "not found" in r["detail"]


def test_non_alpaca_account_refused(monkeypatch):
    monkeypatch.setattr(flat, "_load_account",
                        lambda aid: {"account_id": "bybit_2", "exchange": "bybit"})
    r = flat.flatten("bybit_2", "BTCUSDT", apply=True)
    assert r["ok"] is False and "not Alpaca" in r["detail"]


def test_unreadable_aborts(monkeypatch):
    _patch_live(monkeypatch, [None])  # could-not-read
    r = flat.flatten("alpaca_live", "IEF", apply=True)
    assert r["ok"] is False and r["action"] == "abort_unreadable"


def test_already_flat_noop(monkeypatch):
    _patch_live(monkeypatch, [{}])  # read OK, no position
    r = flat.flatten("alpaca_live", "IEF", apply=True)
    assert r["ok"] is True and r["action"] == "noop_already_flat"


def test_dry_run_does_not_place(monkeypatch):
    _patch_live(monkeypatch, [{"symbol": "IEF", "side": "long", "size": 1.0,
                               "entry_price": 94.1078, "unrealised_pnl": -0.59}])

    def _boom(*a, **k):  # close must NOT be called in dry-run
        raise AssertionError("close_open_position called during dry-run")

    monkeypatch.setattr(flat, "_place_close", _boom, raising=False)
    r = flat.flatten("alpaca_live", "IEF", apply=False)
    assert r["ok"] is True and r["action"] == "dry_run"
    # long → close is a SELL of the full size
    assert r["planned_close"]["action"] == "SELL" and r["planned_close"]["qty"] == 1.0


def test_apply_flattens_and_verifies(monkeypatch):
    # first read: long 1.0; post-flatten read: flat ({})
    _patch_live(monkeypatch, [
        {"symbol": "IEF", "side": "long", "size": 1.0},
        {},
    ])
    monkeypatch.setattr(flat, "_build_ops_client", lambda cfg: object())
    captured = {}

    def _close(client, cfg, *, symbol, side, qty):
        captured.update(symbol=symbol, side=side, qty=qty)
        return {"ok": True, "exchange_order_id": "abc"}

    monkeypatch.setattr(flat, "_place_close", _close)
    r = flat.flatten("alpaca_live", "IEF", apply=True)
    assert r["ok"] is True and r["action"] == "flattened"
    # long → close side passed to close_open_position is the ENTRY side
    # ('long'); the Alpaca native flatten closes the whole position.
    assert captured == {"symbol": "IEF", "side": "long", "qty": 1.0}


def test_apply_reports_close_failure(monkeypatch):
    _patch_live(monkeypatch, [{"symbol": "IEF", "side": "long", "size": 1.0}])
    monkeypatch.setattr(flat, "_build_ops_client", lambda cfg: object())
    monkeypatch.setattr(flat, "_place_close",
                        lambda *a, **k: {"ok": False, "error": "market is closed"})
    r = flat.flatten("alpaca_live", "IEF", apply=True)
    assert r["ok"] is False and r["action"] == "close_failed"


def test_apply_no_client_aborts(monkeypatch):
    _patch_live(monkeypatch, [{"symbol": "IEF", "side": "long", "size": 1.0}])
    monkeypatch.setattr(flat, "_build_ops_client", lambda cfg: None)  # missing creds
    r = flat.flatten("alpaca_live", "IEF", apply=True)
    assert r["ok"] is False and r["action"] == "abort_no_client"


def test_apply_still_open_after_close(monkeypatch):
    _patch_live(monkeypatch, [
        {"symbol": "IEF", "side": "long", "size": 1.0},
        {"symbol": "IEF", "side": "long", "size": 1.0},  # still there
    ])
    monkeypatch.setattr(flat, "_build_ops_client", lambda cfg: object())
    monkeypatch.setattr(flat, "_place_close", lambda *a, **k: {"ok": True})
    r = flat.flatten("alpaca_live", "IEF", apply=True)
    assert r["ok"] is False and r["action"] == "close_placed_still_open"
