"""Per-sample training weights — recency decay + de Prado average uniqueness.

S-MLOPT-S2 (M14 Session 0.2). Opt-in via `trainer_config.sample_weight`; when
the knob is absent the trainers behave exactly as before (Tier-1,
default-preserving — same discipline as the S-MLOPT-S1 splitter).

Two independent, composable factors, both returned as a multiplicative weight
vector the LightGBM trainers fold into any `class_weight` already in play:

  - **Recency (age decay)** — `half_life_days: N`. A sample `t` days older than
    the most recent training row gets weight ``0.5 ** (t / N)``. Directly
    attacks the "wide window dilutes the recent regime" finding
    (MB-20260601-001): older history still trains the model but counts for
    less, so a 5-year window can keep its sample size without drowning the
    recent volatility regime.
  - **Average uniqueness** (de Prado, *AFML* Ch. 4) — `uniqueness: true`. Labels
    whose forward windows overlap many other labels are *concurrent* and carry
    redundant information; each sample is down-weighted by the average, over its
    label span, of ``1 / concurrency``. NOTE: for **fixed-horizon** bar labels
    (a constant `label_horizon`) concurrency is near-constant in the interior,
    so this factor is close to a uniform rescale — it only bites meaningfully
    once label spans *vary* (triple-barrier / variable holding times, Phase 1).
    Implemented generally here so that work can reuse it.

The combined recency×uniqueness vector is **mean-normalised to 1.0** before it
multiplies any `class_weight`, so it re-weights *within* the sample without
silently rescaling the class-weight semantics.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


def _parse_ts(value: Any) -> float | None:
    """Best-effort parse to epoch seconds. Accepts epoch int/float or ISO-8601
    (``Z`` / offset / naive treated as UTC). Returns None if unparseable."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    # Pure epoch string.
    try:
        return float(s)
    except ValueError:
        pass
    iso = s.replace("Z", "+00:00") if s.endswith("Z") else s
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def recency_weights(timestamps: Sequence[float], half_life_days: float) -> list[float]:
    """``0.5 ** (age_days / half_life_days)`` per row, age relative to the
    newest timestamp. All-equal timestamps → all weights 1.0."""
    if half_life_days <= 0:
        raise ValueError(f"half_life_days must be > 0; got {half_life_days}")
    newest = max(timestamps)
    hl_seconds = half_life_days * 86400.0
    return [0.5 ** ((newest - ts) / hl_seconds) for ts in timestamps]


def average_uniqueness_weights(
    starts: Sequence[int], ends: Sequence[int]
) -> list[float]:
    """de Prado average uniqueness over integer label spans ``[start, end]``
    (inclusive). ``uniqueness_i = mean_{t in span_i} 1 / concurrency(t)`` where
    concurrency(t) = number of spans covering bar ``t``. Returns a weight per
    span in the input order."""
    n = len(starts)
    if n == 0:
        return []
    if len(ends) != n:
        raise ValueError("starts and ends must be the same length")
    max_t = max(ends)
    # Concurrency via a difference array over [0, max_t].
    diff = [0] * (max_t + 2)
    for s, e in zip(starts, ends):
        if e < s:
            raise ValueError(f"span end {e} before start {s}")
        diff[s] += 1
        diff[e + 1] -= 1
    concurrency = [0] * (max_t + 1)
    running = 0
    for t in range(max_t + 1):
        running += diff[t]
        concurrency[t] = running
    out: list[float] = []
    for s, e in zip(starts, ends):
        span = e - s + 1
        acc = 0.0
        for t in range(s, e + 1):
            c = concurrency[t]
            acc += 1.0 / c if c > 0 else 0.0
        out.append(acc / span if span > 0 else 0.0)
    return out


def compute_sample_weights(
    timestamps: Sequence[Any],
    config: Mapping[str, Any],
) -> list[float] | None:
    """Resolve the opt-in ``trainer_config.sample_weight`` block into a
    mean-1.0 multiplicative weight per row, or None if nothing is enabled.

    ``config`` is the ``sample_weight`` sub-mapping. Recognised keys:
      - ``half_life_days`` (float > 0) — enables recency decay.
      - ``uniqueness`` (bool) — enables average-uniqueness down-weighting.
      - ``label_horizon`` (int >= 0, default 1) — span width for uniqueness
        (fixed-horizon bar labels); rows are ranked by timestamp and span
        ``[rank, rank + label_horizon]``.

    Raises if a factor is enabled but a timestamp is missing/unparseable —
    fail-loud rather than silently mis-weight a money-adjacent model.
    """
    if not isinstance(config, Mapping):
        raise ValueError("sample_weight must be a mapping")
    half_life = config.get("half_life_days")
    use_recency = half_life is not None
    use_uniqueness = bool(config.get("uniqueness", False))
    if not use_recency and not use_uniqueness:
        return None

    n = len(timestamps)
    if n == 0:
        return None
    parsed = [_parse_ts(t) for t in timestamps]
    if any(p is None for p in parsed):
        bad = next(i for i, p in enumerate(parsed) if p is None)
        raise ValueError(
            "sample_weight needs a parseable timestamp on every row; "
            f"row {bad} has {timestamps[bad]!r}"
        )

    weights = [1.0] * n
    if use_recency:
        rec = recency_weights(parsed, float(half_life))
        weights = [w * r for w, r in zip(weights, rec)]
    if use_uniqueness:
        horizon = int(config.get("label_horizon", 1))
        if horizon < 0:
            raise ValueError(f"label_horizon must be >= 0; got {horizon}")
        # Rank rows chronologically; fixed-horizon span over the ranks.
        order = sorted(range(n), key=lambda i: parsed[i])
        rank = [0] * n
        for r, i in enumerate(order):
            rank[i] = r
        starts = [rank[i] for i in range(n)]
        ends = [min(rank[i] + horizon, n - 1) for i in range(n)]
        uniq = average_uniqueness_weights(starts, ends)
        weights = [w * u for w, u in zip(weights, uniq)]

    # Mean-normalise to 1.0 so this re-weights within the sample without
    # rescaling any class_weight it later multiplies.
    mean = sum(weights) / n
    if mean <= 0:
        return None
    return [w / mean for w in weights]
