#!/usr/bin/env python3
"""M28 value-thesis CONSTRUCTION sweep — grade D1/D2/D3 value constructions through the P4 gate.

The M28-P4 value gate ran once, on the **level** construction (the S1 former reading
each metric's trailing-percentile ``cheap_score``): 1,104 theses over 21yr, but
``edge_vs_baseline = -0.0047`` (loses to naive all-long) and ``calibration_rank ≈ 0``
(conviction doesn't predict return) — a clean NULL, recorded as the honest baseline the
construction search must beat (`docs/research/M28-P4-value-gate-run-2026-07-27.md`).

This module iterates the **unexplored construction dimensions** (D1 transform / D2
conditioning / D3 cross-section — `docs/research/M28-signal-research-methodology.md`) on
the SAME value inputs, and grades each through the **UNCHANGED** P4 lifecycle gate
(`thesis_backtest_run`: net-of-cost + calibration + beat-baseline). It is the P4-gate
counterpart to `construction_sweep.py` (which grades constructions through the
horizon-IC/`grade_construction` S2+S3 arbiter). Prior value D1/D2/D3 work
(ledger entry 12, #7534) was graded through `grade_construction`, NOT the P4 lifecycle
gate — so this is a genuine re-test under the arbiter the operator's directive names.

**Where the raw series come from (no FRED, no network for emit):** the committed
backfill `comms/macro/valuation_snapshots_backfill.jsonl` already carries the raw
``value`` per ``(symbol, metric, observed_at)`` — so every construction variant is
derived LOCALLY from those committed values by re-computing ``cheap_score`` under a
transform. Only the forward-return **candles** need fetching (the P4 gate's price
source), same as the baseline run.

**Point-in-time discipline** is inherited unchanged: the transforms (`signal_constructions`)
and the emit path (`build_percentile_snapshots`) are all trailing-window leakage-safe,
and the gate's price reader is as-of-or-prior. A construction only changes HOW
``cheap_score`` is computed from past values; the schema + PIT + gate are identical.

Observe-only: reads the committed backfill + candle CSVs, writes construction JSONLs +
a scorecard. No order path, no DB write, no live influence.

Usage:
    # emit the construction variants only (pure, no candles needed):
    python scripts/macro/value_construction_sweep.py --out-dir comms/macro/constructions

    # emit AND grade each variant through the P4 gate (needs candle CSVs):
    python scripts/macro/value_construction_sweep.py \
        --out-dir comms/macro/constructions --candles-dir data/macro_candles \
        --rebalance-every 30 --horizon-days 30 --fee-frac 0.001 \
        --json comms/macro/value_construction_scorecard.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

# scripts/macro (sibling adapters) + repo root on path so ``python scripts/...`` works.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import signal_constructions as sc  # noqa: E402
from crypto_signals_data import build_percentile_snapshots  # noqa: E402

DEFAULT_BACKFILL = os.path.join("comms", "macro", "valuation_snapshots_backfill.jsonl")

# The tradeable value universe — symbols with an ETF/price the P4 gate can forward-price.
# credit_risk / term_structure carry no tradeable proxy in the seed universe, so they are
# excluded (the baseline run dropped them at the price step). Mirrors the baseline's
# effective universe so the comparison is apples-to-apples.
TRADEABLE_SYMBOLS = ("SPY", "TLT", "IEF", "GLD", "SLV")

# The distinct-driver map for the D3 cross-section (one comparable driver per symbol,
# avoiding the degenerate TLT/IEF/GLD/SLV-share-DFII10 trap that ledger entry 12 flagged):
# each symbol contributes ONE oriented cheapness axis to the cross-section.
XSEC_DRIVERS = (
    ("SPY", "equity_risk_premium"),
    ("TLT", "real_yield_10y"),
    ("GLD", "gold_silver_ratio"),
    ("SLV", "gold_silver_ratio"),
)

# ---------------------------------------------------------------------------
# Load the raw value drivers from the committed backfill.
# ---------------------------------------------------------------------------


def load_value_drivers(backfill_path: str = DEFAULT_BACKFILL,
                       tradeable: tuple = TRADEABLE_SYMBOLS) -> dict:
    """Read the committed backfill → ``{(symbol, metric): {series, hic, asset_class}}``.

    ``series`` is the ascending ``[(observed_at, value), ...]`` raw metric series (rows
    with a null value dropped). ``hic`` is the row's committed ``higher_is_cheaper``
    orientation. Only ``tradeable`` symbols are kept (others carry no price)."""
    by_key: dict = defaultdict(lambda: {"series": [], "hic": None, "asset_class": "unknown"})
    with open(backfill_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            sym = r.get("symbol")
            if tradeable and sym not in tradeable:
                continue
            val = r.get("value")
            day = str(r.get("observed_at") or "")[:10]
            if val is None or len(day) != 10:
                continue
            k = (sym, r.get("metric"))
            d = by_key[k]
            d["series"].append((day, float(val)))
            d["hic"] = bool(r.get("higher_is_cheaper"))
            d["asset_class"] = r.get("asset_class") or "unknown"
    # de-dup + sort each series by date (a date can appear once per driver)
    out = {}
    for k, d in by_key.items():
        seen = {}
        for day, v in d["series"]:
            seen[day] = v  # last wins (backfill is one row per date per driver)
        d["series"] = sorted(seen.items())
        out[k] = d
    return out


# ---------------------------------------------------------------------------
# Emit the construction variants (D1 / D2 / D3) — pure, leakage-safe.
# ---------------------------------------------------------------------------

# D1/D2 constructions emitted per (symbol, metric) driver, merged across drivers.
D1_D2_CONSTRUCTIONS = ("level", "change", "detrend", "accel", "level_x_turning")


def emit_value_constructions(drivers: dict, *, lookback: int = 156, min_history: int = 52,
                             constructions: tuple = D1_D2_CONSTRUCTIONS) -> dict:
    """Emit ``{construction_name: [snapshot rows across all drivers]}``.

    Per driver (``(symbol, metric)`` with its raw value series + orientation ``hic``):
      - ``level``   — trailing-percentile of the raw value (the baseline construction,
        re-emitted here as the internal control; ``hic`` = the driver's own orientation).
      - ``change``  — D1 percentile of the week-over-week Δvalue (`pct_change_series`);
        the "value edge lives in the shift, not the level" hypothesis. Same ``hic``:
        a change TOWARD the cheap side reads cheaper.
      - ``detrend`` — D1 percentile of the deviation-from-trailing-mean residual.
      - ``accel``   — D1 percentile of the 2nd difference (change-of-change).
      - ``level_x_turning`` — D2: the ``level`` read NEUTRALIZED unless the value is
        currently moving TOWARD cheap (its oriented 1-step change > 0) — "cheap AND
        turning cheaper", not catching a knife mid-fall.

    Every variant flows through the UNCHANGED `build_percentile_snapshots` emit path, so
    the valuation-snapshot schema + ``observed_at``/``as_of`` PIT discipline are identical
    across constructions. ``metric`` is tagged ``<metric>_<construction>`` so a merged
    file carries one row-family per construction."""
    merged: dict = {name: [] for name in constructions}
    for (symbol, metric), d in drivers.items():
        series = d["series"]
        hic = bool(d["hic"])
        acls = d["asset_class"]
        if len(series) < min_history:
            continue

        # oriented 1-step change: positive = moving toward the CHEAP side (for the
        # turning gate + as the `change` construction's raw). value units preserved.
        raw_change = sc.pct_change_series(series, periods=1)
        oriented_change = raw_change if hic else [(dd, -v) for dd, v in raw_change]

        if "level" in merged:
            merged["level"].extend(build_percentile_snapshots(
                symbol, f"{metric}_level", series, asset_class=acls, lookback=lookback,
                min_history=min_history, higher_is_cheaper=hic, note="D0:level",
                source="value_construction_sweep"))
        if "change" in merged:
            merged["change"].extend(build_percentile_snapshots(
                symbol, f"{metric}_change", raw_change, asset_class=acls, lookback=lookback,
                min_history=min_history, higher_is_cheaper=hic, note="D1:change",
                source="value_construction_sweep"))
        if "detrend" in merged:
            merged["detrend"].extend(build_percentile_snapshots(
                symbol, f"{metric}_detrend", sc.detrend_series(series, lookback=lookback,
                min_history=min_history), asset_class=acls, lookback=lookback,
                min_history=min_history, higher_is_cheaper=hic, note="D1:detrend",
                source="value_construction_sweep"))
        if "accel" in merged:
            accel = sc.pct_change_series(raw_change, periods=1)
            merged["accel"].extend(build_percentile_snapshots(
                symbol, f"{metric}_accel", accel, asset_class=acls, lookback=lookback,
                min_history=min_history, higher_is_cheaper=hic, note="D1:accel",
                source="value_construction_sweep"))
        if "level_x_turning" in merged:
            level_rows = build_percentile_snapshots(
                symbol, f"{metric}_level_x_turning", series, asset_class=acls,
                lookback=lookback, min_history=min_history, higher_is_cheaper=hic,
                note="D2:level_x_turning", source="value_construction_sweep")
            # keep conviction only when value is turning toward cheap (oriented Δ > 0)
            merged["level_x_turning"].extend(
                sc.condition_snapshots(level_rows, oriented_change, lambda x: x > 0))
    return merged


def _base_metric(metric: str) -> str:
    """Strip the trailing ``_<construction>`` tag emitted by ``emit_value_constructions``
    (``equity_risk_premium_change`` → ``equity_risk_premium``) so the composite can
    align a driver's ``change`` and ``detrend`` legs by their common base driver."""
    return str(metric).rsplit("_", 1)[0]


def emit_composite_construction(constructions: dict, *, legs=("change", "detrend"),
                                weights: Optional[dict] = None, name: str = "composite") -> list:
    """D4 composite — blend the per-driver ``legs`` cheap_scores into ONE conviction.

    Each leg's rows already carry a leakage-safe ``cheap_score`` in ``[0,1]`` on the
    SAME oriented axis (cheaper→higher), so the composite is their weighted mean on
    the intersecting ``(symbol, base-driver, as_of)`` keys — no re-percentiling, no
    new leakage surface (blending point-in-time reads is itself point-in-time). A
    date is emitted only when EVERY leg reports it for that driver (a real composite,
    never a single-leg passthrough).

    ``weights`` = ``{leg: w}`` (defaults equal). The blended row is graded through the
    UNCHANGED P4 gate exactly like any other construction (``form_tick_theses`` reads
    ``cheap_score`` regardless of the ``metric`` tag). Pure — no candles, no IO."""
    leg_rows = {leg: constructions.get(leg) or [] for leg in legs}
    if any(not rows for rows in leg_rows.values()):
        return []
    w = {leg: float((weights or {}).get(leg, 1.0)) for leg in legs}
    tot = sum(w.values()) or 1.0
    # index each leg by (symbol, base-driver, as_of) → (cheap_score, row)
    leg_idx: dict = {leg: {} for leg in legs}
    for leg, rows in leg_rows.items():
        for r in rows:
            key = (r["symbol"], _base_metric(r["metric"]), r["as_of"])
            leg_idx[leg][key] = r
    # keys present in EVERY leg
    common = set(leg_idx[legs[0]])
    for leg in legs[1:]:
        common &= set(leg_idx[leg])
    out = []
    for key in sorted(common):
        symbol, base, as_of = key
        blended = sum(w[leg] * float(leg_idx[leg][key]["cheap_score"]) for leg in legs) / tot
        template = leg_idx["change"][key] if "change" in legs else leg_idx[legs[0]][key]
        r = dict(template)
        r["metric"] = f"{base}_{name}"
        r["cheap_score"] = blended
        r["label"] = "cheap" if blended >= 0.7 else "rich" if blended <= 0.3 else "fair"
        r["value"] = None
        r["z_score"] = None
        r["percentile"] = blended
        r["note"] = f"D4:{name}(" + "+".join(f"{leg}*{w[leg]:.2f}" for leg in legs) + ")"
        r["inputs"] = {"composite_legs": {leg: float(leg_idx[leg][key]["cheap_score"])
                                          for leg in legs}, "weights": dict(w)}
        out.append(r)
    return out


def emit_xsec_construction(drivers: dict, *, lookback: int = 156, min_history: int = 52,
                           xsec_drivers: tuple = XSEC_DRIVERS) -> list:
    """D3 cross-section: rank the distinct drivers against EACH OTHER per date on their
    own oriented trailing cheapness, long the cheapest / short the richest of the basket.

    The cross-comparable axis is each driver's own trailing-percentile ``cheap_score``
    (unit-free 0..1; raw metric values aren't comparable across ERP / real-yield / GSR),
    then re-ranked cross-sectionally per date via `cross_sectional_snapshots`. Reproduces
    ledger entry 12's construction, but the OUTPUT is graded through the P4 lifecycle gate
    here (entry 12 graded through `grade_construction`). Leakage-safe: the per-driver
    cheap_score uses a trailing window; the cross-section uses only same-date reads."""
    cheap_by_symbol: dict = {}
    acls_by_symbol: dict = {}
    for symbol, metric in xsec_drivers:
        d = drivers.get((symbol, metric))
        if not d or len(d["series"]) < min_history:
            continue
        rows = build_percentile_snapshots(
            symbol, f"{metric}_xsec_src", d["series"], asset_class=d["asset_class"],
            lookback=lookback, min_history=min_history, higher_is_cheaper=bool(d["hic"]),
            source="value_construction_sweep")
        # feed each driver's own cheap_score (already oriented) as the cross-section axis
        cheap_by_symbol[symbol] = [(r["as_of"], r["cheap_score"]) for r in rows]
        acls_by_symbol[symbol] = d["asset_class"]
    # higher own-cheapness → cheaper in the cross-section (already oriented), so hic=True
    return sc.cross_sectional_snapshots(
        cheap_by_symbol, "value_xsec", asset_class_by_symbol=acls_by_symbol,
        higher_is_cheaper=True, min_symbols=3, note="D3:xsec", source="value_construction_sweep")


# ---------------------------------------------------------------------------
# Grade each construction through the UNCHANGED P4 gate.
# ---------------------------------------------------------------------------


def _price_momentum_gate(panels: dict, symbol: str, *, lookback_days: int = 63):
    """Build a dated ``[(date, momentum)]`` series = trailing price change over
    ``lookback_days`` for a symbol, for the D2 price-turning conditioner. Uses the
    same as-of-or-prior close panel the gate uses; positive = price rising."""
    rows = panels.get(symbol.upper())
    if not rows:
        return []
    out = []
    for i in range(lookback_days, len(rows)):
        prev = rows[i - lookback_days][1]
        if prev:
            out.append((rows[i][0], (rows[i][1] - prev) / abs(prev)))
    return out


def _asof_gate(gate_series, snapshot_rows) -> list:
    """Resolve a trading-day gate series onto the snapshot ``as_of`` dates AS-OF-OR-PRIOR.

    The value-snapshot ``as_of`` dates are weekly Saturdays (FRED weekly series), which
    are never trading days — so an exact-date gate match (``condition_snapshots``'
    ``gate.get(as_of)``) finds NOTHING and neutralizes every row (the n=0 that voided
    the price-turning cell). This keys a gate value onto each ``as_of`` using the most
    recent trading-day reading at-or-before that Saturday (PIT-safe: never a future
    bar). An ``as_of`` before the gate's first reading is omitted (honestly neutralized:
    no gate known yet). Returns ``[(as_of, gate_value)]`` keyed exactly on the rows'
    ``as_of`` strings so ``condition_snapshots`` reads the right value."""
    g = sorted((str(d)[:10], float(v)) for d, v in (gate_series or []))
    gdates = [d for d, _ in g]
    out, seen = [], set()
    import bisect
    for r in snapshot_rows:
        aso = r.get("as_of")
        if aso is None or aso in seen:
            continue
        seen.add(aso)
        pos = bisect.bisect_right(gdates, str(aso)[:10])
        if pos > 0:
            out.append((aso, g[pos - 1][1]))
    return out


def _price_vol_regime_gate(panels: dict, symbol: str, *, vol_window: int = 21,
                           median_window: int = 252):
    """Build a dated ``[(date, calm_flag)]`` series for the D2 regime conditioner:
    ``calm_flag = 1.0`` when the symbol's trailing ``vol_window``-day realized vol is
    at/below its trailing ``median_window``-day median (a CALM regime), else ``0.0``.

    Hypothesis: a value/mean-reversion read resolves in calm regimes and gets run over
    in a stress regime (catching-a-knife) — so keep conviction only when calm. Both
    windows are trailing/past-only (PIT-safe): the flag on date t uses only closes up to
    t. Uses the same as-of panel the gate prices from."""
    rows = panels.get(symbol.upper())
    if not rows or len(rows) < vol_window + 2:
        return []
    # daily log-ish returns then a trailing rolling stdev = realized vol at each date.
    rets = []
    for i in range(1, len(rows)):
        prev = rows[i - 1][1]
        if prev:
            rets.append((rows[i][0], (rows[i][1] - prev) / abs(prev)))
    import statistics as _st
    vol_series = []
    for i in range(vol_window, len(rets)):
        window = [v for _d, v in rets[i - vol_window:i]]
        try:
            vol_series.append((rets[i][0], _st.pstdev(window)))
        except _st.StatisticsError:
            continue
    out = []
    for i in range(len(vol_series)):
        hist = [v for _d, v in vol_series[max(0, i + 1 - median_window):i + 1]]
        if len(hist) < 2:
            continue
        med = _st.median(hist)
        out.append((vol_series[i][0], 1.0 if vol_series[i][1] <= med else 0.0))
    return out


def grade_construction_records(records: list, price_at, *, cfg, rebalance_every: int,
                               horizon_days: float, fee_frac: float,
                               carry_frac_per_day: float = 0.0, n_bins: int = 4) -> dict:
    """Run one construction's snapshot records through the UNCHANGED P4 gate internals
    and return the scorecard dict (n / win_rate / mean_net / calibration_rank /
    baseline_mean_net / edge_vs_baseline / bins)."""
    from thesis_backtest_run import derive_rebalance_dates  # sibling script
    from src.units.strategies.macro_thesis.thesis_backtest import run_thesis_backtest
    from src.units.strategies.macro_thesis.thesis_replay import build_replay_entries

    if not records:
        return {"n": 0}
    rebalance_dates = derive_rebalance_dates(records, rebalance_every)
    entries = build_replay_entries(
        records, price_at, rebalance_dates=rebalance_dates, cfg=cfg, horizon_days=horizon_days)
    return run_thesis_backtest(
        entries, fee_frac=fee_frac, carry_frac_per_day=carry_frac_per_day, n_bins=n_bins)


def _summary_row(name: str, card: dict) -> dict:
    return {
        "construction": name,
        "n": card.get("n"),
        "win_rate": card.get("win_rate"),
        "mean_net_return": card.get("mean_net_return"),
        "calibration_rank": card.get("calibration_rank"),
        "baseline_mean_net": card.get("baseline_mean_net_return"),
        "edge_vs_baseline": card.get("edge_vs_baseline"),
    }


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:+.4f}"
    return str(v)


def render_table(rows: list) -> str:
    hdr = f"{'construction':22s} {'n':>5s} {'win':>7s} {'mean_net':>9s} {'calib':>8s} {'edge_vs_base':>13s}"
    out = ["M28 value-construction sweep — P4 lifecycle gate (observe-only)", "=" * 72, hdr, "-" * 72]
    for r in rows:
        out.append(
            f"{r['construction']:22s} {str(r['n'] or 0):>5s} {_fmt(r['win_rate']):>7s} "
            f"{_fmt(r['mean_net_return']):>9s} {_fmt(r['calibration_rank']):>8s} "
            f"{_fmt(r['edge_vs_baseline']):>13s}")
    out += ["-" * 72,
            "GATE (operator directive): a construction CLEARS iff edge_vs_baseline > 0 net-of-cost",
            "AND calibration_rank > 0, OOS. Baseline to beat (level/S1-former): edge_vs_baseline = -0.0047."]
    return "\n".join(out)


def render_horizon_table(rows: list, *, fee_frac: float) -> str:
    """D5 horizon-sweep table: the `change` cell graded at each hold horizon, so a
    reader can see where (if anywhere) its edge_vs_baseline turns positive net-of-cost."""
    hdr = f"{'horizon':22s} {'n':>5s} {'win':>7s} {'mean_net':>9s} {'calib':>8s} {'edge_vs_base':>13s}"
    out = [f"M28 value D5 — horizon × cost sweep on the `change` cell (fee_frac={fee_frac})",
           "=" * 72, hdr, "-" * 72]
    for r in rows:
        out.append(
            f"{r['construction']:22s} {str(r['n'] or 0):>5s} {_fmt(r['win_rate']):>7s} "
            f"{_fmt(r['mean_net_return']):>9s} {_fmt(r['calibration_rank']):>8s} "
            f"{_fmt(r['edge_vs_baseline']):>13s}")
    out += ["-" * 72,
            "D5 clears iff SOME horizon has edge_vs_baseline > 0 net-of-cost (its edge survives cost)."]
    return "\n".join(out)


def write_construction_files(out_dir: str, constructions: dict, xsec: Optional[list] = None) -> dict:
    """Write one JSONL per construction under ``out_dir``; return {name: path}."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    paths = {}
    allc = dict(constructions)
    if xsec is not None:
        allc["xsec"] = xsec
    for name, rows in allc.items():
        p = d / f"value_{name}.jsonl"
        with p.open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, default=str) + "\n")
        paths[name] = str(p)
    return paths


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="M28 value-construction sweep through the P4 gate")
    ap.add_argument("--backfill", default=DEFAULT_BACKFILL, help="committed valuation backfill JSONL")
    ap.add_argument("--out-dir", default=os.path.join("comms", "macro", "constructions"),
                    help="write one JSONL per construction here")
    ap.add_argument("--candles-dir", default=None,
                    help="per-symbol daily-close CSV dir; when given, grade each construction")
    ap.add_argument("--lookback", type=int, default=156, help="trailing percentile window (rows)")
    ap.add_argument("--min-history", type=int, default=52, help="min rows before emitting")
    ap.add_argument("--rebalance-every", type=int, default=30)
    ap.add_argument("--horizon-days", type=float, default=30.0)
    ap.add_argument("--fee-frac", type=float, default=0.001)
    ap.add_argument("--carry-frac-per-day", type=float, default=0.0)
    ap.add_argument("--n-bins", type=int, default=4)
    ap.add_argument("--horizon-sweep", default="7,14,30,60,90,180",
                    help="CSV of hold horizons (days) for the D5 sweep on the `change` cell")
    ap.add_argument("--json", default=None, help="write the sweep scorecard JSON here")
    args = ap.parse_args(argv)

    drivers = load_value_drivers(args.backfill)
    constructions = emit_value_constructions(
        drivers, lookback=args.lookback, min_history=args.min_history)
    xsec = emit_xsec_construction(drivers, lookback=args.lookback, min_history=args.min_history)
    paths = write_construction_files(args.out_dir, constructions, xsec=xsec)
    print(f"emitted {len(paths)} constructions to {args.out_dir}:")
    for name, p in paths.items():
        allc = dict(constructions)
        allc["xsec"] = xsec
        print(f"  {name:22s} rows={len(allc[name]):6d}  {p}")

    if not args.candles_dir:
        print("\n(no --candles-dir: emit-only. Re-run with candles to grade through the P4 gate.)")
        return 0

    # Grade through the UNCHANGED P4 gate.
    from thesis_backtest_run import load_close_panels, make_price_at
    from src.units.strategies.macro_thesis.thesis_tick import load_sleeve_config

    panels = load_close_panels(args.candles_dir)
    price_at = make_price_at(panels)
    cfg = load_sleeve_config(None)

    gate_kwargs = dict(cfg=cfg, rebalance_every=args.rebalance_every,
                       horizon_days=args.horizon_days, fee_frac=args.fee_frac,
                       carry_frac_per_day=args.carry_frac_per_day, n_bins=args.n_bins)

    summary_rows = []
    cards: dict = {}  # name -> full scorecard, so D4 IC-weights can read leg calibration
    # baseline (control): the committed backfill itself, run through the same gate.
    from thesis_backtest_run import read_snapshot_records
    base_records = read_snapshot_records(path=args.backfill)
    summary_rows.append(_summary_row(
        "baseline(committed)", grade_construction_records(base_records, price_at, **gate_kwargs)))

    allc = dict(constructions)
    allc["xsec"] = xsec
    for name, rows in allc.items():
        card = grade_construction_records(rows, price_at, **gate_kwargs)
        cards[name] = card
        summary_rows.append(_summary_row(name, card))

    # D4 · composite (change ⊕ detrend). Equal-weight first (pure), then IC-weighted:
    # weight each leg by its OWN standalone calibration_rank clamped >= 0 (a leg whose
    # conviction anti-predicts gets zero weight). The IC weights are read from the
    # full-sample grade above — deliberately IN-SAMPLE-OPTIMISTIC: if even a composite
    # tuned on the same data it's graded over fails to clear, the null is conclusive.
    comp_equal = emit_composite_construction(constructions, name="composite_eq")
    if comp_equal:
        summary_rows.append(_summary_row(
            "composite_eq(D4)", grade_construction_records(comp_equal, price_at, **gate_kwargs)))
    ic_w = {leg: max(0.0, (cards.get(leg, {}).get("calibration_rank") or 0.0))
            for leg in ("change", "detrend")}
    if sum(ic_w.values()) <= 0:
        ic_w = {"change": 1.0, "detrend": 1.0}  # both non-predictive → fall back to equal
    comp_ic = emit_composite_construction(constructions, weights=ic_w, name="composite_ic")
    if comp_ic:
        summary_rows.append(_summary_row(
            "composite_ic(D4)", grade_construction_records(comp_ic, price_at, **gate_kwargs)))

    # D2 · regime-conditioning — condition the `change` cell (the identified lever) on
    # a PRICE-DERIVED regime gate: (a) calm realized-vol regime, (b) up-trend (price
    # momentum > 0). "value read resolves in a calm/up regime, gets run over in stress."
    change_rows = constructions.get("change", [])
    if change_rows:
        for gate_name, gate_fn, pred in (
            ("change_x_calm_vol(D2reg)", _price_vol_regime_gate, lambda x: x > 0.5),
            ("change_x_uptrend(D2reg)", _price_momentum_gate, lambda x: x > 0),
        ):
            conditioned = []
            for sym in {r["symbol"] for r in change_rows}:
                sym_rows = [dict(r) for r in change_rows if r["symbol"] == sym]
                gate = _asof_gate(gate_fn(panels, sym), sym_rows)
                conditioned.extend(sc.condition_snapshots(sym_rows, gate, pred))
            summary_rows.append(_summary_row(
                gate_name, grade_construction_records(conditioned, price_at, **gate_kwargs)))

    # D2 price-turning conditioner (needs candles): level neutralized unless price rising.
    price_cond_rows = []
    for r in constructions.get("level", []):
        price_cond_rows.append(dict(r))
    if price_cond_rows:
        # condition each symbol's level rows on its own price momentum > 0
        conditioned = []
        for sym in {r["symbol"] for r in price_cond_rows}:
            sym_rows = [r for r in price_cond_rows if r["symbol"] == sym]
            gate = _asof_gate(_price_momentum_gate(panels, sym), sym_rows)
            conditioned.extend(sc.condition_snapshots(sym_rows, gate, lambda x: x > 0))
        summary_rows.append(_summary_row(
            "level_x_price_turning", grade_construction_records(conditioned, price_at, **gate_kwargs)))

    # sort: clearing constructions (edge>0 & calib>0) first, then by edge desc
    def _key(r):
        edge = r["edge_vs_baseline"]
        calib = r["calibration_rank"]
        clears = 1 if (edge is not None and edge > 0 and calib is not None and calib > 0) else 0
        return (clears, edge if edge is not None else -9)
    summary_rows.sort(key=_key, reverse=True)
    print("\n" + render_table(summary_rows))

    # D5 · horizon × cost sweep on the `change` cell — find the hold horizon where its
    # edge_vs_baseline survives the fee model (each horizon's baseline is horizon-matched
    # inside run_thesis_backtest, so edge is apples-to-apples per horizon).
    horizon_sweep = []
    try:
        horizons = [float(h) for h in str(args.horizon_sweep).split(",") if h.strip()]
    except ValueError:
        horizons = []
    if change_rows and horizons:
        for h in horizons:
            hk = dict(gate_kwargs)
            hk["horizon_days"] = h
            horizon_sweep.append(_summary_row(f"change@{int(h)}d",
                                              grade_construction_records(change_rows, price_at, **hk)))
        print("\n" + render_horizon_table(horizon_sweep, fee_frac=args.fee_frac))

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"rows": summary_rows, "horizon_sweep": horizon_sweep, "meta": {
            "backfill": args.backfill, "candles_dir": args.candles_dir,
            "lookback": args.lookback, "min_history": args.min_history,
            "rebalance_every": args.rebalance_every, "horizon_days": args.horizon_days,
            "fee_frac": args.fee_frac, "ic_weights": ic_w,
            "symbols_with_candles": sorted(panels.keys()),
        }}, indent=2, default=str), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
