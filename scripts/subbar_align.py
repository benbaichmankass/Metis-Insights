"""Map each leg bar to the finer bars inside it — the ONE definition.

Built for the intrabar exit-evaluation experiment
(``docs/live-exit-monitor-cadence-DESIGN.md`` § 4): a 1h leg's entries are
decided on 1h bars while its exits are evaluated on the 5m bars within each,
so the harness needs a faithful leg-bar -> sub-bar index.

**The coverage report is the load-bearing part, not the mapping.** A leg bar
with no sub-bars is not an error and not an empty result — it is a bar the
finer frame does not describe, and an exit arm that silently falls back to
bar-close evaluation there would dilute the A/B by an unstated amount and
report the diluted number as the intrabar result. That is the
`rCoverage`/`pnlCoverage` discipline applied to a time axis: report how much of
the population the finer grain actually covers, never a bare verdict over an
unstated denominator.

So ``align`` returns coverage alongside the mapping, and the caller is expected
to refuse to grade a run whose coverage is materially below 1.0 rather than to
quietly average over the gap.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


def _to_utc_epoch(values: Sequence[Any]) -> List[Optional[float]]:
    """Timestamps -> UTC epoch seconds. Unparseable rows become None.

    Deliberately tolerant of the two shapes this repo's frames carry — the
    loaders differ on ``utc=True`` (``backtest_ict_scalp._load_candles`` omits
    it, ``backtest_trend`` does not), so a naive and an aware column must both
    land on the same axis before they are compared. Comparing them directly is
    a TypeError, which is the failure this function exists to prevent.
    """
    import pandas as pd

    ts = pd.to_datetime(pd.Series(list(values)), utc=True, errors="coerce")
    out: List[Optional[float]] = []
    for v in ts:
        out.append(None if pd.isna(v) else float(v.timestamp()))
    return out


def _median_spacing(epochs: Sequence[Optional[float]]) -> Optional[float]:
    """Median gap between consecutive parseable stamps, in seconds.

    MEASURED from the frame rather than taken from the leg's declared
    timeframe: a frame with gaps, a resampled frame, or a mislabelled file
    would otherwise silently get the wrong bar width, and the last bar's window
    (which has no successor to bound it) is derived from this.
    """
    gaps = []
    prev = None
    for e in epochs:
        if e is None:
            continue
        if prev is not None and e > prev:
            gaps.append(e - prev)
        prev = e
    if not gaps:
        return None
    gaps.sort()
    n = len(gaps)
    return gaps[n // 2] if n % 2 else 0.5 * (gaps[n // 2 - 1] + gaps[n // 2])


def align(leg_times: Sequence[Any], fine_times: Sequence[Any]) -> Dict[str, Any]:
    """Index the finer frame by leg bar.

    Returns::

        {
          "slices":   [(start, stop), ...]   one per leg bar, half-open into fine
          "coverage": float | None           share of leg bars with >= 1 sub-bar
          "covered":  int,   "total": int,
          "empty_bars": [i, ...]             leg bars the finer frame misses
          "leg_seconds": float | None, "fine_seconds": float | None,
          "reason": str | None               why the mapping is unusable, if so
        }

    A leg bar owns the fine rows in ``[t_i, t_{i+1})`` — half-open, so a fine
    bar stamped exactly on a leg boundary belongs to the NEW leg bar and is
    never double-counted. The final leg bar has no successor, so its window is
    ``[t_last, t_last + leg_seconds)`` using the MEASURED median spacing.

    ``reason`` is set (and coverage left None) when the mapping cannot be
    trusted at all — no parseable stamps, or a "finer" frame that is not
    actually finer. Both are refusals, not degraded results: resampling a
    coarser frame up would fabricate sub-bars that never traded.
    """
    leg = _to_utc_epoch(leg_times)
    fine = _to_utc_epoch(fine_times)
    total = len(leg)
    out: Dict[str, Any] = {"slices": [(0, 0)] * total, "coverage": None,
                           "covered": 0, "total": total, "empty_bars": [],
                           "leg_seconds": None, "fine_seconds": None,
                           "reason": None}
    if total == 0:
        out["reason"] = "leg frame is empty"
        return out
    if not any(e is not None for e in fine):
        out["reason"] = "finer frame has no parseable timestamps"
        return out

    leg_s = _median_spacing(leg)
    fine_s = _median_spacing(fine)
    out["leg_seconds"], out["fine_seconds"] = leg_s, fine_s
    if leg_s is None:
        out["reason"] = "leg frame has fewer than two parseable timestamps"
        return out
    if fine_s is not None and fine_s >= leg_s:
        # Not finer. Proceeding would map ~one sub-bar per leg bar and the
        # "intrabar" arm would silently be the baseline wearing a new label.
        out["reason"] = (f"frame is not finer than the leg ({fine_s:.0f}s vs "
                         f"{leg_s:.0f}s) — refusing to fabricate sub-bars")
        return out

    # Fine rows are assumed ascending (every loader sorts). A two-pointer walk
    # keeps this O(n+m) rather than a per-bar scan; the frames are large enough
    # (370k 5m rows) that the quadratic version is not academic.
    slices: List[tuple] = []
    j, m = 0, len(fine)
    covered = 0
    empty: List[int] = []
    for i in range(total):
        t0 = leg[i]
        if t0 is None:
            slices.append((j, j))
            empty.append(i)
            continue
        t1 = None
        for k in range(i + 1, total):
            if leg[k] is not None:
                t1 = leg[k]
                break
        if t1 is None:
            t1 = t0 + leg_s
        while j < m and (fine[j] is None or fine[j] < t0):
            j += 1
        start = j
        while j < m and fine[j] is not None and fine[j] < t1:
            j += 1
        slices.append((start, j))
        if j > start:
            covered += 1
        else:
            empty.append(i)

    out["slices"] = slices
    out["covered"] = covered
    out["empty_bars"] = empty
    out["coverage"] = round(covered / total, 4) if total else None
    return out
