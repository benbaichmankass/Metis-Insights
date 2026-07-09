"""RISK-3 / BL-20260707-HEALTHAPI-ACCTBAL-BLOCKING-DB.

Two layers:

1. Self-test for the ``async-route blocking`` CI guard
   (``scripts/ci/check_async_route_blocking.py``) — a planted async-no-await
   route and a planted blocking-sqlite-on-the-loop route must fail; a plain
   ``def`` route and a properly-offloaded async route must pass; and — the live
   invariant — the real ``src/web/api`` tree must currently be clean.

2. Behavioural checks that the fix actually landed: the read routes that used to
   block the loop are now plain ``def`` (FastAPI threadpools them); the routes
   that must stay ``async`` (a real ``await``) still are; the prop GETs
   graceful-degrade to ``present:false`` instead of a 500; and the shared DB
   connect helpers set ``busy_timeout``.
"""
from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

from scripts.ci.check_async_route_blocking import find_violations_in_source, scan_paths

_REPO_ROOT = Path(__file__).resolve().parents[1]


# --- guard self-test ---------------------------------------------------------

def test_async_route_without_await_is_flagged():
    src = (
        '@router.get("/x")\n'
        "async def x():\n"
        "    return 1\n"
    )
    hits = find_violations_in_source(src, "src/web/api/routers/t.py")
    assert len(hits) == 1 and hits[0][0] == 2 and "awaits nothing" in hits[0][1]


def test_blocking_sqlite_in_async_route_is_flagged():
    src = (
        '@router.get("/y")\n'
        "async def y(request):\n"
        "    conn = sqlite3.connect('z')\n"
        "    return await request.json()\n"
    )
    hits = find_violations_in_source(src, "src/web/api/routers/t.py")
    assert len(hits) == 1 and hits[0][0] == 3 and "sqlite3.connect" in hits[0][1]


def test_sync_def_route_with_blocking_is_clean():
    # A plain ``def`` route is threadpooled by FastAPI — blocking is fine.
    src = (
        '@router.get("/a")\n'
        "def a():\n"
        "    conn = sqlite3.connect('z')\n"
        "    return 1\n"
    )
    assert find_violations_in_source(src, "src/web/api/routers/t.py") == []


def test_offloaded_blocking_in_async_route_is_clean():
    src = (
        '@router.post("/b")\n'
        "async def b(request):\n"
        "    body = await request.json()\n"
        "    return await asyncio.to_thread(sqlite3.connect, 'z')\n"
    )
    assert find_violations_in_source(src, "src/web/api/routers/t.py") == []


def test_allow_marker_suppresses():
    src = (
        '@router.get("/c")\n'
        "async def c():  # async-route-allow: intentional\n"
        "    return 1\n"
    )
    assert find_violations_in_source(src, "src/web/api/routers/t.py") == []


def test_live_web_api_tree_is_clean():
    """The real invariant: no route in src/web/api blocks the event loop."""
    violations = scan_paths([_REPO_ROOT / "src" / "web" / "api"], _REPO_ROOT)
    assert violations == [], "async-route blocking regressions:\n" + "\n".join(violations)


# --- behavioural: the fix landed --------------------------------------------

def test_read_routes_are_sync_def():
    """Routes that only do blocking work are plain ``def`` (threadpooled)."""
    from src.web.api.routers import accounts, dashboard, devices, prop

    for fn in (
        accounts.get_account_balances,
        dashboard.get_stats, dashboard.get_logs, dashboard.get_signals,
        prop.get_fills, prop.get_tickets, prop.get_status, prop.get_reconcile,
        devices.list_devices, devices.revoke_device, devices.get_event_kinds,
    ):
        assert not inspect.iscoroutinefunction(fn), f"{fn.__name__} must be a sync def"


def test_await_routes_stay_async():
    """Routes with a real ``await`` (request.json / broker read) stay async."""
    from src.web.api.routers import dashboard, devices, prop

    for fn in (
        dashboard.get_positions,
        devices.register_device, devices.update_subscriptions,
        prop.post_report,
    ):
        assert inspect.iscoroutinefunction(fn), f"{fn.__name__} must stay async"


def test_prop_gets_graceful_degrade_on_db_error(monkeypatch):
    """A read error degrades to present:false, never a 500."""
    from src.prop import prop_journal
    from src.web.api.routers import prop

    def _boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(prop_journal, "list_fills", _boom)
    out = prop.get_fills()
    assert out == {"present": False, "count": 0, "fills": []}


def test_database_connect_sets_busy_timeout(tmp_path):
    from src.units.db.database import Database

    db = Database(db_path=str(tmp_path / "t.db"))
    conn = db.connect()
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 3000
    finally:
        conn.close()


def test_prop_journal_connect_sets_busy_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "t.db"))
    from src.prop import prop_journal

    conn = prop_journal._connect()
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 3000
    finally:
        conn.close()
