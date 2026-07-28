"""M30 × M20 — López-de-Prado meta-labeling primitives for the exit head.

Per-bar exit-panel rows are massively **overlapping**: the label for bar ``t`` and
bar ``t+1`` of the same trade (and of any concurrently-open trade) share almost
their whole forward window, so the samples are far from IID. Training/evaluating
on them naively over-counts redundant information and inflates apparent
significance. This module provides the standard de-Prado corrections
(*Advances in Financial Machine Learning*, ch. 4 & ch. 14), pure-python and
unit-testable:

- **average_uniqueness** — each sample's average uniqueness = mean over its label
  span of ``1 / concurrency``; the sample weights that down-weight overlapping
  labels (feed as ``sample_weight`` to the weighted logistic/ridge).
- **sequential_bootstrap** — draw a decorrelated bag (each draw favors samples
  that overlap least with those already drawn) for a bagged robustness estimate.
- **probabilistic_sharpe_ratio / deflated_sharpe_ratio** — is the exit policy's
  net-of-fee Sharpe real given non-normal returns AND the number of trials
  searched? (guards the multiple-testing overfit the per-bar volume invites).
- **pbo_cscv** — the Probability of Backtest Overfitting via Combinatorially
  Symmetric Cross-Validation across a small config grid.

Observe-only, Tier-1 by construction — arithmetic over supplied arrays; no I/O.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from itertools import combinations
from typing import Dict, List, Optional, Sequence, Tuple

Span = Tuple[int, int]  # (t0, t1) inclusive absolute bar indices


# ---------------------------------------------------------------------------
# concurrency + uniqueness (ch. 4)
# ---------------------------------------------------------------------------


def _concurrency(spans: Sequence[Span]) -> Dict[int, int]:
    """bar -> number of label spans covering it."""
    conc: Dict[int, int] = defaultdict(int)
    for t0, t1 in spans:
        if t1 < t0:
            continue
        for b in range(int(t0), int(t1) + 1):
            conc[b] += 1
    return conc


def average_uniqueness(spans: Sequence[Span]) -> List[float]:
    """Per-sample average uniqueness ``u_i = mean_{b in span_i} 1/concurrency[b]``.

    A sample whose label window is shared by many concurrent labels gets a small
    weight; a sample that is the sole occupant of its window gets 1.0. Returns a
    list aligned with ``spans`` (a degenerate ``t1 < t0`` span → 0.0). These are
    the ``sample_weight`` for the exit head's weighted fit.
    """
    conc = _concurrency(spans)
    out: List[float] = []
    for t0, t1 in spans:
        if t1 < t0:
            out.append(0.0)
            continue
        acc = 0.0
        length = int(t1) - int(t0) + 1
        for b in range(int(t0), int(t1) + 1):
            c = conc.get(b, 1)
            acc += 1.0 / c if c > 0 else 1.0
        out.append(acc / length if length else 0.0)
    return out


def sequential_bootstrap(
    spans: Sequence[Span], n_draws: Optional[int] = None, *, seed: int = 0
) -> List[int]:
    """A decorrelated bootstrap sample of indices into ``spans`` (ch. 4).

    Draw ``n_draws`` (default ``len(spans)``) indices one at a time; each draw is
    proportional to the candidate's average uniqueness **given the already-drawn
    set**, so the bag favors samples that overlap least with what's in it —
    reducing the redundancy of a naive IID bootstrap.

    Complexity is ``O(n_draws · N · avg_span)``, so the CALLER caps the candidate
    pool (``spans``) before calling this on a large panel — it is meant for a
    bounded per-fold pool, not the full 10⁵-row panel.
    """
    n = len(spans)
    if n == 0:
        return []
    draws = int(n_draws) if n_draws is not None else n
    rng = random.Random(seed)
    drawn: List[int] = []
    drawn_conc: Dict[int, int] = defaultdict(int)
    spans_i = [(int(t0), int(t1)) for t0, t1 in spans]
    for _ in range(draws):
        weights = []
        for t0, t1 in spans_i:
            if t1 < t0:
                weights.append(0.0)
                continue
            acc = 0.0
            length = t1 - t0 + 1
            for b in range(t0, t1 + 1):
                acc += 1.0 / (1.0 + drawn_conc[b])
            weights.append(acc / length)
        total = sum(weights)
        if total <= 0:
            idx = rng.randrange(n)
        else:
            pick = rng.random() * total
            acc = 0.0
            idx = n - 1
            for i, w in enumerate(weights):
                acc += w
                if acc >= pick:
                    idx = i
                    break
        drawn.append(idx)
        t0, t1 = spans_i[idx]
        for b in range(t0, t1 + 1):
            drawn_conc[b] += 1
    return drawn


# ---------------------------------------------------------------------------
# normal CDF / inverse-CDF (pure — no scipy in the runner)
# ---------------------------------------------------------------------------


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse normal CDF (Acklam's rational approximation; |err| < 1.2e-9)."""
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def _moments(returns: Sequence[float]) -> Tuple[float, float, float, float, int]:
    xs = [float(r) for r in returns if r is not None and r == r]
    n = len(xs)
    if n < 2:
        return 0.0, 0.0, 0.0, 3.0, n
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / n
    sd = math.sqrt(var) if var > 0 else 0.0
    if sd == 0:
        return mean, 0.0, 0.0, 3.0, n
    skew = sum(((x - mean) / sd) ** 3 for x in xs) / n
    kurt = sum(((x - mean) / sd) ** 4 for x in xs) / n
    return mean, sd, skew, kurt, n


def sharpe_ratio(returns: Sequence[float]) -> Optional[float]:
    """Non-annualized Sharpe (mean/stdev) of a per-observation return series."""
    mean, sd, _, _, n = _moments(returns)
    if n < 2 or sd == 0:
        return None
    return mean / sd


def probabilistic_sharpe_ratio(
    returns: Sequence[float], *, sr_benchmark: float = 0.0
) -> Optional[float]:
    """PSR — P(true Sharpe > ``sr_benchmark``) given non-normal returns (ch. 14)."""
    mean, sd, skew, kurt, n = _moments(returns)
    if n < 2 or sd == 0:
        return None
    sr = mean / sd
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if denom <= 0:
        return None
    z = (sr - sr_benchmark) * math.sqrt(n - 1) / math.sqrt(denom)
    return _norm_cdf(z)


def expected_max_sharpe(n_trials: int, variance_of_trial_sr: float) -> float:
    """The expected MAXIMUM Sharpe across ``n_trials`` independent null trials.

    ``sr0 = sqrt(var_sr) · ((1-γ)·Φ⁻¹(1 - 1/T) + γ·Φ⁻¹(1 - 1/(T·e)))`` — the
    de-Prado benchmark the deflated Sharpe deflates against (γ = Euler-Mascheroni).
    """
    if n_trials < 2 or variance_of_trial_sr <= 0:
        return 0.0
    gamma = 0.5772156649015329
    t = float(n_trials)
    term = (1 - gamma) * _norm_ppf(1 - 1.0 / t) + gamma * _norm_ppf(1 - 1.0 / (t * math.e))
    return math.sqrt(variance_of_trial_sr) * term


def deflated_sharpe_ratio(
    returns: Sequence[float], *, n_trials: int, variance_of_trial_sr: float
) -> Optional[float]:
    """DSR — the PSR deflated by the expected-max-Sharpe of ``n_trials`` (ch. 14).

    DSR > 0.95 is the usual bar for "the observed Sharpe is real after accounting
    for how many configs were searched." ``variance_of_trial_sr`` is the variance
    of the per-trial Sharpe estimates across the search grid.
    """
    sr0 = expected_max_sharpe(n_trials, variance_of_trial_sr)
    return probabilistic_sharpe_ratio(returns, sr_benchmark=sr0)


# ---------------------------------------------------------------------------
# PBO via CSCV (ch. 11)
# ---------------------------------------------------------------------------


def pbo_cscv(perf_matrix: Sequence[Sequence[float]], *, n_blocks: int = 8) -> Dict[str, object]:
    """Probability of Backtest Overfitting via Combinatorially Symmetric CV.

    ``perf_matrix`` is ``T × C`` — ``T`` per-observation performance samples (rows)
    for each of ``C`` configs (cols). Splits the rows into ``n_blocks`` contiguous
    blocks; over every balanced IS/OOS block partition, picks the best-mean config
    IS and records its OOS rank; PBO = fraction of partitions where the IS-best
    config lands **below the OOS median** (logit λ < 0). Needs ``C ≥ 2`` and an
    even ``n_blocks``; returns ``{computed:false, note}`` otherwise.
    """
    rows = [list(map(float, r)) for r in perf_matrix]
    t = len(rows)
    c = len(rows[0]) if rows else 0
    if c < 2:
        return {"computed": False, "note": "need >= 2 configs for PBO"}
    if n_blocks % 2 != 0:
        n_blocks -= 1
    if n_blocks < 2 or t < n_blocks:
        return {"computed": False, "note": f"too few rows ({t}) for {n_blocks} blocks"}

    # contiguous block index ranges
    edges = [round(i * t / n_blocks) for i in range(n_blocks + 1)]
    blocks = [list(range(edges[i], edges[i + 1])) for i in range(n_blocks)]

    def _col_means(idxs: Sequence[int]) -> List[float]:
        if not idxs:
            return [0.0] * c
        return [sum(rows[i][j] for i in idxs) / len(idxs) for j in range(c)]

    lambdas: List[float] = []
    half = n_blocks // 2
    for is_blocks in combinations(range(n_blocks), half):
        is_set = set(is_blocks)
        is_idx = [i for b in is_blocks for i in blocks[b]]
        oos_idx = [i for b in range(n_blocks) if b not in is_set for i in blocks[b]]
        is_means = _col_means(is_idx)
        oos_means = _col_means(oos_idx)
        best = max(range(c), key=lambda j: is_means[j])
        # OOS rank of the IS-best config (fraction; 1 = best OOS)
        oos_sorted = sorted(range(c), key=lambda j: oos_means[j])
        rank = (oos_sorted.index(best) + 1) / (c + 1)
        rank = min(max(rank, 1e-6), 1 - 1e-6)
        lambdas.append(math.log(rank / (1 - rank)))

    if not lambdas:
        return {"computed": False, "note": "no CSCV partitions produced"}
    pbo = sum(1 for x in lambdas if x <= 0) / len(lambdas)
    return {
        "computed": True,
        "pbo": round(pbo, 4),
        "n_partitions": len(lambdas),
        "n_blocks": n_blocks,
        "n_configs": c,
        "median_logit": round(sorted(lambdas)[len(lambdas) // 2], 4),
        "note": "PBO = P(the IS-best config underperforms the OOS median). Lower is better; > 0.5 ⇒ overfit.",
    }
