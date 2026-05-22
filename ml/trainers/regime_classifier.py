"""Regime classifier baseline trainer (S-AI-WS5-B-PART-2 PR 2B).

Simplest 3-class baseline: per-bucket modal class. For each
distinct value of `feature_column` (typically `vol_bucket`),
compute the empirical class-probability distribution over the
training set; fall back to the training-set marginal distribution
for unseen feature values at evaluation time.

Pairs with `ml.predictors.per_bucket_multiclass.PerBucketMulticlassPredictor`.

Leakage discipline (S-AI-WS5-B-PART-2): the trainer raises
`ValueError` if `feature_column` references one of the forward-
looking columns or the label column itself. The forbidden list is
the same one documented on the `market_features` family.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..predictors.per_bucket_multiclass import PerBucketMulticlassPredictor
from .base import Trainer

# Columns that would constitute label leakage if used as a feature
# against the regime label. Pinned here so a typo in the manifest
# fails at fit-time rather than silently overfitting.
_LEAKING_FEATURES: frozenset[str] = frozenset(
    {
        "regime_label",
        "forward_log_return",
        "forward_log_return_vol",
    }
)


def _bucket_sort_key(label: str) -> tuple[int, str]:
    """Order bucket labels by their `vol_b{i}` numeric suffix.

    Falls back to lexicographic ordering for labels that don't match the
    `vol_b{int}` convention so a non-standard bucket scheme still yields
    a deterministic order.
    """
    if label.startswith("vol_b") and label[5:].isdigit():
        return (int(label[5:]), label)
    return (1_000_000, label)


def _reconstruct_vol_bucket_edges(
    vol_by_bucket: Mapping[str, list[float]],
) -> tuple[list[float], list[str]]:
    """Recover the quantile cut points from per-bucket raw-vol samples.

    `market_features` assigns a vol to bucket ``i`` where ``i`` is the
    first index with ``vol <= boundaries[i]`` (saturating to the last).
    The largest sample in bucket ``i`` is therefore exactly the original
    ``boundaries[i]``, so the edges are recoverable from the training
    rows alone — no need to plumb the dataset-build boundaries through
    the trainer. Returns ``(edges, labels)`` where ``edges`` has
    ``len(labels) - 1`` entries (empty when fewer than two buckets carry
    samples, which the live path treats as un-bucketable).
    """
    labels = sorted(
        (b for b, vols in vol_by_bucket.items() if vols),
        key=_bucket_sort_key,
    )
    if len(labels) < 2:
        return [], labels
    edges = [max(vol_by_bucket[labels[i]]) for i in range(len(labels) - 1)]
    return edges, labels


class RegimeClassifierTrainer(Trainer):
    PREDICTOR_CLASS = PerBucketMulticlassPredictor

    def fit(
        self,
        rows: Iterable[Mapping[str, Any]],
        config: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        target = config.get("target_column", "regime_label")
        feature = config.get("feature_column", "vol_bucket")
        unknown_bucket = str(config.get("unknown_bucket", ""))
        class_labels_cfg = config.get(
            "class_labels", ["trend", "range", "volatile"]
        )
        class_labels = tuple(str(c) for c in class_labels_cfg)
        # Live-scoring spec (2026-05-22): freeze the raw-vol column name,
        # rolling window, and per-bucket raw-vol samples so the live
        # shadow path can map a tick's rolling vol to the same bucket the
        # model was trained on. Without this the live feature row carries
        # no `vol_bucket`, the predictor falls to its marginal, and every
        # tick scores the same constant. See src/runtime/regime_shadow.py.
        vol_feature_column = str(
            config.get("vol_feature_column", "rolling_log_return_vol")
        )
        vol_window_n = int(config.get("vol_window_n", 20))
        vol_by_bucket: dict[str, list[float]] = {}
        spec_symbol = ""
        spec_timeframe = ""

        if feature in _LEAKING_FEATURES:
            raise ValueError(
                f"feature_column={feature!r} would leak the label; "
                f"forbidden against market_features: "
                f"{sorted(_LEAKING_FEATURES)}"
            )
        if target == feature:
            raise ValueError(
                f"feature_column == target_column ({target!r}); trivial leakage"
            )
        if not class_labels:
            raise ValueError("class_labels must list at least one class")

        # Per-bucket class counts + marginal counts.
        per_bucket_counts: dict[str, dict[str, int]] = {}
        marginal_counts: dict[str, int] = {label: 0 for label in class_labels}
        n_train = 0

        for row in rows:
            target_value = row.get(target)
            if target_value is None:
                continue
            label = str(target_value).strip()
            if not label:
                continue
            if label not in marginal_counts:
                # Out-of-vocab class in training — treat as a new class
                # but warn via state metadata so this doesn't silently
                # creep in. Add it to class_labels for downstream
                # consumers.
                marginal_counts[label] = 0
                class_labels = (*class_labels, label)
            key_value = row.get(feature)
            key = (
                str(key_value).strip()
                if key_value is not None and str(key_value).strip()
                else unknown_bucket
            )
            bucket_counts = per_bucket_counts.setdefault(
                key, {c: 0 for c in marginal_counts}
            )
            # If marginal_counts has been extended by an OOV class,
            # bucket_counts may not have that class yet — ensure all
            # known classes exist as keys.
            for c in marginal_counts:
                bucket_counts.setdefault(c, 0)
            bucket_counts[label] = bucket_counts.get(label, 0) + 1
            marginal_counts[label] += 1
            n_train += 1

            # Collect the raw rolling-vol sample for this bucket so the
            # bucket edges can be reconstructed for live scoring. Rows
            # that don't carry the raw column (e.g. hand-built test rows)
            # simply contribute no samples → no edges → the live path
            # treats the model as un-bucketable and skips it.
            raw_vol = row.get(vol_feature_column)
            if raw_vol is not None:
                try:
                    vol_by_bucket.setdefault(key, []).append(float(raw_vol))
                except (TypeError, ValueError):
                    pass
            if not spec_symbol:
                spec_symbol = str(row.get("symbol") or "")
            if not spec_timeframe:
                spec_timeframe = str(row.get("timeframe") or "")

        if n_train == 0:
            raise ValueError(
                f"no rows with non-null target {target!r} found in training set"
            )

        # Normalise to probabilities.
        per_bucket_proba: dict[str, dict[str, float]] = {}
        for bucket, counts in per_bucket_counts.items():
            total = sum(counts.values())
            if total == 0:
                continue
            per_bucket_proba[bucket] = {
                c: counts.get(c, 0) / total for c in marginal_counts
            }

        marginal_proba = {
            c: marginal_counts[c] / n_train for c in marginal_counts
        }

        vol_bucket_edges, vol_bucket_labels = _reconstruct_vol_bucket_edges(
            vol_by_bucket
        )

        return {
            "trainer": "ml.trainers.regime_classifier.RegimeClassifierTrainer",
            "target_column": target,
            "feature_column": feature,
            "unknown_bucket": unknown_bucket,
            "class_labels": list(class_labels),
            "per_bucket_proba": per_bucket_proba,
            "per_bucket_counts": per_bucket_counts,
            "marginal_proba": marginal_proba,
            "marginal_counts": marginal_counts,
            "n_train": n_train,
            # Live-scoring spec (consumed by src/runtime/regime_shadow.py).
            "vol_feature_column": vol_feature_column,
            "vol_window_n": vol_window_n,
            "vol_bucket_edges": vol_bucket_edges,
            "vol_bucket_labels": vol_bucket_labels,
            "symbol": spec_symbol,
            "timeframe": spec_timeframe,
        }
