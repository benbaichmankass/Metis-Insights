"""Tests for scripts/ops/flatten_bybit_position.py — the one-shot guarded Bybit
flatten (the Bybit sibling of flatten_ib_position.py).

All broker I/O is monkeypatched: these verify the script's decision logic
(dry-run vs apply, already-flat noop, unreadable abort, non-Bybit refusal,
side derivation, post-flatten verify) without touching Bybit.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "flatten_bybit_position",
    Path(__file__).resolve().parents[1] / "scripts" / "ops" / "flatten_bybit_position.py",
)
flat = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(flat)  # type: ignore


_BYBIT_ACCT = {"account_id": "bybit_2", "exchange": "bybit",
               "market_type": "linear", "api_key_env": "BYBIT_API_KEY_2"}


@pytest.fixture(autouse=True)
def _patch_account(monkeypatch):
    monkeypatch.setattr(flat, "_load_account",
                        lambda aid: _BYBIT_ACCT if aid == "bybit_2" else None)


def _patch_live(monkeypatch, sequence):
    """sequence: list of return values for successive _live_position calls."""
    calls = {"i": 0}

    def _lp(cfg, symbol):
        i = min(calls["i"], len(sequence) - 1)
        calls["i"] += 1
        return sequence[i]

    monkeypatch.setattr(flat, "_live_position", _lp)


def test_unknown_account_aborts():
    r = flat.flatten("nope", "BTCUSDT", apply=False)
    assert r["ok"] is False and "not found" in r["detail"]


def test_non_bybit_account_refused(monkeypatch):
    monkeypatch.setattr(flat, "_load_account",
                        lambda aid: {"account_id": "ib_paper", "exchange": "interactive_brokers"})
    r = flat.flatten("ib_paper", "MGC", apply=True)
    assert r["ok"] is False and "not Bybit" in r["detail"]


def test_unreadable_aborts(monkeypatch):
    _patch_live(monkeypatch, [None])  # could-not-read
    r = flat.flatten("bybit_2", "BTCUSDT", apply=True)
    assert r["ok"] is False and r["action"] == "abort_unreadable"


def test_already_flat_noop(monkeypatch):
    _patch_live(monkeypatch, [{}])  # read OK, no position
    r = flat.flatten("bybit_2", "BTCUSDT", apply=True)
    assert r["ok"] is True and r["action"] == "noop_already_flat"


def test_dry_run_does_not_place(monkeypatch):
    _patch_live(monkeypatch, [{"symbol": "BTCUSDT", "side": "long", "size": 0.001,
                               "entry_price": 59822.7, "unrealised_pnl": 0.16}])

    def _boom(*a, **k):  # close must NOT be called in dry-run
        raise AssertionError("close_open_position called during dry-run")

    monkeypatch.setattr(flat, "_place_close", _boom, raising=False)
    r = flat.flatten("bybit_2", "BTCUSDT", apply=False)
    assert r["ok"] is True and r["action"] == "dry_run"
    # long → close is a SELL of the full size
    assert r["planned_close"]["action"] == "SELL" and r["planned_close"]["qty"] == 0.001


def test_apply_flattens_and_verifies(monkeypatch):
    # first read: short 0.01; post-flatten read: flat ({})
    _patch_live(monkeypatch, [
        {"symbol": "ETHUSDT", "side": "short", "size": 0.01},
        {},
    ])
    monkeypatch.setattr(flat, "_build_ops_client", lambda cfg: object())
    captured = {}

    def _close(client, cfg, *, symbol, side, qty):
        captured.update(symbol=symbol, side=side, qty=qty)
        return {"ok": True, "exchange_order_id": "abc"}

    monkeypatch.setattr(flat, "_place_close", _close)
    r = flat.flatten("bybit_2", "ETHUSDT", apply=True)
    assert r["ok"] is True and r["action"] == "flattened"
    # short → close side passed to close_open_position is the ENTRY side
    # ('short'); close_open_position derives the opposing Buy internally.
    assert captured == {"symbol": "ETHUSDT", "side": "short", "qty": 0.01}


def test_apply_reports_close_failure(monkeypatch):
    _patch_live(monkeypatch, [{"symbol": "BTCUSDT", "side": "long", "size": 0.001}])
    monkeypatch.setattr(flat, "_build_ops_client", lambda cfg: object())
    monkeypatch.setattr(flat, "_place_close",
                        lambda *a, **k: {"ok": False, "error": "10010 Unmatched IP"})
    r = flat.flatten("bybit_2", "BTCUSDT", apply=True)
    assert r["ok"] is False and r["action"] == "close_failed"


def test_apply_no_client_aborts(monkeypatch):
    _patch_live(monkeypatch, [{"symbol": "BTCUSDT", "side": "long", "size": 0.001}])
    monkeypatch.setattr(flat, "_build_ops_client", lambda cfg: None)  # missing creds
    r = flat.flatten("bybit_2", "BTCUSDT", apply=True)
    assert r["ok"] is False and r["action"] == "abort_no_client"


def test_apply_still_open_after_close(monkeypatch):
    _patch_live(monkeypatch, [
        {"symbol": "BTCUSDT", "side": "long", "size": 0.001},
        {"symbol": "BTCUSDT", "side": "long", "size": 0.001},  # still there
    ])
    monkeypatch.setattr(flat, "_build_ops_client", lambda cfg: object())
    monkeypatch.setattr(flat, "_place_close", lambda *a, **k: {"ok": True})
    r = flat.flatten("bybit_2", "BTCUSDT", apply=True)
    assert r["ok"] is False and r["action"] == "close_placed_still_open"
