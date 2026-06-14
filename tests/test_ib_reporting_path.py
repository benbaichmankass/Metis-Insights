"""Tests for the Interactive Brokers *reporting / observability* path.

The execution path for IB (place/balance for sizing) was wired in the
2026-05-21 MES go-live, but the read path that feeds the hourly Telegram
digest and the Streamlit dashboard was never taught about IB:

  * ``_load_yaml_accounts`` dropped the ``ib_*`` connection fields + mode,
    so the read path couldn't even build an IB client.
  * ``account_balance_with_diagnostic`` / ``account_open_positions`` had
    no ``interactive_brokers`` branch, so IB fell through to
    "unsupported" → the digest showed "API ERROR" and no balance snapshot
    was ever written → the dashboard rendered blank balances.

These tests pin the read path: field preservation, the balance/positions
branches, the dry-run gate (the live gateway is never dialled from the
read path), and ``IBClient.positions()``.
"""
from __future__ import annotations

from src.units.accounts import clients
from src.units.accounts.ib_client import IBClient, IBConnectionError
from src.units.ui.data_loaders import (
    account_balance_with_diagnostic,
    list_accounts,
)


class _StubIBClient:
    """Minimal stand-in for the IBClient surface the read path uses."""

    def __init__(self, *, balance=None, positions=None, raises=None):
        self._balance = balance if balance is not None else {
            "net_liquidation": 52345.67,
            "available_funds": 40000.0,
            "currency": "USD",
            "account": "DUQ325724",
        }
        self._positions = positions if positions is not None else []
        self._raises = raises

    def balance(self):
        if self._raises:
            raise self._raises
        return self._balance

    def positions(self):
        if self._raises:
            raise self._raises
        return self._positions


def _ib_account(mode="live", aid="ib_paper", port=4002):
    return {
        "account_id": aid,
        "exchange": "interactive_brokers",
        "mode": mode,
        "ib_host": "127.0.0.1",
        "ib_port": port,
        "ib_account": "DUQ325724",
        "ib_client_id": 497,
    }


# ---------------------------------------------------------------------------
# account_balance_with_diagnostic — IB branch
# ---------------------------------------------------------------------------


class TestIBBalanceDiagnostic:
    def test_live_ok_reports_net_liquidation(self, monkeypatch):
        stub = _StubIBClient(balance={"net_liquidation": 12345.0,
                                      "available_funds": 9000.0})
        monkeypatch.setattr(clients, "ib_read_client_for", lambda acc: stub)
        diag = account_balance_with_diagnostic(_ib_account())
        assert diag["status"] == "ok"
        assert diag["total_usdt"] == 12345.0
        assert diag["error"] is None

    def test_live_falls_back_to_available_funds(self, monkeypatch):
        stub = _StubIBClient(balance={"net_liquidation": 0.0,
                                      "available_funds": 777.0})
        monkeypatch.setattr(clients, "ib_read_client_for", lambda acc: stub)
        diag = account_balance_with_diagnostic(_ib_account())
        assert diag["status"] == "ok"
        assert diag["total_usdt"] == 777.0

    def test_gateway_down_surfaces_precise_api_error(self, monkeypatch):
        err = IBConnectionError(
            "IBClient: failed to connect to IB Gateway at 127.0.0.1:4002"
        )
        stub = _StubIBClient(raises=err)
        monkeypatch.setattr(clients, "ib_read_client_for", lambda acc: stub)
        diag = account_balance_with_diagnostic(_ib_account())
        assert diag["status"] == "api_error"
        assert "IB Gateway" in diag["error"]
        assert diag["total_usdt"] is None

    def test_dry_run_account_never_dials_live_gateway(self, monkeypatch):
        def _boom(acc):  # pragma: no cover - must not be called
            raise AssertionError("dry IB account must not open a socket")

        monkeypatch.setattr(clients, "ib_read_client_for", _boom)
        diag = account_balance_with_diagnostic(
            _ib_account(mode="dry_run", aid="ib_live", port=7496)
        )
        assert diag["status"] == "dry_run"
        assert diag["total_usdt"] is None

    def test_client_none_when_port_unset(self, monkeypatch):
        monkeypatch.setattr(clients, "ib_read_client_for", lambda acc: None)
        diag = account_balance_with_diagnostic(_ib_account())
        assert diag["status"] == "api_error"
        assert "ib_port" in diag["error"]


# ---------------------------------------------------------------------------
# account_open_positions — IB branch
# ---------------------------------------------------------------------------


class TestIBOpenPositions:
    def test_live_returns_normalized_positions(self, monkeypatch):
        rows = [{"symbol": "MESM6", "side": "long", "size": 1.0,
                 "entry_price": 5300.0, "unrealised_pnl": 12.5}]
        stub = _StubIBClient(positions=rows)
        monkeypatch.setattr(clients, "ib_read_client_for", lambda acc: stub)
        assert clients.account_open_positions(_ib_account()) == rows

    def test_live_empty_is_empty_list_not_none(self, monkeypatch):
        stub = _StubIBClient(positions=[])
        monkeypatch.setattr(clients, "ib_read_client_for", lambda acc: stub)
        assert clients.account_open_positions(_ib_account()) == []

    def test_gateway_down_returns_none(self, monkeypatch):
        stub = _StubIBClient(raises=IBConnectionError("gateway down"))
        monkeypatch.setattr(clients, "ib_read_client_for", lambda acc: stub)
        assert clients.account_open_positions(_ib_account()) is None

    def test_dry_run_account_returns_none_without_dialling(self, monkeypatch):
        def _boom(acc):  # pragma: no cover - must not be called
            raise AssertionError("dry IB account must not open a socket")

        monkeypatch.setattr(clients, "ib_read_client_for", _boom)
        acc = _ib_account(mode="dry_run", aid="ib_live", port=7496)
        assert clients.account_open_positions(acc) is None


# ---------------------------------------------------------------------------
# _load_yaml_accounts field preservation (end-to-end against the real YAML)
# ---------------------------------------------------------------------------


class TestAccountFieldPreservation:
    def test_ib_connection_fields_survive_list_accounts(self):
        accs = {a["account_id"]: a for a in list_accounts()}
        paper = accs["ib_paper"]
        assert paper["exchange"] == "interactive_brokers"
        assert paper["mode"] == "live"
        assert paper["ib_port"] == 4002
        assert paper["ib_account"] == "DUQ325724"
        assert paper["ib_client_id"] == 497

        live = accs["ib_live"]
        assert live["mode"] == "dry_run"
        assert live["ib_port"] == 7496
        assert live["ib_account"] == "U25907316"


# ---------------------------------------------------------------------------
# IBClient.positions() — portfolio normalization
# ---------------------------------------------------------------------------


class _PortItem:
    def __init__(self, position, account, root, local, avg, upnl, multiplier):
        self.position = position
        self.account = account
        # Real IB shape: ``symbol`` is the generic root (``MES``/``MHG``),
        # ``localSymbol`` carries the expiry month code (``MESM6``/``MHGN6``),
        # and ``averageCost`` is the per-unit price × the contract multiplier.
        self.contract = type(
            "C", (), {"localSymbol": local, "symbol": root, "multiplier": multiplier},
        )()
        self.averageCost = avg
        self.unrealizedPNL = upnl


class _FakePortfolioIB:
    def __init__(self):
        self._connected = False

    def connect(self, *a, **k):
        self._connected = True

    def isConnected(self):
        return self._connected

    def disconnect(self):
        self._connected = False

    def portfolio(self):
        return [
            # MES: avgCost 26500 = 5300 × 5 multiplier → entry_price 5300.
            _PortItem(1.0, "DUQ325724", "MES", "MESM6", 26500.0, 12.5, "5"),
            _PortItem(0.0, "DUQ325724", "MES", "MESM6", 0.0, 0.0, "5"),   # flat → skipped
            _PortItem(2.0, "OTHERACCT", "ES", "ESZ5", 100.0, 5.0, "50"),  # other acct → skipped
        ]


class _FakeMHGPortfolioIB(_FakePortfolioIB):
    def portfolio(self):
        # The ib_paper corruption case (BL-20260613-IBPOS): a Micro Copper
        # position whose avgCost is 6.396 × 2500 = 15989.72.
        return [
            _PortItem(3.0, "DUQ325724", "MHG", "MHGN6", 15989.72, 4.0, "2500"),
        ]


class TestIBClientPositions:
    def test_portfolio_normalized_and_filtered(self):
        c = IBClient(
            host="127.0.0.1", port=4002, client_id=9001,
            account="DUQ325724", _ib_factory=lambda: _FakePortfolioIB(),
        )
        out = c.positions()
        # Emits the generic root symbol (not the localSymbol month code) and a
        # per-unit entry price (avgCost ÷ multiplier).
        assert out == [{
            "symbol": "MES", "side": "long", "size": 1.0,
            "entry_price": 5300.0, "unrealised_pnl": 12.5,
        }]

    def test_futures_entry_price_divides_by_multiplier(self):
        c = IBClient(
            host="127.0.0.1", port=4002, client_id=9002,
            account="DUQ325724", _ib_factory=lambda: _FakeMHGPortfolioIB(),
        )
        out = c.positions()
        assert len(out) == 1
        row = out[0]
        assert row["symbol"] == "MHG"          # root, not MHGN6
        assert row["size"] == 3.0
        assert abs(row["entry_price"] - 6.39588) < 1e-4   # 15989.72 / 2500
        assert row["entry_price"] < 10.0       # per-unit copper, not 15989.72
