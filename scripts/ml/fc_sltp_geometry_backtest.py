#!/usr/bin/env python3
"""M19 Phase-2 — fc-informed SL/TP geometry: offline first-look backtest.

The T0.4 `fc` forecast head is M19's one durable win (a vol-regime signal). This
asks a *different* use of the same forecast: instead of feeding `fc_*` as a
classifier feature, **size the stop/target from the forecast's own quantiles** —
for a long, TP at the q90 upside forecast and SL at the q10 downside forecast
(mirrored for a short) — and see, over historical trades, whether that geometry
would have improved outcomes vs the SL/TP the bot actually placed.

**This is an exploratory first-look, NOT a production backtest.** Caveats:
- Fills are assumed exactly at the barrier (no slippage/gaps beyond the bar).
- The realized outcome of the hypothetical SL/TP is simulated by a forward
  triple-barrier walk over the symbol's own candles from the trade's entry bar,
  capped at `--max-hold` bars.
- The fc forecast is as-of joined at entry (strictly-prior, mirroring the live
  one-day/one-bar-lag serving contract of `forecast_features`), so it never peeks.
- Only symbols with an fc side-stream (BTCUSDT/ETHUSDT) and both a candle feed and
  a resolvable entry are scored; everything else is skipped and counted.
- R is in units of the trade's own fc-stop distance, so cross-trade/-symbol
  comparison is on one axis. The `actual` arm re-simulates the actual SL/TP the
  same way (same candles, same cap) so the comparison is apples-to-apples.

Reads only the synced `trade_journal.db` + `datasets-out/{forecasts,market_raw}`;
writes nothing. Tier-1 research; never touches the order path.

Run on the trainer:
    python3 scripts/ml/fc_sltp_geometry_backtest.py --symbols BTCUSDT,ETHUSDT \
      --db data/trade_journal.db --max-hold 96
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from bisect import bisect_right
from pathlib import Path
from typing import Any, Optional


def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _epoch(ts: Any) -> Optional[float]:
    """Parse an ISO-8601 or epoch(-ms) timestamp to epoch seconds."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        v = float(ts)
        return v / 1000.0 if v > 1e11 else v
    s = str(ts).strip()
    if not s:
        return None
    if s.replace(".", "", 1).isdigit():
        v = float(s)
        return v / 1000.0 if v > 1e11 else v
    s = s.replace("Z", "+00:00")
    try:
        from datetime import datetime

        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def _newest_data_jsonl(root: Path) -> Optional[Path]:
    cands = sorted(root.glob("*/data.jsonl"))
    return cands[-1] if cands else None


def _load_forecasts(sym: str, ds_root: Path) -> tuple[list[float], list[dict]]:
    """(sorted entry-epochs, rows) for the fc side-stream; q10/q90 rel log-returns."""
    d = ds_root / "forecasts" / sym / "15m"
    p = _newest_data_jsonl(d)
    ts, rows = [], []
    if p is None:
        return ts, rows
    tmp = []
    for line in p.open():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        e = _epoch(r.get("ts") or r.get("time") or r.get("timestamp"))
        q10, q90 = _f(r.get("fc_q10_rel")), _f(r.get("fc_q90_rel"))
        if e is None or q10 is None or q90 is None:
            continue
        tmp.append((e, {"q10": q10, "q90": q90, "range": _f(r.get("fc_range_rel"))}))
    tmp.sort(key=lambda x: x[0])
    ts = [t for t, _ in tmp]
    rows = [r for _, r in tmp]
    return ts, rows


def _load_candles(sym: str, ds_root: Path) -> list[dict]:
    """Sorted OHLC candles: {t, high, low, close}."""
    d = ds_root / "market_raw" / sym / "15m"
    p = _newest_data_jsonl(d)
    out = []
    if p is None:
        return out
    for line in p.open():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        t = _epoch(r.get("ts") or r.get("time") or r.get("timestamp"))
        hi, lo, cl = _f(r.get("high")), _f(r.get("low")), _f(r.get("close"))
        if t is None or hi is None or lo is None or cl is None:
            continue
        out.append({"t": t, "high": hi, "low": lo, "close": cl})
    out.sort(key=lambda x: x["t"])
    return out


def _asof(sorted_ts: list[float], rows: list[dict], when: float) -> Optional[dict]:
    """Strictly-prior as-of: the last row with ts <= when (no peeking)."""
    i = bisect_right(sorted_ts, when)
    return rows[i - 1] if i > 0 else None


def _simulate(
    candles: list[dict], cand_ts: list[float], entry_epoch: float,
    entry: float, is_long: bool, sl: float, tp: float, max_hold: int,
) -> Optional[float]:
    """Forward triple-barrier walk from the first bar strictly after entry.

    Returns realized R in units of the stop distance |entry - sl|: +R_tp on a TP
    hit, -1.0 on an SL hit, else marked-to-close at the cap. None if un-simulable.
    """
    risk = abs(entry - sl)
    if risk <= 0 or entry <= 0:
        return None
    r_tp = abs(tp - entry) / risk
    i = bisect_right(cand_ts, entry_epoch)
    end = min(i + max_hold, len(candles))
    for j in range(i, end):
        c = candles[j]
        if is_long:
            if c["low"] <= sl:
                return -1.0
            if c["high"] >= tp:
                return r_tp
        else:
            if c["high"] >= sl:
                return -1.0
            if c["low"] <= tp:
                return r_tp
    if end > i:  # mark-to-close at the cap
        close = candles[end - 1]["close"]
        return ((close - entry) if is_long else (entry - close)) / risk
    return None


def _agg(rs: list[float]) -> dict:
    if not rs:
        return {"n": 0}
    wins = sum(1 for r in rs if r > 0)
    cum, peak, mdd = 0.0, 0.0, 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    return {
        "n": len(rs),
        "win_rate": round(wins / len(rs), 4),
        "mean_R": round(sum(rs) / len(rs), 4),
        "sum_R": round(cum, 3),
        "maxDD_R": round(mdd, 3),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/trade_journal.db")
    ap.add_argument("--datasets-root", default="datasets-out")
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--max-hold", type=int, default=96, help="max bars to hold (96 = 24h at 15m)")
    args = ap.parse_args(argv)

    ds_root = Path(args.datasets_root)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    skipped = {"no_fc_cover": 0, "no_candle": 0, "bad_row": 0, "unsimulable": 0}
    fc_rs: dict[str, list[float]] = {s: [] for s in symbols}
    actual_rs: dict[str, list[float]] = {s: [] for s in symbols}

    for sym in symbols:
        fts, frows = _load_forecasts(sym, ds_root)
        candles = _load_candles(sym, ds_root)
        cand_ts = [c["t"] for c in candles]
        if not fts or not candles:
            print(f"[{sym}] no fc side-stream ({len(fts)} rows) or candles ({len(candles)}) — skipping")
            continue
        rows = con.execute(
            "SELECT entry_price, direction, stop_loss, take_profit_1, timestamp, created_at "
            "FROM trades WHERE symbol=? AND status='closed' AND COALESCE(is_backtest,0)=0",
            (sym,),
        ).fetchall()
        print(f"[{sym}] closed trades={len(rows)} fc_rows={len(fts)} candles={len(candles)} "
              f"fc_span=[{_iso(fts[0])}..{_iso(fts[-1])}]")
        for r in rows:
            entry = _f(r["entry_price"])
            when = _epoch(r["timestamp"] or r["created_at"])
            dirn = (r["direction"] or "").lower()
            is_long = dirn in ("buy", "long")
            if entry is None or when is None or not dirn:
                skipped["bad_row"] += 1
                continue
            fc = _asof(fts, frows, when)
            if fc is None:
                skipped["no_fc_cover"] += 1
                continue
            # fc quantiles are log-returns; convert to prices around entry.
            q10p = entry * math.exp(fc["q10"])
            q90p = entry * math.exp(fc["q90"])
            if is_long:
                fc_sl, fc_tp = q10p, q90p
            else:
                fc_sl, fc_tp = q90p, q10p
            fr = _simulate(candles, cand_ts, when, entry, is_long, fc_sl, fc_tp, args.max_hold)
            if fr is None:
                skipped["unsimulable"] += 1
                continue
            fc_rs[sym].append(fr)
            # actual arm: re-simulate the SL/TP the bot actually placed, same engine.
            a_sl, a_tp = _f(r["stop_loss"]), _f(r["take_profit_1"])
            if a_sl is not None and a_tp is not None:
                ar = _simulate(candles, cand_ts, when, entry, is_long, a_sl, a_tp, args.max_hold)
                if ar is not None:
                    actual_rs[sym].append(ar)

    print("\n=== RESULTS (R in units of each arm's own stop distance) ===")
    for sym in symbols:
        print(f"[{sym}] fc-geometry {_agg(fc_rs[sym])}")
        print(f"[{sym}] actual-SLTP {_agg(actual_rs[sym])}")
    all_fc = [r for s in symbols for r in fc_rs[s]]
    all_ac = [r for s in symbols for r in actual_rs[s]]
    print(f"[ALL] fc-geometry {_agg(all_fc)}")
    print(f"[ALL] actual-SLTP {_agg(all_ac)}")
    print(f"skipped: {skipped}")
    con.close()
    return 0


def _iso(e: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(e, timezone.utc).strftime("%Y-%m-%d")


if __name__ == "__main__":
    raise SystemExit(main())
