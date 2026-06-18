"""Tests for the pure cross-asset feature transforms (S-CROSS-ASSET-PROBE)."""
from __future__ import annotations

import math

from ml.datasets.cross_asset_features import (
    CROSS_ASSET_FEATURE_COLUMNS,
    N_PEER_SLOTS,
    compute_cross_asset_feature_rows,
    log_returns,
    rel_strength,
    rolling_beta,
    rolling_vol,
)


class TestPureHelpers:
    def test_log_returns_first_is_none_and_positive_guard(self):
        lr = log_returns([100.0, 110.0, 0.0, 121.0])
        assert lr[0] is None
        assert lr[1] is not None and math.isclose(lr[1], math.log(110.0 / 100.0))
        assert lr[2] is None  # close == 0 → guarded
        assert lr[3] is None  # prev close was 0 → guarded

    def test_rolling_vol_needs_two_points(self):
        assert rolling_vol([0.1]) is None
        assert rolling_vol([0.1, -0.1, 0.2]) is not None

    def test_rel_strength_sign(self):
        # target up more than peer → positive relative strength.
        assert rel_strength([0.02, 0.03], [0.01, 0.01]) > 0
        assert rel_strength([0.0], []) is None

    def test_rolling_beta_perfect_correlation_is_one(self):
        peer = [0.01, -0.02, 0.03, -0.01, 0.02]
        tgt = [2 * x for x in peer]  # beta should be ~2
        b = rolling_beta(tgt, peer)
        assert b is not None and math.isclose(b, 2.0, rel_tol=1e-9)

    def test_rolling_beta_zero_variance_is_none(self):
        assert rolling_beta([0.0] * 6, [0.0] * 6) is None

    def test_rolling_beta_too_few_pairs_is_none(self):
        assert rolling_beta([0.1, 0.2], [0.1, 0.2], min_n=5) is None


class TestComputeCrossAssetFeatureRows:
    def _bars(self, closes, start_hour=0):
        return [
            {"ts": f"2025-01-01T{start_hour + i:02d}:00:00Z",
             "symbol": "X", "close": c}
            for i, c in enumerate(closes)
        ]

    def test_emits_all_columns_keyed_at_target_ts(self):
        tgt = self._bars([100.0 + i for i in range(30)])
        p1 = self._bars([200.0 + i for i in range(30)])
        p2 = self._bars([50.0 + i * 0.5 for i in range(30)])
        rows = compute_cross_asset_feature_rows(tgt, [p1, p2], vol_window_n=5,
                                                beta_window_n=10)
        assert len(rows) == len(tgt)
        assert rows[0]["ts"] == tgt[0]["ts"]
        for r in rows:
            for c in CROSS_ASSET_FEATURE_COLUMNS:
                assert c in r and isinstance(r[c], float)

    def test_absent_peer_slot_emits_zero(self):
        tgt = self._bars([100.0 + i for i in range(20)])
        p1 = self._bars([200.0 + i for i in range(20)])
        rows = compute_cross_asset_feature_rows(tgt, [p1], vol_window_n=5)
        # peer2 slot absent → all its columns are 0.0 everywhere.
        for r in rows:
            assert r["xa_peer2_ret"] == 0.0
            assert r["xa_peer2_vol"] == 0.0
            assert r["xa_peer2_beta"] == 0.0

    def test_breadth_up_fraction(self):
        # both peers rising → breadth 1.0 once both have a return.
        tgt = self._bars([100.0 + i for i in range(10)])
        p1 = self._bars([200.0 + i for i in range(10)])
        p2 = self._bars([50.0 + i for i in range(10)])
        rows = compute_cross_asset_feature_rows(tgt, [p1, p2], vol_window_n=3)
        assert rows[-1]["xa_breadth_up"] == 1.0
        # a falling peer2 drops breadth to 0.5.
        p2_down = self._bars([50.0 - i for i in range(10)])
        rows2 = compute_cross_asset_feature_rows(tgt, [p1, p2_down], vol_window_n=3)
        assert rows2[-1]["xa_breadth_up"] == 0.5

    def test_peer_ret_lag1_is_previous_bar(self):
        tgt = self._bars([100.0, 101.0, 102.0, 103.0, 104.0])
        p1 = self._bars([200.0, 210.0, 220.0, 230.0, 240.0])
        rows = compute_cross_asset_feature_rows(tgt, [p1], vol_window_n=2)
        # bar i's lag1 equals bar i-1's contemporaneous ret.
        assert math.isclose(rows[3]["xa_peer1_ret_lag1"], rows[2]["xa_peer1_ret"])

    def test_misaligned_peer_grid_maps_by_ts(self):
        # peer missing an early ts → that target bar sees 0.0 for the peer ret
        # but later aligned bars are populated (exact ts→ts map).
        tgt = self._bars([100.0 + i for i in range(6)])
        p1 = self._bars([200.0 + i for i in range(6)])[2:]  # drop first two ts
        rows = compute_cross_asset_feature_rows(tgt, [p1], vol_window_n=2)
        assert rows[0]["xa_peer1_ret"] == 0.0  # no peer bar at this ts
        assert rows[-1]["xa_peer1_ret"] != 0.0  # aligned later

    def test_empty_target(self):
        assert compute_cross_asset_feature_rows([], [[]]) == []

    def test_column_count_matches_slots(self):
        # 6 per-peer features × N_PEER_SLOTS + 1 breadth column.
        assert len(CROSS_ASSET_FEATURE_COLUMNS) == 6 * N_PEER_SLOTS + 1
