#!/usr/bin/env python3
"""Diagnose the MES/ETH live-labeling gap (MB-20260627-002 / MB-20260626-001 #1).

RG4 (replay_pregate_live) labels a logged shadow row by joining it to a realized
regime label built FROM the market_raw candle dataset. A row is "unlabeled" when
its timestamp has no candle bar within tolerance. The hypothesis: the alt-symbol
market_raw datasets are STALE (built at training time, not refreshed), so they end
before the recent live shadow rows → empty join → all-unlabeled → RG4 unscoreable.

This probe prints, per (symbol, tf): the candle dataset's first/last bar time and
the symbol's shadow-row time range + count, so the coverage gap (candle_last <
shadow_last) is visible. Read-only; trainer-side. No args.

  python scripts/ml/_labeling_gap_probe.py
"""
import glob
import json
from pathlib import Path

# (symbol, tf) pairs to probe — the heads we care about for multi-symbol A.
TARGETS = [("BTCUSDT", "15m"), ("ETHUSDT", "1h"), ("MES", "5m"), ("MES", "15m")]
SHADOW = "runtime_logs/shadow_predictions.jsonl"

_TS_KEYS = ("timestamp", "time", "open_time", "close_time", "bar_time", "ts", "t")
_SYM_KEYS = ("symbol", "sym")


def _first(d, keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _ts_str(v):
    """Normalise a ts value (epoch s/ms or ISO) to a comparable ISO-ish string."""
    if v is None:
        return None
    try:
        n = float(v)
        if n > 1e12:      # epoch ms
            n /= 1000.0
        if n > 1e8:       # epoch s
            from datetime import datetime, timezone
            return datetime.fromtimestamp(n, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        pass
    return str(v)[:25]


def _candle_range(sym, tf):
    cands = sorted(glob.glob(f"datasets-out/market_raw/{sym}/{tf}/*/data.jsonl"))
    if not cands:
        return None, None, None
    path = cands[-1]
    first = last = None
    with open(path) as fh:
        for i, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = _first(row, _TS_KEYS)
            if t is None:
                continue
            if first is None:
                first = t
            last = t
    return path, _ts_str(first), _ts_str(last)


def _shadow_range(sym):
    p = Path(SHADOW)
    if not p.exists():
        return 0, None, None
    lo = hi = None
    n = 0
    with open(p) as fh:
        for line in fh:
            if sym not in line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if _first(row, _SYM_KEYS) != sym:
                continue
            # only regime heads (the vol-label join target)
            mid = str(row.get("model_id", ""))
            if "regime" not in mid:
                continue
            t = _ts_str(_first(row, _TS_KEYS))
            if t is None:
                continue
            n += 1
            if lo is None or t < lo:
                lo = t
            if hi is None or t > hi:
                hi = t
    return n, lo, hi


def main():
    print("== labeling-gap coverage probe ==")
    print(f"shadow_log={SHADOW} present={Path(SHADOW).exists()}")
    for sym, tf in TARGETS:
        path, c_first, c_last = _candle_range(sym, tf)
        n, s_lo, s_hi = _shadow_range(sym)
        print(f"\n-- {sym}/{tf} --")
        print(f"  candle file : {path}")
        print(f"  candle range: {c_first}  ..  {c_last}")
        print(f"  shadow rows : {n}  range {s_lo} .. {s_hi}")
        if c_last and s_hi:
            gap = "CANDLES STALE (end < shadow_last)" if c_last < s_hi else "candles cover shadow"
            print(f"  -> {gap}")


if __name__ == "__main__":
    main()
