#!/usr/bin/env python3
"""Donchian legs: closed-bar (Stage 0) vs forming-bar (live) parity census.

PI-20260930-QZSE4AMA-0002 / lane DONCHIAN-PARITY. Generalises the RQ-301
whole-signal replay (which hard-codes ada_pullback_2h + trend_donchian_xrp_4h)
to any donchian leg, reusing its arms, ``compare`` and ``delta_stats`` and the
RQ-302 YAML->harness-kwargs mapping — nothing re-implemented. Adds the
per-direction/per-year census of backtest-only (live misses) vs forming-only
(live-only) entries and A_impl (what a closed-bar live change would trade).

Input: Binance USD-M 1m kline zips (labelled proxy for Bybit).
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "research"))
sys.path.insert(0, str(ROOT / "scripts"))
import whole_signal_forming_bar_replay as ws  # noqa: E402
import forming_bar_pooled_regrade as pr  # noqa: E402
import vol_skip_forming_bar_replay as vs  # noqa: E402

sys.path.insert(0, str(ROOT))
import forming_bar_entries as fbe  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402


def run_leg(leg: str, block: dict, klines_dir: str, workers: int) -> dict:
    tf = str(block["timeframe"]); sym = str(block["symbols"][0])
    tf_min = fbe.tf_minutes(tf)
    m1 = vs.load_1m(klines_dir, sym)
    bars = vs.build_bars(m1, tf_min)
    mats = fbe.minute_matrices(m1, bars, tf_min)
    mod = vs.backtest_trend
    costs = vs._set_costs(mod, sym)
    kw, unmodelled = pr.harness_kwargs(block, "trend")
    b_ovr = fbe.forming_entry_override(bars, None, block, family="trend", label=leg,
                                       workers=workers, mats=mats)
    a_live = fbe.closed_liveframe_override(bars, block, family="trend", label=leg, workers=workers)
    a_impl = {}
    for i, ov in a_live.items():
        px = mats["last_c"][i + 1, ws.IMPL_OFFSET - 1] if i + 1 < len(bars) else np.nan
        if not np.isnan(px):
            a_impl[i] = {**ov, "entry": float(px)}
    off = ws._ENTRY_GATES_OFF["trend"]

    def arm(ovr):
        k = dict(kw)
        if ovr is not None:
            k.update(off); k["side_filter"] = "both"
        with tempfile.TemporaryDirectory() as td:
            return pr._run(mod, bars, leg, sym, tf, k, Path(td) / "t.jsonl",
                           **({"entry_override": ovr} if ovr is not None else {}))
    trades = {"A_closed": arm(None), "B_forming": arm(b_ovr), "A_impl": arm(a_impl)}
    first, last = str(bars["timestamp"].iloc[fbe.WINDOW - 1]), str(bars["timestamp"].iloc[-1])
    trades = {n: [t for t in ts if t["entry_time"] >= first] for n, ts in trades.items()}
    out = {"leg": leg, "symbol": sym, "timeframe": tf, "execution": block.get("execution"),
           "costs": costs, "harness_kwargs": kw, "unmodelled_yaml_levers": unmodelled,
           "first_decision_bar": first, "last_bar": last, "bars": len(bars),
           "arms": {n: ws._summ(ts) for n, ts in trades.items()}}
    for other in ("B_forming", "A_impl"):
        c = ws.compare(trades["A_closed"], trades[other])
        ds = ws.delta_stats(trades["A_closed"], trades[other], first, last)
        # census: closed-only = live MISSES it; arm-only = live-only
        by_dir = {}
        for key, ents in (("closed_only", c["only_closed_entries"]), ("arm_only", c["only_arm_entries"])):
            d = {}
            for _, direction, r in ents:
                x = d.setdefault(direction, {"n": 0, "net_r": 0.0}); x["n"] += 1; x["net_r"] = round(x["net_r"] + r, 4)
            by_dir[key] = d
        out[f"A_closed_vs_{other}"] = {**{k: v for k, v in c.items() if not k.endswith("_entries")},
                                        "by_direction": by_dir, **ds,
                                        "grade": ws.grade(c["jaccard"], ds)}
        out[f"A_closed_vs_{other}"]["closed_only_last10"] = c["only_closed_entries"][-10:]
    out["oos_2026"] = {n: ws._summ([t for t in ts if t["entry_time"] >= "2026-01-01"]) for n, ts in trades.items()}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--klines-dir", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--leg", action="append", required=True)
    ap.add_argument("--fetch", action="store_true"); ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    cfg = yaml.safe_load((ROOT / "config/strategies.yaml").read_text()); cfg = cfg.get("strategies", cfg)
    rec = {"unit": "PI-20260930-QZSE4AMA-0002", "data_source": "Binance USD-M 1m proxy", "legs": {}}
    for leg in a.leg:
        if a.fetch:
            vs.fetch_1m(a.klines_dir, str(cfg[leg]["symbols"][0]))
        rec["legs"][leg] = run_leg(leg, cfg[leg], a.klines_dir, a.workers)
        print(leg, json.dumps(rec["legs"][leg]["arms"]), flush=True)
        Path(a.out).write_text(json.dumps(rec, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
