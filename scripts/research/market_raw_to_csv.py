#!/usr/bin/env python3
"""Convert a datasets-out/market_raw/<SYM>/<interval>/*/data.jsonl candle
side-stream into the OHLCV CSV shape the standalone backtest harnesses read
(timestamp,open,high,low,close,volume). Stdlib-only; trainer-side utility
(M20). Usage: market_raw_to_csv.py SYMBOL DATASETS_ROOT OUT_CSV [INTERVAL]

REFUSES TO WRITE A NATIVE-NAMED CSV THAT IS ACTUALLY THE PROXY
(BL-20260814-EQUITY-DAILY-LABELS-PROXY-DATA-AS-THE-NATIVE-SYMBOL).

`build_trainer_datasets.sh` builds some shards under the MICRO symbol from the
FULL-SIZE contract -- `build_equity_daily MGC "GC=F"`, `MHG "HG=F"` -- so
`market_raw/MGC/1d` holds GC=F bars. Converting that to `data/MGC_1d.csv`
produces a file whose NAME asserts a provenance its CONTENT does not have, and
nothing downstream can tell: the rows are real market data, the counts are real,
the prices are plausible.

That is not a cosmetic mislabel. `m20_fleet_exit_sweep.resolve_data(...,
prefer_native=True)` would resolve such a file and report `proxy=False`, and
`m20_exit_head_round` REFUSES proxied data ("native history required for head
training") -- so the refusal would PASS and the head would train on exactly the
series the check exists to exclude. A session did this on 2026-08-14, caught it
one command before running, and removed the files.

So this tool now checks its own output before writing: if the symbol has a
declared proxy and that proxy's CSV already exists, the two are compared on
overlapping timestamps. At `_IDENTICAL_REFUSE_FRAC` or more identical closes
they are the same series, and the write is REFUSED with the measurement in the
message. `--allow-proxy-alias` overrides for the legitimate case of deliberately
materialising a proxy under its own name.

Deliberately a CONSUMER-side check: it needs no change to the producer and no
new field in the shard, so it works on every shard already on disk. The
producer-side fix (record the fetched ticker IN the shard) is the better long
answer and is criterion (a) of the backlog item; this is the half that can ship
without touching the trainer's nightly dataset build.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Symbols whose harness data may legitimately come from a different instrument.
# Kept in sync with `m20_fleet_exit_sweep.PROXY_DATA` BY VALUE rather than by
# import: this script is stdlib-only and trainer-side, and importing the sweep
# would drag yaml + the whole module in. A drift here fails OPEN (no check),
# never closed, so a missing entry cannot block a legitimate conversion.
_PROXY_OF = {"MGC": "GC_F", "XAUUSD": "GC_F", "MES": "ES_F", "MHG": "HG_F"}

# Share of overlapping closes that must match before two series are called the
# same.
#
# THE DISCRIMINATOR IS BIMODAL, WHICH IS WHY THIS IS NOT A SIMILARITY SCORE.
# Compared at 1e-6, two DIFFERENT instruments share ~0% exactly-identical
# closes -- even highly correlated ones, because the low-order digits differ on
# essentially every bar. Two copies of the SAME series share ~100%. Nothing
# real lands in between, so the threshold only has to separate "near 1" from
# "near 0" and every value in 0.5..0.99 picks the same answer on real data.
#
# 0.95 rather than 0.99 because the high side is not exactly 1.0 and the
# shortfall does not scale with n: the proxy CSV's final bar is often a stale or
# unsettled print (the measured MGC case was 2,511 of 2,512), and on a SHORT
# overlap that single bar is a large fraction -- 19 of 20 is 0.95, which a 0.99
# cut would read as "a different series" and wave through. Choosing the looser
# cut costs nothing against genuinely native data (~0%) and removes a
# length-dependent blind spot.
_IDENTICAL_REFUSE_FRAC = 0.95


def _load_closes(path: Path) -> dict[str, str]:
    """{date -> close} from an OHLCV CSV. Empty dict if unreadable."""
    try:
        with path.open() as f:
            return {r["timestamp"][:10]: r["close"] for r in csv.DictReader(f)
                    if r.get("timestamp") and r.get("close")}
    except (OSError, KeyError, csv.Error):
        return {}


def proxy_identity(rows: list[tuple], sym: str, out: str) -> tuple[str, int, int] | None:
    """(proxy_name, identical, overlap) when `rows` IS the proxy series.

    None when there is no declared proxy, no proxy CSV to compare against, no
    overlapping timestamps, or the two genuinely differ. Every one of those is
    a FAIL-OPEN: this check can refuse a write only on positive evidence that
    the two series are the same, never on absence of evidence.
    """
    proxy = _PROXY_OF.get(sym.upper())
    if not proxy:
        return None
    # The proxy CSV sits beside the output, under the same interval suffix.
    name = Path(out).name
    suffix = name[len(sym):] if name.upper().startswith(sym.upper()) else None
    if not suffix:
        return None
    other = _load_closes(Path(out).parent / f"{proxy}{suffix}")
    if not other:
        return None
    mine = {str(r[0])[:10]: r[4] for r in rows}
    shared = set(mine) & set(other)
    if not shared:
        return None
    identical = 0
    for d in shared:
        try:
            if abs(float(mine[d]) - float(other[d])) < 1e-6:
                identical += 1
        except (TypeError, ValueError):
            continue
    if identical >= len(shared) * _IDENTICAL_REFUSE_FRAC:
        return proxy, identical, len(shared)
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbol")
    ap.add_argument("datasets_root")
    ap.add_argument("out_csv")
    ap.add_argument("interval", nargs="?", default="15m")
    ap.add_argument("--allow-proxy-alias", action="store_true",
                    help="write even when the shard is the proxy series under a "
                         "native name. For deliberately materialising a proxy; "
                         "NOT for feeding a consumer that refuses proxied data.")
    a = ap.parse_args(argv)

    sym, root, out, interval = a.symbol, Path(a.datasets_root), a.out_csv, a.interval
    d = root / "market_raw" / sym / interval
    cands = sorted(d.glob("*/data.jsonl"))
    if not cands:
        print(f"no data.jsonl under {d}", file=sys.stderr)
        return 1
    rows = []
    for line in cands[-1].open():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = r.get("ts") or r.get("time") or r.get("timestamp")
        try:
            if str(ts).replace(".", "").isdigit():
                ts = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
            o, h, lo, c = (float(r.get("open", r.get("close"))), float(r["high"]),
                           float(r["low"]), float(r["close"]))
        except (KeyError, TypeError, ValueError):
            continue
        rows.append((ts, o, h, lo, c, float(r.get("volume") or 0.0)))
    rows.sort(key=lambda x: x[0])

    same = proxy_identity(rows, sym, out)
    if same and not a.allow_proxy_alias:
        proxy, identical, overlap = same
        print(
            f"REFUSING to write {out}: this shard is the {proxy} series, not native {sym}.\n"
            f"  MEASURED: {identical} of {overlap} overlapping closes are IDENTICAL to "
            f"{proxy}{Path(out).name[len(sym):]}.\n"
            f"  {d} is very likely built from the full-size contract "
            f"(see build_trainer_datasets.sh::build_equity_daily).\n"
            f"  Writing it would give the file a NAME asserting a provenance its CONTENT\n"
            f"  lacks; resolve_data(prefer_native=True) would then report proxy=False and\n"
            f"  m20_exit_head_round's native-only refusal would PASS on proxy data.\n"
            f"  Native {sym} lives under data/ibkr_datasets/market_raw/. Pass\n"
            f"  --allow-proxy-alias only if you actually want the proxy under this name.",
            file=sys.stderr)
        return 2
    if same:
        proxy, identical, overlap = same
        print(f"WARNING: {out} is the {proxy} series ({identical}/{overlap} closes "
              f"identical) — writing anyway because --allow-proxy-alias was passed.",
              file=sys.stderr)

    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        w.writerows(rows)
    print(f"{sym}: wrote {len(rows)} rows -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
