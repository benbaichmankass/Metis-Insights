"""Tests for live cross-asset feature computation (S-CROSS-ASSET-PROBE D2a)."""
from __future__ import annotations

import importlib

import pytest

cal = importlib.import_module("src.runtime.cross_asset_live")


class _FakeFrame:
    """Minimal duck-typed candles frame: a `.columns` + column access + index."""

    def __init__(self, ts, closes):
        self._cols = {"time": ts, "close": closes}
        self.columns = list(self._cols)

    def __getitem__(self, key):
        class _Col:
            def __init__(self, vals):
                self._vals = vals

            def tolist(self):
                return list(self._vals)

        return _Col(self._cols[key])


def _frame(n, base, step, start_hour=0):
    ts = [f"2025-01-01T{start_hour + i:02d}:00:00Z" for i in range(n)]
    closes = [base + step * i for i in range(n)]
    return _FakeFrame(ts, closes)


@pytest.fixture
def eth_peers(monkeypatch):
    # Force a known peer config for ETHUSDT (no dependence on the on-disk file).
    cal._cfg_cache = None
    cal._cfg_mtime = None
    monkeypatch.setattr(cal, "load_peer_config",
                        lambda: {"ETHUSDT": ["BTCUSDT", "SOLUSDT"]})
    yield


def test_peers_for_reads_config(eth_peers):
    assert cal.peers_for("ETHUSDT") == ["BTCUSDT", "SOLUSDT"]
    assert cal.peers_for("BTCUSDT") == []


def test_compute_live_row_populates_xa_columns(eth_peers):
    target = _frame(40, 100.0, 1.0)
    peers = {"BTCUSDT": _frame(40, 200.0, 1.3), "SOLUSDT": _frame(40, 50.0, 0.7)}

    def fetch(sym, tf):
        return peers[sym]

    row = cal.compute_live_cross_asset_row("ETHUSDT", "1h", target, fetch)
    assert row is not None
    from ml.datasets.cross_asset_features import CROSS_ASSET_FEATURE_COLUMNS
    for c in CROSS_ASSET_FEATURE_COLUMNS:
        assert c in row and isinstance(row[c], float)
    # both peers rising → breadth 1.0 on the last bar
    assert row["xa_breadth_up"] == 1.0
    assert row["xa_peer1_beta"] != 0.0


def test_no_peers_returns_none(monkeypatch):
    monkeypatch.setattr(cal, "load_peer_config", lambda: {})
    assert cal.compute_live_cross_asset_row(
        "ETHUSDT", "1h", _frame(40, 100.0, 1.0), lambda s, t: None) is None


def test_fetch_error_is_failpermissive(eth_peers):
    def fetch(sym, tf):
        raise RuntimeError("peer feed down")

    # all peer fetches raise → no peer data → None (not a crash, not zeros)
    assert cal.compute_live_cross_asset_row(
        "ETHUSDT", "1h", _frame(40, 100.0, 1.0), fetch) is None


def test_partial_peer_still_computes(eth_peers):
    target = _frame(40, 100.0, 1.0)

    def fetch(sym, tf):
        return _frame(40, 200.0, 1.3) if sym == "BTCUSDT" else None

    row = cal.compute_live_cross_asset_row("ETHUSDT", "1h", target, fetch)
    assert row is not None
    # peer1 (BTC) present → its block is populated; peer2 (SOL) absent → zeros
    assert row["xa_peer1_beta"] != 0.0
    assert row["xa_peer2_ret"] == 0.0


def test_kill_switch_empties_config(monkeypatch):
    # The real loader short-circuits to {} under the kill switch (every head
    # then degrades to NaN xa — the feature merge becomes a no-op).
    monkeypatch.setenv("CROSS_ASSET_LIVE_DISABLED", "1")
    import src.runtime.cross_asset_live as real
    real._cfg_cache = None
    real._cfg_mtime = None
    assert real.cross_asset_live_disabled() is True
    assert real.load_peer_config() == {}


class _Wrapped:
    def __init__(self, cols):
        self._feature_columns = cols


class _Pred:
    def __init__(self, cols):
        self._wrapped = _Wrapped(cols)


def test_head_wants_cross_asset_detection():
    assert cal.head_wants_cross_asset(
        _Pred(["vol_bucket", "xa_peer1_ret", "log_return"])) is True
    assert cal.head_wants_cross_asset(
        _Pred(["vol_bucket", "log_return"])) is False
    assert cal.head_wants_cross_asset(object()) is False  # fail-permissive


def test_group_needs_cross_asset(eth_peers):
    xa_head = _Pred(["xa_peer1_ret"])
    plain = _Pred(["vol_bucket"])
    assert cal.group_needs_cross_asset("ETHUSDT", [plain, xa_head]) is True
    assert cal.group_needs_cross_asset("ETHUSDT", [plain]) is False
