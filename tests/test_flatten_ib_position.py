"""Tests for scripts/ops/flatten_ib_position.py — the one-shot guarded IB flatten
(BL-20260618-RECONCILE-DUP residual cleanup).

All broker I/O is monkeypatched: these verify the script's decision logic
(dry-run vs apply, already-flat noop, unreadable abort, side derivation,
post-flatten verify) without touching IB.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "flatten_ib_position",
    Path(__file__).resolve().parents[1] / "scripts" / "ops" / "flatten_ib_position.py",
)
flat = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(flat)  # type: ignore


_IB_ACCT = {"account_id": "ib_paper", "exchange": "interactive_brokers",
            "ib_host": "10.0.0.251", "ib_port": 4002, "ib_client_id": 497}


@pytest.fixture(autouse=True)
def _patch_account(monkeypatch):
    monkeypatch.setattr(flat, "_load_account", lambda aid: _IB_ACCT if aid == "ib_paper" else None)


def _patch_live(monkeypatch, sequence):
    """sequence: list of return values for successive _live_position calls."""
    calls = {"i": 0}

    def _lp(cfg, symbol):
        i = min(calls["i"], len(sequence) - 1)
        calls["i"] += 1
        return sequence[i]

    monkeypatch.setattr(flat, "_live_position", _lp)


def test_unknown_account_aborts():
    r = flat.flatten("nope", "MGC", apply=False)
    assert r["ok"] is False and "not found" in r["detail"]


def test_non_ib_account_refused(monkeypatch):
    monkeypatch.setattr(flat, "_load_account", lambda aid: {"account_id": "bybit_2", "exchange": "bybit"})
    r = flat.flatten("bybit_2", "BTCUSDT", apply=True)
    assert r["ok"] is False and "not IB" in r["detail"]


def test_unreadable_aborts(monkeypatch):
    _patch_live(monkeypatch, [None])  # could-not-read
    r = flat.flatten("ib_paper", "MGC", apply=True)
    assert r["ok"] is False and r["action"] == "abort_unreadable"


def test_already_flat_noop(monkeypatch):
    _patch_live(monkeypatch, [{}])  # read OK, no position
    r = flat.flatten("ib_paper", "MGC", apply=True)
    assert r["ok"] is True and r["action"] == "noop_already_flat"


def test_dry_run_does_not_place(monkeypatch):
    _patch_live(monkeypatch, [{"symbol": "MGC", "side": "short", "size": 232.0,
                               "entry_price": 4303.8, "unrealised_pnl": 309338.96}])

    def _boom(*a, **k):  # close must NOT be called in dry-run
        raise AssertionError("close_open_position called during dry-run")

    monkeypatch.setattr(flat, "_place_close", _boom, raising=False)
    r = flat.flatten("ib_paper", "MGC", apply=False)
    assert r["ok"] is True and r["action"] == "dry_run"
    # short → close is a BUY of the full size
    assert r["planned_close"]["action"] == "BUY" and r["planned_close"]["qty"] == 232.0


def test_apply_flattens_and_verifies(monkeypatch):
    # first read: short 232; post-flatten read: flat ({})
    _patch_live(monkeypatch, [
        {"symbol": "MGC", "side": "short", "size": 232.0},
        {},
    ])
    monkeypatch.setattr(flat, "_build_ops_client", lambda cfg: object())
    captured = {}

    def _close(client, cfg, *, symbol, side, qty):
        captured.update(symbol=symbol, side=side, qty=qty)
        return {"ok": True, "exchange_order_id": "abc"}

    monkeypatch.setattr(flat, "_place_close", _close)
    r = flat.flatten("ib_paper", "MGC", apply=True)
    assert r["ok"] is True and r["action"] == "flattened"
    assert captured == {"symbol": "MGC", "side": "short", "qty": 232.0}


def test_apply_reports_close_failure(monkeypatch):
    _patch_live(monkeypatch, [{"symbol": "MGC", "side": "short", "size": 232.0}])
    monkeypatch.setattr(flat, "_build_ops_client", lambda cfg: object())
    monkeypatch.setattr(flat, "_place_close",
                        lambda *a, **k: {"ok": False, "error": "IB connect failed"})
    r = flat.flatten("ib_paper", "MGC", apply=True)
    assert r["ok"] is False and r["action"] == "close_failed"


def test_apply_still_open_after_close(monkeypatch):
    _patch_live(monkeypatch, [
        {"symbol": "MGC", "side": "short", "size": 232.0},
        {"symbol": "MGC", "side": "short", "size": 232.0},  # still there
    ])
    monkeypatch.setattr(flat, "_build_ops_client", lambda cfg: object())
    monkeypatch.setattr(flat, "_place_close", lambda *a, **k: {"ok": True})
    r = flat.flatten("ib_paper", "MGC", apply=True)
    assert r["ok"] is False and r["action"] == "close_placed_still_open"


def test_ops_client_id_range():
    # Must avoid the trader execution ids (496/497) and the read range (9000-9899).
    cid = flat._ib_ops_client_id()
    assert 9900 <= cid <= 9989
