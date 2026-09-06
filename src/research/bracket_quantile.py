"""ML-2 · the predictive bracket — the model and its calibration grader.

`docs/design/exit-mechanism-construction-PROCESS.md` § E-ML names ML-2:

    *"Regress the exit location/time an entry should expect, and grade it on
    calibration before P&L (per E3.6's falsifier)."*

and E3.6 states the falsifier this module exists to be judged by:

    *"a predictive bracket is a claim about WHERE the trade will exit, so it is
    graded against realised exits — calibration first (does the stated
    expectation match the observed distribution?), P&L second. A bracket that
    improves net R while being systematically wrong about where trades exit has
    NOT met this bar."*

--------------------------------------------------------------------------
WHY A QUANTILE, AND NOT A POINT PREDICTION
--------------------------------------------------------------------------
A take-profit is not a forecast of the mean exit. It is a **level the trade is
expected to reach with some probability**: a target at the q-quantile of the
favourable-excursion distribution is reached about (1 - q) of the time. So the
object to regress is a CONDITIONAL QUANTILE, and the grading falls straight out
of it — a model that says "q = 0.70" and is reached 0.30 of the time is
calibrated; one reached 0.03 of the time is not.

This is the same reading MI-148's instrument already publishes. It measured the
fleet's declared target sitting at quantile **0.9024** of realised exits
(`measured`-exit stratum, n=82) and reached by **12.2%** of trades — two
statements of one fact. `src/runtime/bracket_calibration.py` is that instrument
and it is the one that governs; this module does not re-derive it, it produces
the thing it grades.

--------------------------------------------------------------------------
⚠️ THE TRAP THIS MODULE IS BUILT AROUND: CALIBRATION ALONE IS VACUOUS
--------------------------------------------------------------------------
**The unconditional empirical quantile is perfectly calibrated by
construction** — in-sample, exactly; out-of-sample, to sampling error. So
"our model is calibrated" is, on its own, evidence of nothing whatever: it is
satisfied by a model that ignores every feature and reads one number off the
leg's own history.

A predictive bracket therefore has to clear **two bars, and neither substitutes
for the other**:

  1. **CALIBRATION** — empirical coverage matches the stated quantile.
     Necessary. Achieved trivially by the baseline.
  2. **SHARPNESS** — out-of-sample pinball loss beats the UNCONDITIONAL
     quantile of the same training data. This is the only bar that says the
     features carry information about where THIS trade will exit rather than
     where trades on this leg go on average.

`grade_model` reports both and refuses to collapse them. A model that is
calibrated and not sharper than the baseline has not found a predictive
bracket — it has rediscovered the leg's own MFE histogram, which is what
MI-148 already proposed and which needs no model. Saying that plainly is a
result, not a failure to produce one.

--------------------------------------------------------------------------
THE BASIS IS PERCENT-OF-ENTRY, NEVER R
--------------------------------------------------------------------------
Inherited from MI-148 rather than re-argued, because a second basis would be
free to drift from the one that governs:

  * the R denominator is measurably contaminated — `trades.stop_loss` is the
    FINAL trailed stop and `order_packages.sl` is overwritten by the same
    `_apply_update` path, so both erase the level they replace;
  * `TP_VENUE_CAP_PCT` is itself 9.9% **of entry**, so percent-of-entry is the
    basis on which "prediction vs venue artefact" is directly decidable.

⚠️ A harness emit carries `mfe_r` (an R figure) and NOT a percent. The
conversion is exact and belongs to the corpus builder, not here:
`mfe_frac = mfe_r * |entry - sl| / entry`. In a BACKTEST that risk is the
entry-time risk and is never overwritten, so the backtest corpus does not
inherit the live contamination — see `scripts/research/ml2_bracket_corpus.py`.

--------------------------------------------------------------------------
FEATURES MUST BE DECISION-TIME AND EXOGENOUS (§ 0.2)
--------------------------------------------------------------------------
§ 0.2 names the root cause of every negative exit result to date: all **11 of
11** features in `INTRABAR_FEATURE_NAMES` are ENDOGENOUS — functions of the
trade's own path, clock, geometry or symbol. Its corollary is that *"no lever
beats holding"* means only *"no function of these eleven inputs beats
holding"*.

This module takes no position on which features are supplied — it is a
regressor. But it exists to be fed a **decision-time** vector, and the
corpus builder is where that is enforced. A feature read from the trade's own
subsequent path reproduces § 0.2 with extra steps.

--------------------------------------------------------------------------
Pure stdlib, no I/O, never raises on caller data. Same discipline as
`src/runtime/bracket_calibration.py` and `src/research/excursions.py`, so the
tests run in a lean sandbox with no numeric stack installed.

**No runtime caller. Nothing in `src/` imports this module.** It is research
code behind a Tier-3 proposal; the geometry it informs is operator-gated.
"""
from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# States. Never collapsed — "we did not look" is not "we looked and found
# nothing" (docs/CLAUDE-RULES-CANONICAL.md § "Collapsed states").
# ---------------------------------------------------------------------------

#: A coverage figure exists and was compared to its target.
CAL_GRADED = "graded"
#: Fewer rows than the floor, so NO coverage figure exists. Emphatically not
#: "calibrated" — there is nothing to be calibrated against.
CAL_INSUFFICIENT_N = "insufficient_n"
#: The outcome column is constant (or has no spread), so every quantile
#: coincides and coverage is meaningless rather than perfect.
CAL_DEGENERATE = "degenerate"
#: We could not read the inputs at all.
CAL_UNKNOWN = "unknown"

#: Sharpness verdicts, kept apart from calibration for the reason in the
#: module docstring.
#:
#: ⚠️ THREE states, not two, and the middle one is why. A conditional fit beats
#: the raw empirical quantile by a SMALL margin even on data with no signal at
#: all -- the SGD intercept is a shrunk, lower-variance estimator of the same
#: quantity, which is estimator efficiency and NOT information about the market.
#: MEASURED on this module's own synthetic no-signal control (n=900, 5
#: quantiles): improvements of +0.006 to +0.027 with the features carrying
#: nothing. A two-state `mp < bp` test calls that a win at every quantile.
#: So `beats_baseline` alone is not evidence, and the shuffled-label null is
#: what separates the two.
SHARP_BEATS_BASELINE = "beats_baseline_and_null"
SHARP_WITHIN_NULL = "beats_baseline_within_null"
SHARP_NO_BETTER = "no_better_than_baseline"
SHARP_NOT_MEASURED = "not_measured"

#: Below this many held-out rows no coverage is reported. A coverage figure on
#: n=5 is a number, not a measurement: its standard error at q=0.8 is ~0.18.
MIN_EVAL_N = 30

#: Default target quantiles for the reliability curve.
DEFAULT_QUANTILES: Tuple[float, ...] = (0.5, 0.6, 0.7, 0.8, 0.9)


def _f(value: Any) -> Optional[float]:
    """Best-effort float. Returns None rather than raising, and rejects
    non-finite values — a NaN that reaches a loss function silently poisons
    every downstream aggregate."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


# ---------------------------------------------------------------------------
# Loss
# ---------------------------------------------------------------------------

def pinball_loss(actual: float, predicted: float, q: float) -> float:
    """The quantile (pinball) loss.

    Minimised in expectation by the true conditional q-quantile, which is the
    whole reason this is the training objective: it makes "predict the level
    reached (1-q) of the time" the thing the fit is actually optimising, rather
    than something hoped for afterwards.
    """
    delta = actual - predicted
    return q * delta if delta >= 0.0 else (q - 1.0) * delta


def mean_pinball(actuals: Sequence[float], preds: Sequence[float], q: float) -> Optional[float]:
    """Mean pinball loss over paired sequences, or None when nothing is gradeable."""
    n = 0
    total = 0.0
    for a, p in zip(actuals, preds):
        fa, fp = _f(a), _f(p)
        if fa is None or fp is None:
            continue
        total += pinball_loss(fa, fp, q)
        n += 1
    return total / n if n else None


# ---------------------------------------------------------------------------
# Baseline: the unconditional quantile
# ---------------------------------------------------------------------------

def empirical_quantile(values: Sequence[float], q: float) -> Optional[float]:
    """Linear-interpolated empirical quantile. None on an empty/unreadable sample.

    This is THE BASELINE the conditional model must beat on sharpness. It is
    also, deliberately, the strongest calibration competitor there is.
    """
    clean = sorted(v for v in (_f(x) for x in values) if v is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    qq = min(max(q, 0.0), 1.0)
    pos = qq * (len(clean) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(clean) - 1)
    frac = pos - lo
    return clean[lo] * (1.0 - frac) + clean[hi] * frac


# ---------------------------------------------------------------------------
# The conditional model
# ---------------------------------------------------------------------------

class QuantileRegressor:
    """Linear conditional-quantile regressor, fit by subgradient descent on the
    pinball loss.

    Linear and small on purpose. The corpus this is aimed at has per-leg n in
    the low hundreds at best (live n is 1-8 per leg, which is exactly why
    MI-148 refused to propose per-leg values), and a gradient-boosted model on
    that sample would fit noise and produce a confident wrong level on a live
    order path. A model whose capacity exceeds its corpus is the fitted-
    threshold failure this repo already pays for repeatedly.

    Features are standardised internally so one column measured in fractions
    (`risk_frac` ~ 0.02) and another in hours (0-23) contribute comparably;
    the caller gets predictions back in the original units of ``y``.
    """

    def __init__(self, q: float, *, lr: float = 0.02, epochs: int = 300, seed: int = 0) -> None:
        self.q = float(q)
        self.lr = float(lr)
        self.epochs = int(epochs)
        self.seed = int(seed)
        self.coef_: List[float] = []
        self.intercept_: float = 0.0
        self._mu: List[float] = []
        self._sd: List[float] = []
        # The TARGET is standardised too. Without this the step size is not
        # scale-free: `mfe_frac` lives at ~0.01-0.10, so a step of `lr` moves
        # the prediction by a large fraction of the entire target range and the
        # intercept random-walks away from the calibrated start. Measured
        # before the fix: MACE 0.179 on PURE NOISE, where the unconditional
        # baseline the fit starts from is calibrated by construction -- i.e.
        # the optimiser was destroying calibration it was handed for free.
        self._y_mu: float = 0.0
        self._y_sd: float = 1.0
        self.fitted: bool = False
        self.n_train: int = 0

    # -- internals ---------------------------------------------------------
    def _standardise(self, X: Sequence[Sequence[float]]) -> List[List[float]]:
        out: List[List[float]] = []
        for row in X:
            out.append([
                (row[j] - self._mu[j]) / self._sd[j] if self._sd[j] > 0 else 0.0
                for j in range(len(self._mu))
            ])
        return out

    # -- API ---------------------------------------------------------------
    def fit(self, X: Sequence[Sequence[float]], y: Sequence[float]) -> "QuantileRegressor":
        """Fit. Returns self and NEVER raises — an unusable sample leaves
        ``fitted`` False, which callers must branch on rather than reading a
        prediction of 0.0 as a real level."""
        rows: List[List[float]] = []
        targets: List[float] = []
        for xr, yv in zip(X, y):
            fy = _f(yv)
            if fy is None:
                continue
            fx = [_f(v) for v in xr]
            if any(v is None for v in fx):
                continue
            rows.append([float(v) for v in fx])  # type: ignore[arg-type]
            targets.append(fy)
        if not rows:
            self.fitted = False
            return self
        d = len(rows[0])
        if any(len(r) != d for r in rows):
            self.fitted = False
            return self

        self._mu = [sum(r[j] for r in rows) / len(rows) for j in range(d)]
        self._sd = []
        for j in range(d):
            var = sum((r[j] - self._mu[j]) ** 2 for r in rows) / len(rows)
            self._sd.append(math.sqrt(var) if var > 0 else 0.0)

        Z = self._standardise(rows)

        # Standardise the target so the step size is scale-free (see __init__).
        self._y_mu = sum(targets) / len(targets)
        yvar = sum((t - self._y_mu) ** 2 for t in targets) / len(targets)
        self._y_sd = math.sqrt(yvar) if yvar > 0 else 0.0
        if self._y_sd <= 0:
            # Degenerate target: every quantile coincides. Predict the constant
            # and say we fitted nothing rather than descending on a flat loss.
            self.coef_ = [0.0] * d
            self.intercept_ = 0.0
            self.fitted = True
            self.n_train = len(rows)
            return self
        ys = [(t - self._y_mu) / self._y_sd for t in targets]

        self.coef_ = [0.0] * d
        # Start the intercept at the unconditional quantile: the model then
        # begins AT the baseline and can only be judged on what it adds.
        self.intercept_ = empirical_quantile(ys, self.q) or 0.0

        rng = random.Random(self.seed)
        idx = list(range(len(Z)))
        for epoch in range(self.epochs):
            rng.shuffle(idx)
            # Robbins-Monro 1/sqrt(t) decay. A plain subgradient method on a
            # non-smooth objective does not converge at a fixed step -- it
            # oscillates in a ball whose radius is proportional to the step,
            # which is exactly the calibration damage described in __init__.
            step = self.lr / math.sqrt(1.0 + epoch)
            for i in idx:
                pred = self.intercept_ + sum(self.coef_[j] * Z[i][j] for j in range(d))
                # d/dpred of pinball: -q if under-predicting, (1-q) if over.
                g = -self.q if ys[i] >= pred else (1.0 - self.q)
                self.intercept_ -= step * g
                for j in range(d):
                    self.coef_[j] -= step * g * Z[i][j]
        self.fitted = True
        self.n_train = len(rows)
        return self

    def predict(self, X: Sequence[Sequence[float]]) -> List[Optional[float]]:
        """Predicted q-quantile per row. ``None`` for a row that cannot be read
        or when the model never fit — never a silent 0.0."""
        if not self.fitted:
            return [None] * len(list(X))
        out: List[Optional[float]] = []
        for row in X:
            fx = [_f(v) for v in row]
            if any(v is None for v in fx) or len(fx) != len(self._mu):
                out.append(None)
                continue
            z = [
                (float(fx[j]) - self._mu[j]) / self._sd[j] if self._sd[j] > 0 else 0.0  # type: ignore[arg-type]
                for j in range(len(self._mu))
            ]
            zpred = self.intercept_ + sum(self.coef_[j] * z[j] for j in range(len(z)))
            # back to the caller's units
            out.append(self._y_mu + zpred * self._y_sd)
        return out


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

def empirical_coverage(actuals: Sequence[float], preds: Sequence[Optional[float]]) -> Optional[float]:
    """Fraction of realised outcomes at or below the predicted level.

    For a predicted q-quantile this should be ~q. It is the direct, and the
    ONLY, answer to E3.6's question — *does the stated expectation match the
    observed distribution?*
    """
    n = 0
    hit = 0
    for a, p in zip(actuals, preds):
        fa, fp = _f(a), _f(p)
        if fa is None or fp is None:
            continue
        n += 1
        if fa <= fp:
            hit += 1
    return hit / n if n else None


def grade_model(
    actuals: Sequence[float],
    model_preds: Sequence[Optional[float]],
    baseline_preds: Sequence[Optional[float]],
    q: float,
    *,
    min_n: int = MIN_EVAL_N,
    tolerance: float = 0.05,
    null_p95: Optional[float] = None,
) -> Dict[str, Any]:
    """Grade ONE quantile on BOTH bars, held apart.

    ``tolerance`` is the absolute coverage band inside which the model counts
    as calibrated. 0.05 is a STATED CHOICE, not a measured one — at n=100 the
    standard error of a coverage estimate at q=0.8 is 0.04, so a tighter band
    would grade sampling noise.

    ``null_p95`` is the 95th percentile of the SHUFFLED-LABEL improvement
    distribution at this same q. When supplied it GATES the sharpness verdict:
    an improvement inside the null's upper tail grades `beats_baseline_within_
    null`, which is a refusal, not a pass. When it is None the verdict can only
    reach `beats_baseline_within_null` — **the gate is never assumed passed
    because it was not run.**

    Returns a dict whose ``calibration_state`` and ``sharpness_state`` are
    independent. Read both: a `graded` + `no_better_than_baseline` pair is the
    vacuous-calibration case the module docstring warns about, and it is a
    RESULT.
    """
    n_gradeable = sum(
        1 for a, p in zip(actuals, model_preds)
        if _f(a) is not None and _f(p) is not None
    )
    out: Dict[str, Any] = {
        "q": q,
        "n_gradeable": n_gradeable,
        "min_n": min_n,
        "tolerance": tolerance,
        "coverage": None,
        "coverage_error": None,
        "calibration_state": CAL_UNKNOWN,
        "model_pinball": None,
        "baseline_pinball": None,
        "pinball_improvement": None,
        "null_p95": null_p95,
        "sharpness_state": SHARP_NOT_MEASURED,
    }

    clean_actuals = [v for v in (_f(a) for a in actuals) if v is not None]
    if not clean_actuals:
        return out
    if len(set(clean_actuals)) <= 1:
        out["calibration_state"] = CAL_DEGENERATE
        return out
    if n_gradeable < min_n:
        # NOT "calibrated" and NOT "miscalibrated" — no coverage figure exists.
        out["calibration_state"] = CAL_INSUFFICIENT_N
        return out

    cov = empirical_coverage(actuals, model_preds)
    out["coverage"] = cov
    if cov is not None:
        out["coverage_error"] = abs(cov - q)
        out["calibration_state"] = CAL_GRADED

    mp = mean_pinball(actuals, model_preds, q)  # type: ignore[arg-type]
    bp = mean_pinball(actuals, baseline_preds, q)  # type: ignore[arg-type]
    out["model_pinball"] = mp
    out["baseline_pinball"] = bp
    if mp is not None and bp is not None and bp > 0:
        # Positive => the conditional model is sharper than the unconditional
        # quantile. This is the bar that is NOT satisfied by the baseline.
        imp = (bp - mp) / bp
        out["pinball_improvement"] = imp
        out["null_p95"] = null_p95
        if mp >= bp:
            out["sharpness_state"] = SHARP_NO_BETTER
        elif null_p95 is None or imp <= null_p95:
            # Better than the baseline, but not by more than shuffled labels
            # manage. Carries no information.
            out["sharpness_state"] = SHARP_WITHIN_NULL
        else:
            out["sharpness_state"] = SHARP_BEATS_BASELINE
    return out


def calibration_curve(
    actuals: Sequence[float],
    preds_by_q: Dict[float, Sequence[Optional[float]]],
) -> List[Dict[str, Any]]:
    """Reliability curve: one (target q, empirical coverage) point per quantile.

    A calibrated model traces the diagonal. A systematically-above curve means
    the levels are too far away — which is precisely the fleet's measured
    condition, from the other direction (MI-148: declared target at quantile
    0.9024, reached 12.2%).
    """
    rows: List[Dict[str, Any]] = []
    for q in sorted(preds_by_q):
        cov = empirical_coverage(actuals, preds_by_q[q])
        rows.append({
            "q": q,
            "coverage": cov,
            "coverage_error": (abs(cov - q) if cov is not None else None),
        })
    return rows


def mean_absolute_calibration_error(curve: Sequence[Dict[str, Any]]) -> Optional[float]:
    """MACE over a reliability curve. None when no point is gradeable — never 0.0,
    which would read as perfect calibration."""
    errs = [r["coverage_error"] for r in curve if r.get("coverage_error") is not None]
    return sum(errs) / len(errs) if errs else None


# ---------------------------------------------------------------------------
# The information control (E2's, inherited unchanged by E-ML)
# ---------------------------------------------------------------------------

def shuffled_label_control(
    X_train: Sequence[Sequence[float]],
    y_train: Sequence[float],
    X_eval: Sequence[Sequence[float]],
    y_eval: Sequence[float],
    q: float,
    *,
    trials: int = 20,
    seed: int = 0,
    min_n: int = MIN_EVAL_N,
) -> Dict[str, Any]:
    """Refit on SHUFFLED labels and report the improvement distribution.

    E3.6: *"E2's information test with a shuffled-label control THAT IS SHOWN TO
    FIRE"* — the parenthetical is the whole point. A control that never fires
    proves nothing about the model; it proves the control is broken
    (`BL-20260820-E2-NEGATIVE-CONTROL-GATED-POINTWISE-NOT-FWER`, and
    `e2_null_calibration.py`, which exists because one Bernoulli draw cannot
    distinguish 5% bad luck from a dead null).

    So this returns the full null DISTRIBUTION of the sharpness improvement,
    not a single draw, and the caller compares the real improvement against
    that distribution's upper tail.
    """
    rng = random.Random(seed)
    improvements: List[float] = []
    y_list = [v for v in y_train]
    for t in range(trials):
        shuffled = list(y_list)
        rng.shuffle(shuffled)
        m = QuantileRegressor(q, seed=seed + t + 1).fit(X_train, shuffled)
        if not m.fitted:
            continue
        base = empirical_quantile(shuffled, q)
        if base is None:
            continue
        mp = mean_pinball(y_eval, m.predict(X_eval), q)
        bp = mean_pinball(y_eval, [base] * len(list(y_eval)), q)
        if mp is None or bp is None or bp <= 0:
            continue
        improvements.append((bp - mp) / bp)

    n_eval = sum(1 for v in y_eval if _f(v) is not None)
    return {
        "trials_requested": trials,
        "trials_usable": len(improvements),
        "n_eval": n_eval,
        "null_mean_improvement": (sum(improvements) / len(improvements)) if improvements else None,
        "null_p95_improvement": empirical_quantile(improvements, 0.95) if improvements else None,
        "null_max_improvement": max(improvements) if improvements else None,
        # `not_measured` is a third state: an unusable control says nothing
        # about the model, and must never read as the model passing.
        "control_state": ("measured" if improvements and n_eval >= min_n else "not_measured"),
    }
