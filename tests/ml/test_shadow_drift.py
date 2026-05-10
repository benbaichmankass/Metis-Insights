"""Tests for `ml.shadow.drift` (S-AI-WS8-PART-3)."""
from __future__ import annotations

import math
import random

import pytest

from ml.shadow.drift import (
    DriftReport,
    Summary,
    compute_drift,
    histogram,
    interpret_ks,
    interpret_psi,
    ks_statistic,
    psi,
    summarize,
)


class TestSummarize:
    def test_empty(self):
        s = summarize([])
        assert s.is_empty
        assert s == Summary(0, 0.0, 0.0, 0.0, 0.0)

    def test_single_value(self):
        s = summarize([0.5])
        assert s.count == 1
        assert s.mean == 0.5
        assert s.stdev == 0.0
        assert s.minimum == 0.5
        assert s.maximum == 0.5

    def test_multiple_values(self):
        s = summarize([0.1, 0.2, 0.3, 0.4, 0.5])
        assert s.count == 5
        assert s.mean == pytest.approx(0.3)
        # Sample stdev = sqrt(0.025) ≈ 0.1581
        assert s.stdev == pytest.approx(math.sqrt(0.025))
        assert s.minimum == 0.1
        assert s.maximum == 0.5


class TestKsStatistic:
    def test_identical_samples_returns_zero(self):
        a = [0.1, 0.2, 0.3, 0.4, 0.5]
        assert ks_statistic(a, list(a)) == 0.0

    def test_disjoint_samples_returns_one(self):
        a = [0.0, 0.0, 0.0]
        b = [1.0, 1.0, 1.0]
        assert ks_statistic(a, b) == pytest.approx(1.0)

    def test_partial_overlap(self):
        a = [0.1, 0.2, 0.3, 0.4]
        b = [0.3, 0.4, 0.5, 0.6]
        # At x=0.2: F_a=0.5, F_b=0.0 → diff 0.5. This is the max.
        assert ks_statistic(a, b) == pytest.approx(0.5)

    def test_empty_returns_zero(self):
        assert ks_statistic([], [0.1]) == 0.0
        assert ks_statistic([0.1], []) == 0.0

    def test_interpret_buckets(self):
        assert interpret_ks(0.0) == "no_change"
        assert interpret_ks(0.04) == "no_change"
        assert interpret_ks(0.06) == "minor"
        assert interpret_ks(0.15) == "moderate"
        assert interpret_ks(0.5) == "significant"


class TestHistogram:
    def test_uniform_clamped_to_range(self):
        # All values inside [0,1], 10 bins. Each value falls in exactly
        # one bin.
        scores = [0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95]
        h = histogram(scores, bins=10)
        assert h == [1] * 10

    def test_values_outside_range_clamped(self):
        scores = [-0.5, 1.5, 0.5]
        h = histogram(scores, bins=5)
        # -0.5 → bin 0 (clamped), 1.5 → bin 4 (clamped), 0.5 → bin 2
        assert h[0] == 1
        assert h[2] == 1
        assert h[4] == 1

    def test_empty_returns_zero_counts(self):
        assert histogram([], bins=4) == [0, 0, 0, 0]

    def test_invalid_bins_raises(self):
        with pytest.raises(ValueError, match="positive"):
            histogram([], bins=0)

    def test_invalid_range_raises(self):
        with pytest.raises(ValueError, match="score_max"):
            histogram([], bins=4, score_min=1.0, score_max=0.5)


class TestPsi:
    def test_identical_returns_near_zero(self):
        h = [10, 20, 30, 40]
        assert psi(h, h) == pytest.approx(0.0, abs=1e-8)

    def test_mismatched_bins_raises(self):
        with pytest.raises(ValueError):
            psi([1, 2, 3], [1, 2])

    def test_empty_reference_returns_zero(self):
        assert psi([], []) == 0.0

    def test_drift_increases_psi(self):
        ref = [50, 50, 0, 0]  # all in bottom half
        cur = [0, 0, 50, 50]  # shifted to top half
        score = psi(ref, cur)
        assert score > 0.25  # "significant" by convention

    def test_smoothing_handles_zero_bins(self):
        ref = [100, 0, 0, 0]
        cur = [0, 100, 0, 0]
        # Should not raise from log(0).
        score = psi(ref, cur)
        assert score > 0

    def test_interpret_buckets(self):
        assert interpret_psi(0.05) == "no_change"
        assert interpret_psi(0.15) == "moderate"
        assert interpret_psi(0.3) == "significant"


class TestComputeDrift:
    def test_no_change_when_distributions_identical(self):
        rng = random.Random(42)
        sample = [rng.random() for _ in range(500)]
        report = compute_drift(sample, list(sample))
        assert report.overall_verdict == "no_change"
        assert report.ks < 0.05
        assert report.psi < 0.05

    def test_significant_when_distributions_shifted(self):
        rng = random.Random(42)
        # Reference: centered around 0.2.
        ref = [max(0.0, min(1.0, rng.gauss(0.2, 0.05))) for _ in range(500)]
        # Current: centered around 0.8 — large shift.
        cur = [max(0.0, min(1.0, rng.gauss(0.8, 0.05))) for _ in range(500)]
        report = compute_drift(ref, cur)
        assert report.overall_verdict == "significant"
        assert report.ks > 0.5
        assert report.psi > 0.25

    def test_returns_drift_report_dataclass(self):
        report = compute_drift([0.1, 0.5], [0.4, 0.6])
        assert isinstance(report, DriftReport)
        assert isinstance(report.reference, Summary)
        assert isinstance(report.current, Summary)

    def test_overall_takes_worst_of_ks_and_psi(self):
        # Construct samples where KS is moderate but PSI is small.
        # Easier path: test the helper directly.
        from ml.shadow.drift import _worst
        assert _worst({"no_change", "moderate"}) == "moderate"
        assert _worst({"minor", "significant"}) == "significant"
        assert _worst({"no_change", "no_change"}) == "no_change"
