"""S2 — wire OANDA close + account_open_positions before it leaves dry_run.

Ref: docs/audits/live-trade-management-contract-2026-06-16.md § Design plan §2
(OANDA: same, before it's promoted off dry_run); backlog BL-20260616-LTMGMT-OANDA.

OANDA's client already implements ``close()`` (v20 closeout) + ``positions()``;
the gap was the wiring — ``execute.close_open_position`` had no oanda branch,
``account_open_positions`` had no oanda branch, ``_build_account_client`` didn't
build an OANDA client, and the caps declared nothing. Done now so promoting
``oanda_practice`` off ``dry_run`` can't recreate the unmanaged-live-position
gap. No kill-switch (baseline correctness).

These tests pin:
  1. ``EXCHANGE_MANAGEMENT_CAPS`` declares ``close`` + ``open_positions`` for
     oanda (and NOT modify/partial_close/order_status).
  2. ``execute.close_open_position`` routes oanda → ``OandaClient.close(symbol)``
     (ok envelope; wrong client type / error refused; idempotent no-position ok).
  3. ``account_open_positions`` oanda branch normalises the position shape; a
     dry account / missing creds returns None (never a false empty list).
  4. ``_send_close_to_exchange`` reaches a mocked OANDA client.
"""
from __future__ import annotations

from unittest.mock import MagicMock


from src.runtime import order_monitor as om
from src.units.accounts import clients
from src.units.accounts.execute import close_open_position
from src.units.accounts.oanda_client import OandaClient


# ---------------------------------------------------------------------------
# 1. capability map + resolver
# ---------------------------------------------------------------------------


def test_caps_declare_close_and_open_positions_for_oanda():
    caps = clients.exchange_management_caps("oanda")
    assert caps == frozenset({"close", "open_positions"})
    # modify / partial_close / order_status are NOT wired for OANDA.
    assert "modify" not in caps
    assert "partial_close" not in caps
    assert "order_status" not in caps


def test_account_supports_management_oanda():
    acc = {"exchange": "oanda"}
    assert clients.account_supports_management(acc, "close") is True
    assert clients.account_supports_management(acc, "open_positions") is True
    assert clients.account_supports_management(acc, "modify") is False


# ---------------------------------------------------------------------------
# 2. execute.close_open_position routing
# ---------------------------------------------------------------------------


def test_close_open_position_routes_oanda():
    oanda_client = MagicMock(spec=OandaClient)
    oanda_client.close.return_value = {
        "retCode": 0, "result": {"orderId": "O-1"},
    }
    cfg = {"account_id": "oanda_practice", "exchange": "oanda"}

    res = close_open_position(oanda_client, cfg, symbol="XAU_USD", side="long", qty=3)

    oanda_client.close.assert_called_once_with("XAU_USD")
    assert res["ok"] is True
    assert res["exchange_order_id"] == "O-1"


def test_close_open_position_oanda_idempotent_no_position_ok():
    oanda_client = MagicMock(spec=OandaClient)
    oanda_client.close.return_value = {"retCode": 0, "result": {"note": "no open position"}}
    cfg = {"account_id": "oanda_practice", "exchange": "oanda"}
    res = close_open_position(oanda_client, cfg, symbol="EUR_USD", side="short", qty=1)
    assert res["ok"] is True


def test_close_open_position_oanda_error_not_ok():
    oanda_client = MagicMock(spec=OandaClient)
    oanda_client.close.return_value = {"retCode": 400, "retMsg": "CLOSEOUT_REJECT"}
    cfg = {"account_id": "oanda_practice", "exchange": "oanda"}
    res = close_open_position(oanda_client, cfg, symbol="XAU_USD", side="long", qty=1)
    assert res["ok"] is False
    assert "CLOSEOUT_REJECT" in res["error"]


def test_close_open_position_oanda_wrong_client_type_not_ok():
    cfg = {"account_id": "oanda_practice", "exchange": "oanda"}
    res = close_open_position(object(), cfg, symbol="XAU_USD", side="long", qty=1)
    assert res["ok"] is False
    assert "OandaClient" in res["error"]


# ---------------------------------------------------------------------------
# 3. account_open_positions oanda branch
# ---------------------------------------------------------------------------


def test_account_open_positions_oanda_normalises(monkeypatch):
    fake = MagicMock()
    fake.positions.return_value = [
        {"symbol": "XAU_USD", "side": "buy", "qty": 3.0,
         "avg_price": 4355.0, "unrealized_pnl": 12.5},
        {"symbol": "EUR_USD", "side": "sell", "qty": 0.0,  # flat → dropped
         "avg_price": 0.0, "unrealized_pnl": 0.0},
    ]
    monkeypatch.setattr(clients, "oanda_client_for", lambda acc: fake)

    out = clients.account_open_positions(
        {"account_id": "oanda_live", "exchange": "oanda", "mode": "live"}
    )
    assert out == [
        {"symbol": "XAU_USD", "side": "long", "size": 3.0,
         "entry_price": 4355.0, "unrealised_pnl": 12.5},
    ]


def test_account_open_positions_oanda_dry_returns_none(monkeypatch):
    """A dry oanda account is never dialled from the read path (no false []).
    This is what keeps the reverse reconciler from closing OANDA rows while
    dry, and gives it real coverage once promoted to live."""
    called = []
    monkeypatch.setattr(
        clients, "oanda_client_for",
        lambda acc: called.append(1) or MagicMock(),
    )
    out = clients.account_open_positions(
        {"account_id": "oanda_practice", "exchange": "oanda", "mode": "dry_run"}
    )
    assert out is None
    assert called == []


def test_account_open_positions_oanda_no_creds_returns_none(monkeypatch):
    monkeypatch.setattr(clients, "oanda_client_for", lambda acc: None)
    out = clients.account_open_positions(
        {"account_id": "oanda_live", "exchange": "oanda", "mode": "live"}
    )
    assert out is None


# ---------------------------------------------------------------------------
# 4. _send_close_to_exchange reaches a mocked OANDA client
# ---------------------------------------------------------------------------


def test_send_close_to_exchange_oanda_ok(monkeypatch):
    oanda_client = MagicMock(spec=OandaClient)
    cfg = {"account_id": "oanda_live", "exchange": "oanda", "mode": "live"}
    monkeypatch.setattr(om, "_build_account_client", lambda a: (oanda_client, cfg))
    monkeypatch.setattr(
        "src.units.accounts.execute.close_open_position",
        lambda client, c, *, symbol, side, qty, sl_order_id=None, tp_order_id=None: {
            "ok": True, "exchange_order_id": "O-9", "error": None,
        },
    )
    res = om._send_close_to_exchange(
        {"account_id": "oanda_live", "symbol": "XAU_USD", "direction": "long",
         "position_size": 3}
    )
    assert res["ok"] is True
    assert res["exchange_order_id"] == "O-9"


def test_send_close_to_exchange_oanda_dry_run_short_circuit(monkeypatch):
    oanda_client = MagicMock(spec=OandaClient)
    cfg = {"account_id": "oanda_practice", "exchange": "oanda", "mode": "dry_run"}
    monkeypatch.setattr(om, "_build_account_client", lambda a: (oanda_client, cfg))
    called = []
    monkeypatch.setattr(
        "src.units.accounts.execute.close_open_position",
        lambda *a, **kw: called.append(1) or {"ok": True},
    )
    res = om._send_close_to_exchange(
        {"account_id": "oanda_practice", "symbol": "XAU_USD", "direction": "long",
         "position_size": 3}
    )
    assert res["ok"] is True
    assert res["skipped"] == "dry_run"
    assert called == []
