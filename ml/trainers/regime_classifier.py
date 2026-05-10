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
        }
