#!/usr/bin/env python3
"""Regime x direction net-R from ANY harness's emitted per-trade JSONL.

The companion to ``regime_matrix.py``. That tool drives the Donchian-trend
engine in-process; this one is **engine-agnostic** — it post-processes the
``--emit-trades`` JSONL that ``backtest_fade.py`` / ``backtest_squeeze.py`` /
``backtest_fvg_range.py`` / ``scripts/research/backtest_trend.py`` (and the
vwap harness) already write, so the whole roster goes through one tagger.

Why a post-processor: each harness simulates its own exits/fees and writes one
row per trade ``{strategy, entry_time, direction, gross_r, net_r, confidence}``
with ``net_r`` ALREADY fee-adjusted. We only need to label each trade by the
ADX regime *at its entry bar* and aggregate — the same regime primitive the
live strategies gate on (chop <20, transitional 20-25, trending >=25).

CRITICAL (the reconciliation lesson, see docs/research/session-handoff-2026-06-01.md):
the harness MUST be driven with the strategy's EXACT live params from
config/strategies.yaml before emitting — wrong params give a misleading matrix.
And ``--resample`` must be the strategy's live timeframe so the regime label is
computed on the same bars the strategy trades.

THE VOL AXIS (2026-07-30, BL-20260730-2D-VOL-CELLS-UNAUDITABLE):
the live router gates on a **2-D** ``(trend, vol)`` cell, not the 1-D trend cell
alone. Six authored ``trend_vol`` cells drop real BTC intents, and a 1-D grade of
a strategy that also carries a 2-D cell POOLS vol states live already refuses —
so its verdict measures a population live does not trade. Pass ``--vol-labels``
(the JSONL from ``ml_vol_label_replay.py``, which replays the **advisory ML
head** the router actually reads — NOT the frozen ``vol_detector``, whose label
is documented to behave oppositely) and every table gains the vol dimension.

Without ``--vol-labels`` the output is unchanged and is explicitly reported as
``vol_axis: absent`` — a 1-D grade is still correct for a strategy with no 2-D
cell, and must not be silently presented as if it covered the vol axis.

Research only (Tier-1). Reads OHLCV CSV / Parquet / JSONL + a trades JSONL.

Usage:
    python scripts/research/regime_tag_emitted.py \
        --trades /tmp/fade_trades.jsonl \
        --data data/btc_1h_multiyear.csv \
        --resample 4h --label fade_breakout_4h

    # 2-D (trend x vol) — the grade a strategy with a trend_vol cell needs:
    python scripts/research/regime_tag_emitted.py \
        --trades /tmp/squeeze_trades.jsonl --data data/btc_1h_multiyear.csv \
        --resample 4h --label squeeze_breakout_4h \
        --vol-labels /tmp/btc_vol_labels.jsonl
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from typing import Any, Dict, List

import pandas as pd

# Reuse the committed engines (same dir) regardless of cwd: the loader/resampler
# from the trend engine, the ADX + regime primitives from regime_matrix.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# `_load`/`_resample` were pure IO helpers on the retired research trend
# engine; they now live in scripts/candle_io.py (lifted verbatim, so this
# script's behaviour is unchanged). See
# BL-20260808-RESEARCH-TREND-ENGINE-RETIREMENT-BLOCKED-BY-TEST-COUPLING.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from candle_io import load_candles as _load  # type: ignore  # noqa: E402
from candle_io import resample_ohlcv as _resample  # type: ignore  # noqa: E402
from regime_matrix import (  # type: ignore  # noqa: E402
    _CHOP_MAX,
    _TREND_MIN,
    _adx,
    _regime,
    regime_distribution,
)

# The hard regime cutoffs the whole grade is bucketed on. A trade whose entry
# ADX sits near one of these is a coin-flip between two buckets under any
# equally-valid alternative candle feed — see boundary_exposure() below.
_REGIME_BOUNDARIES = (_CHOP_MAX, _TREND_MIN)

# Provisional flag threshold, NOT a validated gate (see the docstring of
# boundary_exposure). Stated in the output so a reader can re-judge it.
_FRAGILE_EXPOSURE_PCT = 25.0


def load_vol_labels(path: str) -> List[tuple]:
    """Load ``ml_vol_label_replay`` output into a time-sorted ``[(ts, label)]``.

    Only concrete ``calm``/``volatile`` rows are kept — an ``unknown`` bar is
    one the live gate would ALSO resolve permissively, so folding it in as a
    third bucket would invent a state the router never gates on.
    """
    rows: List[tuple] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            ts = d.get("ts")
            lab = d.get("vol_regime")
            if ts and lab in ("calm", "volatile"):
                rows.append((pd.to_datetime(ts, utc=True, errors="coerce"), lab))
    rows = [(t, lab) for t, lab in rows if t is not pd.NaT]
    rows.sort(key=lambda r: r[0])
    return rows


def vol_label_at(vol_labels: List[tuple], when: Any) -> str:
    """As-of lookup: the vol label of the bar at/just-before ``when``.

    Never reads a FUTURE bar's label (the live gate can only know the current
    bar), and returns ``"unknown"`` before the first labelled bar rather than
    borrowing the earliest one.
    """
    if not vol_labels or when is pd.NaT:
        return "unknown"
    lo, hi = 0, len(vol_labels)
    while lo < hi:  # bisect_right on the ts column
        mid = (lo + hi) // 2
        if vol_labels[mid][0] <= when:
            lo = mid + 1
        else:
            hi = mid
    return vol_labels[lo - 1][1] if lo > 0 else "unknown"


def _read_trades(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            # vwap emits ``net_pnl_r``; normalise to ``net_r``.
            if "net_r" not in d and "net_pnl_r" in d:
                d["net_r"] = d["net_pnl_r"]
            rows.append(d)
    return rows


def annotate_trades_with_regime(trades: List[Dict[str, Any]], adx: pd.Series,
                                df: pd.DataFrame,
                                vol_labels: List[tuple] | None = None) -> List[Dict[str, Any]]:
    """Return a copy of each trade with a ``regime`` field = the ADX regime at
    its entry bar (the same primitive ``tag_emitted_by_regime`` buckets on).

    When ``vol_labels`` is supplied each trade also gets ``vol_regime`` (the
    router's ML vol label as-of the entry bar) and ``cell`` = ``"<trend>/<vol>"``
    — the 2-D key ``config/regime_policy.yaml::trend_vol`` is written in.

    Trades whose ``entry_time`` is unparseable are dropped (they have no bar to
    label against) — the count is reported by the caller via the difference.
    """
    ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    out: List[Dict[str, Any]] = []
    for t in trades:
        et = pd.to_datetime(t.get("entry_time"), utc=True, errors="coerce")
        if et is pd.NaT:
            continue
        idx = ts.searchsorted(et, side="right") - 1  # nearest bar at/just-before entry
        a = float(adx.iloc[idx]) if 0 <= idx < len(adx) else float("nan")
        tagged = dict(t)
        tagged["regime"] = _regime(a)
        if vol_labels is not None:
            v = vol_label_at(vol_labels, et)
            tagged["vol_regime"] = v
            tagged["cell"] = f"{tagged['regime']}/{v}"
        out.append(tagged)
    return out


def tag_emitted_by_cell(trades: List[Dict[str, Any]], adx: pd.Series,
                        df: pd.DataFrame,
                        vol_labels: List[tuple]) -> Dict[str, Dict[str, Any]]:
    """Bucket each emitted trade's net R by the 2-D ``(trend, vol)`` cell.

    The vol-aware sibling of :func:`tag_emitted_by_regime`. Keys are
    ``"<trend>/<vol>"`` so they read exactly like the cells authored in
    ``config/regime_policy.yaml::trend_vol``.

    Trades whose entry bar has no vol label land in ``"<trend>/unknown"`` and are
    counted separately — they are NOT silently folded into calm or volatile,
    because an unlabelled trade is precisely a trade whose live gate outcome this
    tool cannot reconstruct.
    """
    tagged = annotate_trades_with_regime(trades, adx, df, vol_labels)
    by: Dict[str, Dict[str, Any]] = {}
    for t in tagged:
        key = str(t.get("cell") or "unknown/unknown")
        direction = str(t.get("direction", "?")).lower()
        net = float(t.get("net_r", 0.0))
        slot = by.setdefault(key, {"trades": 0, "wins": 0, "net_r": 0.0,
                                   "long_r": 0.0, "short_r": 0.0,
                                   "long_n": 0, "short_n": 0})
        slot["trades"] += 1
        slot["wins"] += 1 if net > 0 else 0
        slot["net_r"] = round(slot["net_r"] + net, 4)
        if direction == "short":
            slot["short_r"] = round(slot["short_r"] + net, 4)
            slot["short_n"] += 1
        else:
            slot["long_r"] = round(slot["long_r"] + net, 4)
            slot["long_n"] += 1
    for _key, s in by.items():
        s["win_pct"] = round(100 * s["wins"] / s["trades"], 1) if s["trades"] else 0.0
        s["exp_r"] = round(s["net_r"] / s["trades"], 4) if s["trades"] else 0.0
        s["long_exp_r"] = round(s["long_r"] / s["long_n"], 4) if s["long_n"] else None
        s["short_exp_r"] = round(s["short_r"] / s["short_n"], 4) if s["short_n"] else None
    return by


def _structural_floor_pct(regime: str, band: float) -> float | None:
    """Share of a regime's ADX WIDTH that is within ``band`` of a cutoff.

    The denominator that stops a tautology reading as an alarm. ``transitional``
    spans exactly [20, 25) — 5 ADX wide — so every point in it is within 2.5 of a
    boundary and the bucket is 100% "near" for any ``band >= 2.5``, regardless of
    the data. ``chop`` is [0, 20) with only ONE interior edge, so its floor is
    ``band/20``. ``trending`` is unbounded above, so no width-based floor exists
    and this returns ``None`` rather than inventing one.
    """
    width = _TREND_MIN - _CHOP_MAX
    if regime == "transitional":
        return round(min(100.0, 100.0 * min(2.0 * band, width) / width), 1)
    if regime == "chop":
        return round(min(100.0, 100.0 * band / _CHOP_MAX), 1)
    return None  # trending is unbounded; unknown has no ADX at all


def boundary_exposure(trades: List[Dict[str, Any]], adx: pd.Series, df: pd.DataFrame,
                      band: float = 2.0) -> Dict[str, Any]:
    """How much of this grade's net-R is decided by a coin-flip at a cutoff.

    THE DEFECT THIS MEASURES (BL-20260731-REGIME-ATTRIBUTION-FEED-SENSITIVE).
    ``_regime`` is a HARD cutoff (chop <20, transitional 20-25, trending >=25)
    applied to a noisy rolling indicator, and per-trade R is heavy-tailed. So a
    trade whose entry ADX sits at 24.9 vs 25.1 lands in a different bucket, and
    a handful of large winners near a boundary carry tens of R across it on
    sub-1% input differences.

    Measured live: the SAME 357 trades re-tagged against a second, equally-valid
    BTCUSDT 1h feed moved one bucket by **24.92R — ~31% of the strategy's
    lifetime net-R** — while the two feeds agreed on trade outcomes to 1.05R
    across 331 shared trades and on regime base rates to 0.5pp. The instrument
    moved; the market did not.

    This is the SINGLE-FEED proxy for that, and it is the primary metric
    precisely because it needs no second feed: it measures the mechanism
    directly rather than one sampled consequence of it. ``feed_sensitivity``
    below is the confirmatory two-feed version when a second feed is available.

    ``exposure_pct`` is share of ``sum |net_r|`` sitting within ``band`` ADX of
    a cutoff — an absolute-value share deliberately, because a +8R and a −8R
    trade near a boundary are BOTH one relabel away from moving, and netting
    them first would hide exactly that.

    ``fragile`` is a flag, not a verdict: the threshold is provisional
    (``_FRAGILE_EXPOSURE_PCT``), anchored on a single measured incident, and is
    reported alongside the raw numbers so a reader can re-judge it rather than
    quote it. It is deliberately NOT used to suppress or alter any number.
    """
    ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    rows: List[Dict[str, Any]] = []
    for t in trades:
        et = pd.to_datetime(t.get("entry_time"), utc=True, errors="coerce")
        if et is pd.NaT:
            continue
        idx = ts.searchsorted(et, side="right") - 1
        a = float(adx.iloc[idx]) if 0 <= idx < len(adx) else float("nan")
        if a != a:  # NaN — no bar to judge, counted but never called "safe"
            rows.append({"adx": None, "dist": None, "near": False, "unknown": True,
                         "net_r": float(t.get("net_r", 0.0)), "regime": "unknown"})
            continue
        dist = min(abs(a - b) for b in _REGIME_BOUNDARIES)
        rows.append({"adx": round(a, 4), "dist": round(dist, 4), "near": dist <= band,
                     "unknown": False, "net_r": float(t.get("net_r", 0.0)),
                     "regime": _regime(a)})

    total_abs = sum(abs(r["net_r"]) for r in rows)
    near = [r for r in rows if r["near"]]
    near_abs = sum(abs(r["net_r"]) for r in near)
    by_regime: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        slot = by_regime.setdefault(r["regime"], {"trades": 0, "near": 0,
                                                  "abs_net_r": 0.0, "near_abs_net_r": 0.0})
        slot["trades"] += 1
        slot["abs_net_r"] = round(slot["abs_net_r"] + abs(r["net_r"]), 4)
        if r["near"]:
            slot["near"] += 1
            slot["near_abs_net_r"] = round(slot["near_abs_net_r"] + abs(r["net_r"]), 4)
    for reg, slot in by_regime.items():
        slot["exposure_pct"] = (round(100 * slot["near_abs_net_r"] / slot["abs_net_r"], 1)
                                if slot["abs_net_r"] else 0.0)
        # STRUCTURAL FLOOR — without this, `transitional` reads ~100% exposed on
        # every run and the number looks like an alarm when it is a tautology:
        # the transitional band is only (25-20)=5 ADX wide, so the furthest any
        # point in it can sit from a cutoff is 2.5. At band >= 2.5 the bucket is
        # 100% "near" BY CONSTRUCTION, at band 2.0 it is 80% of the band's width.
        # Reporting exposure without its floor is the unprovenanced-diagnostic
        # class: a real number under a label that implies something it does not.
        slot["structural_floor_pct"] = _structural_floor_pct(reg, band)
        floor = slot["structural_floor_pct"]
        slot["exposure_above_floor_pct"] = (round(slot["exposure_pct"] - floor, 1)
                                            if floor is not None else None)

    exposure_pct = round(100 * near_abs / total_abs, 1) if total_abs else 0.0
    # The single most consequential relabel: one trade able to move a bucket by
    # this much on its own. A small exposure_pct with a huge max is still fragile.
    largest = max((abs(r["net_r"]) for r in near), default=0.0)
    return {
        "band_adx": band,
        "boundaries": list(_REGIME_BOUNDARIES),
        "trades": len(rows),
        "trades_near_boundary": len(near),
        "abs_net_r_total": round(total_abs, 4),
        "abs_net_r_near_boundary": round(near_abs, 4),
        "exposure_pct": exposure_pct,
        "largest_single_near_boundary_abs_r": round(largest, 4),
        "by_regime": by_regime,
        "fragile": exposure_pct >= _FRAGILE_EXPOSURE_PCT,
        "fragile_threshold_pct": _FRAGILE_EXPOSURE_PCT,
        "fragile_threshold_basis": "provisional heuristic, one measured incident — not a validated gate",
    }


def feed_sensitivity(trades: List[Dict[str, Any]], baseline: Dict[str, Dict[str, Any]],
                     adx_b: pd.Series, df_b: pd.DataFrame) -> Dict[str, Any]:
    """Re-tag the SAME trades against a second feed's ADX and report the delta.

    The confirmatory half of ``boundary_exposure``. Holding the trade set fixed
    is the whole point: any difference here is attributable to LABELLING alone,
    never to the two feeds generating different trades. That separation is what
    turned "two runs disagree" into "the labelling moved 79.9% of the swing".

    A ``sign_flip`` is the finding that matters most — a bucket that reads
    positive on one feed and negative on another cannot source a Tier-3 cell
    proposal in either direction, whatever its n.
    """
    rebucket: Dict[str, Dict[str, Any]] = {}
    for t in annotate_trades_with_regime(trades, adx_b, df_b):
        slot = rebucket.setdefault(str(t.get("regime")), {"trades": 0, "net_r": 0.0})
        slot["trades"] += 1
        slot["net_r"] = round(slot["net_r"] + float(t.get("net_r", 0.0)), 4)

    deltas: Dict[str, Dict[str, Any]] = {}
    flips: List[str] = []
    for reg in sorted(set(baseline) | set(rebucket)):
        if reg.startswith("_"):
            continue
        a_r = float(baseline.get(reg, {}).get("net_r", 0.0))
        b_r = float(rebucket.get(reg, {}).get("net_r", 0.0))
        flip = (a_r > 0 > b_r) or (b_r > 0 > a_r)
        if flip:
            flips.append(reg)
        deltas[reg] = {
            "net_r_feed_a": round(a_r, 4), "net_r_feed_b": round(b_r, 4),
            "delta_r": round(b_r - a_r, 4),
            "n_feed_a": int(baseline.get(reg, {}).get("trades", 0)),
            "n_feed_b": int(rebucket.get(reg, {}).get("trades", 0)),
            "sign_flip": flip,
        }
    worst = max((abs(d["delta_r"]) for d in deltas.values()), default=0.0)
    return {
        "by_regime": deltas,
        "max_abs_delta_r": round(worst, 4),
        "sign_flips": flips,
        "any_sign_flip": bool(flips),
        "note": ("same trade set on both feeds — every delta here is LABELLING, "
                 "not trade generation"),
    }


def vol_coverage(by_cell: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """How much of the graded population actually carries a vol label.

    The denominator that makes a 2-D grade meaningful: a cell table built from
    mostly-``unknown`` trades looks complete and measures nothing. Reported
    alongside every 2-D table so the coverage can never be assumed.
    """
    total = sum(s["trades"] for s in by_cell.values())
    unknown = sum(s["trades"] for k, s in by_cell.items() if k.endswith("/unknown"))
    return {
        "trades": total,
        "vol_labelled": total - unknown,
        "vol_unknown": unknown,
        "coverage_pct": round(100.0 * (total - unknown) / total, 1) if total else None,
    }


def tag_emitted_by_regime(trades: List[Dict[str, Any]], adx: pd.Series,
                          df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    """Bucket each emitted trade's (already-net) R by the ADX regime at entry.

    Mirrors ``regime_matrix.tag_trades_by_regime`` but consumes plain dicts with
    a precomputed ``net_r`` rather than engine Trade objects, so it works for any
    harness's JSONL.
    """
    ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    by: Dict[str, Dict[str, Any]] = {}
    skipped = 0
    for t in trades:
        et = pd.to_datetime(t.get("entry_time"), utc=True, errors="coerce")
        if et is pd.NaT:
            skipped += 1
            continue
        idx = ts.searchsorted(et, side="right") - 1  # nearest bar at/just-before entry
        a = float(adx.iloc[idx]) if 0 <= idx < len(adx) else float("nan")
        reg = _regime(a)
        direction = str(t.get("direction", "?")).lower()
        net = float(t.get("net_r", 0.0))
        slot = by.setdefault(reg, {"trades": 0, "wins": 0, "net_r": 0.0,
                                   "long_r": 0.0, "short_r": 0.0,
                                   "long_n": 0, "short_n": 0})
        slot["trades"] += 1
        slot["wins"] += 1 if net > 0 else 0
        slot["net_r"] = round(slot["net_r"] + net, 4)
        if direction == "short":
            slot["short_r"] = round(slot["short_r"] + net, 4)
            slot["short_n"] += 1
        else:
            slot["long_r"] = round(slot["long_r"] + net, 4)
            slot["long_n"] += 1
    for reg, s in by.items():
        s["win_pct"] = round(100 * s["wins"] / s["trades"], 1) if s["trades"] else 0.0
        s["exp_r"] = round(s["net_r"] / s["trades"], 4) if s["trades"] else 0.0
    if skipped:
        by["_skipped_no_entry_time"] = {"trades": skipped}
    return by


def _totals(by: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    longs = sum(s.get("long_r", 0.0) for r, s in by.items() if not r.startswith("_"))
    shorts = sum(s.get("short_r", 0.0) for r, s in by.items() if not r.startswith("_"))
    net = sum(s.get("net_r", 0.0) for r, s in by.items() if not r.startswith("_"))
    n = sum(s.get("trades", 0) for r, s in by.items() if not r.startswith("_"))
    return {"trades": n, "net_r": round(net, 3),
            "long_r": round(longs, 3), "short_r": round(shorts, 3)}


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description="Regime x direction net-R from emitted trade JSONL.")
    p.add_argument("--trades", required=True, help="per-trade JSONL from a harness's --emit-trades")
    p.add_argument("--data", required=True, help="OHLCV the harness ran on (CSV/Parquet/JSONL)")
    p.add_argument("--resample", default="1h", help="strategy's LIVE timeframe (regime is computed on these bars)")
    p.add_argument("--adx-period", type=int, default=14)
    p.add_argument("--label", default="strategy")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON for roster aggregation")
    p.add_argument("--emit-tagged", default=None,
                   help="write each trade + its entry `regime` field to this JSONL "
                        "(for a downstream regime-filtered walk-forward)")
    p.add_argument("--only-regime", default=None,
                   choices=["trending", "transitional", "chop", "unknown"],
                   help="when set with --emit-tagged, write ONLY trades in this regime")
    p.add_argument("--vol-labels", default=None,
                   help="per-bar ML vol labels JSONL from ml_vol_label_replay.py — "
                        "adds the 2-D (trend x vol) cell breakdown the live router gates on")
    p.add_argument("--only-vol", default=None, choices=["calm", "volatile", "unknown"],
                   help="when set with --emit-tagged, write ONLY trades in this vol "
                        "state (combine with --only-regime to isolate ONE live cell)")
    p.add_argument("--boundary-band", type=float, default=2.0,
                   help="ADX distance from a regime cutoff (20/25) within which a trade "
                        "is one relabel away from moving bucket (default 2.0)")
    p.add_argument("--sensitivity-data", default=None,
                   help="a SECOND candle feed for the same symbol/timeframe — re-tags the "
                        "SAME trades against its ADX and reports the per-bucket delta, "
                        "isolating LABELLING from trade generation")
    p.add_argument("--sensitivity-resample", default=None,
                   help="resample for --sensitivity-data (defaults to --resample)")
    a = p.parse_args(argv)

    df = _load(a.data)
    if a.resample:
        df = _resample(df, a.resample)
    adx = _adx(df, a.adx_period)
    trades = _read_trades(a.trades)

    vol_labels = load_vol_labels(a.vol_labels) if a.vol_labels else None
    if a.vol_labels and not vol_labels:
        # An empty labels file would silently produce an all-"unknown" 2-D table
        # that looks like a completed grade. Refuse instead.
        print(f"ERROR: --vol-labels {a.vol_labels} carried 0 usable "
              f"(calm|volatile) rows — refusing to emit a vacuous 2-D grade.",
              file=sys.stderr)
        return 2
    if a.only_vol and not vol_labels:
        print("ERROR: --only-vol requires --vol-labels.", file=sys.stderr)
        return 2

    if a.emit_tagged:
        tagged = annotate_trades_with_regime(trades, adx, df, vol_labels)
        if a.only_regime:
            tagged = [t for t in tagged if t.get("regime") == a.only_regime]
        if a.only_vol:
            tagged = [t for t in tagged if t.get("vol_regime") == a.only_vol]
        with open(a.emit_tagged, "w", encoding="utf-8") as fh:
            for t in tagged:
                fh.write(json.dumps(t, default=str) + "\n")

    by = tag_emitted_by_regime(trades, adx, df)
    dist = regime_distribution(adx)
    totals = _totals(by)
    by_cell = tag_emitted_by_cell(trades, adx, df, vol_labels) if vol_labels else None
    coverage = vol_coverage(by_cell) if by_cell else None

    # Always computed — this grade's own honesty metric, the sibling of
    # vol_coverage. It costs one pass over the trades and needs no extra input,
    # so there is no run for which it is unavailable.
    boundary = boundary_exposure(trades, adx, df, band=a.boundary_band)

    sensitivity = None
    if a.sensitivity_data:
        df_b = _load(a.sensitivity_data)
        rs_b = a.sensitivity_resample or a.resample
        if rs_b:
            df_b = _resample(df_b, rs_b)
        adx_b = _adx(df_b, a.adx_period)
        sensitivity = feed_sensitivity(trades, by, adx_b, df_b)
        sensitivity["feed_a"] = a.data
        sensitivity["feed_b"] = a.sensitivity_data

    if a.json:
        out = {"label": a.label, "resample": a.resample,
               "bars": int(len(df)), "regime_base_rate_pct": dist["pct"],
               "by_regime": by, "totals": totals,
               # Declared on EVERY run so a 1-D grade can never be mistaken for
               # one that covered the vol axis.
               "vol_axis": "present" if by_cell else "absent",
               "boundary_exposure": boundary,
               # Declared on EVERY run for the same reason vol_axis is: a grade
               # with no second feed must not read as one that was cross-checked.
               "feed_sensitivity_checked": bool(sensitivity)}
        if sensitivity:
            out["feed_sensitivity"] = sensitivity
        if by_cell:
            out["by_cell"] = by_cell
            out["vol_coverage"] = coverage
            out["vol_labels_path"] = a.vol_labels
        print(json.dumps(out, default=str))
        return 0

    print(f"strategy={a.label} tf={a.resample} trades={totals['trades']} "
          f"net_r={totals['net_r']} (long {totals['long_r']} / short {totals['short_r']})")
    print(f"data {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]} bars={len(df)}")
    print(f"regime base-rate (bars): chop={dist['pct']['chop']}% "
          f"transitional={dist['pct']['transitional']}% trending={dist['pct']['trending']}%")
    print("--- by regime (entry regime) x direction ---")
    print(f"  {'regime':13} {'trades':>6} {'win%':>6} {'net_r':>9} "
          f"{'long_r':>9}({'n':>4}) {'short_r':>9}({'n':>4})")
    for reg in ("trending", "transitional", "chop", "unknown"):
        s = by.get(reg)
        if not s:
            continue
        print(f"  {reg:13} {s['trades']:6} {s['win_pct']:6} {s['net_r']:9} "
              f"{s['long_r']:9}({s['long_n']:4}) {s['short_r']:9}({s['short_n']:4})")
    if "_skipped_no_entry_time" in by:
        print(f"  (skipped {by['_skipped_no_entry_time']['trades']} trades with unparseable entry_time)")

    if by_cell:
        print("--- by 2-D CELL (trend/vol at entry) x direction  [the axis the live router gates on] ---")
        print(f"  vol labels: {a.vol_labels}")
        print(f"  coverage:   {coverage['vol_labelled']}/{coverage['trades']} trades "
              f"carry a vol label ({coverage['coverage_pct']}%)")
        print(f"  {'cell':24} {'trades':>6} {'win%':>6} {'net_r':>9} {'exp_r':>8} "
              f"{'long_r':>9}({'n':>4}) {'short_r':>9}({'n':>4})")
        for trend in ("trending", "transitional", "chop", "unknown"):
            for vol in ("calm", "volatile", "unknown"):
                s = by_cell.get(f"{trend}/{vol}")
                if not s:
                    continue
                print(f"  {trend + '/' + vol:24} {s['trades']:6} {s['win_pct']:6} "
                      f"{s['net_r']:9} {s['exp_r']:8} "
                      f"{s['long_r']:9}({s['long_n']:4}) {s['short_r']:9}({s['short_n']:4})")
        if coverage["vol_unknown"]:
            print(f"  NOTE: {coverage['vol_unknown']} trades have NO vol label "
                  f"(entry before the labelled span, or an unscorable bar) — they sit "
                  f"in the */unknown rows and are NOT folded into calm/volatile.")
    else:
        print("--- vol axis: ABSENT (no --vol-labels) ---")
        print("  This is a 1-D TREND grade only. If this strategy carries a 2-D "
              "trend_vol cell in config/regime_policy.yaml, the numbers above POOL "
              "vol states the live gate already refuses — do NOT propose a cell "
              "change from them (BL-20260730-2D-VOL-CELLS-UNAUDITABLE).")

    print(f"--- BOUNDARY EXPOSURE (how much of this grade is a coin-flip at ADX "
          f"{'/'.join(str(b) for b in _REGIME_BOUNDARIES)}) ---")
    print(f"  band: +/-{boundary['band_adx']} ADX   "
          f"{boundary['trades_near_boundary']}/{boundary['trades']} trades near a cutoff")
    print(f"  |net_R| within band: {boundary['abs_net_r_near_boundary']} of "
          f"{boundary['abs_net_r_total']}  = {boundary['exposure_pct']}%")
    print(f"  largest single near-boundary |R|: "
          f"{boundary['largest_single_near_boundary_abs_r']}  "
          f"(one relabel can move a bucket by this much on its own)")
    for reg in ("trending", "transitional", "chop", "unknown"):
        s = boundary["by_regime"].get(reg)
        if not s:
            continue
        floor = s.get("structural_floor_pct")
        tail = (f"  (structural floor {floor}% — this band's width alone; "
                f"excess {s.get('exposure_above_floor_pct')}pp)"
                if floor is not None else "  (no width-based floor — unbounded above)")
        print(f"    {reg:13} {s['near']:4}/{s['trades']:<4} trades near   "
              f"{s['near_abs_net_r']:>9} / {s['abs_net_r']:<9} |R|  = {s['exposure_pct']}%{tail}")
    if boundary["fragile"]:
        print(f"  ⚠️  FRAGILE (>= {boundary['fragile_threshold_pct']}%) — a different but "
              f"equally-valid candle feed could move these buckets materially. Do NOT "
              f"source a Tier-3 cell proposal from this grade without a second-feed "
              f"cross-check (--sensitivity-data).")
    print(f"  threshold basis: {boundary['fragile_threshold_basis']}")

    if sensitivity:
        print("--- FEED SENSITIVITY (same trades, second feed's ADX — labelling only) ---")
        print(f"  feed A: {sensitivity['feed_a']}")
        print(f"  feed B: {sensitivity['feed_b']}")
        print(f"  {'regime':13} {'net_r A':>10} {'net_r B':>10} {'delta':>10}  {'n A/B':>9}")
        for reg in ("trending", "transitional", "chop", "unknown"):
            d = sensitivity["by_regime"].get(reg)
            if not d:
                continue
            flag = "  <-- SIGN FLIP" if d["sign_flip"] else ""
            print(f"  {reg:13} {d['net_r_feed_a']:10} {d['net_r_feed_b']:10} "
                  f"{d['delta_r']:10}  {str(d['n_feed_a']) + '/' + str(d['n_feed_b']):>9}{flag}")
        print(f"  max |delta|: {sensitivity['max_abs_delta_r']}R")
        if sensitivity["any_sign_flip"]:
            print(f"  ⚠️  SIGN FLIP in {', '.join(sensitivity['sign_flips'])} — that bucket "
                  f"cannot source a Tier-3 cell proposal in EITHER direction, at any n.")
    else:
        print("--- feed sensitivity: NOT CHECKED (no --sensitivity-data) ---")
        print("  Boundary exposure above is the single-feed proxy for the same defect. "
              "A second feed is the direct test (BL-20260731-REGIME-ATTRIBUTION-FEED-SENSITIVE): "
              "identical trades re-tagged on another BTCUSDT 1h feed moved one bucket "
              "24.92R, ~31% of lifetime net-R.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
