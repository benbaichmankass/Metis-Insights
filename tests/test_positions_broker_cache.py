"""Broker open-position cache: warm-TTL serve + stale-serve-on-failure.

Guards the 2026-07-14 caching hardening (android-live-trades-blank). The
``/api/bot/positions`` uPnL enrichment reads the broker's open positions per
account; that read was cached only 10s while every consumer (dashboard +
Android app + /ws/market) polls on a ~30s cadence, so the cache was cold on
every poll and each poll re-hit the IB gateway / Bybit. The hardening:

  * a longer, env-tunable TTL (default 30s) keeps the cache warm across the
    poll so the gateway is hit at most ~once per TTL per account, and
  * on a read failure the last GOOD list is served for a bounded window so a
    transient gateway wedge neither blanks broker-truth uPnL nor re-hits the
    wedged gateway more than once per TTL.
"""
from __future__ import annotations

import pytest

from src.web.api.routers import dashboard as dashboard_router


class _Clock:
    """Controllable monotonic clock swapped in for the module's ``time``."""

    def __init__(self) -> None:
        self.t = 1000.0

    def monotonic(self) -> float:
        return self.t

    def time(self) -> float:  # dashboard.time.time() exists elsewhere
        return self.t


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(dashboard_router, "time", c)
    return c


@pytest.fixture(autouse=True)
def _clear_caches():
    dashboard_router._BROKER_POSITIONS_CACHE.clear()
    dashboard_router._BROKER_POSITIONS_LAST_GOOD.clear()
    yield
    dashboard_router._BROKER_POSITIONS_CACHE.clear()
    dashboard_router._BROKER_POSITIONS_LAST_GOOD.clear()


def _install_broker(monkeypatch: pytest.MonkeyPatch, results: list):
    """Stub the two lazily-imported broker helpers. ``results`` is yielded one
    per call to ``account_open_positions`` (the last entry repeats); returns a
    dict whose ``n`` counts how many broker reads actually happened."""
    calls = {"n": 0}

    def fake_open(cfg):
        i = calls["n"]
        calls["n"] += 1
        return results[min(i, len(results) - 1)]

    monkeypatch.setattr(
        "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
        lambda: {"acct": {"id": "acct"}},
    )
    monkeypatch.setattr(
        "src.units.accounts.clients.account_open_positions", fake_open
    )
    return calls


def test_warm_ttl_serves_cache_without_refetch(clock: _Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_broker(monkeypatch, [[{"symbol": "X", "side": "long"}]])
    monkeypatch.setenv("POSITIONS_CACHE_TTL_S", "30")

    first = dashboard_router._broker_positions_for("acct")
    assert first == [{"symbol": "X", "side": "long"}]
    assert calls["n"] == 1

    # 20s later — inside the 30s TTL — served from cache, no new broker read.
    clock.t += 20
    assert dashboard_router._broker_positions_for("acct") == first
    assert calls["n"] == 1

    # Past the TTL — a fresh broker read happens.
    clock.t += 12
    dashboard_router._broker_positions_for("acct")
    assert calls["n"] == 2


def test_failure_serves_last_good_within_window(clock: _Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    good = [{"symbol": "X", "side": "long"}]
    _install_broker(monkeypatch, [good, None, None])
    monkeypatch.setenv("POSITIONS_CACHE_TTL_S", "30")
    monkeypatch.setenv("POSITIONS_CACHE_STALE_OK_S", "120")

    assert dashboard_router._broker_positions_for("acct") == good

    # TTL expires, the next read FAILS (None) → serve the last good list.
    clock.t += 31
    assert dashboard_router._broker_positions_for("acct") == good

    # Past the stale window with continued failure → honest None.
    clock.t += 200
    assert dashboard_router._broker_positions_for("acct") is None


def test_stale_serve_can_be_disabled(clock: _Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    good = [{"symbol": "X"}]
    _install_broker(monkeypatch, [good, None])
    monkeypatch.setenv("POSITIONS_CACHE_TTL_S", "30")
    monkeypatch.setenv("POSITIONS_CACHE_STALE_OK_S", "0")  # disabled

    assert dashboard_router._broker_positions_for("acct") == good
    clock.t += 31
    assert dashboard_router._broker_positions_for("acct") is None


def test_bad_ttl_env_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSITIONS_CACHE_TTL_S", "not-a-number")
    assert dashboard_router._positions_ttl_s() == dashboard_router._POSITIONS_TTL_DEFAULT_S
    monkeypatch.setenv("POSITIONS_CACHE_TTL_S", "-5")
    assert dashboard_router._positions_ttl_s() == dashboard_router._POSITIONS_TTL_DEFAULT_S
