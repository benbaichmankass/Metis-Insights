#!/usr/bin/env python3
"""Month-over-month consistency scoring for backtests (S-STRAT-IMPROVE-S9).

Operator directive (2026-05-24): track how STABLE a strategy
configuration's returns are month-over-month, so we don't pick strategies
that look great because of a few exceptional periods but are otherwise
negative or mediocre. This is a *descriptive* score for now (not yet a
hard accept/reject gate) — it travels in every backtest summary so the
month-by-month profile is always visible alongside the headline net-R.

Pure-stdlib + the (time, net_r) stream every backtest already produces;
no pandas dependency so it can be reused anywhere.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple


def _month_key(ts: Any) -> str:
    """Return ``YYYY-MM`` for a timestamp-ish value (str or datetime)."""
    s = str(ts)
    # ISO-8601 / "YYYY-MM-DD ..." both start "YYYY-MM"; slice is robust to
    # the trailing time/zone and to pandas.Timestamp's str().
    return s[:7]


def monthly_consistency(
    events: Iterable[Tuple[Any, float]],
) -> Dict[str, Any]:
    """Score month-over-month return stability from a ``(time, net_r)`` stream.

    Parameters
    ----------
    events : iterable of (timestamp, net_r)
        One entry per trade — the same ``net_r`` (net-of-fee R) the
        backtest already computes, keyed by entry time.

    Returns
    -------
    dict with:
      - ``months``                  — number of distinct calendar months traded
      - ``months_positive``         — count of months with net_r > 0
      - ``pct_months_positive``     — that as a percentage (the headline
                                      "how often does it actually work" number)
      - ``monthly_mean_r``          — mean of per-month net_r
      - ``monthly_std_r``           — population std of per-month net_r
      - ``consistency_ratio``       — monthly_mean / monthly_std (a monthly
                                      Sharpe-like; high = steady, low/neg =
                                      lumpy or period-dependent). ``None``
                                      when std == 0.
      - ``worst_month_r`` / ``best_month_r``
      - ``max_consecutive_negative_months`` — longest losing streak (drawdown
                                      in *time*, the "usually mediocre" tell)
      - ``top_month_share``         — fraction of total net_r contributed by
                                      the single best month (high → the edge
                                      leans on one exceptional period)
      - ``by_month``                — ``{YYYY-MM: round(net_r, 4)}`` (sorted)
    """
    buckets: Dict[str, float] = {}
    for ts, net_r in events:
        k = _month_key(ts)
        buckets[k] = buckets.get(k, 0.0) + float(net_r)

    if not buckets:
        return {
            "months": 0, "months_positive": 0, "pct_months_positive": 0.0,
            "monthly_mean_r": 0.0, "monthly_std_r": 0.0,
            "consistency_ratio": None, "worst_month_r": 0.0,
            "best_month_r": 0.0, "max_consecutive_negative_months": 0,
            "top_month_share": 0.0, "by_month": {},
        }

    months = sorted(buckets)
    vals: List[float] = [buckets[m] for m in months]
    n = len(vals)
    total = sum(vals)
    mean = total / n
    var = sum((v - mean) ** 2 for v in vals) / n
    std = var ** 0.5
    positive = sum(1 for v in vals if v > 0)

    # Longest run of consecutive non-positive months.
    longest_neg = cur = 0
    for v in vals:
        if v <= 0:
            cur += 1
            longest_neg = max(longest_neg, cur)
        else:
            cur = 0

    best = max(vals)
    # Share of total *positive* return carried by the best month. Guard the
    # degenerate total<=0 case (a net loser): share is not meaningful, 0.0.
    top_share = round(best / total, 4) if total > 0 else 0.0

    return {
        "months": n,
        "months_positive": positive,
        "pct_months_positive": round(100.0 * positive / n, 1),
        "monthly_mean_r": round(mean, 4),
        "monthly_std_r": round(std, 4),
        "consistency_ratio": round(mean / std, 3) if std > 0 else None,
        "worst_month_r": round(min(vals), 4),
        "best_month_r": round(best, 4),
        "max_consecutive_negative_months": longest_neg,
        "top_month_share": top_share,
        "by_month": {m: round(buckets[m], 4) for m in months},
    }
