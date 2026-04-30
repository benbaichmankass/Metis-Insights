"""Sampler unit tests — pure-function, no network, no data deps."""
from __future__ import annotations

from datetime import date

from scripts.sprint015 import sample_data as sd


def test_enumerate_buckets_count_and_weights():
    ref = date(2026, 5, 1)
    buckets = sd.enumerate_buckets(ref, total_months=60)
    # 60 calendar months back from May 2026 -> 60 buckets, each weighted.
    assert len(buckets) == 60
    weights = [w for _, w in buckets]
    # First 12 (most recent) are weight 1.0; next 24 are 0.5; last 24 are 0.25.
    assert weights[:12] == [1.00] * 12
    assert weights[12:36] == [0.50] * 24
    assert weights[36:60] == [0.25] * 24


def test_enumerate_buckets_skips_open_month():
    ref = date(2026, 5, 15)
    buckets = sd.enumerate_buckets(ref, total_months=3)
    # The first bucket must be April 2026 — never May (the open month).
    assert buckets[0][0] == sd.MonthBucket(2026, 4)
    assert buckets[1][0] == sd.MonthBucket(2026, 3)
    assert buckets[2][0] == sd.MonthBucket(2026, 2)


def test_stratified_folds_are_disjoint():
    ref = date(2026, 5, 1)
    folds = sd.stratified_folds(ref, n_folds=5, seed=42)
    seen = set()
    for fold in folds:
        for bucket in fold:
            key = (bucket.year, bucket.month)
            assert key not in seen, f"bucket {bucket} appeared in multiple folds"
            seen.add(key)
    # All 60 buckets accounted for.
    assert len(seen) == 60


def test_stratified_folds_balance_recency_bands():
    ref = date(2026, 5, 1)
    folds = sd.stratified_folds(ref, n_folds=5, seed=42)
    summary = sd.fold_summary(folds)
    # Each fold should have a comparable mix of recent / mid / old.
    # 12 recent / 24 mid / 24 old buckets across 5 folds -> ~2-3 / ~5 / ~5 per fold.
    for counts in summary:
        assert 1 <= counts["recent"] <= 4, f"recent imbalanced: {counts}"
        assert 3 <= counts["mid"] <= 7, f"mid imbalanced: {counts}"
        assert 3 <= counts["old"] <= 7, f"old imbalanced: {counts}"


def test_stratified_folds_seed_is_deterministic():
    ref = date(2026, 5, 1)
    a = sd.stratified_folds(ref, n_folds=5, seed=7)
    b = sd.stratified_folds(ref, n_folds=5, seed=7)
    assert a == b


def test_stratified_folds_n_folds_one_returns_all():
    ref = date(2026, 5, 1)
    folds = sd.stratified_folds(ref, n_folds=1, seed=0)
    assert len(folds) == 1
    assert len(folds[0]) == 60
