"""Tests for `ml.trainers.sample_weights` (S-MLOPT-S2 / M14 Session 0.2)."""
from __future__ import annotations

import math

import pytest

from ml.trainers.sample_weights import (
    average_uniqueness_weights,
    compute_sample_weights,
    recency_weights,
)


class TestRecencyWeights:
    def test_newest_is_one_and_half_life_halves(self):
        # Daily timestamps (epoch seconds), newest last.
        day = 86400.0
        ts = [0.0, day * 30, day * 60]  # 60 days span
        w = recency_weights(ts, half_life_days=30)
        # Newest → 1.0; 30 days old → 0.5; 60 days old → 0.25.
        assert w[2] == pytest.approx(1.0)
        assert w[1] == pytest.approx(0.5)
        assert w[0] == pytest.approx(0.25)

    def test_equal_timestamps_all_one(self):
        w = recency_weights([5.0, 5.0, 5.0], half_life_days=10)
        assert all(x == pytest.approx(1.0) for x in w)

    def test_invalid_half_life(self):
        with pytest.raises(ValueError):
            recency_weights([1.0, 2.0], half_life_days=0)


class TestAverageUniqueness:
    def test_disjoint_spans_all_unique(self):
        # Non-overlapping spans → concurrency 1 everywhere → uniqueness 1.0.
        w = average_uniqueness_weights(starts=[0, 2, 4], ends=[1, 3, 5])
        assert w == pytest.approx([1.0, 1.0, 1.0])

    def test_fully_overlapping_spans_downweighted(self):
        # Two identical spans [0,1] → concurrency 2 on both bars → each 0.5.
        w = average_uniqueness_weights(starts=[0, 0], ends=[1, 1])
        assert w == pytest.approx([0.5, 0.5])

    def test_partial_overlap(self):
        # spans [0,2] and [1,3]: concurrency c0=1,c1=2,c2=2,c3=1.
        # span A=[0,2]: mean(1/1,1/2,1/2)=2/3 ; span B=[1,3]: mean(1/2,1/2,1/1)=2/3.
        w = average_uniqueness_weights(starts=[0, 1], ends=[2, 3])
        assert w == pytest.approx([2 / 3, 2 / 3])

    def test_span_end_before_start_rejected(self):
        with pytest.raises(ValueError):
            average_uniqueness_weights(starts=[2], ends=[1])


class TestComputeSampleWeights:
    def test_none_when_nothing_enabled(self):
        assert compute_sample_weights(["2026-01-01"], {}) is None
        assert compute_sample_weights(["2026-01-01"], {"uniqueness": False}) is None

    def test_recency_mean_normalised_to_one(self):
        ts = ["2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z", "2026-03-01T00:00:00Z"]
        w = compute_sample_weights(ts, {"half_life_days": 30})
        assert w is not None
        assert sum(w) / len(w) == pytest.approx(1.0)
        # Monotone: older rows weigh less than newer.
        assert w[0] < w[1] < w[2]

    def test_iso_and_epoch_both_parse(self):
        w_iso = compute_sample_weights(
            ["2026-01-01T00:00:00Z", "2026-01-31T00:00:00Z"], {"half_life_days": 30}
        )
        # 2026-01-01 epoch and 30 days later.
        import datetime as dt
        e0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp()
        w_epoch = compute_sample_weights(
            [e0, e0 + 30 * 86400], {"half_life_days": 30}
        )
        assert w_iso == pytest.approx(w_epoch)

    def test_missing_timestamp_is_fail_loud(self):
        with pytest.raises(ValueError):
            compute_sample_weights(
                ["2026-01-01T00:00:00Z", None], {"half_life_days": 30}
            )
        with pytest.raises(ValueError):
            compute_sample_weights(
                ["2026-01-01T00:00:00Z", "not-a-date"], {"half_life_days": 30}
            )

    def test_uniqueness_only_path(self):
        # 4 rows, label_horizon=1 → ranked spans [0,1],[1,2],[2,3],[3,3].
        ts = [f"2026-01-0{i}T00:00:00Z" for i in range(1, 5)]
        w = compute_sample_weights(ts, {"uniqueness": True, "label_horizon": 1})
        assert w is not None
        assert sum(w) / len(w) == pytest.approx(1.0)

    def test_recency_and_uniqueness_compose(self):
        ts = [f"2026-01-{i:02d}T00:00:00Z" for i in range(1, 9)]
        w = compute_sample_weights(
            ts, {"half_life_days": 10, "uniqueness": True, "label_horizon": 2}
        )
        assert w is not None
        assert len(w) == 8
        assert sum(w) / len(w) == pytest.approx(1.0)
        assert all(x > 0 for x in w)

    def test_unordered_input_recency_tracks_actual_dates(self):
        # Newest date in the middle of the list; it should get the largest weight.
        ts = ["2026-01-01T00:00:00Z", "2026-06-01T00:00:00Z", "2026-03-01T00:00:00Z"]
        w = compute_sample_weights(ts, {"half_life_days": 30})
        assert w is not None
        assert w[1] == max(w)  # the June row is newest
