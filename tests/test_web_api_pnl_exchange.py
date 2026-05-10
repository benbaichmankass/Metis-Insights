"""S-067 follow-up #6 — GET /api/bot/pnl/exchange tests."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.runtime import exchange_fills_store as store
from src.web.api import main as api_main


@pytest.fixture
def client():
    return TestClient(api_main.app, raise_server_exceptions=False)


@pytest.fixture
def fills_db(tmp_path: Path, monkeypatch) -> Path:
    db = tmp_path / "exchange_fills.sqlite"
    monkeypatch.setenv("EXCHANGE_FILLS_DB", str(db))
    # The router calls aggregate_* which call get_fills_db_path() —
    # the env var override is enough to redirect.
    return db


def _row(**overrides):
    base = {
        "exec_id": "e-1",
        "account_id": "bybit_2",
        "symbol": "BTC/USDT:USDT",
        "side": "buy",
        "price": 60000.0,
        "qty": 0.001,
        "fee": 0.012,
        "fee_currency": "USDT",
        "exec_time": "2026-05-09T10:00:00+00:00",
    }
    base.update(overrides)
    return base


def test_returns_zero_aggregates_when_db_missing(client, fills_db):
    """No fills DB yet (puller has never run) → 200 with zero values."""
    assert not fills_db.exists()
    resp = client.get("/api/bot/pnl/exchange?days=7")
    assert resp.status_code == 200
    body = resp.json()
    # Phase-1 keys + Phase-2 total_*_pnl additions, all zero.
    summary = body["summary"]
    assert summary["fill_count"] == 0
    assert summary["total_fees"] == 0.0
    assert summary["symbol_count"] == 0
    assert summary["window_days"] == 7
    assert summary["total_realized_pnl"] == 0.0
    assert summary["total_unrealized_pnl"] == 0.0
    assert body["by_symbol"] == []


def test_returns_per_symbol_aggregates(client, fills_db, monkeypatch):
    store.upsert_fills(
        [
            _row(exec_id="a", symbol="BTC/USDT:USDT",
                 exec_time="2026-05-09T12:00:00+00:00",
                 qty=0.001, price=60000.0, fee=0.10),
            _row(exec_id="b", symbol="BTC/USDT:USDT",
                 exec_time="2026-05-09T13:00:00+00:00",
                 qty=0.002, price=60500.0, fee=0.20),
            _row(exec_id="c", symbol="ETH/USDT:USDT",
                 exec_time="2026-05-09T14:00:00+00:00",
                 qty=0.5, price=3000.0, fee=0.30),
        ],
        path=fills_db,
    )
    # Pin "now" so the day-window filter is deterministic.
    fixed_now = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(store, "datetime", _frozen_datetime(fixed_now))

    resp = client.get("/api/bot/pnl/exchange?days=7")
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["fill_count"] == 3
    assert body["summary"]["symbol_count"] == 2
    assert abs(body["summary"]["total_fees"] - 0.60) < 1e-9
    sym_map = {r["symbol"]: r for r in body["by_symbol"]}
    assert sym_map["BTC/USDT:USDT"]["fill_count"] == 2
    assert sym_map["ETH/USDT:USDT"]["fill_count"] == 1


def test_days_param_clamps_at_max(client, fills_db):
    resp = client.get("/api/bot/pnl/exchange?days=91")
    assert resp.status_code == 422  # Query(le=90)


def test_days_param_rejects_zero(client, fills_db):
    resp = client.get("/api/bot/pnl/exchange?days=0")
    assert resp.status_code == 422  # Query(ge=1)


def test_default_days_is_seven(client, fills_db):
    resp = client.get("/api/bot/pnl/exchange")
    assert resp.status_code == 200
    assert resp.json()["summary"]["window_days"] == 7


def _frozen_datetime(fixed: datetime):
    """Return a datetime-like class whose ``now`` returns ``fixed``."""
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else fixed.astimezone(tz)
    return _Frozen


# ---------------------------------------------------------------------------
# S-067 follow-up C — FIFO lot-matching P&L (Phase-2)
# ---------------------------------------------------------------------------


def test_fifo_match_simple_round_trip():
    """buy 1 @ 100, sell 1 @ 110 → realized 10, unrealized 0."""
    realized, unrealized, open_qty, last_price = store._fifo_match([
        ("buy", 100.0, 1.0, 0.0),
        ("sell", 110.0, 1.0, 0.0),
    ])
    assert abs(realized - 10.0) < 1e-9
    assert abs(unrealized) < 1e-9
    assert abs(open_qty) < 1e-9
    assert last_price == 110.0


def test_fifo_match_partial_close_leaves_open_lot():
    """buy 1 @ 100, sell 0.5 @ 110 → realized 5, unrealized = (110-100)*0.5 = 5."""
    realized, unrealized, open_qty, last_price = store._fifo_match([
        ("buy", 100.0, 1.0, 0.0),
        ("sell", 110.0, 0.5, 0.0),
    ])
    assert abs(realized - 5.0) < 1e-9
    assert abs(unrealized - 5.0) < 1e-9
    assert abs(open_qty - 0.5) < 1e-9


def test_fifo_match_multiple_lots():
    """buy 1 @ 100, buy 1 @ 110, sell 1.5 @ 120.

    FIFO closes the @100 lot first (realised 20), then half the @110 lot
    (realised 5). Remaining 0.5 @ 110 is unrealised at last_price=120
    → 5.
    """
    realized, unrealized, open_qty, last_price = store._fifo_match([
        ("buy", 100.0, 1.0, 0.0),
        ("buy", 110.0, 1.0, 0.0),
        ("sell", 120.0, 1.5, 0.0),
    ])
    assert abs(realized - 25.0) < 1e-9
    assert abs(unrealized - 5.0) < 1e-9
    assert abs(open_qty - 0.5) < 1e-9


def test_fifo_match_short_then_cover():
    """sell 1 @ 100, buy 1 @ 90 → realised (100-90)*1 = 10."""
    realized, unrealized, open_qty, last_price = store._fifo_match([
        ("sell", 100.0, 1.0, 0.0),
        ("buy", 90.0, 1.0, 0.0),
    ])
    assert abs(realized - 10.0) < 1e-9
    assert abs(unrealized) < 1e-9
    assert abs(open_qty) < 1e-9


def test_fifo_match_fees_subtract_from_realized():
    """buy 1 @ 100 fee=0.1, sell 1 @ 110 fee=0.1 → realised 10 - 0.2 = 9.8."""
    realized, _, _, _ = store._fifo_match([
        ("buy", 100.0, 1.0, 0.1),
        ("sell", 110.0, 1.0, 0.1),
    ])
    assert abs(realized - 9.8) < 1e-9


def test_fifo_match_open_short_unrealized():
    """sell 1 @ 100 with no buy → unrealised = (100 - 100) * (-1) = 0
    initially; after a price observation via a later sell at a higher
    price (mark proxy moves to the new last_price), unrealised becomes
    negative for the existing short lot."""
    realized, unrealized, open_qty, last_price = store._fifo_match([
        ("sell", 100.0, 1.0, 0.0),
        ("sell", 105.0, 1.0, 0.0),  # adds to the short, mark moves to 105
    ])
    assert abs(realized) < 1e-9
    # Two short lots: -1 @ 100 and -1 @ 105. Mark = 105.
    # unrealized = (105 - 100) * (-1) + (105 - 105) * (-1) = -5
    assert abs(unrealized + 5.0) < 1e-9
    assert abs(open_qty + 2.0) < 1e-9
    assert last_price == 105.0


def test_endpoint_includes_fifo_fields_when_no_data(client, fills_db):
    """No fills DB → FIFO fields are zero, not absent."""
    resp = client.get("/api/bot/pnl/exchange?days=7")
    body = resp.json()
    assert body["summary"]["total_realized_pnl"] == 0.0
    assert body["summary"]["total_unrealized_pnl"] == 0.0


def test_endpoint_includes_fifo_fields_per_symbol(client, fills_db, monkeypatch):
    """Round-trip BTC + open ETH long → realised on BTC, unrealised on ETH."""
    store.upsert_fills(
        [
            _row(exec_id="b1", symbol="BTC/USDT:USDT",
                 side="buy",  price=60000.0, qty=0.001, fee=0.06,
                 exec_time="2026-05-09T10:00:00+00:00"),
            _row(exec_id="b2", symbol="BTC/USDT:USDT",
                 side="sell", price=61000.0, qty=0.001, fee=0.06,
                 exec_time="2026-05-09T11:00:00+00:00"),
            _row(exec_id="e1", symbol="ETH/USDT:USDT",
                 side="buy",  price=3000.0, qty=1.0, fee=0.30,
                 exec_time="2026-05-09T12:00:00+00:00"),
        ],
        path=fills_db,
    )
    fixed_now = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(store, "datetime", _frozen_datetime(fixed_now))

    resp = client.get("/api/bot/pnl/exchange?days=7")
    body = resp.json()
    by_sym = {r["symbol"]: r for r in body["by_symbol"]}

    btc = by_sym["BTC/USDT:USDT"]
    # realised = (61000-60000)*0.001 - 0.06 - 0.06 = 1.0 - 0.12 = 0.88
    assert abs(btc["realized_pnl"] - 0.88) < 1e-9
    assert abs(btc["unrealized_pnl"]) < 1e-9
    assert abs(btc["open_qty_signed"]) < 1e-9
    assert btc["last_price"] == 61000.0

    eth = by_sym["ETH/USDT:USDT"]
    # 1 open lot @ 3000, mark = 3000 (last fill) → unrealised = 0
    # realised = -fee = -0.30
    assert abs(eth["realized_pnl"] + 0.30) < 1e-9
    assert abs(eth["unrealized_pnl"]) < 1e-9
    assert abs(eth["open_qty_signed"] - 1.0) < 1e-9
    assert eth["last_price"] == 3000.0

    # Summary totals = sum of per-symbol contributions.
    assert abs(body["summary"]["total_realized_pnl"]
               - (0.88 - 0.30)) < 1e-9
    assert abs(body["summary"]["total_unrealized_pnl"]) < 1e-9


def test_endpoint_phase_one_keys_unchanged(client, fills_db):
    """Existing Phase-1 keys (fill_count, total_fees, symbol_count,
    window_days) MUST remain. The Phase-2 additions are additive only —
    no rename, no removal — so old dashboard readers don't break."""
    store.upsert_fills(
        [_row(exec_id="x1", side="buy", price=100.0, qty=1.0, fee=0.1,
              exec_time="2026-05-09T10:00:00+00:00")],
        path=fills_db,
    )
    body = client.get("/api/bot/pnl/exchange?days=7").json()
    summary = body["summary"]
    assert "fill_count" in summary
    assert "total_fees" in summary
    assert "symbol_count" in summary
    assert "window_days" in summary
    row = body["by_symbol"][0]
    for key in ("symbol", "fill_count", "gross_qty", "gross_notional",
                "total_fees", "first_exec_time", "last_exec_time"):
        assert key in row, f"Phase-1 key '{key}' missing from by_symbol row"
