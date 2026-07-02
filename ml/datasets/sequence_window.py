"""Causal sequence-window materialization (M19 T1.1).

A deep sequence model (TCN / Transformer) needs, for each bar ``i``, the feature
vectors of the last ``L`` bars ``[i-L+1 … i]``. But the ML eval/serve interface
scores **one row at a time** (``MulticlassPredictor.predict_label(row)``), and the
purged-walk-forward CV split breaks time-contiguity — so a predictor cannot
reconstruct a window from a rolling buffer over the eval rows alone (the L bars
before the first test sample live in the *train* fold, or are purged entirely).

The only correct design is therefore to **materialize the causal window as a
per-row feature** (``seq_window``) computed over the *contiguous* series
**before** the split. This module is that pure function. It is:

- **Causal** — bar ``i``'s window is bars ``[i-L+1 … i]`` (all ``≤ i``); never a
  forward bar. No look-ahead, so it adds no leakage axis beyond the forward
  *label* horizon the CV embargo already handles.
- **Group-aware** — windows never cross a ``(symbol, timeframe)`` boundary.
- **Fold-safe** — computed once over the full ordered series, so the window is
  identical regardless of which CV fold a bar lands in.
- **Dependency-free** — pure stdlib (no numpy/torch), so it imports on the
  money-box too: the eventual live per-bar scorer reuses this exact function to
  build the window from its live bar buffer, guaranteeing train == serve.

Rows without a full ``L``-bar history (the first ``L-1`` of each group) are
**dropped** — a partial window would be a different, ambiguous input.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

SEQ_WINDOW_COLUMN = "seq_window"


def _to_float(value: Any) -> float:
    """Coerce a feature value to float; missing/unparseable → 0.0.

    The `market_features` numeric columns this consumes (log_return,
    rolling_log_return_vol, hour_of_day, dayofweek) are always populated, so a
    fallback fires only on a genuinely malformed row — 0.0 (post-standardization,
    the channel mean) degrades that bar gracefully rather than crashing the build.
    """
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _row_vector(row: Mapping[str, Any], feature_columns: Sequence[str]) -> list[float]:
    return [_to_float(row.get(col)) for col in feature_columns]


def build_causal_windows(
    rows: Sequence[Mapping[str, Any]],
    *,
    feature_columns: Sequence[str],
    seq_len: int,
    time_column: str = "ts",
    group_columns: Sequence[str] = ("symbol", "timeframe"),
    window_column: str = SEQ_WINDOW_COLUMN,
) -> list[dict[str, Any]]:
    """Attach a causal ``seq_window`` (list[list[float]], shape ``(seq_len, F)``)
    to each row that has a full ``seq_len``-bar history within its group.

    Returns a NEW list of shallow-copied row dicts (original rows untouched),
    preserving every original key plus ``window_column``, in ascending-time order
    within each group. The first ``seq_len - 1`` rows of each group are dropped
    (incomplete window). All other keys — notably the target and ``time_column`` —
    are carried through so the CV splitter and trainer/evaluator see a normal row.
    """
    if seq_len < 1:
        raise ValueError(f"seq_len must be >= 1; got {seq_len}")
    if not feature_columns:
        raise ValueError("feature_columns must be non-empty")

    # Bucket by group key, preserving first-seen order for determinism.
    groups: dict[tuple, list[Mapping[str, Any]]] = {}
    for row in rows:
        key = tuple(str(row.get(c, "")) for c in group_columns)
        groups.setdefault(key, []).append(row)

    out: list[dict[str, Any]] = []
    for _key, group_rows in groups.items():
        ordered = sorted(group_rows, key=lambda r: (r.get(time_column) is None, r.get(time_column)))
        # Precompute each bar's feature vector once.
        vectors = [_row_vector(r, feature_columns) for r in ordered]
        for i in range(seq_len - 1, len(ordered)):
            window = [list(vectors[j]) for j in range(i - seq_len + 1, i + 1)]
            new_row = dict(ordered[i])
            new_row[window_column] = window
            out.append(new_row)
    return out
