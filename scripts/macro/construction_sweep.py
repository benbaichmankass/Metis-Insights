"""M28 Phase B — the construction sweep: emit the construction variants for an input,
grade each through the combined S2+S3 grader, and record the verdicts.

Phase B works the unexplored construction space (D1–D4) on the inputs we already have.
This module is the reusable engine: given a per-symbol raw dated series (already mapped
to tradeable proxy symbols), it emits several **construction variants** — the level
baseline plus the D1 transforms (change / divergence / detrend) — each as
valuation-snapshot rows via the UNCHANGED `build_percentile_snapshots` emit path, then
runs each through `grade_construction.grade` (S2 signal + S3 PnL) against a candle set.

Input-agnostic: a thin adapter (e.g. COT: spec_net primary + comm_net secondary) feeds
the raw series; the sweep produces one graded scorecard per construction so the ledger
gets a row per cell. Pure given series + candles; stdlib + the toolkit only.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import signal_constructions as sc  # noqa: E402
from crypto_signals_data import build_percentile_snapshots  # noqa: E402


def emit_constructions(symbol: str, primary, *, secondary=None, asset_class: str = "unknown",
                       lookback: int = 156, min_history: int = 52,
                       higher_is_cheaper: bool = False, metric: str = "signal",
                       include=("level", "change", "divergence", "detrend")) -> dict:
    """{construction_name: [snapshot rows]} for one symbol.

    - ``level`` — trailing-percentile of the raw series (the first-pass baseline).
    - ``change`` — D1 percentile of the week-over-week change (`pct_change_series`).
    - ``divergence`` — D1 percentile of the primary-vs-``secondary`` rolling-z gap
      (only when ``secondary`` is given; e.g. COT spec-vs-commercial).
    - ``detrend`` — D1 percentile of the deviation-from-trailing-mean residual.

    ``higher_is_cheaper`` orients the level/detrend reads; the change/divergence reads
    keep the same orientation (a rising crowd reading is still the rich side). Each
    variant flows through `build_percentile_snapshots` unchanged, so the schema + PIT
    discipline + gate are identical across constructions."""
    variants = {}
    if "level" in include:
        variants["level"] = primary
    if "change" in include:
        variants["change"] = sc.pct_change_series(primary, periods=1)
    if "divergence" in include and secondary is not None:
        variants["divergence"] = sc.divergence_series(primary, secondary, lookback=lookback,
                                                      min_history=min_history)
    if "detrend" in include:
        variants["detrend"] = sc.detrend_series(primary, lookback=lookback, min_history=min_history)

    out = {}
    for name, series in variants.items():
        # a change/divergence/detrend value's own percentile is the signal; the raw
        # orientation carries through (higher reading = richer for a crowding gauge).
        out[name] = build_percentile_snapshots(
            symbol, f"{metric}_{name}", series, asset_class=asset_class,
            lookback=lookback, min_history=min_history,
            higher_is_cheaper=higher_is_cheaper, note=f"D1:{name}", source="construction_sweep",
        )
    return out


def merge_by_construction(per_symbol: list) -> dict:
    """Combine several symbols' ``emit_constructions`` dicts into one
    ``{construction_name: [rows across all symbols]}`` — the sweep grades the whole
    basket per construction (a construction is graded on all its symbols at once)."""
    merged: dict = {}
    for d in per_symbol:
        for name, rows in d.items():
            merged.setdefault(name, []).extend(rows)
    return merged


def grade_constructions(constructions: dict, price_at, *, cfg, rebalance_every: int,
                        horizons: list, pnl_horizon: int, fee_frac: float = 0.0,
                        carry_frac_per_day: float = 0.0, oos_frac: float = 0.5) -> dict:
    """Grade each construction's merged snapshots through the combined S2+S3 grader.
    Returns ``{construction_name: {verdict, worth_building, s2_signal, s3_pnl, meta}}``
    plus a ``_sweep`` roll-up (which constructions, if any, are worth building)."""
    import grade_construction as gc  # local import: pulls the heavier loaders lazily

    graded = {}
    for name, records in constructions.items():
        if not records:
            graded[name] = {"verdict": "no_data", "worth_building": False}
            continue
        graded[name] = gc.grade(
            records, price_at, cfg=cfg, rebalance_every=rebalance_every, horizons=horizons,
            pnl_horizon=pnl_horizon, fee_frac=fee_frac, carry_frac_per_day=carry_frac_per_day,
            oos_frac=oos_frac,
        )
    winners = [n for n, g in graded.items() if g.get("worth_building")]
    graded["_sweep"] = {
        "constructions": [n for n in graded if n != "_sweep"],
        "verdicts": {n: g.get("verdict") for n, g in graded.items() if n != "_sweep"},
        "worth_building": winners,
        "any_worth_building": bool(winners),
    }
    return graded


# ---------------------------------------------------------------------------
# COT adapter (spec_net primary + comm_net secondary → per-proxy constructions)
# ---------------------------------------------------------------------------


def cot_construction_snapshots(markets_rows: dict, proxy_by_market: dict,
                               asset_class_by_market: Optional[dict] = None, *,
                               lookback: int = 156, min_history: int = 52) -> dict:
    """Build the merged construction snapshots for the COT sleeve. ``markets_rows`` is
    ``{market_code: [cot rows]}``; ``proxy_by_market`` maps each market to its tradeable
    proxy symbol (USO/UNG/GLD/…). For each market: spec_net (primary) + comm_net
    (secondary, for the divergence construction), contrarian on the specs
    (`higher_is_cheaper=False`). Returns ``{construction: [rows across all proxies]}``."""
    from cot_data import comm_net_series, spec_net_series

    acls = asset_class_by_market or {}
    per_symbol = []
    for market, rows in markets_rows.items():
        proxy = proxy_by_market.get(market)
        if not proxy or not rows:
            continue
        per_symbol.append(emit_constructions(
            proxy, spec_net_series(rows), secondary=comm_net_series(rows),
            asset_class=acls.get(market, "unknown"), lookback=lookback,
            min_history=min_history, higher_is_cheaper=False, metric="cot",
        ))
    return merge_by_construction(per_symbol)
