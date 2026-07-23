"""M28 P1 — FRED live-adapter for the valuation feed.

Provides the ``fetch_fn`` / ``history_fn`` that :func:`valuation_feed.run_valuation_feed`
consumes, backed by **free, keyless** FRED ``fredgraph.csv`` series (the same
data source the repo's ML pipeline already uses via
``ml/datasets/adapters/fred_corpus.py``). Split so the parsing + per-metric
history alignment are **pure and fully unit-tested**, and only a thin network
layer touches the wire — that layer is **off-VM-guarded** (won't hit the network
on the live trading VM unless ``ICT_OFFVM_BUILD_HOST`` is set) and takes an
injectable ``urlopen`` so CI needs no network.

No order path. The live sleeve reads the *snapshots* this produces (written by a
later increment); it does not fetch FRED on the money box.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Mapping, Optional, Sequence

from .valuation_feed import _iter_metrics, required_series

_FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"
_TRUTHY = {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Pure parsing + per-metric history alignment (unit-tested, no network).
# ---------------------------------------------------------------------------


def parse_fredgraph_csv(text: str) -> list[float]:
    """Parse a FRED ``fredgraph.csv`` body → the ordered finite float series.

    Format is ``DATE,VALUE`` rows with a header line; missing observations are a
    literal ``"."``. Skips the header, missing values, and any unparseable row
    (honest, never raises)."""
    out: list[float] = []
    if not text:
        return out
    lines = [ln for ln in text.strip().splitlines() if ln]
    for ln in lines[1:]:  # skip header
        parts = ln.split(",")
        if len(parts) != 2:
            continue
        raw = parts[1].strip()
        if raw in (".", ""):
            continue
        try:
            out.append(float(raw))
        except ValueError:
            continue
    return out


def parse_fredgraph_csv_dated(text: str) -> list[tuple[str, float]]:
    """Parse a FRED ``fredgraph.csv`` body → ``[(DATE, value), ...]`` ascending.

    Same format/robustness as :func:`parse_fredgraph_csv` (skip header, missing
    ``"."``, and any unparseable row) but **keeps the observation date** — the
    point-in-time BACKFILL needs each value's as-of date to reconstruct what was
    known at a past instant. ``DATE`` is the raw ``YYYY-MM-DD`` FRED string (ISO,
    so lexical order == chronological)."""
    out: list[tuple[str, float]] = []
    if not text:
        return out
    lines = [ln for ln in text.strip().splitlines() if ln]
    for ln in lines[1:]:  # skip header
        parts = ln.split(",")
        if len(parts) != 2:
            continue
        date_s = parts[0].strip()
        raw = parts[1].strip()
        if not date_s or raw in (".", ""):
            continue
        try:
            out.append((date_s, float(raw)))
        except ValueError:
            continue
    return out


def _leaf_key(leaf: Any) -> Optional[str]:
    """Series-id / source-name of a metric input leaf (see valuation_feed)."""
    if isinstance(leaf, Mapping):
        return leaf.get("series") or leaf.get("source")
    if isinstance(leaf, str):
        return leaf
    return None


def _combine(a: Sequence[float], b: Sequence[float], fn: Callable[[float, float], Optional[float]]) -> list[float]:
    """Elementwise ``fn`` over the aligned tails of two series (drops None)."""
    n = min(len(a), len(b))
    if n == 0:
        return []
    ta, tb = a[len(a) - n:], b[len(b) - n:]
    out: list[float] = []
    for x, y in zip(ta, tb):
        try:
            v = fn(x, y)
        except Exception:  # noqa: BLE001
            v = None
        if v is not None:
            out.append(v)
    return out


def metric_histories(
    config: Mapping[str, Any], series_history: Mapping[str, Sequence[float]]
) -> dict[str, list[float]]:
    """Derive each metric's own history from the raw per-series histories, so
    the cheap/rich ``value_read`` compares like-for-like.

    Direct-series metrics inherit their series' history; ``term_slope`` /
    ``equity_risk_premium`` / ``gold_silver_ratio`` are element-aligned
    combinations. A metric whose inputs aren't in ``series_history`` (e.g. the
    not-yet-wired earnings-yield / metal-price sources) yields ``[]`` — honest-null,
    so ``value_read`` returns ``unknown`` rather than a spurious percentile.
    """
    out: dict[str, list[float]] = {}
    for m in _iter_metrics(config):
        name = str(m["metric"])
        if name in out:
            continue
        inputs = m.get("inputs", {}) or {}
        if name in ("real_yield_10y", "credit_spread"):
            out[name] = list(series_history.get(_leaf_key(inputs.get("series")), []))
        elif name == "term_slope":
            out[name] = _combine(
                series_history.get(_leaf_key(inputs.get("long")), []),
                series_history.get(_leaf_key(inputs.get("short")), []),
                lambda x, y: x - y,
            )
        elif name == "equity_risk_premium":
            out[name] = _combine(
                series_history.get(_leaf_key(inputs.get("earnings_yield")), []),
                series_history.get(_leaf_key(inputs.get("real_yield")), []),
                lambda x, y: x - y,
            )
        elif name == "gold_silver_ratio":
            out[name] = _combine(
                series_history.get(_leaf_key(inputs.get("gold")), []),
                series_history.get(_leaf_key(inputs.get("silver")), []),
                lambda x, y: (x / y) if y else None,
            )
        else:
            out[name] = []
    return out


# ---------------------------------------------------------------------------
# Thin network layer (off-VM-guarded, urlopen-injectable).
# ---------------------------------------------------------------------------


def _offvm_enabled() -> bool:
    return str(os.environ.get("ICT_OFFVM_BUILD_HOST", "")).lower() in _TRUTHY


def fetch_fred_series_history(
    series_ids: Sequence[str], *, urlopen=None, timeout: float = 25.0
) -> dict[str, list[float]]:
    """Fetch each series' full history from FRED. Best-effort per series (a
    failure ⇒ ``[]`` for that id, never fatal).

    **Off-VM guard:** without an injected ``urlopen``, refuses unless
    ``ICT_OFFVM_BUILD_HOST`` is set — so the live trading VM never opens a FRED
    socket. Tests inject a fake ``urlopen``."""
    if urlopen is None:
        if not _offvm_enabled():
            raise RuntimeError(
                "fetch_fred_series_history: network fetch is off-VM only "
                "(set ICT_OFFVM_BUILD_HOST=1) or inject urlopen"
            )
        import urllib.request
        urlopen = urllib.request.urlopen

    out: dict[str, list[float]] = {}
    for sid in series_ids:
        try:
            with urlopen(_FRED_CSV_URL.format(sid), timeout=timeout) as resp:
                text = resp.read().decode()
            out[sid] = parse_fredgraph_csv(text)
        except Exception:  # noqa: BLE001
            out[sid] = []
    return out


def fetch_fred_series_history_dated(
    series_ids: Sequence[str], *, urlopen=None, timeout: float = 25.0
) -> dict[str, list[tuple[str, float]]]:
    """Dated sibling of :func:`fetch_fred_series_history` — returns
    ``{sid: [(date, val), ...]}`` for the point-in-time backfill. Same off-VM
    guard (needs ``ICT_OFFVM_BUILD_HOST`` unless ``urlopen`` is injected) and the
    same best-effort per-series degradation (a failure ⇒ ``[]`` for that id)."""
    if urlopen is None:
        if not _offvm_enabled():
            raise RuntimeError(
                "fetch_fred_series_history_dated: network fetch is off-VM only "
                "(set ICT_OFFVM_BUILD_HOST=1) or inject urlopen"
            )
        import urllib.request
        urlopen = urllib.request.urlopen

    out: dict[str, list[tuple[str, float]]] = {}
    for sid in series_ids:
        try:
            with urlopen(_FRED_CSV_URL.format(sid), timeout=timeout) as resp:
                text = resp.read().decode()
            out[sid] = parse_fredgraph_csv_dated(text)
        except Exception:  # noqa: BLE001
            out[sid] = []
    return out


def fred_fetch_and_history(config: Mapping[str, Any], *, urlopen=None, timeout: float = 25.0):
    """Build the ``(fetch_fn, history_fn)`` pair for ``run_valuation_feed`` from a
    single FRED pull. Fetches the config's required series once, then closes over
    the results (latest value per series + per-metric history)."""
    req = required_series(config)
    hist = fetch_fred_series_history(req["series"], urlopen=urlopen, timeout=timeout)
    latest = {sid: (h[-1] if h else None) for sid, h in hist.items()}
    mhist = metric_histories(config, hist)

    def fetch_fn(ids: Sequence[str]) -> dict[str, Optional[float]]:
        return {i: latest.get(i) for i in ids}

    def history_fn(metric: str) -> list[float]:
        return mhist.get(metric, [])

    return fetch_fn, history_fn
