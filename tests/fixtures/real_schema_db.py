"""Shared real-schema sqlite test fixture.

Generalised from the PR #627 + S-067 audit pattern: read-path tests
must materialise the *production* DB schema (the same shape
``src/units/db/database.py::Database.create_tables`` produces) so a
column rename or column drop fails the regression test instead of
silently returning ``[]`` from the endpoint under test.

Two ways to materialise the schema:

1. **Identity-by-construction.** ``make_canonical_db(path)`` instantiates
   ``src.units.db.database.Database`` against *path* and lets it run
   the real ``create_tables`` migration. Any future schema migration
   in production code is automatically reflected here — no parallel
   ``CREATE TABLE`` to keep in sync. Preferred for new tests.

2. **Per-helper inserts.** ``insert_trade(path, **fields)`` and
   ``insert_order_package(path, **fields)`` are thin wrappers that
   take whatever columns the test cares about and INSERT them with
   plain sqlite — no ORM. Mirrors how the original
   ``_insert_trade`` helper worked in
   ``tests/test_dashboard_data_contract.py`` so existing tests can
   migrate one line at a time.

Use the ``real_schema_db`` pytest fixture (function-scoped) to get a
fresh ``Path`` to a populated tmp DB:

    def test_something(real_schema_db):
        db = real_schema_db()  # canonical empty schema
        insert_trade(db, timestamp="2026-05-09T10:00:00Z", ...)
        ...

Or with optional pre-populated rows:

    def test_with_rows(real_schema_db):
        db = real_schema_db(trades=[
            {"timestamp": "2026-05-09T10:00:00Z", "symbol": "BTCUSDT",
             "direction": "long", "entry_price": 60000.0,
             "position_size": 0.001, "status": "open"},
        ])
"""
from __future__ import annotations

import contextlib
import io
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping

import pytest


# ---------------------------------------------------------------------------
# Schema construction
# ---------------------------------------------------------------------------


def make_canonical_db(path: Path) -> Path:
    """Materialise the production trades + order_packages + signals
    + backtest_results + strategy_versions schema at *path*.

    Calls ``src.units.db.database.Database`` directly so any future
    column add / index add in production code is reflected here too —
    no parallel CREATE TABLE statement to keep in sync.

    The Database constructor prints a "✓ Database tables
    created/verified" line on stdout; suppress it so test output
    stays clean.

    Returns *path*.
    """
    # Imported lazily so this module is import-cheap for tests that
    # only need the insert helpers.
    from src.units.db.database import Database

    with contextlib.redirect_stdout(io.StringIO()):
        Database(str(path))
    return path


# ---------------------------------------------------------------------------
# Insert helpers
# ---------------------------------------------------------------------------


def insert_trade(path: Path, **fields: Any) -> int:
    """INSERT one row into ``trades``. Returns the new row id.

    *fields* is keyword-only; whatever columns the test cares about
    are passed through verbatim. Columns not provided take the
    schema's defaults (e.g. ``status`` defaults to ``'open'``,
    ``account_id`` to ``'live'``, ``is_backtest`` to ``1``).

    NB: the production schema defaults ``is_backtest`` to ``1``.
    Tests for live-only endpoints (``/positions``, ``/trades/closed``,
    etc.) should pass ``is_backtest=0`` explicitly or the row will
    be filtered out.
    """
    conn = sqlite3.connect(str(path))
    try:
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        cur = conn.execute(
            f"INSERT INTO trades ({cols}) VALUES ({placeholders})",
            list(fields.values()),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def insert_order_package(path: Path, **fields: Any) -> str:
    """INSERT one row into ``order_packages``. Returns the package id.

    The schema marks several columns NOT NULL; this helper fills
    sensible defaults for any not provided so most tests can pass only
    the fields they care about. Override any default by passing the
    column explicitly.
    """
    fields.setdefault("strategy_name", "turtle_soup")
    fields.setdefault("symbol", "BTCUSDT")
    fields.setdefault("direction", "long")
    fields.setdefault("entry", 60000.0)
    fields.setdefault("sl", 59000.0)
    fields.setdefault("tp", 62000.0)
    fields.setdefault("created_at", "2026-05-08T10:00:00Z")
    fields.setdefault("updated_at", fields["created_at"])
    fields.setdefault("status", "closed")
    if "order_package_id" not in fields:
        raise ValueError("insert_order_package requires order_package_id")

    conn = sqlite3.connect(str(path))
    try:
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        conn.execute(
            f"INSERT INTO order_packages ({cols}) VALUES ({placeholders})",
            list(fields.values()),
        )
        conn.commit()
    finally:
        conn.close()
    return str(fields["order_package_id"])


# ---------------------------------------------------------------------------
# Pytest fixture factory
# ---------------------------------------------------------------------------


@pytest.fixture
def real_schema_db(tmp_path: Path):
    """Yield a callable that materialises a canonical sqlite DB and
    optionally pre-populates ``trades`` / ``order_packages`` rows.

    Signature:

        real_schema_db(
            trades: Iterable[Mapping[str, Any]] | None = None,
            order_packages: Iterable[Mapping[str, Any]] | None = None,
            name: str = "trade_journal.db",
        ) -> Path

    Each call creates a fresh DB at ``tmp_path / name``. Pass ``name``
    to materialise multiple isolated DBs in a single test.
    """
    created: list[Path] = []

    def _factory(
        trades: Iterable[Mapping[str, Any]] | None = None,
        order_packages: Iterable[Mapping[str, Any]] | None = None,
        name: str = "trade_journal.db",
    ) -> Path:
        path = tmp_path / name
        if path.exists():
            path.unlink()
        make_canonical_db(path)
        for row in trades or ():
            insert_trade(path, **dict(row))
        for row in order_packages or ():
            insert_order_package(path, **dict(row))
        created.append(path)
        return path

    return _factory
