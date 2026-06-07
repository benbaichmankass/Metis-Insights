"""ADWIN drift detector (S-MLOPT-S16, M14 Phase 4.1, 2026-06-07).

Adaptive Windowing (Bifet & Gavaldà 2007) — an online drift detector for
streaming numeric values. Keeps a growing window of recent observations,
and at every update checks whether there exists a cut point that splits
the window into two sub-windows with statistically distinguishable means
(a Hoeffding-bound test). When such a cut exists, the older sub-window
is dropped — the detector "forgets" the stale regime and signals drift.

This complements the existing window-over-window KS/PSI in
``ml.shadow.drift``:

- **KS/PSI** is a snapshot two-sample comparator. It answers "is the
  current 7-day distribution different from the prior 30-day
  distribution?" — useful for a daily report, but the window edges are
  arbitrary and a real drift event has to wait for its window to fill.
- **ADWIN** is an online change detector. It answers "has the streaming
  mean changed *at all* since the last drift, and if so, by how many
  observations is the stale tail?" — useful for triggering a retrain the
  moment a head's score distribution shifts, instead of waiting on the
  daily timer.

Pure-stdlib. Operates on numeric streams; the trigger orchestrator
(``run_drift_retrain.sh`` via ``python -m ml drift-retrain``) feeds in
each deployed head's shadow scores and listens for the boolean drift
flag, then dispatches a recency-weighted retrain when it fires.

Reference: Bifet & Gavaldà, "Learning from Time-Changing Data with
Adaptive Windowing" (SDM 2007).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable


# Default confidence (delta) for the Hoeffding bound. 0.002 matches the
# canonical River default and is conservative — we'd rather miss a
# subtle drift than wake the trainer for noise (a retrain is cheap but
# not free, and downstream gate-check still has to clear before
# anything live-influencing happens). Tightenable per-model via the
# detector constructor.
DEFAULT_DELTA: float = 0.002

# Minimum total window length before the cut search is even attempted.
# With fewer than ~10 observations the Hoeffding bound is loose enough
# that random noise can trip; this floor avoids spurious early drift.
MIN_WINDOW: int = 10

# Cap on window length so a permanently-stationary head doesn't grow
# the detector unboundedly. Once exceeded, the oldest observation is
# dropped on every update; the detector still detects drift inside the
# cap. 10k observations is a few weeks of bar-cadence scoring for the
# 1h heads — plenty of context, bounded memory.
DEFAULT_MAX_WINDOW: int = 10_000


@dataclass
class ADWIN:
    """Adaptive-windowing drift detector for streaming scalar values.

    Usage::

        det = ADWIN(delta=0.002)
        for x in stream:
            if det.update(x):
                # drift detected; the older portion of the window has
                # been dropped — det.width now reflects the recent regime
                trigger_retrain()

    The detector is destructive: a drift event resets the window to the
    *post-cut* portion (the recent regime), as in the original paper. A
    second drift can fire immediately if the freshly-shortened window
    itself splits — this is desired behaviour for fast-moving series.

    The implementation is the **straightforward O(n) per-update** form,
    not the bucket-compressed O(log n) variant from the paper. That's
    fine here: ``update()`` runs at most once per closed bar (~5min for
    the fastest head, ~1h for the slowest), so the per-update cost is
    irrelevant. We trade efficiency for a small, auditable
    implementation that pure-stdlib + tests can pin.
    """

    delta: float = DEFAULT_DELTA
    min_window: int = MIN_WINDOW
    max_window: int = DEFAULT_MAX_WINDOW
    _window: list[float] = field(default_factory=list)
    last_drift_index: int = -1  # cumulative index at which last drift fired
    _index: int = 0  # running count of observations seen

    @property
    def width(self) -> int:
        return len(self._window)

    @property
    def mean(self) -> float:
        return sum(self._window) / len(self._window) if self._window else 0.0

    def variance(self) -> float:
        """Population variance of the current window (0 when n ≤ 1)."""
        n = len(self._window)
        if n <= 1:
            return 0.0
        m = self.mean
        return sum((x - m) ** 2 for x in self._window) / n

    def reset(self) -> None:
        self._window.clear()

    def update(self, value: float) -> bool:
        """Feed in one observation; return True iff drift was detected.

        Behaviour:
          1. Append ``value`` to the window.
          2. Trim from the front if ``max_window`` is exceeded.
          3. Walk every valid cut point; on the first one where the
             two-sample mean gap exceeds the Hoeffding-style bound at
             confidence ``delta``, drop the older half and return True.

        Returns False when the window is shorter than ``min_window`` or
        when no cut crosses the bound.
        """
        x = float(value)
        if not math.isfinite(x):
            # Skip silently — the stream is operational data and one
            # garbage value shouldn't reset the detector.
            return False
        self._window.append(x)
        self._index += 1
        # Front-trim cap. Strictly bounded memory; doesn't itself
        # signal drift.
        if len(self._window) > self.max_window:
            del self._window[: len(self._window) - self.max_window]
        if len(self._window) < self.min_window:
            return False
        cut = self._find_cut()
        if cut is None:
            return False
        # Drift: retain only the post-cut (recent) half. Older half is
        # the stale regime ADWIN tells us to forget.
        del self._window[:cut]
        self.last_drift_index = self._index
        return True

    def _find_cut(self) -> int | None:
        """Return the first cut index whose mean gap beats the bound, or None.

        Cuts are 1 .. n-1 (each cut splits into a non-empty left + non-empty
        right). The Hoeffding-style bound used here is the standard ADWIN
        form (Bifet & Gavaldà 2007, eq. 4):

            epsilon_cut = sqrt( (1 / (2 * m)) * ln(4 * w / delta) )

        where ``m`` is the harmonic mean of the two sub-window sizes
        (``m = (n0 * n1) / (n0 + n1)``) and ``w`` is the total window
        width. The bound shrinks as either sub-window grows or as ``w``
        grows, so a stationary stream eventually drives ``epsilon_cut``
        below any noise floor.
        """
        n = len(self._window)
        w = float(n)
        total = sum(self._window)
        left_sum = 0.0
        for cut in range(1, n):
            left_sum += self._window[cut - 1]
            n0 = cut
            n1 = n - cut
            mean_left = left_sum / n0
            mean_right = (total - left_sum) / n1
            gap = abs(mean_left - mean_right)
            # Harmonic-mean window size; protects against the cut going
            # too close to either end (where one side is tiny and the
            # bound blows up).
            m_hat = (n0 * n1) / (n0 + n1)
            if m_hat <= 0:
                continue
            epsilon_cut = math.sqrt((1.0 / (2.0 * m_hat)) * math.log(4.0 * w / self.delta))
            if gap > epsilon_cut:
                return cut
        return None


@dataclass(frozen=True)
class DriftEvent:
    """One drift detection result for a model_id under ADWIN scan."""

    model_id: str
    drift_detected: bool
    n_observations: int
    n_window_after: int
    mean_window_after: float
    last_drift_index: int

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "drift_detected": self.drift_detected,
            "n_observations": self.n_observations,
            "n_window_after": self.n_window_after,
            "mean_window_after": self.mean_window_after,
            "last_drift_index": self.last_drift_index,
        }


def scan_stream(
    values: Iterable[float],
    *,
    model_id: str,
    delta: float = DEFAULT_DELTA,
    min_window: int = MIN_WINDOW,
    max_window: int = DEFAULT_MAX_WINDOW,
) -> DriftEvent:
    """Drive an ``ADWIN`` over an entire score stream and return the result.

    The orchestrator uses this to ask "did this head's score
    distribution drift at any point in the last N records?" The
    detector is fed in chronological order; ``drift_detected`` is True
    iff at least one drift event fired during the scan. The post-scan
    window is the most recent regime; ``mean_window_after`` is its
    mean, useful for logging the post-drift regime alongside the
    pre-drift one the report comparison shows.
    """
    det = ADWIN(delta=delta, min_window=min_window, max_window=max_window)
    drift_any = False
    n = 0
    for x in values:
        n += 1
        if det.update(x):
            drift_any = True
    return DriftEvent(
        model_id=model_id,
        drift_detected=drift_any,
        n_observations=n,
        n_window_after=det.width,
        mean_window_after=det.mean,
        last_drift_index=det.last_drift_index,
    )
