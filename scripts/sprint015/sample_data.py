"""S-015 month-bucket sampler with recency weights.

The S-015 prompt fixes the weight schedule:

* 0–12 months from ``ref_date``: weight ``1.00``
* 13–36 months: weight ``0.50``
* 37–60 months: weight ``0.25``

Stratified shuffle: every fold contains a recency mix proportional to
the weights (so a fold isn't accidentally all-ancient or all-recent).
Months are unique across folds — no overlap, no leakage.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Sequence, Tuple

# (months_back_inclusive_lower, months_back_exclusive_upper, weight)
WEIGHT_BANDS: List[Tuple[int, int, float]] = [
    (0, 12, 1.00),
    (12, 36, 0.50),
    (36, 60, 0.25),
]


@dataclass(frozen=True)
class MonthBucket:
    year: int
    month: int

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


def _months_back(ref: date, n: int) -> MonthBucket:
    total = ref.year * 12 + (ref.month - 1) - n
    return MonthBucket(year=total // 12, month=total % 12 + 1)


def enumerate_buckets(ref_date: date, total_months: int = 60) -> List[Tuple[MonthBucket, float]]:
    """Return ``[(bucket, weight)]`` for the *total_months* buckets ending
    at ``ref_date`` (inclusive of the *previous* month — never the open
    month, which has incomplete data)."""
    buckets: List[Tuple[MonthBucket, float]] = []
    for offset in range(1, total_months + 1):
        bucket = _months_back(ref_date, offset)
        weight = 0.0
        for lo, hi, w in WEIGHT_BANDS:
            if lo < offset <= hi:
                weight = w
                break
        if weight > 0:
            buckets.append((bucket, weight))
    return buckets


def stratified_folds(
    ref_date: date,
    n_folds: int = 5,
    *,
    total_months: int = 60,
    seed: int = 0,
) -> List[List[MonthBucket]]:
    """Return ``n_folds`` non-overlapping lists of month buckets.

    Each fold is roughly the same total weight; each fold draws from
    every weight band in proportion. Months are partitioned across
    folds — no leakage between folds.
    """
    if n_folds < 1:
        raise ValueError("n_folds must be >= 1")
    rng = random.Random(seed)

    by_band: Dict[float, List[MonthBucket]] = {w: [] for _, _, w in WEIGHT_BANDS}
    for bucket, weight in enumerate_buckets(ref_date, total_months=total_months):
        by_band.setdefault(weight, []).append(bucket)

    for band_buckets in by_band.values():
        rng.shuffle(band_buckets)

    folds: List[List[MonthBucket]] = [[] for _ in range(n_folds)]
    for band_buckets in by_band.values():
        for idx, bucket in enumerate(band_buckets):
            folds[idx % n_folds].append(bucket)
    return folds


def fold_summary(folds: Sequence[Sequence[MonthBucket]]) -> List[Dict[str, int]]:
    """Diagnostic — count buckets per fold per recency band. Used by the
    sampler tests to verify the stratification is balanced."""
    out: List[Dict[str, int]] = []
    today = date.today()
    for fold in folds:
        counts = {"recent": 0, "mid": 0, "old": 0}
        for b in fold:
            months_back = (today.year - b.year) * 12 + (today.month - b.month)
            if months_back <= 12:
                counts["recent"] += 1
            elif months_back <= 36:
                counts["mid"] += 1
            else:
                counts["old"] += 1
        out.append(counts)
    return out
