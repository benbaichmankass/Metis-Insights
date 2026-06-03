"""Tests for `ml.experiments.splitters` (S-AI-WS4-FU)."""
from __future__ import annotations

import pytest

from ml.experiments.splitters import (
    iter_folds,
    purge_and_embargo_indices,
    split,
    split_holdout,
    split_purged_walk_forward,
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


class TestPurgeAndEmbargoIndices:
    """de Prado purge + embargo on a general (two-sided) candidate set."""

    def test_purge_before_and_embargo_after(self):
        # Candidate spans both sides of the test block [8, 12).
        candidate = list(range(20))
        kept = purge_and_embargo_indices(
            candidate, test_start=8, test_end=12, label_horizon=2, embargo_n=3
        )
        # PURGE: rows 6,7 (label window [i,i+2] reaches 8) dropped.
        # TEST block 8..11 never trains.
        # EMBARGO: rows 12,13,14 (within [12, 12+3)) dropped.
        assert kept == [0, 1, 2, 3, 4, 5, 15, 16, 17, 18, 19]

    def test_no_embargo_no_purge_only_drops_test_block(self):
        candidate = list(range(10))
        kept = purge_and_embargo_indices(
            candidate, test_start=4, test_end=6, label_horizon=0, embargo_n=0
        )
        # Only the test block itself is removed.
        assert kept == [0, 1, 2, 3, 6, 7, 8, 9]

    def test_negative_label_horizon_rejected(self):
        with pytest.raises(ValueError):
            purge_and_embargo_indices([0, 1], 0, 1, label_horizon=-1, embargo_n=0)


class TestSplitPurgedWalkForward:
    LABEL_HORIZON = 2
    EMBARGO_N = 3

    def _folds(self):
        rows = [_row(i) for i in range(1, 31)]  # i == chronological position + 1
        return rows, split_purged_walk_forward(
            rows,
            {
                "n_folds": 5,
                "min_train_fraction": 0.5,
                "label_horizon": self.LABEL_HORIZON,
                "embargo_n": self.EMBARGO_N,
            },
        )

    def test_boundaries_pinned(self):
        _, folds = self._folds()
        assert len(folds) == 5
        # Fold 0: 30 rows, n_min_train=15, fold_size=3, test=[15,18) → i 16..18.
        # train = positions [0,10) after a 2-row purge + 3-row embargo → i 1..10.
        first_train, first_test = folds[0]
        assert [r["i"] for r in first_train] == list(range(1, 11))
        assert [r["i"] for r in first_test] == [16, 17, 18]
        # Last fold absorbs the remainder; test ends at the final row.
        last_train, last_test = folds[-1]
        assert [r["i"] for r in last_test] == [28, 29, 30]
        assert [r["i"] for r in last_train] == list(range(1, 23))

    def test_no_future_dated_row_leaks_into_any_train_fold(self):
        """The core guarantee: every training row is strictly before its test
        block, with a purge + embargo gap of label_horizon + embargo_n rows.
        Pinned both on chronological position and on the time column itself."""
        _, folds = self._folds()
        gap = self.LABEL_HORIZON + self.EMBARGO_N
        for train, test in folds:
            max_train_i = max(r["i"] for r in train)
            min_test_i = min(r["i"] for r in test)
            # No future-dated row in train: strict temporal ordering.
            assert max_train_i < min_test_i
            # Purge + embargo gap is at least label_horizon + embargo_n rows.
            assert min_test_i - max_train_i - 1 >= gap
            # Time-column check: every train timestamp precedes every test one.
            max_train_ts = max(r["created_at"] for r in train)
            min_test_ts = min(r["created_at"] for r in test)
            assert max_train_ts < min_test_ts

    def test_embargo_zero_falls_back_to_pure_purge_gap(self):
        rows = [_row(i) for i in range(1, 31)]
        folds = split_purged_walk_forward(
            rows, {"n_folds": 5, "min_train_fraction": 0.5, "label_horizon": 2}
        )
        for train, test in folds:
            max_train_i = max(r["i"] for r in train)
            min_test_i = min(r["i"] for r in test)
            assert min_test_i - max_train_i - 1 >= 2  # only the purge gap

    def test_embargo_fraction_rounds_up(self):
        # embargo_fraction=0.05 over 40 rows → ceil(2.0)=2.
        rows = [_row(i) for i in range(1, 41)]
        folds = split_purged_walk_forward(
            rows,
            {
                "n_folds": 4,
                "min_train_fraction": 0.5,
                "label_horizon": 1,
                "embargo_fraction": 0.05,
            },
        )
        for train, test in folds:
            max_train_i = max(r["i"] for r in train)
            min_test_i = min(r["i"] for r in test)
            assert min_test_i - max_train_i - 1 >= 1 + 2

    def test_sorts_unordered_input(self):
        rows = [_row(i) for i in range(30, 0, -1)]  # reverse-chronological
        folds = split_purged_walk_forward(
            rows, {"n_folds": 5, "min_train_fraction": 0.5, "label_horizon": 1}
        )
        for train, test in folds:
            assert max(r["i"] for r in train) < min(r["i"] for r in test)

    def test_invalid_params_rejected(self):
        rows = [_row(i) for i in range(1, 21)]
        with pytest.raises(ValueError):
            split_purged_walk_forward(rows, {"n_folds": 1})
        with pytest.raises(ValueError):
            split_purged_walk_forward(rows, {"n_folds": 3, "min_train_fraction": 0})
        with pytest.raises(ValueError):
            split_purged_walk_forward(rows, {"n_folds": 3, "label_horizon": -1})
        with pytest.raises(ValueError):
            split_purged_walk_forward(
                rows, {"n_folds": 3, "embargo_fraction": 1.0}
            )

    def test_too_few_rows_rejected(self):
        with pytest.raises(ValueError):
            split_purged_walk_forward([_row(1), _row(2)], {"n_folds": 3})


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

    def test_purged_walk_forward_returns_last_fold(self):
        rows = [_row(i) for i in range(1, 31)]
        train, test = split(
            rows,
            {
                "split_strategy": "purged_walk_forward",
                "n_folds": 5,
                "min_train_fraction": 0.5,
                "label_horizon": 2,
                "embargo_n": 3,
            },
        )
        # Last fold: test absorbs the remainder, ends at the final row.
        assert test[-1]["i"] == 30
        # Gap preserved even through the single-split dispatcher form.
        assert max(r["i"] for r in train) < min(r["i"] for r in test)

    def test_unknown_strategy(self):
        rows = [_row(i) for i in range(5)]
        with pytest.raises(ValueError):
            split(rows, {"split_strategy": "made-up"})


class TestIterFolds:
    def test_returns_full_fold_list_for_purged_wf(self):
        rows = [_row(i) for i in range(1, 31)]
        folds = iter_folds(
            rows,
            {
                "split_strategy": "purged_walk_forward",
                "n_folds": 5,
                "min_train_fraction": 0.5,
                "label_horizon": 2,
                "embargo_n": 3,
            },
        )
        assert len(folds) == 5

    def test_rejects_single_split_strategy(self):
        rows = [_row(i) for i in range(1, 11)]
        with pytest.raises(ValueError):
            iter_folds(rows, {"split_strategy": "holdout"})
        with pytest.raises(ValueError):
            iter_folds(rows, {"split_strategy": "time_aware_holdout"})
