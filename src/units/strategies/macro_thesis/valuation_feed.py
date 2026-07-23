"""M28 P1 — valuation feed composition (config → per-instrument value reads).

The **pure** half of the fundamental/value feed: given the seed-universe config
(``config/macro_valuation.yaml``) and a dict of already-fetched series values
(+ per-series history), assemble each instrument's value metric, run it through
the :mod:`valuation` cheap/fair/rich engine, and shape point-in-time
``valuation_snapshots`` rows (M28-P0 schema).

The **live** half — actually pulling the free FRED series — is a thin adapter
added on the trainer VM (off-VM, matching the existing
``ml/datasets/adapters/fred_corpus.py`` ``ICT_OFFVM_BUILD_HOST`` guard). Keeping
the composition pure means it is fully unit-testable offline: inject the series,
assert the reads. Nothing here touches an order path or the network.

Honest-null throughout: a metric whose input series are missing yields an
``unknown`` read with ``value=None`` and a ``missing`` note — never a fabricated
number, never an exception.
"""

from __future__ import annotations

import os
from typing import Any, Mapping, Optional

from . import valuation
from .valuation import (
    ValueRead,
    credit_spread,
    equity_risk_premium,
    gold_silver_ratio,
    term_slope,
    value_read,
)

# Default config lives beside the other config/*.yaml.
_DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..", "config", "macro_valuation.yaml"
)


def load_valuation_config(path: Optional[str] = None) -> dict:
    """Load ``config/macro_valuation.yaml``. Fail-permissive → ``{}`` on any error."""
    try:
        import yaml  # local import so the pure metric layer needs no yaml
        with open(path or _DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _lookup(leaf: Any, series_values: Mapping[str, Any]) -> Optional[float]:
    """Resolve one input leaf to a finite float from ``series_values``.

    A leaf is a bare series-id string (``"DGS10"``), or a dict declaring the
    lookup key under ``series`` (FRED id) or ``source`` (a non-FRED input like a
    price/earnings-yield the feed injects under that name). Missing / non-finite
    ⇒ ``None`` (honest-null)."""
    if isinstance(leaf, Mapping):
        key = leaf.get("series") or leaf.get("source")
    else:
        key = leaf
    if not key:
        return None
    v = series_values.get(key)
    return float(v) if valuation._is_finite(v) else None


def compute_metric(
    metric: str, inputs: Mapping[str, Any], series_values: Mapping[str, Any]
) -> Optional[float]:
    """Compute one metric's raw value from the injected series. None (honest-null)
    when any required input is missing or the metric name is unknown."""
    if metric == "real_yield_10y":
        # Direct TIPS real-yield read (DFII10); no computation.
        return _lookup(inputs.get("series"), series_values)
    if metric == "credit_spread":
        return credit_spread(_lookup(inputs.get("series"), series_values)) \
            if _lookup(inputs.get("series"), series_values) is not None else None
    if metric == "term_slope":
        return term_slope(
            _lookup(inputs.get("long"), series_values),
            _lookup(inputs.get("short"), series_values),
        ) if (_lookup(inputs.get("long"), series_values) is not None
              and _lookup(inputs.get("short"), series_values) is not None) else None
    if metric == "equity_risk_premium":
        ey = _lookup(inputs.get("earnings_yield"), series_values)
        ry = _lookup(inputs.get("real_yield"), series_values)
        return equity_risk_premium(ey, ry) if (ey is not None and ry is not None) else None
    if metric == "gold_silver_ratio":
        gold = _lookup(inputs.get("gold"), series_values)
        silver = _lookup(inputs.get("silver"), series_values)
        return gold_silver_ratio(gold, silver) if (gold is not None and silver is not None) else None
    return None


def _read_to_row(
    read: ValueRead, *, symbol: str, asset_class: str, inputs: Mapping[str, Any],
    observed_at: str, as_of: str, source: str,
) -> dict:
    """Shape a value read into a ``valuation_snapshots`` row (M28-P0 schema)."""
    return {
        "symbol": symbol,
        "asset_class": asset_class,
        "metric": read.metric,
        "value": read.value,
        "cheap_score": read.cheap_score,
        "label": read.label,
        "z_score": read.z_score,
        "percentile": read.percentile,
        "n_history": read.n,
        "higher_is_cheaper": read.higher_is_cheaper,
        "as_of": as_of,
        "observed_at": observed_at,
        "source": source,
        "inputs": dict(inputs),
        "note": read.note,
    }


def build_valuation_reads(
    config: Mapping[str, Any],
    series_values: Mapping[str, Any],
    series_history: Mapping[str, Any],
    *,
    observed_at: str,
    as_of: str,
    source: str = "fred",
) -> list[dict]:
    """Assemble ``valuation_snapshots`` rows for every instrument×metric + macro
    context read declared in *config*.

    - ``series_values`` — latest value per series-id / source-name.
    - ``series_history`` — per-**metric** history sequence (keyed by metric name)
      for the cheap/rich percentile+z read. A metric with no history yields an
      ``unknown`` label (honest-null) but the row is still emitted (records the
      attempt + the point-in-time value).
    - ``observed_at`` / ``as_of`` — the point-in-time stamps (caller-supplied so
      this stays pure/deterministic; no clock read here).

    Returns rows in declaration order. Never raises — a malformed entry is
    skipped, not fatal.
    """
    rows: list[dict] = []

    def _emit(group: Mapping[str, Any]):
        for symbol, spec in (group or {}).items():
            if not isinstance(spec, Mapping):
                continue
            asset_class = str(spec.get("asset_class", "unknown"))
            for m in spec.get("metrics", []) or []:
                if not isinstance(m, Mapping):
                    continue
                metric = str(m.get("metric", ""))
                inputs = m.get("inputs", {}) or {}
                higher_is_cheaper = bool(m.get("higher_is_cheaper", True))
                if not metric:
                    continue
                value = compute_metric(metric, inputs, series_values)
                hist = series_history.get(metric, []) or []
                read = value_read(
                    metric, value, hist, higher_is_cheaper=higher_is_cheaper
                )
                rows.append(_read_to_row(
                    read, symbol=symbol, asset_class=asset_class, inputs=inputs,
                    observed_at=observed_at, as_of=as_of, source=source,
                ))

    _emit(config.get("instruments", {}))
    _emit(config.get("context", {}))
    return rows


# ---------------------------------------------------------------------------
# Feed runner — resolves what to fetch and orchestrates fetch → compute → rows.
# The fetch + history callables are INJECTED so this stays pure/offline-testable;
# the live implementations are thin trainer-side adapters over the existing
# free FRED fetchers (ml/datasets/adapters/fred_corpus.py) + the corpus panel.
# ---------------------------------------------------------------------------


def _input_keys(inputs: Mapping[str, Any]):
    """Yield ``("series"|"source", key)`` for every input leaf of a metric.

    A leaf is a bare series-id string (``"DGS10"``), or a dict declaring
    ``series`` (a free FRED id) or ``source`` (a non-FRED input to be wired
    later — earnings yield, metal price)."""
    for leaf in (inputs or {}).values():
        if isinstance(leaf, Mapping):
            if leaf.get("series"):
                yield ("series", str(leaf["series"]))
            elif leaf.get("source"):
                yield ("source", str(leaf["source"]))
        elif isinstance(leaf, str) and leaf:
            yield ("series", leaf)


def _iter_metrics(config: Mapping[str, Any]):
    """Yield every metric spec across instruments + context."""
    for group in ("instruments", "context"):
        for spec in (config.get(group, {}) or {}).values():
            if not isinstance(spec, Mapping):
                continue
            for m in spec.get("metrics", []) or []:
                if isinstance(m, Mapping) and m.get("metric"):
                    yield m


def required_series(config: Mapping[str, Any]) -> dict:
    """Resolve what the feed must fetch: the free FRED series ids + the not-yet-
    wired ``source`` names (equity earnings yield, metal prices). Deterministic,
    sorted; the fetch adapter pulls ``series`` and the ``sources`` are the P1
    follow-on inputs that currently honest-null."""
    series_ids: set[str] = set()
    sources: set[str] = set()
    for m in _iter_metrics(config):
        for kind, key in _input_keys(m.get("inputs", {})):
            (series_ids if kind == "series" else sources).add(key)
    return {"series": sorted(series_ids), "sources": sorted(sources)}


def run_valuation_feed(
    config: Mapping[str, Any],
    fetch_fn,
    *,
    observed_at: str,
    as_of: str,
    history_fn=None,
    source: str = "fred",
) -> list[dict]:
    """Run the feed: fetch the required series, gather per-metric history, and
    build the ``valuation_snapshots`` rows.

    - ``fetch_fn(series_ids: list[str]) -> Mapping[str, float]`` — the injected
      (trainer-side) free-FRED fetcher; returns latest value per series/source id.
    - ``history_fn(metric_name: str) -> Sequence[float] | None`` — optional;
      returns the metric's own history for the cheap/rich read (the adapter
      computes it from series history). Absent ⇒ no history ⇒ ``unknown`` labels
      (honest-null; the point-in-time value is still recorded).

    Never raises: a fetch/history exception degrades that input to missing.
    """
    req = required_series(config)
    try:
        series_values = dict(fetch_fn(req["series"]) or {})
    except Exception:  # noqa: BLE001
        series_values = {}

    series_history: dict[str, Any] = {}
    if history_fn is not None:
        for m in _iter_metrics(config):
            name = str(m["metric"])
            if name in series_history:
                continue
            try:
                series_history[name] = history_fn(name) or []
            except Exception:  # noqa: BLE001
                series_history[name] = []

    return build_valuation_reads(
        config, series_values, series_history,
        observed_at=observed_at, as_of=as_of, source=source,
    )
