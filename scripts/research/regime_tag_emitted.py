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
from backtest_trend import _load, _resample  # type: ignore  # noqa: E402
from regime_matrix import _adx, _regime, regime_distribution  # type: ignore  # noqa: E402


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

    if a.json:
        out = {"label": a.label, "resample": a.resample,
               "bars": int(len(df)), "regime_base_rate_pct": dist["pct"],
               "by_regime": by, "totals": totals,
               # Declared on EVERY run so a 1-D grade can never be mistaken for
               # one that covered the vol axis.
               "vol_axis": "present" if by_cell else "absent"}
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
