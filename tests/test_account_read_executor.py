"""BL-20260706-IBCONCURRENCY — account/position reads must never call a
blocking, event-loop-driving IB helper directly from an ``async def`` FastAPI
route handler.

Root cause (confirmed live 2026-07-06): ``IBClient.positions()``/``balance()``
drive ib_insync's own event loop via ``IBClient._ensure_event_loop()``, which
first checks ``asyncio.get_running_loop()`` — inside an async route handler
that check succeeds (uvicorn's loop is already running on this thread), so
ib_insync then tries to run a coroutine to completion on top of the
ALREADY-RUNNING loop: "Cannot run the event loop while another loop is
running" / "Future ... attached to a different loop".

These tests don't spin up a real IB Gateway; they verify (a) the shared
executor helper itself offloads correctly and serializes concurrent callers,
and (b) the two fixed routes (``/api/diag/exchange_positions`` and
``/api/bot/positions``) go through it rather than calling the blocking helper
inline — so a future regression that reintroduces a direct synchronous call
is caught even without a live Gateway to repro the race against.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

from src.web.api import _account_read_executor
from src.web.api._account_read_executor import run_account_read
from src.web.api import main as api_main
from src.web.api.routers import dashboard as dashboard_router
from src.web.api.routers import diag as diag_router


def test_run_account_read_offloads_off_the_calling_thread():
    """The blocking fn must execute on a different (dedicated) thread."""
    caller_thread = threading.current_thread().ident
    seen = {}

    def blocking_fn():
        seen["thread"] = threading.current_thread().ident
        return "ok"

    async def _run():
        return await run_account_read(blocking_fn)

    result = asyncio.run(_run())
    assert result == "ok"
    assert seen["thread"] != caller_thread


def test_run_account_read_serializes_concurrent_callers():
    """Two concurrent callers on the single-worker executor never overlap —
    this is what prevents two requests reading the SAME IB-backed account
    from racing each other's connection/loop state."""
    active = {"n": 0, "max": 0}
    lock = threading.Lock()

    def blocking_fn(i):
        with lock:
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
        time.sleep(0.05)
        with lock:
            active["n"] -= 1
        return i

    async def _run():
        return await asyncio.gather(*[
            run_account_read(blocking_fn, i) for i in range(5)
        ])

    results = asyncio.run(_run())
    assert sorted(results) == [0, 1, 2, 3, 4]
    # max concurrently-active invocations must never exceed 1 — proves the
    # single-worker executor genuinely serializes, not merely offloads.
    assert active["max"] == 1


def test_run_account_read_propagates_exceptions():
    def boom():
        raise RuntimeError("IB gateway unreachable")

    async def _run():
        return await run_account_read(boom)

    with pytest.raises(RuntimeError, match="IB gateway unreachable"):
        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Route-level regression guards: assert the fixed routes go through the
# executor rather than calling the blocking helper directly.
# ---------------------------------------------------------------------------

_TOKEN = "test-diag-token-not-a-real-secret"


def _bearer(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def diag_client(monkeypatch):
    monkeypatch.setenv("DIAG_READ_TOKEN", _TOKEN)
    return TestClient(api_main.app, raise_server_exceptions=False)


def test_diag_exchange_positions_uses_account_read_executor(diag_client, monkeypatch):
    calls = {"n": 0}

    async def fake_run_account_read(fn, *args):
        calls["n"] += 1
        return fn(*args)

    monkeypatch.setattr(diag_router, "run_account_read", fake_run_account_read)

    # get_exchange_positions imports both symbols locally
    # (`from src.units.ui.data_loaders import account_open_positions,
    # list_accounts`) at call time, so patch them on the source module.
    import src.units.ui.data_loaders as data_loaders
    monkeypatch.setattr(
        data_loaders, "list_accounts",
        lambda: [{"account_id": "ib_paper", "exchange": "interactive_brokers"}],
    )
    monkeypatch.setattr(data_loaders, "account_open_positions", lambda acc: [])

    resp = diag_client.get("/api/diag/exchange_positions", headers=_bearer(_TOKEN))
    assert resp.status_code == 200
    assert calls["n"] == 1
    body = resp.json()
    assert body["accounts"][0]["account_id"] == "ib_paper"
    assert body["accounts"][0]["positions"] == []


def test_diag_exchange_positions_direct_call_would_be_a_regression(diag_client, monkeypatch):
    """If a future change reintroduces a direct (non-offloaded) call to
    ``account_open_positions``, this test's patched ``run_account_read``
    (which asserts it is invoked) stops being exercised and the prior test
    fails instead — belt-and-suspenders: this test additionally asserts the
    route module still imports the shared helper by name."""
    assert hasattr(diag_router, "run_account_read")
    assert diag_router.run_account_read is _account_read_executor.run_account_read


def test_dashboard_positions_uses_account_read_executor(monkeypatch, tmp_path):
    calls = {"n": 0}

    async def fake_run_account_read(fn, *args):
        calls["n"] += 1
        return fn(*args)

    monkeypatch.setattr(dashboard_router, "run_account_read", fake_run_account_read)

    import sqlite3
    db_path = tmp_path / "trade_journal.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY, account_id TEXT, symbol TEXT, direction TEXT,
            position_size REAL, entry_price REAL, created_at TEXT,
            stop_loss REAL, take_profit_1 REAL, strategy_name TEXT,
            is_demo INTEGER, account_class TEXT, notes TEXT,
            status TEXT, is_backtest INTEGER
        )
        """
    )
    conn.execute(
        "INSERT INTO trades (account_id, symbol, direction, position_size, entry_price, "
        "created_at, stop_loss, take_profit_1, strategy_name, is_demo, account_class, "
        "notes, status, is_backtest) VALUES ('ib_paper','MES','long',1,100.0,'2026-07-06T00:00:00Z',"
        "95.0,110.0,'trend_donchian',0,'real_money',NULL,'open',0)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(dashboard_router, "_DB_PATH", db_path)
    monkeypatch.setattr(
        dashboard_router, "_resolve_position_pnl",
        lambda *a, **k: (None, "unavailable"),
    )

    client = TestClient(api_main.app, raise_server_exceptions=False)
    resp = client.get("/api/bot/positions")
    assert resp.status_code == 200
    assert calls["n"] == 1
    body = resp.json()
    assert len(body) == 1
    assert body[0]["symbol"] == "MES"
