"""VWAP strategy self-suppression — defence-in-depth contract.

Pulls the strategy_monocle gate (which lives in
``src.runtime.pipeline._has_open_package_for_strategy``) inside the
strategy module itself so a bypassed pipeline gate doesn't re-open
the floodgates of duplicate vwap entries every tick. The pipeline
gate remains the primary line of defence; this is belt-and-braces.

Contract:
* No DB / no open vwap package → ``order_package`` builds the dict.
* Linked open vwap package → ``order_package`` raises ValueError
  (matching the existing "non-actionable signal" exit pattern; the
  pipeline catches it and records a flat tick).
* Closed / orphaned package → no block; new package is generated.
* Unlinked open package → no block; matches the
  ``linked_only=True`` gate semantic from BUG-049.
* Open package for a *different* strategy → no block.
* DB read failure → no block (best-effort; a journal outage must
  not silence the strategy).
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.units.db.database import Database
from src.units.strategies import vwap


def _bullish_candles(n: int = 11) -> pd.DataFrame:
    """Linearly rising candles — enough volume + length for the VWAP
    signal builder to produce a non-degenerate result. Last close is
    above the linear trend's mean so the natural side resolves to
    'sell' (mean-reversion short) — but the *direction* is irrelevant
    for these tests, only that ``order_package`` returns a dict
    (rather than raising) when self-suppression is dormant."""
    prices = [100.0 + i for i in range(n)]
    return pd.DataFrame({
        "open": prices,
        "high": [p + 1 for p in prices],
        "low": [p - 1 for p in prices],
        "close": prices,
        "volume": [1000.0] * n,
        "timestamp": list(range(n)),
    })


@pytest.fixture
def tmp_journal(tmp_path, monkeypatch):
    """Isolated trade-journal DB for the test. ``TRADE_JOURNAL_DB``
    is read by ``_has_open_vwap_package`` so the strategy points at
    this file rather than any production journal that may exist on
    the dev machine."""
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    return Database(db_path=str(db_path))


def _insert_pkg(
    db,
    *,
    pkg_id: str,
    strategy: str = "vwap",
    status: str = "open",
    linked_trade_id=None,
):
    db.insert_order_package({
        "order_package_id": pkg_id,
        "strategy_name": strategy,
        "symbol": "BTCUSDT",
        "direction": "long",
        "entry": 80_000.0,
        "sl": 79_500.0,
        "tp": 80_500.0,
        "confidence": 0.42,
        "status": status,
        "linked_trade_id": linked_trade_id,
        "meta": {},
    })


# ---------------------------------------------------------------------------
# Positive: open + linked package suppresses a new entry
# ---------------------------------------------------------------------------


def test_linked_open_package_suppresses_new_entry(tmp_journal):
    _insert_pkg(tmp_journal, pkg_id="pkg-vwap-live", linked_trade_id=42)
    with pytest.raises(ValueError, match="open package already exists"):
        vwap.order_package({"symbol": "BTCUSDT"}, candles_df=_bullish_candles())


# ---------------------------------------------------------------------------
# Negative: scenarios that must NOT block dispatch
# ---------------------------------------------------------------------------


def test_no_open_package_dispatch_proceeds(tmp_journal):
    """Empty journal → strategy generates a package as normal."""
    pkg = vwap.order_package({"symbol": "BTCUSDT"}, candles_df=_bullish_candles())
    assert isinstance(pkg, dict)
    assert pkg.get("symbol") == "BTCUSDT"
    assert pkg.get("direction") in ("long", "short")


def test_closed_package_does_not_block(tmp_journal):
    _insert_pkg(tmp_journal, pkg_id="pkg-vwap-old", status="closed",
                linked_trade_id=42)
    pkg = vwap.order_package({"symbol": "BTCUSDT"}, candles_df=_bullish_candles())
    assert isinstance(pkg, dict)


def test_unlinked_open_package_does_not_block(tmp_journal):
    """A status='open' row without a linked_trade_id was never
    actually placed at the broker (BUG-049). Self-suppression mirrors
    the pipeline gate's ``linked_only=True`` filter and lets the new
    signal through."""
    _insert_pkg(tmp_journal, pkg_id="pkg-vwap-unlinked", linked_trade_id=None)
    pkg = vwap.order_package({"symbol": "BTCUSDT"}, candles_df=_bullish_candles())
    assert isinstance(pkg, dict)


def test_other_strategy_open_package_does_not_block(tmp_journal):
    """A turtle_soup open package must not silence vwap entries —
    each strategy has its own monocle scope."""
    _insert_pkg(tmp_journal, pkg_id="pkg-ts-live",
                strategy="turtle_soup", linked_trade_id=99)
    pkg = vwap.order_package({"symbol": "BTCUSDT"}, candles_df=_bullish_candles())
    assert isinstance(pkg, dict)


def test_db_read_failure_does_not_block(monkeypatch):
    """Best-effort: a journal outage must degrade to "no
    defence-in-depth" rather than "strategy stops generating
    signals". The pipeline gate stays the primary line of defence;
    raising here would silence the strategy under the same outage
    that already disables the gate."""
    class _BoomDb:
        def __init__(self, *a, **kw):
            pass

        def get_order_packages_by_strategy(self, *a, **kw):
            raise RuntimeError("simulated DB outage")

    # Force the lookup path to find a "DB" so the boom triggers.
    monkeypatch.setattr("os.path.exists", lambda p: True)
    monkeypatch.setattr("src.units.db.database.Database", _BoomDb)
    pkg = vwap.order_package({"symbol": "BTCUSDT"}, candles_df=_bullish_candles())
    assert isinstance(pkg, dict)
