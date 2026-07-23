"""M28 signal-construction toolkit — pure, tested transforms over dated series.

The three first-pass sleeves (value/COT/crypto) each hand-rolled the SAME weakest
construction: trailing-percentile of a single raw series, contrarian. This module
factors the **unexplored construction dimensions** (see
`docs/research/M28-signal-research-methodology.md`) into pure functions so a new
construction is a few lines over the existing readers, not a new bespoke sleeve:

- **D1 transform** — `pct_change_series` (change/impulse), `zscore_series`
  (rolling z), `divergence_series` (A vs B), `detrend_series`.
- **D2 conditioning** — `condition_snapshots` (neutralize a signal unless a gate
  series meets a predicate).
- **D3 cross-section** — `cross_sectional_snapshots` (rank instruments against each
  OTHER per date, not each vs its own history).
- **D4 composite** — `composite_series` (blend aligned series into one).

Every transform is a leakage-safe `dated-series -> dated-series` (or a snapshot
post-processor) that feeds the UNCHANGED `crypto_signals_data.build_percentile_snapshots`
emit path, so the valuation-snapshot schema + `observed_at`/`as_of` PIT discipline +
the P4/horizon gate all stay identical. Stdlib-only; no IO (all IO stays in the
backfill scripts). Dates are ISO `YYYY-MM-DD` strings; series are `[(date, value), ...]`.
"""

from __future__ import annotations

import statistics
from typing import Callable, Optional

# A dated series is a list of (iso_date, float) pairs.


def _sorted(series) -> list:
    return sorted([(d, float(v)) for d, v in (series or []) if v is not None], key=lambda x: x[0])


# ---------------------------------------------------------------------------
# D1 · transforms (change / z-score / divergence / detrend)
# ---------------------------------------------------------------------------


def pct_change_series(series, *, periods: int = 1, relative: bool = False) -> list:
    """Δ over ``periods`` steps (the impulse form). ``relative=True`` → fractional
    change ``(x_t - x_{t-k}) / |x_{t-k}|`` (guard 0 denom → skip); else the raw
    difference. Leakage-safe: value at t uses only t and t−periods. The first
    ``periods`` points have no predecessor and are dropped."""
    s = _sorted(series)
    out = []
    for i in range(periods, len(s)):
        d, cur = s[i]
        prev = s[i - periods][1]
        if relative:
            if prev == 0:
                continue
            out.append((d, (cur - prev) / abs(prev)))
        else:
            out.append((d, cur - prev))
    return out


def zscore_series(series, *, lookback: int = 90, min_history: int = 30) -> list:
    """Rolling trailing z-score: at each t, z of the current value vs the trailing
    ``lookback`` window ending at t (inclusive). Leakage-safe (past-only window).
    Emits from the ``min_history``-th point; a zero-stdev window is skipped."""
    s = _sorted(series)
    out = []
    for i in range(len(s)):
        if i + 1 < min_history:
            continue
        window = [v for _d, v in s[max(0, i + 1 - lookback):i + 1]]
        if len(window) < 2:
            continue
        try:
            sd = statistics.pstdev(window)
        except statistics.StatisticsError:
            continue
        if sd <= 0:
            continue
        out.append((s[i][0], (s[i][1] - statistics.fmean(window)) / sd))
    return out


def _align(a, b) -> list:
    """Inner-join two dated series on date → [(date, a_val, b_val), ...] ascending."""
    bd = dict(_sorted(b))
    return [(d, av, bd[d]) for d, av in _sorted(a) if d in bd]


def divergence_series(series_a, series_b, *, lookback: int = 90, min_history: int = 30) -> list:
    """The DIVERGENCE between two series as their **rolling-z gap** (`z_a − z_b`),
    aligned on date. The classic positioning edge (COT large-spec vs commercial;
    crypto funding vs basis) lives in the divergence, not either level. Both legs are
    z-scored on their OWN trailing window first (so different units/scales compare),
    then subtracted on the intersecting dates. Leakage-safe via `zscore_series`."""
    za = dict(zscore_series(series_a, lookback=lookback, min_history=min_history))
    zb = dict(zscore_series(series_b, lookback=lookback, min_history=min_history))
    return [(d, za[d] - zb[d]) for d in sorted(za) if d in zb]


def detrend_series(series, *, lookback: int = 90, min_history: int = 30) -> list:
    """Deviation from a trailing simple-moving-average trend (the residual). At t:
    value − mean(trailing ``lookback``). Leakage-safe; emits from ``min_history``."""
    s = _sorted(series)
    out = []
    for i in range(len(s)):
        if i + 1 < min_history:
            continue
        window = [v for _d, v in s[max(0, i + 1 - lookback):i + 1]]
        out.append((s[i][0], s[i][1] - statistics.fmean(window)))
    return out


# ---------------------------------------------------------------------------
# D4 · composite (blend aligned series)
# ---------------------------------------------------------------------------


def composite_series(series_list, *, weights: Optional[list] = None) -> list:
    """Weighted mean of several dated series on their intersecting dates (D4). Each
    leg should already be on a comparable scale (z-score or percentile) before
    blending. ``weights`` defaults to equal; length must match ``series_list``."""
    if not series_list:
        return []
    dicts = [dict(_sorted(s)) for s in series_list]
    w = weights if weights is not None else [1.0] * len(dicts)
    if len(w) != len(dicts):
        raise ValueError("weights length must match series_list")
    common = set(dicts[0])
    for d in dicts[1:]:
        common &= set(d)
    tot = float(sum(w)) or 1.0
    return [(day, sum(wi * dd[day] for wi, dd in zip(w, dicts)) / tot) for day in sorted(common)]


# ---------------------------------------------------------------------------
# D2 · conditioning (neutralize a signal unless a gate predicate holds)
# ---------------------------------------------------------------------------


def condition_snapshots(snapshots, gate_series, predicate: Callable[[float], bool],
                        *, neutral: float = 0.5) -> list:
    """Gate emitted valuation-snapshot rows on a second series (D2): keep a row's
    ``cheap_score`` only when ``predicate(gate_value_on_that_date)`` is True,
    otherwise pull it to ``neutral`` (0.5 = no conviction, so the P4/horizon gate
    reads it as a flat, non-actionable read). A row whose ``as_of`` date is absent
    from ``gate_series`` is neutralized (honest: no gate reading → no conviction).
    Relabels + records the gate outcome in ``inputs`` so the ledger can trace it.
    Returns NEW row dicts (does not mutate input)."""
    gate = dict(_sorted(gate_series))
    out = []
    for row in snapshots:
        r = dict(row)
        gv = gate.get(r.get("as_of"))
        passed = gv is not None and bool(predicate(gv))
        if not passed:
            r["cheap_score"] = neutral
            r["label"] = "fair"
        inp = dict(r.get("inputs") or {})
        inp["conditioned"] = {"gate_value": gv, "passed": passed}
        r["inputs"] = inp
        r["note"] = (r.get("note") or "") + (" [conditioned:pass]" if passed else " [conditioned:neutralized]")
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# D3 · cross-section (rank instruments against EACH OTHER per date)
# ---------------------------------------------------------------------------


def cross_sectional_snapshots(series_by_symbol, metric, *, asset_class_by_symbol=None,
                              higher_is_cheaper: bool = True, min_symbols: int = 3,
                              note: str = "", source: str = "xsec") -> list:
    """Cross-sectional construction (D3): on each date, rank the symbols against EACH
    OTHER (not each vs its own history) and emit a valuation-snapshot row per symbol
    whose ``cheap_score`` is its cross-sectional rank that day (cheapest→1). This is
    the classic value/carry basket construction — long the cheapest, short the
    richest — where the market-neutral edge often lives.

    ``series_by_symbol``: ``{symbol: [(date, value), ...]}``. A date is scored only
    when ≥ ``min_symbols`` symbols report it (a real cross-section). Leakage-safe:
    each date uses only that date's cross-section (no future, no own-history window).
    ``higher_is_cheaper`` orients the rank (e.g. a higher ERP = cheaper equity → True).
    """
    # Gather per-date {symbol: value}.
    by_date: dict = {}
    for sym, series in (series_by_symbol or {}).items():
        for d, v in _sorted(series):
            by_date.setdefault(d, {})[sym] = v
    acls = asset_class_by_symbol or {}
    out = []
    for day in sorted(by_date):
        row = by_date[day]
        if len(row) < min_symbols:
            continue
        n = len(row)
        for sym, val in row.items():
            below = sum(1 for o in row.values() if o < val)
            equal = sum(1 for o in row.values() if o == val)
            pct = (below + 0.5 * equal) / n           # cross-sectional percentile
            cheap = pct if higher_is_cheaper else (1.0 - pct)
            out.append({
                "symbol": sym,
                "asset_class": acls.get(sym, "unknown"),
                "metric": metric,
                "value": val,
                "cheap_score": cheap,
                "label": "cheap" if cheap >= 0.7 else "rich" if cheap <= 0.3 else "fair",
                "z_score": None,
                "percentile": pct,
                "n_history": n,                        # here: cross-section width, not history depth
                "higher_is_cheaper": higher_is_cheaper,
                "as_of": day,
                "observed_at": day,
                "source": source,
                "inputs": {"value": val, "cross_section_n": n},
                "note": note,
            })
    return out
