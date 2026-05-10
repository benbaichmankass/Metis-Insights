"""Score-distribution drift detection (S-AI-WS8-PART-3).

Compares the shadow predictor's score distribution across two
time windows — typically "reference window" (e.g. the last 30
days) vs "current window" (e.g. the last 7 days). If the model's
output distribution shifts materially, something has changed:

  - the input feature distribution drifted (typical concept
    drift),
  - the model's training data no longer reflects production,
  - or a deployment / config change altered what flows in.

Metrics:

- **Kolmogorov–Smirnov (KS) statistic** — max |F_ref(x) − F_cur(x)|.
  Distribution-free, sensitive to any shape change. Returns a
  value in `[0, 1]`; small (~0.05) is healthy, large (>0.2) is
  worth investigating.
- **Population Stability Index (PSI)** — sum over bins of
  `(p_cur − p_ref) * ln(p_cur / p_ref)`. The industry standard
  for monitoring score-based models. Rules of thumb:
  - `< 0.1` → no significant change
  - `0.1–0.25` → moderate change, monitor
  - `> 0.25` → significant drift, investigate
- **Summary stats** — count, mean, stdev, min, max per window.
  Often enough to spot obvious shifts without thresholding.

Pure-stdlib. The functions accept iterables / sequences of
floats and never depend on numpy or scipy. Callers project audit
records into score arrays via `record_from_dict` (from
`ml.shadow.inspector`) and feed them in.

No external "reference distribution" required. PART-3 ships
**window-over-window self-comparison**. A future part can wire
in a registry-stored reference distribution from the model's
training-set predictions.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence


# ---------------------------------------------------------------------------
# Summary stats
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Summary:
    """Lightweight univariate summary of a score series."""

    count: int
    mean: float
    stdev: float
    minimum: float
    maximum: float

    @property
    def is_empty(self) -> bool:
        return self.count == 0


def summarize(scores: Iterable[float]) -> Summary:
    """Compute a `Summary` from a score iterable. Empty input
    returns a `Summary(count=0, mean=0, stdev=0, min=0, max=0)`."""
    xs = [float(x) for x in scores]
    n = len(xs)
    if n == 0:
        return Summary(count=0, mean=0.0, stdev=0.0, minimum=0.0, maximum=0.0)
    mean = sum(xs) / n
    if n == 1:
        return Summary(count=1, mean=mean, stdev=0.0,
                       minimum=xs[0], maximum=xs[0])
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    return Summary(
        count=n, mean=mean, stdev=math.sqrt(var),
        minimum=min(xs), maximum=max(xs),
    )


# ---------------------------------------------------------------------------
# Kolmogorov–Smirnov
# ---------------------------------------------------------------------------


def ks_statistic(a: Sequence[float], b: Sequence[float]) -> float:
    """Two-sample Kolmogorov–Smirnov statistic.

    Returns `max |F_a(x) − F_b(x)|` over the union of unique x
    values. Result is in `[0, 1]`. Returns 0.0 when either
    sample is empty (no signal — caller decides what to do).

    No p-value computed. PART-3 is monitoring, not hypothesis
    testing; the raw statistic + the operator-readable buckets
    in `interpret_ks` is enough.
    """
    if not a or not b:
        return 0.0
    sorted_a = sorted(a)
    sorted_b = sorted(b)
    union = sorted(set(sorted_a) | set(sorted_b))
    na, nb = len(sorted_a), len(sorted_b)
    max_diff = 0.0
    for x in union:
        fa = _ecdf(sorted_a, x) / na
        fb = _ecdf(sorted_b, x) / nb
        diff = abs(fa - fb)
        if diff > max_diff:
            max_diff = diff
    return max_diff


def _ecdf(sorted_xs: Sequence[float], x: float) -> int:
    """Empirical CDF count: number of elements in `sorted_xs` ≤ `x`."""
    # Binary search via bisect-style logic without importing bisect
    # (one-line dependency saving). For our list sizes this is fine.
    lo, hi = 0, len(sorted_xs)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_xs[mid] <= x:
            lo = mid + 1
        else:
            hi = mid
    return lo


def interpret_ks(stat: float) -> str:
    """Plain-English bucket for a KS value. Conservative."""
    if stat < 0.05:
        return "no_change"
    if stat < 0.1:
        return "minor"
    if stat < 0.2:
        return "moderate"
    return "significant"


# ---------------------------------------------------------------------------
# Population Stability Index
# ---------------------------------------------------------------------------


def histogram(
    scores: Iterable[float],
    *,
    bins: int = 10,
    score_min: float = 0.0,
    score_max: float = 1.0,
) -> list[int]:
    """Fixed-edge histogram. Values outside `[score_min, score_max]`
    are clamped into the nearest end bucket — appropriate for
    score outputs that are nominally bounded but can occasionally
    spill (e.g. probability scores that round outside [0, 1]).
    """
    if bins <= 0:
        raise ValueError(f"bins must be positive; got {bins}")
    if score_max <= score_min:
        raise ValueError(
            f"score_max ({score_max}) must be > score_min ({score_min})"
        )
    counts = [0] * bins
    width = (score_max - score_min) / bins
    for raw in scores:
        x = float(raw)
        if x <= score_min:
            counts[0] += 1
            continue
        if x >= score_max:
            counts[-1] += 1
            continue
        idx = int((x - score_min) / width)
        if idx >= bins:
            idx = bins - 1
        counts[idx] += 1
    return counts


def psi(
    reference: Sequence[int],
    current: Sequence[int],
    *,
    smoothing: float = 1e-4,
) -> float:
    """Population Stability Index over matched histograms.

    `smoothing` is added to both numerator and denominator
    proportions before the log to avoid `log(0)` when a bin is
    empty in one of the series — standard practice in production
    PSI implementations. Default 1e-4 is small enough not to
    distort genuine drift but large enough to handle the all-zero-
    bin case.
    """
    if len(reference) != len(current):
        raise ValueError(
            f"reference and current must have the same number of bins; "
            f"got {len(reference)} vs {len(current)}"
        )
    if not reference:
        return 0.0
    ref_total = sum(reference) or 1
    cur_total = sum(current) or 1
    score = 0.0
    for r, c in zip(reference, current):
        p_ref = r / ref_total + smoothing
        p_cur = c / cur_total + smoothing
        score += (p_cur - p_ref) * math.log(p_cur / p_ref)
    return score


def interpret_psi(psi_value: float) -> str:
    """Plain-English bucket for PSI. Industry-standard thresholds."""
    if psi_value < 0.1:
        return "no_change"
    if psi_value < 0.25:
        return "moderate"
    return "significant"


# ---------------------------------------------------------------------------
# Compound report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DriftReport:
    """End-to-end drift result over a reference + current sample."""

    reference: Summary
    current: Summary
    ks: float
    ks_verdict: str
    psi: float
    psi_verdict: str
    overall_verdict: str  # "no_change" | "minor" | "moderate" | "significant"


def compute_drift(
    reference: Sequence[float],
    current: Sequence[float],
    *,
    bins: int = 10,
    score_min: float = 0.0,
    score_max: float = 1.0,
) -> DriftReport:
    """Run summary + KS + PSI in one pass; return a `DriftReport`.

    `overall_verdict` is the worse of the KS and PSI buckets —
    "significant" if EITHER triggers it, "moderate" if either is
    moderate but neither is significant, etc.
    """
    ref_summary = summarize(reference)
    cur_summary = summarize(current)
    ks = ks_statistic(reference, current)
    ks_v = interpret_ks(ks)
    psi_val = psi(
        histogram(reference, bins=bins, score_min=score_min, score_max=score_max),
        histogram(current, bins=bins, score_min=score_min, score_max=score_max),
    )
    psi_v = interpret_psi(psi_val)
    overall = _worst({ks_v, psi_v})
    return DriftReport(
        reference=ref_summary, current=cur_summary,
        ks=ks, ks_verdict=ks_v,
        psi=psi_val, psi_verdict=psi_v,
        overall_verdict=overall,
    )


_VERDICT_ORDER: tuple[str, ...] = ("no_change", "minor", "moderate", "significant")


def _worst(verdicts: set[str]) -> str:
    return max(verdicts, key=lambda v: _VERDICT_ORDER.index(v))
