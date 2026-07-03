"""Live TSFM quantile-forecast feature serving (M19 Track-1 PR 1b).

Serves the ``fc_*`` quantile-forecast feature block on the LIVE money-box at
score time, so a ``shadow``-stage forecast head sees the SAME features live that
it trained on (train==live parity). Unlike the offline / trainer side, the live
reader runs **no forecaster** — it does NOT import torch, chronos, pandas, or
numpy. It only READS a tiny pre-computed JSON artifact
(``runtime_logs/trainer_mirror/forecasts/<SYMBOL>.json``) that the trainer-side
producer (``scripts/ml/publish_live_forecasts.py``, PR 1a) wrote and
``scripts/ops/publish_trainer_mirror.sh`` rsynced onto this VM. The heavy Chronos
call stays entirely trainer-side; the live path is a stdlib file read.

**Observe-only.** This only conditions a shadow-stage regime/forecast head's
feature row; nothing here touches the order path. Wired into the per-bar regime
scorer (`src/runtime/regime_bar_scoring.py`), which is itself observe-only.

**Fail-permissive.** A missing artifact, a parse error, the kill switch, or a
timeframe mismatch all return ``None`` (the caller then merges nothing → the
head scores its ``fc_*`` columns as NaN, which the LightGBM predictor handles as
missing — the honest degraded state, NOT a fabricated all-zero vector the model
never saw in training). The producer computes the row from the SAME pure
``ml.datasets.forecast_features`` fn the offline build uses, so a served row is
bit-for-bit the offline row for that bar.

**Kill switch:** ``FORECAST_LIVE_DISABLED`` truthy → every read returns ``None``
(the feature merge becomes a no-op → the head degrades to NaN ``fc_*``).

This mirrors the structure of ``src.runtime.cross_asset_live`` (the D2a
peer-feature precedent): a module-level kill switch, an mtime-cached read, a
``head_wants_forecast`` / ``group_needs_forecast`` gate (true iff a head's
feature list carries any ``fc_*`` column), and fail-permissive degradation.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Mapping, Sequence

# The fixed forecast feature columns — the single source of truth shared by the
# offline builder, the PR-1a producer, and this serve reader. NEVER hardcode the
# list here. ``forecast_features`` is stdlib-only (math + typing), so importing
# it adds ZERO heavy deps to the live import chain.
from ml.datasets.forecast_features import FORECAST_FEATURE_COLUMNS


def forecast_live_disabled() -> bool:
    """``FORECAST_LIVE_DISABLED`` truthy → skip live forecast features."""
    raw = os.environ.get("FORECAST_LIVE_DISABLED", "").strip().lower()  # allow-silent: observe-only shadow-feature kill-switch, default-ON (inverse of the BUG-039 default-OFF capability gate); only conditions a shadow head's features, never the live/dry order path (RiskManager.dry_run stays the only execution switch)
    return raw in ("1", "true", "yes", "on")


def _norm(value: Any) -> str:
    """Lowercase-stripped string, for the timeframe parity comparison."""
    return str(value or "").strip().lower()


def head_wants_forecast(feature_columns: Sequence[Any] | None) -> bool:
    """True iff ``feature_columns`` contains any column starting ``fc_``.

    The cheap gate the scorer uses to decide whether a head trained on the
    forecast block. Fail-permissive (any iteration error → False, so a head we
    can't classify is simply not fed forecast features rather than crashing).
    """
    try:
        return any(str(c).startswith("fc_") for c in (feature_columns or []))
    except Exception:
        return False


def _predictor_feature_columns(predictor: Any) -> list:
    """Best-effort frozen feature-column list off a (possibly wrapped) predictor.

    Digs to the wrapped LightGBM predictor's frozen feature list, mirroring
    ``cross_asset_live.head_wants_cross_asset``. Fail-permissive → ``[]``.
    """
    try:
        wrapped = predictor
        for attr in ("wrapped", "_wrapped"):
            inner = getattr(wrapped, attr, None)
            if inner is not None:
                wrapped = inner
        cols = (
            getattr(wrapped, "_feature_columns", None)
            or getattr(wrapped, "feature_columns", None)
            or []
        )
        return list(cols)
    except Exception:
        return []


def group_needs_forecast(predictors: Sequence[Any]) -> bool:
    """True if any head in the group trained on a ``fc_*`` column.

    The pre-check the scorer uses to decide whether to READ the forecast
    artifact for a ``(symbol, timeframe)`` group at all.
    """
    return any(head_wants_forecast(_predictor_feature_columns(p)) for p in predictors)


# --------------------------------------------------------------------------- #
# artifact read (runtime_logs/trainer_mirror/forecasts/<SYMBOL>.json)
# — cached, mtime-refreshed, like cross_asset_live's config cache
# --------------------------------------------------------------------------- #
_read_lock = threading.Lock()
# symbol -> (mtime, parsed_artifact_dict_or_None)
_artifact_cache: dict[str, tuple[float, dict | None]] = {}


def _forecasts_dir() -> str:
    """The trainer-mirror forecasts dir on the live VM, or ``""``.

    Resolved through the same ``runtime_logs_dir()`` helper the rest of the live
    path uses (so it tracks ``DATA_DIR``), landing at
    ``runtime_logs/trainer_mirror/forecasts/`` — where
    ``scripts/ops/publish_trainer_mirror.sh`` rsyncs the producer's per-symbol
    artifacts. ``src.utils.paths`` is stdlib-only, imported lazily so this module
    stays importable in a bare env. Best-effort: any resolution error → ``""``.
    """
    try:
        from src.utils.paths import runtime_logs_dir

        return str(runtime_logs_dir() / "trainer_mirror" / "forecasts")
    except Exception:
        return ""


def _forecast_path(symbol: str) -> str:
    """``<forecasts_dir>/<SYMBOL>.json`` (matches the producer's write path)."""
    d = _forecasts_dir()
    if not d:
        return ""
    return os.path.join(d, f"{symbol}.json")


def _load_artifact(path: str) -> dict | None:
    """Read + JSON-parse the per-symbol artifact. Fail-permissive → ``None``."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def compute_live_forecast_row(
    symbol: str, *, timeframe: str | None = None
) -> dict[str, float] | None:
    """The current bar's ``fc_*`` feature dict for ``symbol``, or ``None``.

    Reads ``runtime_logs/trainer_mirror/forecasts/<SYMBOL>.json`` (mtime-cached,
    so an rsync refresh is picked up) and returns its ``fc_row`` projected onto
    the canonical :data:`FORECAST_FEATURE_COLUMNS`, each coerced to ``float``.

    Returns ``None`` (fail-permissive — the caller then leaves the head's
    ``fc_*`` columns missing/NaN, the honest degraded state) when:

    - the kill switch ``FORECAST_LIVE_DISABLED`` is set;
    - the forecasts dir can't be resolved;
    - the per-symbol artifact file is missing / unreadable / malformed;
    - ``timeframe`` is given AND the artifact was published for a DIFFERENT
      timeframe — a **parity guard** so a (say) 15m forecast is never merged
      onto a head scored on a different cadence; and
    - the projected row would be empty (no usable ``fc_*`` value).

    No staleness/age drop is applied by default: a missing/refreshed file is the
    real freshness signal (the producer overwrites the artifact each run and the
    mirror rsyncs it), and dropping a slightly-old row would only turn a usable
    forecast into a NaN degrade. (If a bound is ever wanted, add it here as a
    generous, configurable window — never a tight default.)
    """
    if forecast_live_disabled():
        return None
    sym = str(symbol)
    path = _forecast_path(sym)
    if not path:
        return None
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None  # file missing — the main degraded case, no artifact yet
    with _read_lock:
        cached = _artifact_cache.get(sym)
        if cached is None or cached[0] != mtime:
            artifact = _load_artifact(path)
            _artifact_cache[sym] = (mtime, artifact)
        else:
            artifact = cached[1]
    if not artifact:
        return None
    # Parity guard: never merge a forecast published for a different cadence.
    if timeframe is not None:
        art_tf = artifact.get("timeframe")
        if art_tf is not None and _norm(art_tf) != _norm(timeframe):
            return None
    fc_row = artifact.get("fc_row")
    if not isinstance(fc_row, Mapping):
        return None
    out: dict[str, float] = {}
    for col in FORECAST_FEATURE_COLUMNS:
        if col in fc_row:
            try:
                out[col] = float(fc_row[col])
            except (TypeError, ValueError):
                continue
    return out or None


__all__ = [
    "forecast_live_disabled",
    "head_wants_forecast",
    "group_needs_forecast",
    "compute_live_forecast_row",
]
