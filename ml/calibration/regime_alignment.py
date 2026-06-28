"""Regime-alignment calibrators — map a regime head's signal-time score (+ the
trade ``direction``) onto ``P(favorable regime | score, direction)`` so the
conviction lens' ``c_reg`` input becomes a usable, calibrated probability.

Background (``docs/research/B-conviction-graduation-DESIGN-2026-06-27.md`` §
"c_reg enabler"): ``conviction_inputs._default_normalize`` deliberately refuses
to turn a single stored regime scalar into an alignment probability — a raw
``P(volatile)=0.98`` says nothing on its own about whether *this direction*
trades favorably under that regime. The honest fix is a fitted mapping from a
corpus of historical CLOSED trades, each carrying ``(regime score at signal
time, direction, realized win/loss)``. This module is that mapping.

## "Favorable regime" definition (the corpus label)

A closed trade is **favorable** iff it **won** (``pnl > 0``) — the exact label
the ``conviction_meta`` dataset family already uses (``won = pnl > 0``). So the
calibrator answers: *given the regime head scored S and we traded ``direction``,
what is the realized P(win)?* That is precisely the alignment signal ``c_reg``
is meant to contribute (regime *favors* the trade), grounded in realized
outcomes rather than invented from a scalar.

## Per-(model_id, direction)

Fit is keyed **per regime ``model_id``** (mirroring the per-model confidence
calibrators) and, within a model, **per direction** (``long`` / ``short``) plus
an ``all`` (direction-pooled) fallback — long and short can map the same regime
score to different win-rates, so direction sensitivity is first-class. At
predict time the direction-specific calibrator is used when present, else the
pooled ``all`` calibrator.

## Fit / predict split

Predict is **pure Python** (the serialized ``Calibrator`` objects from
``calibrators.py``) so the live trader needs no sklearn/numpy. The default fit
here is a **stdlib logistic** (no numpy/sklearn) so the fitter + its tests are
hermetic; ``method="auto"`` delegates to the existing
``ml.calibration.fit.fit_calibrator`` (sklearn offline) to match the confidence
fitter's footprint when richer methods are wanted on the trainer.

The artifact lives in the SAME ``calibrators.json`` family the confidence
calibrators ship in, under a reserved top-level ``regime_alignment`` section so
it rides the existing trainer-mirror → live path with no new plumbing:

    {
      "trend_donchian": {... a confidence Calibrator ...},
      "regime_alignment": {
        "<model_id>": {
          "all":   {... a Calibrator ...},
          "long":  {... a Calibrator ...},
          "short": {... a Calibrator ...}
        }
      }
    }
"""
from __future__ import annotations

import math
from typing import Iterable, Mapping, Sequence

from .calibrators import Calibrator, ConstantCalibrator, PlattCalibrator

# Reserved top-level key in calibrators.json under which the per-model
# regime-alignment calibrators live (kept distinct from the per-strategy /
# per-model confidence calibrators, which sit at the top level).
REGIME_ALIGNMENT_KEY = "regime_alignment"

# Direction-pooled fallback key inside a model's section.
POOLED_DIRECTION = "all"


def canon_direction(direction: object) -> str | None:
    """Normalize a trade direction to ``"long"`` / ``"short"`` (else ``None``).

    Tolerates the shapes the journal / signal path use: ``long``/``short``,
    ``buy``/``sell``, ``b``/``s``. Anything else → ``None`` (pooled lookup).
    """
    d = str(direction or "").strip().lower()
    if d in ("long", "buy", "b"):
        return "long"
    if d in ("short", "sell", "s"):
        return "short"
    return None


# --------------------------------------------------------------------------- #
# corpus transform (pure; hermetically testable on in-memory rows)
# --------------------------------------------------------------------------- #
def regime_score_from_model_scores(
    model_scores: Mapping[str, object] | None, model_id: str
) -> float | None:
    """Pull the regime head's stored score for ``model_id`` out of a
    ``{model_id: {stage, score}}`` map. ``None`` when absent / non-numeric."""
    if not isinstance(model_scores, Mapping):
        return None
    rec = model_scores.get(model_id)
    if not isinstance(rec, Mapping):
        return None
    score = rec.get("score")
    try:
        return float(score)
    except (TypeError, ValueError):
        return None


def corpus_for_model(
    rows: Iterable[Mapping[str, object]], model_id: str
) -> dict[str, list[tuple[float, int]]]:
    """Transform closed-trade rows into ``{direction: [(score, won), ...]}``.

    Each input row is a closed trade carrying at least ``model_scores`` (the
    signal-time ``{model_id: {stage, score}}`` map) and an outcome (``won``
    boolean, or ``pnl`` from which ``won = pnl > 0`` is derived) and a
    ``direction``. Rows without a usable regime score for ``model_id`` are
    skipped (the head didn't score that decision). The pooled ``"all"`` bucket
    accumulates every usable row; the per-direction buckets accumulate the
    direction-resolved subset. Pure; never raises on a malformed row.
    """
    out: dict[str, list[tuple[float, int]]] = {POOLED_DIRECTION: []}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        score = regime_score_from_model_scores(row.get("model_scores"), model_id)
        if score is None:
            continue
        won = _won_of(row)
        if won is None:
            continue
        out[POOLED_DIRECTION].append((score, won))
        direction = canon_direction(row.get("direction"))
        if direction is not None:
            out.setdefault(direction, []).append((score, won))
    return out


def _won_of(row: Mapping[str, object]) -> int | None:
    """Resolve a row's binary favorable/win label. ``won`` wins; else ``pnl>0``."""
    won = row.get("won")
    if won is not None:
        try:
            return 1 if bool(won) else 0
        except (TypeError, ValueError):
            return None
    pnl = row.get("pnl")
    if pnl is None:
        return None
    try:
        return 1 if float(pnl) > 0.0 else 0
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# fit (default: stdlib logistic — no numpy/sklearn, so the fitter is hermetic)
# --------------------------------------------------------------------------- #
# Sample-size floor below which we don't pretend to discriminate — return a
# ConstantCalibrator at the empirical base rate (honest "no signal").
MIN_FIT_ROWS = 10


def fit_regime_alignment_calibrator(
    pairs: Sequence[tuple[float, int]],
    *,
    method: str = "logistic",
    min_rows: int = MIN_FIT_ROWS,
) -> Calibrator:
    """Fit a single ``score -> P(favorable)`` calibrator from ``(score, won)``.

    * ``method="logistic"`` (default) — stdlib gradient-descent logistic
      regression (no numpy/sklearn), so this fits in any environment and the
      tests are hermetic. Monotone in the score (sign of the fitted slope).
    * ``method="auto"`` — delegate to ``ml.calibration.fit.fit_calibrator``
      (sklearn offline) to match the confidence fitter's richer method ladder.

    Degenerate inputs (too few rows / single-class labels) → a
    ``ConstantCalibrator`` at the base rate (never overfit noise).
    """
    xs = [float(s) for s, _ in pairs]
    ys = [1 if int(w) > 0 else 0 for _, w in pairs]
    n = len(xs)
    if n == 0:
        return ConstantCalibrator(rate=0.5)
    base_rate = sum(ys) / n
    if n < min_rows or len(set(ys)) < 2:
        return ConstantCalibrator(rate=base_rate)

    if method == "auto":
        from .fit import fit_calibrator  # offline (sklearn) — matches confidence

        return fit_calibrator(xs, ys, method="auto")
    return _fit_logistic_stdlib(xs, ys, base_rate=base_rate)


def _fit_logistic_stdlib(
    xs: list[float],
    ys: list[int],
    *,
    base_rate: float,
    iters: int = 500,
    lr: float = 0.1,
) -> Calibrator:
    """Stdlib 1-feature logistic regression -> a ``PlattCalibrator``.

    Standardizes the score (zero-mean/unit-var) for stable gradient descent,
    then folds the standardization back into the ``(a, b)`` of the raw-score
    sigmoid so the returned ``PlattCalibrator.predict`` takes the RAW score —
    the same contract as every other calibrator. Falls back to a
    ``ConstantCalibrator`` if the score has no spread (can't discriminate).
    """
    n = len(xs)
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / n
    std = math.sqrt(var)
    if std <= 0.0:
        return ConstantCalibrator(rate=base_rate)
    zs = [(x - mean) / std for x in xs]

    # gradient descent on standardized feature: P = sigmoid(w*z + c)
    w = 0.0
    c = 0.0
    for _ in range(iters):
        gw = 0.0
        gc = 0.0
        for z, y in zip(zs, ys):
            p = _sigmoid(w * z + c)
            err = p - y
            gw += err * z
            gc += err
        w -= lr * gw / n
        c -= lr * gc / n

    # Unfold standardization: w*z + c = w*(x-mean)/std + c
    #   = (w/std)*x + (c - w*mean/std)  ->  a=w/std, b=c - w*mean/std
    a = w / std
    b = c - w * mean / std
    return PlattCalibrator(a=a, b=b)


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1.0 + ez)


def fit_model_section(
    rows: Iterable[Mapping[str, object]],
    model_id: str,
    *,
    method: str = "logistic",
    min_rows: int = MIN_FIT_ROWS,
) -> dict[str, dict]:
    """Fit the per-direction calibrators for one regime ``model_id``.

    Returns ``{"all": <cal.to_dict()>, "long": ..., "short": ...}`` (only the
    buckets that met ``min_rows`` with both classes present; ``all`` is always
    emitted when ANY usable rows exist). Empty dict when the model scored no
    usable closed trades.
    """
    by_dir = corpus_for_model(rows, model_id)
    section: dict[str, dict] = {}
    for direction, pairs in by_dir.items():
        if not pairs:
            continue
        if direction != POOLED_DIRECTION and len(pairs) < min_rows:
            # Don't emit a thin per-direction calibrator; the pooled "all"
            # already covers it as the predict-time fallback.
            continue
        cal = fit_regime_alignment_calibrator(pairs, method=method, min_rows=min_rows)
        section[direction] = cal.to_dict()
    return section


# --------------------------------------------------------------------------- #
# predict-side load (pure; live-path safe)
# --------------------------------------------------------------------------- #
def load_regime_alignment(raw: Mapping[str, object] | None) -> dict[str, dict[str, Calibrator]]:
    """Deserialize the ``regime_alignment`` section of a calibrators artifact.

    Returns ``{model_id: {direction: Calibrator}}``. Fail-permissive: a
    malformed model / direction entry is skipped, never raised. ``{}`` when the
    section is absent / unusable.
    """
    out: dict[str, dict[str, Calibrator]] = {}
    section = (raw or {}).get(REGIME_ALIGNMENT_KEY)
    if not isinstance(section, Mapping):
        return out
    for model_id, by_dir in section.items():
        if not isinstance(by_dir, Mapping):
            continue
        cals: dict[str, Calibrator] = {}
        for direction, cal_d in by_dir.items():
            if not isinstance(cal_d, Mapping):
                continue
            try:
                cals[str(direction)] = Calibrator.from_dict(dict(cal_d))
            except (ValueError, KeyError, TypeError):
                continue
        if cals:
            out[str(model_id)] = cals
    return out


def predict_alignment(
    model_cals: Mapping[str, Calibrator],
    score: float,
    direction: object,
) -> float | None:
    """``P(favorable | score, direction)`` for one model's calibrator set.

    Prefers the direction-specific calibrator; falls back to the pooled
    ``"all"`` calibrator. ``None`` when neither is present or the score is
    non-numeric. Never raises.
    """
    try:
        s = float(score)
    except (TypeError, ValueError):
        return None
    d = canon_direction(direction)
    cal = None
    if d is not None:
        cal = model_cals.get(d)
    if cal is None:
        cal = model_cals.get(POOLED_DIRECTION)
    if cal is None:
        return None
    try:
        return cal.predict(s)
    except (TypeError, ValueError):
        return None
