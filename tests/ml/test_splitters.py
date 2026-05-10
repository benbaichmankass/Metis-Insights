"""Tests for `ml.experiments.splitters` (S-AI-WS4-FU)."""
from __future__ import annotations

import pytest

from ml.experiments.splitters import (
    split,
    split_holdout,
    split_time_aware_holdout,
    split_walk_forward,
)


def _row(i, **overrides):
    base = {"i": i, "created_at": f"2026-05-{i:02d}T00:00:00Z"}
    base.update(overrides)
    return base


class TestSplitHoldout:
    def test_default_fraction(self):
        rows = [_row(i) for i in range(1, 11)]
        train, test = split_holdout(rows, {})
        assert len(train) == 8
        assert len(test) == 2
        assert train[0]["i"] == 1 and test[-1]["i"] == 10

    def test_custom_fraction(self):
        rows = [_row(i) for i in range(1, 11)]
        train, test = split_holdout(rows, {"holdout_fraction": 0.5})
        assert len(train) == 5
        assert len(test) == 5

    def test_invalid_fraction(self):
        rows = [_row(i) for i in range(5)]
        with pytest.raises(ValueError):
            split_holdout(rows, {"holdout_fraction": 0.0})
        with pytest.raises(ValueError):
            split_holdout(rows, {"holdout_fraction": 1.0})

    def test_too_few_rows(self):
        with pytest.raises(ValueError):
            split_holdout([], {})
        with pytest.raises(ValueError):
            split_holdout([_row(1)], {})

    def test_at_least_one_in_each_split(self):
        # 2 rows with 0.01 fraction → still 1+1
        rows = [_row(1), _row(2)]
        train, test = split_holdout(rows, {"holdout_fraction": 0.01})
        assert len(train) == 1 and len(test) == 1


class TestSplitTimeAwareHoldout:
    def test_sorts_before_split(self):
        rows = [
            _row(3, created_at="2026-05-03"),
            _row(1, created_at="2026-05-01"),
            _row(2, created_at="2026-05-02"),
            _row(4, created_at="2026-05-04"),
        ]
        train, test = split_time_aware_holdout(rows, {"holdout_fraction": 0.25})
        # last in chronological order is i=4
        assert test[0]["i"] == 4
        # train preserves chronological order 1,2,3
        assert [r["i"] for r in train] == [1, 2, 3]

    def test_uses_custom_time_column(self):
        rows = [
            {"i": 1, "ts": "b"},
            {"i": 2, "ts": "a"},
            {"i": 3, "ts": "c"},
        ]
        train, test = split_time_aware_holdout(
            rows, {"holdout_fraction": 0.34, "time_column": "ts"}
        )
        assert test[0]["i"] == 3  # ts="c" is last


class TestSplitWalkForward:
    def test_basic(self):
        rows = [_row(i) for i in range(1, 11)]
        folds = split_walk_forward(
            rows, {"n_folds": 5, "min_train_fraction": 0.5}
        )
        assert len(folds) <= 5
        # First fold: train of 5 rows, test of 1 row
        first_train, first_test = folds[0]
        assert len(first_train) == 5
        assert len(first_test) == 1
        assert first_train[-1]["i"] == 5
        assert first_test[0]["i"] == 6
        # Last fold's test absorbs remainder — ends at row 10
        _, last_test = folds[-1]
        assert last_test[-1]["i"] == 10

    def test_invalid_n_folds(self):
        rows = [_row(i) for i in range(20)]
        with pytest.raises(ValueError):
            split_walk_forward(rows, {"n_folds": 1})

    def test_invalid_min_fraction(self):
        rows = [_row(i) for i in range(20)]
        with pytest.raises(ValueError):
            split_walk_forward(rows, {"n_folds": 3, "min_train_fraction": 0})

    def test_too_few_rows(self):
        with pytest.raises(ValueError):
            split_walk_forward([_row(1), _row(2)], {"n_folds": 3})


class TestSplitDispatcher:
    def test_default_is_holdout(self):
        rows = [_row(i) for i in range(1, 11)]
        train, test = split(rows, {})
        assert len(train) == 8 and len(test) == 2

    def test_time_aware(self):
        rows = [_row(i, created_at=f"day-{20-i:02d}") for i in range(1, 11)]
        train, test = split(rows, {"split_strategy": "time_aware_holdout"})
        # sorted by created_at → i=10 is first (day-10), i=1 is last (day-19)
        # but we only verify that test is the chronological tail (last 20%)
        assert len(test) == 2

    def test_walk_forward_returns_last_fold(self):
        rows = [_row(i) for i in range(1, 21)]
        train, test = split(
            rows,
            {"split_strategy": "walk_forward", "n_folds": 4, "min_train_fraction": 0.5},
        )
        # Last fold should have the most training data + tail test
        assert len(train) >= 10
        assert len(test) >= 1
        assert test[-1]["i"] == 20

    def test_unknown_strategy(self):
        rows = [_row(i) for i in range(5)]
        with pytest.raises(ValueError):
            split(rows, {"split_strategy": "made-up"})
