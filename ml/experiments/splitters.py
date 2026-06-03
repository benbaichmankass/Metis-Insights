"""Train / eval split strategies (S-AI-WS4-FU; purged WF-CV in S-MLOPT-S1).

Dispatched from `evaluator_config.split_strategy`:
  - `holdout` (default)             — stable suffix split (existing WS4 behavior).
  - `time_aware_holdout`            — sort by `time_column`, then suffix split.
  - `walk_forward`                  — rolling-origin folds; single-split form
                                      returns the last fold.
  - `purged_walk_forward`           — walk-forward folds with a de Prado
                                      (AFML Ch. 7) PURGE of training rows whose
                                      label window overlaps the test block, plus
                                      an EMBARGO buffer after each test block.
                                      Multi-fold: the runner iterates the fold
                                      list and aggregates metrics; the single-
                                      split dispatcher form returns the last fold.

All splitters preserve the row order of the input where possible.
"""
from __future__ import annotations

import math
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


def split_live_holdout(
    rows: list[dict[str, Any]],
    config: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Domain-shift eval (S-MLOPT-S6): train on SYNTHETIC rows, evaluate on REAL.

    The mandatory discipline for models trained on `setup_candidates`: a model
    fit on synthetic triple-barrier candidates must prove itself on a held-out
    set of REAL live trades, never on synthetic rows. This split partitions on
    the `flag_column` (default `is_live_trade`): train = rows where the flag is
    falsey (synthetic), eval = rows where it is truthy (real trades). Both sides
    are time-sorted for reproducibility. Raises if either side is empty (you
    cannot certify domain transfer without both populations).
    """
    flag_col = config.get("live_flag_column", "is_live_trade")
    time_col = config.get("time_column", "created_at")

    def _truthy(v: Any) -> bool:
        return bool(v) and v not in (0, "0", "false", "False")

    train = [r for r in rows if not _truthy(r.get(flag_col))]
    evaluation = [r for r in rows if _truthy(r.get(flag_col))]
    if not train:
        raise ValueError("live_holdout: no synthetic (train) rows")
    if not evaluation:
        raise ValueError(
            "live_holdout: no real (is_live_trade) rows to evaluate on — build "
            "the dataset with a live-trades source first"
        )
    train.sort(key=lambda r: r.get(time_col, ""))
    evaluation.sort(key=lambda r: r.get(time_col, ""))
    return train, evaluation


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


def _embargo_n_from_config(config: Mapping[str, Any], n: int) -> int:
    """Resolve the embargo size in rows from config.

    `embargo_n` (int) wins if present; otherwise `embargo_fraction`
    (a fraction of the total sample count, de Prado's convention) is
    rounded UP so a non-zero fraction always embargoes at least one row.
    """
    if "embargo_n" in config and config["embargo_n"] is not None:
        embargo_n = int(config["embargo_n"])
    else:
        frac = float(config.get("embargo_fraction", 0.0))
        if frac < 0 or frac >= 1:
            raise ValueError(
                f"embargo_fraction must be in [0,1); got {frac}"
            )
        embargo_n = math.ceil(frac * n) if frac > 0 else 0
    if embargo_n < 0:
        raise ValueError(f"embargo_n must be >= 0; got {embargo_n}")
    return embargo_n


def purge_and_embargo_indices(
    candidate_train_idx: list[int],
    test_start: int,
    test_end: int,
    label_horizon: int,
    embargo_n: int,
) -> list[int]:
    """de Prado (AFML Ch. 7) PURGE + EMBARGO over an arbitrary candidate set.

    Given a candidate set of training row indices (positions in the
    chronologically-sorted dataset) and a test block ``[test_start, test_end)``,
    drop any index that would leak the test outcome:

      - **PURGE** — a training row ``i`` *before* the test block whose forward
        label window ``[i, i + label_horizon]`` reaches into the test block
        (``i + label_horizon >= test_start``). Its label is determined by
        price action inside the test window, so training on it leaks.
      - **EMBARGO** — a training row ``i`` in the buffer ``[test_end,
        test_end + embargo_n)`` *after* the test block. Serial correlation
        means a row immediately following the test window still carries
        test-period information; the embargo gaps it out.

    Indices inside the test block itself are never training rows. Order is
    preserved. This helper is general — it handles training data on *either*
    side of the test block, so the EMBARGO (post-test) branch is meaningful
    for a future combinatorial / k-fold purged-CV. The walk-forward driver
    below feeds it a forward-only candidate set (train always precedes test),
    where the post-test branch is vacuous; that driver realizes the embargo
    as an additional pre-test buffer instead (see
    :func:`split_purged_walk_forward`).
    """
    if label_horizon < 0:
        raise ValueError(f"label_horizon must be >= 0; got {label_horizon}")
    kept: list[int] = []
    for i in candidate_train_idx:
        if test_start <= i < test_end:
            continue  # the test block never trains
        if i < test_start and (i + label_horizon) >= test_start:
            continue  # PURGE: label window overlaps the test block
        if test_end <= i < test_end + embargo_n:
            continue  # EMBARGO: serial-correlation buffer after the test block
        kept.append(i)
    return kept


def split_purged_walk_forward(
    rows: list[dict[str, Any]],
    config: Mapping[str, Any],
) -> list[tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
    """Purged & embargoed walk-forward folds (de Prado, AFML Ch. 7).

    Same rolling/expanding-origin fold geometry as :func:`split_walk_forward`
    (first train block ≈ ``min_train_fraction`` of rows; each fold's test is
    the next ``fold_size`` block; the last fold absorbs the remainder), but
    every fold's training set is purged + embargoed against that fold's test
    block:

      - **PURGE** drops training rows whose forward label window overlaps the
        test block (:func:`purge_and_embargo_indices`).
      - **EMBARGO** — because the folds are forward-only (train always
        precedes test), de Prado's *post-test* embargo collapses into a
        *pre-test* buffer: training never follows a test block, so the
        serial-correlation gap the embargo enforces is realized as an extra
        ``embargo_n`` rows dropped immediately before the purge boundary.

    The net effect is a clean gap of ``label_horizon + embargo_n`` rows
    between the last training row and the first test row, so no future-dated
    label can leak backward into any training fold.

    Config keys (under ``evaluator_config``):
      - ``n_folds`` (default 5), ``min_train_fraction`` (default 0.5),
        ``time_column`` (default ``created_at``) — as for ``walk_forward``.
      - ``label_horizon`` (int rows, default 1) — how many rows forward each
        sample's label spans; the PURGE width.
      - ``embargo_fraction`` (float in [0,1), default 0.0) or ``embargo_n``
        (int rows) — the EMBARGO width.

    Folds whose training set is emptied by the gap (or whose test block is
    empty) are skipped, so a too-aggressive gap yields fewer folds rather
    than leaking.
    """
    n_folds = int(config.get("n_folds", 5))
    min_frac = float(config.get("min_train_fraction", 0.5))
    time_col = config.get("time_column", "created_at")
    label_horizon = int(config.get("label_horizon", 1))
    if n_folds < 2:
        raise ValueError(f"n_folds must be >= 2; got {n_folds}")
    if min_frac <= 0 or min_frac >= 1:
        raise ValueError(f"min_train_fraction must be in (0,1); got {min_frac}")
    if label_horizon < 0:
        raise ValueError(f"label_horizon must be >= 0; got {label_horizon}")

    sorted_rows = sorted(rows, key=lambda r: r.get(time_col, ""))
    n = len(sorted_rows)
    if n < n_folds + 1:
        raise ValueError(f"need at least n_folds+1 rows ({n_folds + 1}); got {n}")

    embargo_n = _embargo_n_from_config(config, n)

    n_min_train = max(1, int(round(n * min_frac)))
    remainder = n - n_min_train
    fold_size = max(1, remainder // n_folds)

    folds: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []
    for i in range(n_folds):
        test_start = n_min_train + i * fold_size
        test_end = test_start + fold_size
        if i == n_folds - 1:
            test_end = n
        if test_start >= n:
            break
        # Forward-only candidate training set: everything before the test block.
        candidate = list(range(0, test_start))
        train_idx = purge_and_embargo_indices(
            candidate, test_start, test_end, label_horizon, embargo_n
        )
        # Forward-only embargo buffer: drop the `embargo_n` rows adjacent to
        # the purge boundary so the gap before the test block is
        # `label_horizon + embargo_n` (see docstring).
        if embargo_n:
            boundary = test_start - label_horizon - embargo_n
            train_idx = [j for j in train_idx if j < boundary]
        train = [sorted_rows[j] for j in train_idx]
        test = sorted_rows[test_start:test_end]
        if train and test:
            folds.append((train, test))
    return folds


def split(
    rows: list[dict[str, Any]],
    config: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Single (train, eval) split based on `split_strategy`.

    Default `holdout` matches the existing WS4 behavior.
    For `walk_forward` / `purged_walk_forward`, returns the last fold;
    the multi-fold (averaged-across-folds) path lives in the runner.
    """
    strategy = config.get("split_strategy", "holdout")
    if strategy == "holdout":
        return split_holdout(rows, config)
    if strategy == "time_aware_holdout":
        return split_time_aware_holdout(rows, config)
    if strategy == "live_holdout":
        return split_live_holdout(rows, config)
    if strategy == "walk_forward":
        folds = split_walk_forward(rows, config)
        if not folds:
            raise ValueError("walk_forward produced no folds")
        return folds[-1]
    if strategy == "purged_walk_forward":
        folds = split_purged_walk_forward(rows, config)
        if not folds:
            raise ValueError("purged_walk_forward produced no folds")
        return folds[-1]
    raise ValueError(f"unknown split_strategy {strategy!r}")


# Split strategies that produce multiple (train, test) folds the runner must
# iterate + aggregate, rather than a single suffix split.
MULTI_FOLD_STRATEGIES: frozenset[str] = frozenset({"purged_walk_forward"})


def iter_folds(
    rows: list[dict[str, Any]],
    config: Mapping[str, Any],
) -> list[tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
    """Return the full fold list for a multi-fold `split_strategy`.

    Only `purged_walk_forward` is wired here (opt-in CV). `walk_forward`
    keeps its historical single-split-via-`split()` behavior so the default
    eval path is unchanged.
    """
    strategy = config.get("split_strategy", "holdout")
    if strategy == "purged_walk_forward":
        folds = split_purged_walk_forward(rows, config)
        if not folds:
            raise ValueError("purged_walk_forward produced no folds")
        return folds
    raise ValueError(f"{strategy!r} is not a multi-fold split strategy")
