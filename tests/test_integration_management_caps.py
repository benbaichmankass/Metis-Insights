"""P2 — per-integration management-capability layer (behavior-preserving).

Ref: docs/audits/live-trade-management-contract-2026-06-16.md § Design plan §2.

These tests pin:
  1. The capability declaration matches CURRENT WIRED REALITY (verified against
     the code: account_open_positions covers bybit+IB; modify/close/
     order_status are bybit-only).
  2. The three order-monitor senders return ``unsupported_op:<op>`` (NOT the
     misleading ``no_client``) for an unsupported integration (IB / Alpaca),
     and return the SAME result as today for Bybit (the mocked
     modify/close path proves the Bybit branch is byte-for-byte unchanged).
  3. The ``_apply_update`` / ``_apply_partial_close`` log throttle dedupes an
     ``unsupported_op`` failure to one WARNING per (order_package_id, op), while
     a genuine failure on a supported integration still logs ERROR every tick.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from src.runtime import order_monitor as om
from src.units.accounts import clients


# ---------------------------------------------------------------------------
# 1. Capability declaration matches current wired reality
# ---------------------------------------------------------------------------


def test_bybit_supports_all_management_ops():
    caps = clients.exchange_management_caps("bybit")
    assert caps == frozenset(
        {"modify", "close", "partial_close", "order_status", "open_positions"}
    )


def test_interactive_brokers_modify_close_and_open_positions():
    # P3 (close-first) wired IBClient.close; S2 (BL-20260616-LTMGMT-MODIFY)
    # wired IBClient.modify_protective (cancel resting OCA legs + re-arm a fresh
    # GTC OCA pair via place_protective) through execute.modify_open_order — so
    # ``modify`` joins ``close`` + ``open_positions``. ``partial_close`` /
    # ``order_status`` remain unwired.
    assert clients.exchange_management_caps("interactive_brokers") == frozenset(
        {"modify", "close", "open_positions"}
    )
    # 'ib' alias (account_open_positions accepts both) resolves identically.
    assert clients.exchange_management_caps("ib") == frozenset(
        {"modify", "close", "open_positions"}
    )
    # partial_close/order_status still unsupported for IB.
    assert "partial_close" not in clients.exchange_management_caps("interactive_brokers")
    assert "order_status" not in clients.exchange_management_caps("ib")


def test_alpaca_modify_close_and_open_positions_oanda_still_nothing():
    # P3 wired AlpacaClient.close + an alpaca account_open_positions branch; S2
    # wired AlpacaClient.modify_protective (PATCH the resting bracket legs) — so
    # alpaca supports ``modify`` + ``close`` + ``open_positions``.
    # ``partial_close`` remains unwired. OANDA is a later item — still nothing.
    assert clients.exchange_management_caps("alpaca") == frozenset(
        {"modify", "close", "open_positions"}
    )
    assert "partial_close" not in clients.exchange_management_caps("alpaca")
    # OANDA close + open_positions were wired in S2 (BL-20260616-LTMGMT-OANDA),
    # before oanda_practice leaves dry_run; modify/partial_close still unwired.
    assert clients.exchange_management_caps("oanda") == frozenset(
        {"close", "open_positions"}
    )
    assert "modify" not in clients.exchange_management_caps("oanda")


def test_unknown_exchange_safe_default_empty():
    assert clients.exchange_management_caps("breakout") == frozenset()
    assert clients.exchange_management_caps(None) == frozenset()
    assert clients.exchange_management_caps("") == frozenset()
    assert clients.exchange_management_caps("   ") == frozenset()


def test_exchange_caps_case_insensitive():
    assert clients.exchange_management_caps("BYBIT") == clients.exchange_management_caps(
        "bybit"
    )


def test_account_supports_management_resolver():
    bybit_acc = {"exchange": "bybit"}
    ib_acc = {"exchange": "interactive_brokers"}
    alpaca_acc = {"exchange": "alpaca"}

    assert clients.account_supports_management(bybit_acc, "modify") is True
    assert clients.account_supports_management(bybit_acc, "close") is True
    assert clients.account_supports_management(bybit_acc, "partial_close") is True

    # IB: P3 wired close + open_positions; S2 wired modify.
    assert clients.account_supports_management(ib_acc, "open_positions") is True
    assert clients.account_supports_management(ib_acc, "close") is True
    assert clients.account_supports_management(ib_acc, "modify") is True
    # partial_close still unsupported for IB.
    assert clients.account_supports_management(ib_acc, "partial_close") is False

    # Alpaca: P3 wired close + open_positions; S2 wired modify.
    assert clients.account_supports_management(alpaca_acc, "close") is True
    assert clients.account_supports_management(alpaca_acc, "modify") is True


def test_account_supports_management_never_raises_on_bad_input():
    assert clients.account_supports_management(None, "modify") is False
    assert clients.account_supports_management({}, "modify") is False
    assert clients.account_supports_management({"exchange": "bybit"}, "") is False
    assert clients.account_supports_management("not-a-dict", "close") is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 2. Senders consult the declaration
# ---------------------------------------------------------------------------


def _patch_build_client(monkeypatch, exchange, *, client):
    """Make _build_account_client return (client, cfg) for the given exchange.

    Mirrors the real contract: bybit builds a client; everything else
    returns (None, cfg) from the live ``_build_account_client``.
    """
    cfg = {"account_id": "acct", "exchange": exchange, "mode": "live",
           "market_type": "linear"}
    monkeypatch.setattr(
        om, "_build_account_client", lambda account_id: (client, cfg)
    )
    return cfg


# S2 (BL-20260616-LTMGMT-MODIFY) wired modify for IB + alpaca, so only OANDA
# still returns unsupported_op:modify. IB/alpaca modify routing is covered in
# tests/test_ltmgmt_modify_wiring.py.
@pytest.mark.parametrize("exchange", ["oanda"])
def test_modify_returns_unsupported_op_for_unwired_integration(monkeypatch, exchange):
    _patch_build_client(monkeypatch, exchange, client=None)
    res = om._send_modify_to_exchange({"account_id": "acct", "symbol": "MGC"},
                                      sl=1990.0)
    assert res["ok"] is False
    assert res["error"] == "unsupported_op:modify"
    assert res["integration"] == exchange


@pytest.mark.parametrize("exchange", ["interactive_brokers", "alpaca", "oanda"])
def test_partial_close_returns_unsupported_op_for_unwired_integration(
    monkeypatch, exchange,
):
    _patch_build_client(monkeypatch, exchange, client=None)
    res = om._send_partial_close_to_exchange(
        {"account_id": "acct", "symbol": "MGC", "direction": "long",
         "position_size": 4}, 1.0,
    )
    assert res["ok"] is False
    assert res["error"] == "unsupported_op:partial_close"
    assert res["integration"] == exchange


def test_bybit_modify_path_unchanged(monkeypatch):
    """Bybit supports modify → the capability gate is a no-op and the existing
    code path (build client → not dry_run → call modify_open_order) runs
    exactly as before. We mock modify_open_order and assert the sender returns
    its result verbatim with the same call args."""
    client = MagicMock()
    _patch_build_client(monkeypatch, "bybit", client=client)

    sentinel = {"ok": True, "exchange_response": {"retCode": 0}, "error": None}
    captured = {}

    def _modify(c, cfg, *, symbol, sl=None, tp=None, **kwargs):
        captured["client"] = c
        captured["symbol"] = symbol
        captured["sl"] = sl
        captured["tp"] = tp
        return sentinel

    monkeypatch.setattr("src.units.accounts.execute.modify_open_order", _modify)

    res = om._send_modify_to_exchange(
        {"account_id": "acct", "symbol": "BTCUSDT"}, sl=49500.0, tp=51000.0,
    )
    # Returned verbatim — no wrapping, no unsupported_op.
    assert res is sentinel
    assert captured["client"] is client
    assert captured["symbol"] == "BTCUSDT"
    assert captured["sl"] == 49500.0
    assert captured["tp"] == 51000.0


def test_bybit_close_path_unchanged(monkeypatch):
    """Bybit supports close → existing path runs; sender returns
    close_open_position's result verbatim (byte-for-byte unchanged)."""
    client = MagicMock()
    _patch_build_client(monkeypatch, "bybit", client=client)

    sentinel = {"ok": True, "exchange_order_id": "X-1", "error": None}
    captured = {}

    def _close(c, cfg, *, symbol, side, qty, sl_order_id=None, tp_order_id=None):
        captured.update({"client": c, "symbol": symbol, "side": side, "qty": qty})
        return sentinel

    monkeypatch.setattr("src.units.accounts.execute.close_open_position", _close)

    res = om._send_close_to_exchange(
        {"account_id": "acct", "symbol": "BTCUSDT", "direction": "long",
         "position_size": 0.001},
    )
    assert res is sentinel
    assert captured["client"] is client
    assert captured["symbol"] == "BTCUSDT"
    assert captured["side"] == "long"
    assert captured["qty"] == 0.001


def test_bybit_dry_run_short_circuit_unchanged(monkeypatch):
    """The dry_run short-circuit still fires for a supported integration
    (Bybit) AFTER the capability gate — modify_open_order is never called."""
    client = MagicMock()
    cfg = {"account_id": "acct", "exchange": "bybit", "mode": "dry_run",
           "market_type": "linear"}
    monkeypatch.setattr(om, "_build_account_client", lambda a: (client, cfg))

    called = []
    monkeypatch.setattr(
        "src.units.accounts.execute.modify_open_order",
        lambda *a, **kw: called.append(1) or {"ok": True},
    )
    res = om._send_modify_to_exchange({"account_id": "acct", "symbol": "BTCUSDT"},
                                      sl=49500.0)
    assert res["ok"] is True
    assert res["skipped"] == "dry_run"
    assert called == []  # modify_open_order never reached on dry_run


# ---------------------------------------------------------------------------
# 3. Throttle: one WARNING per (pkg, op); supported failures still log ERROR
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_throttle_state():
    om._UNSUPPORTED_OP_LOGGED.clear()
    yield
    om._UNSUPPORTED_OP_LOGGED.clear()


def test_is_unsupported_op_error_sentinel():
    assert om._is_unsupported_op_error("unsupported_op:modify") is True
    assert om._is_unsupported_op_error("unsupported_op:close") is True
    assert om._is_unsupported_op_error("no_client") is False
    assert om._is_unsupported_op_error("Bybit retCode=10001") is False
    assert om._is_unsupported_op_error(None) is False


def test_throttle_logs_once_per_pkg_op(caplog):
    with caplog.at_level(logging.WARNING, logger="src.runtime.order_monitor"):
        for _ in range(5):
            om._note_unsupported_management_op(
                pkg_id="pkg-1", op="modify", account_id="ib_paper",
                integration="interactive_brokers",
                err_str="unsupported_op:modify",
            )
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "modify" in warnings[0].getMessage()


def test_throttle_dedupe_is_per_op_and_per_pkg(caplog):
    with caplog.at_level(logging.WARNING, logger="src.runtime.order_monitor"):
        om._note_unsupported_management_op(
            pkg_id="pkg-1", op="modify", account_id="a",
            integration="interactive_brokers", err_str="unsupported_op:modify",
        )
        om._note_unsupported_management_op(
            pkg_id="pkg-1", op="close", account_id="a",
            integration="interactive_brokers", err_str="unsupported_op:close",
        )
        om._note_unsupported_management_op(
            pkg_id="pkg-2", op="modify", account_id="a",
            integration="interactive_brokers", err_str="unsupported_op:modify",
        )
        # Repeat of the first — suppressed.
        om._note_unsupported_management_op(
            pkg_id="pkg-1", op="modify", account_id="a",
            integration="interactive_brokers", err_str="unsupported_op:modify",
        )
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    # (pkg-1, modify), (pkg-1, close), (pkg-2, modify) → 3 distinct keys.
    assert len(warnings) == 3


def test_apply_update_modify_unsupported_logs_warning_once_no_error(monkeypatch, caplog):
    """A modify verdict on an unsupported integration logs ONE WARNING across
    repeated ticks and never the per-tick ERROR — but still counts an error in
    the per-tick summary (behavior-preserving)."""
    monkeypatch.setattr(
        om, "_send_modify_to_exchange",
        lambda matched, *, sl=None, tp=None, **kwargs: {
            "ok": False, "error": "unsupported_op:modify",
            "integration": "oanda",
        },
    )
    matched_trade = {"account_id": "oanda_practice", "symbol": "EUR_USD", "id": 1}
    monkeypatch.setattr(
        om, "_find_trade_by_match", lambda *a, **kw: matched_trade
    )

    db = MagicMock()
    db.get_trades.return_value = [matched_trade]
    open_pkg = {"order_package_id": "pkg-mgc", "strategy_name": "trend",
                "symbol": "MGC", "linked_trade_id": 1}
    verdict = {"sl": 1990.0}

    with caplog.at_level(logging.WARNING, logger="src.runtime.order_monitor"):
        for _ in range(4):
            summary = om._StrategyTickSummary()
            om._apply_update(db, dict(open_pkg), verdict, summary)
            # DB row never written (left unchanged) — same as a genuine failure.
            assert summary.error_count == 1
            assert summary.updated_count == 0

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(warnings) == 1, [w.getMessage() for w in warnings]
    assert errors == []
    db.update_order_package.assert_not_called()


def test_apply_update_modify_supported_failure_logs_error_every_tick(monkeypatch, caplog):
    """A GENUINE failure on a SUPPORTED integration (Bybit retCode) still logs
    ERROR every tick — the throttle must not swallow real failures."""
    monkeypatch.setattr(
        om, "_send_modify_to_exchange",
        lambda matched, *, sl=None, tp=None, **kwargs: {
            "ok": False, "error": "Bybit retCode=10001 SL race",
        },
    )
    matched_trade = {"account_id": "bybit_2", "symbol": "BTCUSDT", "id": 1}
    db = MagicMock()
    db.get_trades.return_value = [matched_trade]
    open_pkg = {"order_package_id": "pkg-btc", "strategy_name": "vwap",
                "symbol": "BTCUSDT", "linked_trade_id": 1}
    verdict = {"sl": 100.0}

    with caplog.at_level(logging.ERROR, logger="src.runtime.order_monitor"):
        for _ in range(3):
            summary = om._StrategyTickSummary()
            om._apply_update(db, dict(open_pkg), verdict, summary)

    errors = [
        r for r in caplog.records
        if r.levelno >= logging.ERROR
        and "exchange modify failed" in r.getMessage()
    ]
    assert len(errors) == 3  # every tick, unchanged
