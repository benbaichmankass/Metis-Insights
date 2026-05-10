"""S-067 follow-up #3 — closed → exchange-flat invariant tests.

Mock-exchange tests for ``src/runtime/closed_flat_invariant.py``.
The module is the safety-net for the trade #1049 class of bug
(closed in DB, still open on exchange).

Tier 2 / live-order path. The module ships in alert-only mode and
is NOT yet wired into the tick loop in this PR — these tests pin
the alert-firing behaviour so the wiring PR can land confidently.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

from src.runtime import closed_flat_invariant as inv


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def trades_db(real_schema_db) -> Path:
    """A canonical trades DB (uses the shared S-067 fixture)."""
    return real_schema_db()


class _StubAccount:
    """Minimal TradingAccount stand-in for the resolver injection."""

    def __init__(self, positions):
        self._positions = positions

    def open_positions(self):
        return self._positions


def _resolver(account_map: dict[str, _StubAccount]):
    def _fn(account_id: str) -> Optional[_StubAccount]:
        return account_map.get(account_id)
    return _fn


def _alerter_capture():
    captured = []

    def _fn(channel, summary, payload):
        captured.append({"channel": channel, "summary": summary, "payload": payload})

    return captured, _fn


def _close_trade(db_path: Path, trade_id: int, *,
                 closed_at: str = "2026-05-10T10:00:00+00:00") -> None:
    """Mark *trade_id* closed and stash closed_at into notes JSON."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE trades SET status='closed', "
            "notes=json_object('closed_at', ?) WHERE id=?",
            (closed_at, trade_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Trade #1049 retrospective — the canonical bug class
# ---------------------------------------------------------------------------


def test_violation_fires_when_db_closed_but_exchange_still_open(
    trades_db, tmp_path, real_schema_db,
):
    """The actual bug shape from the 2026-05-10 review."""
    from tests.fixtures.real_schema_db import insert_trade

    trade_id = insert_trade(
        trades_db,
        timestamp="2026-05-10T09:55:00+00:00",
        symbol="BTCUSDT",
        direction="long",
        entry_price=60000.0,
        position_size=0.001,
        status="open",
        is_backtest=0,
        account_id="bybit_2",
        created_at="2026-05-10T09:55:00+00:00",
    )
    _close_trade(trades_db, trade_id)

    # Exchange still shows 0.001 BTCUSDT long — the bug.
    account_map = {
        "bybit_2": _StubAccount(positions=[
            {"symbol": "BTCUSDT", "side": "Buy", "qty": 0.001},
        ]),
    }
    log = tmp_path / "violations.jsonl"
    captured, alerter = _alerter_capture()

    violations = inv.check(
        sqlite3.connect(str(trades_db)),
        _resolver(account_map),
        window_seconds=60,
        now=datetime(2026, 5, 10, 10, 0, 30, tzinfo=timezone.utc),
        violations_log=log,
        alerter=alerter,
    )

    assert len(violations) == 1
    v = violations[0]
    assert v.trade_id == trade_id
    assert v.account_id == "bybit_2"
    assert v.symbol == "BTCUSDT"
    assert v.exchange_qty == 0.001
    assert v.phase == "alert_only"

    # Telegram alert fired.
    assert len(captured) == 1
    assert "closed_flat_invariant" == captured[0]["channel"]
    assert f"trade #{trade_id}" in captured[0]["summary"]
    assert "0.001" in captured[0]["summary"]

    # JSONL row written.
    assert log.exists()
    rows = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln]
    assert len(rows) == 1
    assert rows[0]["trade_id"] == trade_id


def test_no_violation_when_exchange_is_actually_flat(trades_db, tmp_path):
    """Happy path: trade closed in DB, exchange has no residual position."""
    from tests.fixtures.real_schema_db import insert_trade

    trade_id = insert_trade(
        trades_db,
        timestamp="2026-05-10T09:55:00+00:00",
        symbol="BTCUSDT",
        direction="long",
        entry_price=60000.0,
        position_size=0.001,
        status="open",
        is_backtest=0,
        account_id="bybit_2",
        created_at="2026-05-10T09:55:00+00:00",
    )
    _close_trade(trades_db, trade_id)

    account_map = {"bybit_2": _StubAccount(positions=[])}  # flat
    captured, alerter = _alerter_capture()

    violations = inv.check(
        sqlite3.connect(str(trades_db)),
        _resolver(account_map),
        window_seconds=60,
        now=datetime(2026, 5, 10, 10, 0, 30, tzinfo=timezone.utc),
        violations_log=tmp_path / "violations.jsonl",
        alerter=alerter,
    )
    assert violations == []
    assert captured == []


def test_dust_residual_is_treated_as_flat(trades_db, tmp_path):
    """Bybit occasionally returns 1e-9 residuals on a fully-closed
    position — these are dust, not violations."""
    from tests.fixtures.real_schema_db import insert_trade

    trade_id = insert_trade(
        trades_db,
        timestamp="2026-05-10T09:55:00+00:00",
        symbol="BTCUSDT",
        direction="long",
        entry_price=60000.0,
        position_size=0.001,
        status="open",
        is_backtest=0,
        account_id="bybit_2",
        created_at="2026-05-10T09:55:00+00:00",
    )
    _close_trade(trades_db, trade_id)

    account_map = {
        "bybit_2": _StubAccount(positions=[
            {"symbol": "BTCUSDT", "side": "Buy", "qty": 1e-10},
        ]),
    }
    captured, alerter = _alerter_capture()
    violations = inv.check(
        sqlite3.connect(str(trades_db)),
        _resolver(account_map),
        now=datetime(2026, 5, 10, 10, 0, 30, tzinfo=timezone.utc),
        violations_log=tmp_path / "violations.jsonl",
        alerter=alerter,
    )
    assert violations == []
    assert captured == []


def test_window_filters_old_closes(trades_db, tmp_path):
    """A trade closed > window_seconds ago must not be reported —
    the orphan reconciler will pick those up. The invariant is
    the *fast* path."""
    from tests.fixtures.real_schema_db import insert_trade

    trade_id = insert_trade(
        trades_db,
        timestamp="2026-05-10T08:00:00+00:00",
        symbol="BTCUSDT",
        direction="long",
        entry_price=60000.0,
        position_size=0.001,
        status="open",
        is_backtest=0,
        account_id="bybit_2",
        created_at="2026-05-10T08:00:00+00:00",
    )
    # Close happened 2 hours ago.
    _close_trade(trades_db, trade_id, closed_at="2026-05-10T08:00:00+00:00")

    account_map = {
        "bybit_2": _StubAccount(positions=[
            {"symbol": "BTCUSDT", "side": "Buy", "qty": 0.001},
        ]),
    }
    captured, alerter = _alerter_capture()
    violations = inv.check(
        sqlite3.connect(str(trades_db)),
        _resolver(account_map),
        window_seconds=60,
        now=datetime(2026, 5, 10, 10, 0, 0, tzinfo=timezone.utc),
        violations_log=tmp_path / "violations.jsonl",
        alerter=alerter,
    )
    assert violations == []  # fell out of the window


def test_short_side_residual_reported_as_negative(trades_db, tmp_path):
    """Short-side residuals come out signed: exchange_qty < 0."""
    from tests.fixtures.real_schema_db import insert_trade

    trade_id = insert_trade(
        trades_db,
        timestamp="2026-05-10T09:55:00+00:00",
        symbol="ETHUSDT",
        direction="short",
        entry_price=3000.0,
        position_size=0.5,
        status="open",
        is_backtest=0,
        account_id="bybit_2",
        created_at="2026-05-10T09:55:00+00:00",
    )
    _close_trade(trades_db, trade_id)

    account_map = {
        "bybit_2": _StubAccount(positions=[
            {"symbol": "ETHUSDT", "side": "Sell", "qty": 0.5},
        ]),
    }
    captured, alerter = _alerter_capture()
    violations = inv.check(
        sqlite3.connect(str(trades_db)),
        _resolver(account_map),
        now=datetime(2026, 5, 10, 10, 0, 30, tzinfo=timezone.utc),
        violations_log=tmp_path / "violations.jsonl",
        alerter=alerter,
    )
    assert len(violations) == 1
    assert violations[0].exchange_qty == -0.5


# ---------------------------------------------------------------------------
# Never-raise contract
# ---------------------------------------------------------------------------


def test_check_never_raises_on_db_error(trades_db, tmp_path):
    """A connection to a non-existent DB file must not propagate."""
    bad_db = tmp_path / "missing.db"
    captured, alerter = _alerter_capture()
    violations = inv.check(
        sqlite3.connect(str(bad_db)),
        _resolver({}),
        violations_log=tmp_path / "violations.jsonl",
        alerter=alerter,
    )
    # Empty DB → no rows; no violations; no crash.
    assert violations == []


def test_check_never_raises_on_account_resolver_failure(trades_db, tmp_path):
    """A resolver that raises must not crash the tick loop."""
    from tests.fixtures.real_schema_db import insert_trade

    trade_id = insert_trade(
        trades_db,
        timestamp="2026-05-10T09:55:00+00:00",
        symbol="BTCUSDT",
        direction="long",
        entry_price=60000.0,
        position_size=0.001,
        status="open",
        is_backtest=0,
        account_id="bybit_2",
        created_at="2026-05-10T09:55:00+00:00",
    )
    _close_trade(trades_db, trade_id)

    def _bad_resolver(account_id):
        raise RuntimeError("synthetic resolver crash")

    violations = inv.check(
        sqlite3.connect(str(trades_db)),
        _bad_resolver,
        now=datetime(2026, 5, 10, 10, 0, 30, tzinfo=timezone.utc),
        violations_log=tmp_path / "violations.jsonl",
    )
    # Caught at the outer try/except — empty list, no crash.
    assert violations == []


def test_check_never_raises_on_open_positions_failure(trades_db, tmp_path):
    """A TradingAccount whose open_positions() raises must not crash."""
    from tests.fixtures.real_schema_db import insert_trade

    class _CrashingAccount:
        def open_positions(self):
            raise RuntimeError("synthetic exchange API failure")

    trade_id = insert_trade(
        trades_db,
        timestamp="2026-05-10T09:55:00+00:00",
        symbol="BTCUSDT",
        direction="long",
        entry_price=60000.0,
        position_size=0.001,
        status="open",
        is_backtest=0,
        account_id="bybit_2",
        created_at="2026-05-10T09:55:00+00:00",
    )
    _close_trade(trades_db, trade_id)

    captured, alerter = _alerter_capture()
    violations = inv.check(
        sqlite3.connect(str(trades_db)),
        _resolver({"bybit_2": _CrashingAccount()}),
        now=datetime(2026, 5, 10, 10, 0, 30, tzinfo=timezone.utc),
        violations_log=tmp_path / "violations.jsonl",
        alerter=alerter,
    )
    # Account fetch failed → residual=0.0 → no violation reported.
    assert violations == []


# ---------------------------------------------------------------------------
# Env gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [("1", True), ("true", True), ("True", True), ("yes", True),
     ("on", True), ("0", False), ("false", False), ("", False),
     (None, False)],
)
def test_is_enabled_env_gate(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("CLOSED_FLAT_INVARIANT_ENABLED", raising=False)
    else:
        monkeypatch.setenv("CLOSED_FLAT_INVARIANT_ENABLED", raw)
    assert inv.is_enabled() is expected


# ---------------------------------------------------------------------------
# Multi-trade batch
# ---------------------------------------------------------------------------


def test_multiple_violations_in_one_check(trades_db, tmp_path):
    """If two trades closed in the window and both have residuals,
    both fire."""
    from tests.fixtures.real_schema_db import insert_trade

    t1 = insert_trade(
        trades_db,
        timestamp="2026-05-10T09:55:00+00:00",
        symbol="BTCUSDT",
        direction="long",
        entry_price=60000.0,
        position_size=0.001,
        status="open",
        is_backtest=0,
        account_id="bybit_2",
        created_at="2026-05-10T09:55:00+00:00",
    )
    t2 = insert_trade(
        trades_db,
        timestamp="2026-05-10T09:56:00+00:00",
        symbol="ETHUSDT",
        direction="short",
        entry_price=3000.0,
        position_size=0.5,
        status="open",
        is_backtest=0,
        account_id="bybit_2",
        created_at="2026-05-10T09:56:00+00:00",
    )
    _close_trade(trades_db, t1)
    _close_trade(trades_db, t2)

    account_map = {"bybit_2": _StubAccount(positions=[
        {"symbol": "BTCUSDT", "side": "Buy", "qty": 0.001},
        {"symbol": "ETHUSDT", "side": "Sell", "qty": 0.5},
    ])}
    captured, alerter = _alerter_capture()
    violations = inv.check(
        sqlite3.connect(str(trades_db)),
        _resolver(account_map),
        now=datetime(2026, 5, 10, 10, 0, 30, tzinfo=timezone.utc),
        violations_log=tmp_path / "violations.jsonl",
        alerter=alerter,
    )
    assert len(violations) == 2
    assert {v.trade_id for v in violations} == {t1, t2}
    assert len(captured) == 2  # one alert per violation


def test_backtest_trades_excluded(trades_db, tmp_path):
    """Backtest trades must never trigger the invariant — their
    'positions' aren't on the live exchange."""
    from tests.fixtures.real_schema_db import insert_trade

    trade_id = insert_trade(
        trades_db,
        timestamp="2026-05-10T09:55:00+00:00",
        symbol="BTCUSDT",
        direction="long",
        entry_price=60000.0,
        position_size=0.001,
        status="open",
        is_backtest=1,  # SYNTHETIC
        account_id="backtest",
        created_at="2026-05-10T09:55:00+00:00",
    )
    _close_trade(trades_db, trade_id)

    # Even if the exchange somehow has a position (it shouldn't),
    # backtest trades are never reported.
    account_map = {"backtest": _StubAccount(positions=[
        {"symbol": "BTCUSDT", "side": "Buy", "qty": 0.001},
    ])}
    captured, alerter = _alerter_capture()
    violations = inv.check(
        sqlite3.connect(str(trades_db)),
        _resolver(account_map),
        now=datetime(2026, 5, 10, 10, 0, 30, tzinfo=timezone.utc),
        violations_log=tmp_path / "violations.jsonl",
        alerter=alerter,
    )
    assert violations == []


# ---------------------------------------------------------------------------
# S-CFI-FIX — defensive db-type check on _fetch_recently_closed
# ---------------------------------------------------------------------------
#
# Regression for the PR #658 leak: nine zero-byte files named
# "<sqlite3.Connection object at 0x...>" landed at the repo root because
# the `else: sqlite3.connect(str(db))` branch happily stringified
# whatever was passed and let sqlite3 create a file at that path.


def test_fetch_recently_closed_rejects_unsupported_db_type(tmp_path, monkeypatch):
    """Unsupported db argument types must raise TypeError, not silently
    create a file at repr(db). Run from a clean cwd to assert no
    spurious file appears."""
    monkeypatch.chdir(tmp_path)

    class _NotADb:
        pass

    with pytest.raises(TypeError, match="must be a sqlite3.Connection"):
        inv._fetch_recently_closed(_NotADb(), cutoff_iso="2026-05-10T00:00:00+00:00")

    # No file was created at the repr() of the bad object, anywhere
    # under the temp cwd.
    leaked = list(tmp_path.glob("<*>")) + list(tmp_path.glob("*Connection*"))
    assert leaked == []


def test_fetch_recently_closed_accepts_path_like(trades_db, tmp_path):
    """Path-like (str / pathlib.Path) inputs still work after the
    type-tightening."""
    # Pre-condition: trades_db fixture returns a Path. Both forms
    # should produce the same (empty, fresh DB) result.
    rows_from_path = inv._fetch_recently_closed(
        trades_db, cutoff_iso="2099-01-01T00:00:00+00:00",
    )
    rows_from_str = inv._fetch_recently_closed(
        str(trades_db), cutoff_iso="2099-01-01T00:00:00+00:00",
    )
    assert rows_from_path == []
    assert rows_from_str == []
