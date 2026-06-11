#!/usr/bin/env python3
"""M15 soak — Dukascopy-vs-venue candle fidelity cross-check.

The Phase-0 sweep validated the new legs on Dukascopy data (bid-side);
the live legs trade OANDA / Alpaca venue candles. This compares the two
series over their overlap so the soak report can state how far the
backtest data sits from what the strategies actually see.

Inputs:
  --venue-json   JSON from the bot's /api/bot/candles endpoint
                 ({"candles":[{time, open, high, low, close, ...}]},
                 time = epoch seconds) — fetched via the diag relay.
  --dukascopy-csv  The Phase-0 CSV (timestamp,open,high,low,close,volume).
  --resample     Optional rule (e.g. "1h") applied to the CSV first —
                 the Phase-0 FX data is 15m, the gold leg trades 1h.
  --daily        Join on calendar date instead of exact timestamp
                 (venue daily bars are session-stamped, Dukascopy 00:00).

Reports matched-bar count and close/high/low deltas in bps (median /
p95 / max). Dukascopy is bid-side, so a small one-sided offset ~ the
spread is expected; structural divergence (tens of bps on close) is not.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone

import pandas as pd


def main(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--venue-json", required=True)
    p.add_argument("--dukascopy-csv", required=True)
    p.add_argument("--resample", default=None)
    p.add_argument("--daily", action="store_true")
    p.add_argument("--label", default="")
    a = p.parse_args(argv)

    v = json.load(open(a.venue_json))
    candles = v.get("candles") or []
    if not candles:
        print(json.dumps({"label": a.label, "error": "no_venue_candles",
                          "venue_error": v.get("error")}))
        return 1
    vdf = pd.DataFrame(candles)
    vdf["timestamp"] = pd.to_datetime(vdf["time"], unit="s", utc=True)

    ddf = pd.read_csv(a.dukascopy_csv, parse_dates=["timestamp"])
    if ddf["timestamp"].dt.tz is None:
        ddf["timestamp"] = ddf["timestamp"].dt.tz_localize("UTC")
    if a.resample:
        ddf = (ddf.set_index("timestamp")
               .resample(a.resample)
               .agg({"open": "first", "high": "max", "low": "min",
                     "close": "last", "volume": "sum"})
               .dropna(subset=["close"]).reset_index())

    if a.daily:
        vdf["key"] = vdf["timestamp"].dt.date
        ddf["key"] = ddf["timestamp"].dt.date
    else:
        vdf["key"] = vdf["timestamp"]
        ddf["key"] = ddf["timestamp"]

    m = vdf.merge(ddf, on="key", suffixes=("_v", "_d"))
    if m.empty:
        print(json.dumps({
            "label": a.label, "error": "no_overlap",
            "venue_range": [str(vdf['timestamp'].min()), str(vdf['timestamp'].max())],
            "dukascopy_range": [str(ddf['timestamp'].min()), str(ddf['timestamp'].max())],
        }))
        return 1

    out = {"label": a.label, "matched_bars": int(len(m)),
           "venue_bars": int(len(vdf)),
           "overlap_start": str(m["key"].min()), "overlap_end": str(m["key"].max())}
    for col in ("close", "high", "low"):
        d = ((m[f"{col}_v"] - m[f"{col}_d"]) / m[f"{col}_d"] * 1e4)
        signed = [round(x, 2) for x in
                  (statistics.median(d), d.quantile(0.05), d.quantile(0.95))]
        out[f"{col}_bps"] = {
            "median_signed": signed[0], "p05_signed": signed[1],
            "p95_signed": signed[2],
            "median_abs": round(float(d.abs().median()), 2),
            "p95_abs": round(float(d.abs().quantile(0.95)), 2),
            "max_abs": round(float(d.abs().max()), 2),
        }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
