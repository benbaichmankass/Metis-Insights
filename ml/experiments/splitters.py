"""Train / eval split strategies (S-AI-WS4-FU).

Dispatched from `evaluator_config.split_strategy`:
  - `holdout` (default)             — stable suffix split (existing WS4 behavior).
  - `time_aware_holdout`            — sort by `time_column`, then suffix split.
  - `walk_forward`                  — rolling-origin folds; single-split form
                                      returns the last fold. Aggregated
                                      walk-forward (averaging metrics across
                                      folds) is filed as a follow-up.

All splitters preserve the row order of the input where possible.
"""
from __future__ import annotations

from typing import Any, Mapping


def split_holdout(
    rows: list[dict[str, Any]],
    config: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fraction = float(config.get("holdout_fraction", 0.2))
    if fraction <= 0 or fraction >= 1:
        raise ValueError(f"holdout_fraction must be in (0,1); got {fraction}")
    n = len(rows)
    if n < 2:
        raise ValueError(f"need at least 2 rows to split; got {n}")
    n_test = max(1, int(round(n * fraction)))
    n_test = min(n_test, n - 1)
    return rows[: n - n_test], rows[n - n_test :]


def split_time_aware_holdout(
    rows: list[dict[str, Any]],
    config: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    time_col = config.get("time_column", "created_at")
    sorted_rows = sorted(rows, key=lambda r: r.get(time_col, ""))
    return split_holdout(sorted_rows, config)


def split_walk_forward(
    rows: list[dict[str, Any]],
    config: Mapping[str, Any],
) -> list[tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
    """Rolling-origin folds.

    First train block = `min_train_fraction` of rows. Each subsequent
    fold appends one fold-sized block to the training set; the test
    set is the next fold-sized block. Last fold absorbs any
    remainder so coverage is exhaustive.
    """
    n_folds = int(config.get("n_folds", 5))
    min_frac = float(config.get("min_train_fraction", 0.5))
    time_col = config.get("time_column", "created_at")
    if n_folds < 2:
        raise ValueError(f"n_folds must be >= 2; got {n_folds}")
    if min_frac <= 0 or min_frac >= 1:
        raise ValueError(f"min_train_fraction must be in (0,1); got {min_frac}")
    sorted_rows = sorted(rows, key=lambda r: r.get(time_col, ""))
    n = len(sorted_rows)
    if n < n_folds + 1:
        raise ValueError(
            f"need at least n_folds+1 rows ({n_folds + 1}); got {n}"
        )

    n_min_train = max(1, int(round(n * min_frac)))
    remainder = n - n_min_train
    fold_size = max(1, remainder // n_folds)

    folds: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []
    for i in range(n_folds):
        train_end = n_min_train + i * fold_size
        test_end = train_end + fold_size
        if i == n_folds - 1:
            test_end = n
        if train_end >= n:
            break
        train = sorted_rows[:train_end]
        test = sorted_rows[train_end:test_end]
        if test:
            folds.append((train, test))
    return folds


def split(
    rows: list[dict[str, Any]],
    config: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Single (train, eval) split based on `split_strategy`.

    Default `holdout` matches the existing WS4 behavior.
    For `walk_forward`, returns the last fold; aggregated walk-forward
    (averaging metrics across folds) is filed as a follow-up.
    """
    strategy = config.get("split_strategy", "holdout")
    if strategy == "holdout":
        return split_holdout(rows, config)
    if strategy == "time_aware_holdout":
        return split_time_aware_holdout(rows, config)
    if strategy == "walk_forward":
        folds = split_walk_forward(rows, config)
        if not folds:
            raise ValueError("walk_forward produced no folds")
        return folds[-1]
    raise ValueError(f"unknown split_strategy {strategy!r}")
