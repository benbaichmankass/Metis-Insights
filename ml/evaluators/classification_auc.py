"""Classification evaluator with a threshold-free **AUC** metric (exit-policy P0).

The base :class:`ml.evaluators.classification.ClassificationEvaluator` reports
threshold-dependent metrics (accuracy / precision / recall / f1 / brier). The
exit-management feasibility experiment
(``docs/research/exit-management-ml-experiment-DESIGN.md`` §4) pre-registers its
kill-criterion against **OOS AUC** — the rank-quality of ``P(should_hold)``,
which is threshold-free and so is the right discriminator for "is exit timing
learnable OOS?".

Rather than mutate the shared base evaluator (other manifests depend on its
exact metric set), this subclass reuses the base score and *adds* an ``auc``
key alongside it — the same metrics, plus the threshold-free area under the ROC.
A model_id pointed at this evaluator therefore gets accuracy/precision/recall/
f1/brier (unchanged) AND ``auc`` for the gate.

AUC implementation: the Mann–Whitney-U / rank-sum form (stdlib only — no
numpy/scipy), which equals the probability that a random positive scores higher
than a random negative, with tied scores contributing 0.5. Degenerate eval sets
(all-positive or all-negative, or empty) yield ``auc = 0.5`` — the no-information
value, never a fabricated edge.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from .classification import ClassificationEvaluator


def _auc_from_scores(scored: list[tuple[float, int]]) -> float:
    """Rank-sum (Mann–Whitney-U) AUC over ``[(prob, label0/1), ...]``.

    Equals P(score of a random positive > score of a random negative), tied
    pairs counting 0.5. Returns ``0.5`` (no-information) when one class is
    absent — a single-class eval set has no separable order to score.
    """
    n_pos = sum(1 for _, lbl in scored if lbl == 1)
    n_neg = len(scored) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5
    # Average-rank assignment over ties so equal scores don't bias the U-stat.
    ordered = sorted(scored, key=lambda sl: sl[0])
    ranks = [0.0] * len(ordered)
    i = 0
    n = len(ordered)
    while i < n:
        j = i
        while j + 1 < n and ordered[j + 1][0] == ordered[i][0]:
            j += 1
        # Ranks are 1-based; tied block [i..j] shares the average rank.
        avg_rank = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            ranks[k] = avg_rank
        i = j + 1
    sum_ranks_pos = sum(
        rank for rank, (_, lbl) in zip(ranks, ordered) if lbl == 1
    )
    u_pos = sum_ranks_pos - n_pos * (n_pos + 1) / 2.0
    return float(u_pos / (n_pos * n_neg))


class ClassificationAUCEvaluator(ClassificationEvaluator):
    """:class:`ClassificationEvaluator` + a threshold-free ``auc`` metric.

    Identical config contract as the base (``target_column`` /
    ``threshold``); the predictor is resolved the same way. The returned
    metric mapping is the base set with ``auc`` added.
    """

    def score(
        self,
        model_state: Mapping[str, Any],
        rows: Iterable[Mapping[str, Any]],
        config: Mapping[str, Any],
    ) -> Mapping[str, float]:
        target = config.get("target_column", "should_hold")
        predictor = self._resolve_predictor(model_state)

        # Materialise once: the base evaluator needs the rows too, and a
        # generator would be exhausted by the first pass.
        materialised = list(rows)

        scored: list[tuple[float, int]] = []
        for row in materialised:
            target_value = row.get(target)
            if target_value is None:
                continue
            label = 1 if bool(target_value) else 0
            prob = predictor.predict(row)
            if prob < 0.0:
                prob = 0.0
            elif prob > 1.0:
                prob = 1.0
            scored.append((float(prob), label))

        base = dict(
            super().score(model_state, materialised, config)
        )
        base["auc"] = _auc_from_scores(scored)
        return base
