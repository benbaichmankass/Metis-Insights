#!/usr/bin/env python3
"""Build a roll-adjusted CONTINUOUS futures series from per-contract bars.

Offline research tool (Tier-1, stdlib-only, no socket, no live path). Reads
per-contract OHLCV bars and writes a single back-adjusted continuous series in
the canonical `market_raw` 9-key shape, so the existing backtest harnesses
(`scripts/research/backtest_trend.py`, `scripts/backtest_pullback.py`) read it
with NO change — the roll gaps that make a spliced native-futures series lie to
a breakout/trend backtest are removed.

See `docs/research/roll-adjusted-continuous-futures-DESIGN.md` for the why and
the end-to-end plan. The per-contract INPUT is produced by the per-contract
IBKR pull (increment 2); until that lands you can also feed any per-contract
jsonl in the documented shape.

INPUT — two accepted forms (auto-detected):
  1. A single flat jsonl where each bar carries a `contract` field
     (`{"ts","contract","open","high","low","close","volume", ...}`) — the
     per-contract-pull output. Pass with `--tagged FILE`.
  2. One jsonl file per contract, each named/globbed, all for one symbol+tf.
     Pass with `--contract MONTH=FILE` (repeatable) or `--contract-glob GLOB`
     where the contract month is parsed from the path (`.../<MONTH>/...` or a
     `<sym>_<MONTH>.jsonl` basename).

OUTPUT — canonical `market_raw` jsonl (one row per line), continuous, ascending
by ts, symbol defaulting to `<SYMBOL>.c`. Writes to `--out` (or stdout with
`--out -`). Also prints a one-line summary of the rolls applied to stderr.

Examples:
  # from the per-contract pull's tagged stream
  python scripts/research/build_continuous_contract.py \
      --tagged data/ibkr_datasets/market_raw_percontract/MGC/1h/v001/data.jsonl \
      --symbol MGC --timeframe 1h --method panama \
      --out /tmp/MGC.c_1h.jsonl

  # then backtest the trend cell on the CLEAN series
  python scripts/research/backtest_trend.py --data /tmp/MGC.c_1h.jsonl \
      --symbol MGC --timeframe 1h --donchian 20 --atr-period 14 \
      --atr-stop-mult 2.5 --trail-mult 3.0 --json -
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

# Import the package regardless of CWD (mirrors the other scripts/research tools).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from ml.datasets.continuous import (  # noqa: E402
    METHODS,
    build_continuous,
    group_bars_by_contract,
)

_MONTH_RE = re.compile(r"(\d{6,8})")


def _read_jsonl(path: str) -> list[dict]:
    rows: list[dict] = []
    opener = sys.stdin if path == "-" else open(path, encoding="utf-8")
    close = path != "-"
    try:
        for line in opener:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    finally:
        if close:
            opener.close()
    return rows


def _month_from_path(path: str) -> str:
    # Prefer a `.../<MONTH>/...` path segment, else the last digit-run in the base.
    for seg in reversed(path.replace("\\", "/").split("/")):
        m = _MONTH_RE.fullmatch(seg)
        if m:
            return m.group(1)
    m = _MONTH_RE.search(os.path.basename(path))
    if not m:
        raise SystemExit(f"cannot parse a contract month from path: {path}")
    return m.group(1)


def _load_contracts(args: argparse.Namespace) -> list[dict]:
    if args.tagged:
        tagged = _read_jsonl(args.tagged)
        groups = group_bars_by_contract(tagged, contract_key=args.contract_key)
        dropped = sum(1 for b in tagged if not b.get(args.contract_key))
        if dropped:
            print(f"[warn] dropped {dropped} bar(s) with no "
                  f"'{args.contract_key}' tag (cannot roll-attribute)",
                  file=sys.stderr)
        return groups

    contracts: list[dict] = []
    specs: list[tuple[str, str]] = []
    for c in args.contract or []:
        if "=" not in c:
            raise SystemExit(f"--contract expects MONTH=FILE, got {c!r}")
        month, path = c.split("=", 1)
        specs.append((month, path))
    for g in args.contract_glob or []:
        for path in sorted(glob.glob(g)):
            specs.append((_month_from_path(path), path))
    if not specs:
        raise SystemExit("no input: pass --tagged, or --contract / --contract-glob")
    for month, path in specs:
        bars = _read_jsonl(path)
        contracts.append({"month": month, "bars": bars})
    return contracts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tagged", help="single flat jsonl with a per-bar contract field")
    ap.add_argument("--contract", action="append",
                    help="MONTH=FILE per-contract jsonl (repeatable)")
    ap.add_argument("--contract-glob", action="append",
                    help="glob of per-contract jsonl files; month parsed from path")
    ap.add_argument("--contract-key", default="contract",
                    help="field name carrying the contract month in --tagged input")
    ap.add_argument("--symbol", required=True, help="base symbol, e.g. MGC")
    ap.add_argument("--timeframe", required=True, help="canonical tf token, e.g. 1h")
    ap.add_argument("--method", default="panama", choices=METHODS,
                    help="back-adjust method (panama=additive default / ratio / none)")
    ap.add_argument("--out-symbol", default=None,
                    help="output symbol token (default <SYMBOL>.c)")
    ap.add_argument("--out", default="-", help="output jsonl path (- = stdout)")
    args = ap.parse_args(argv)

    contracts = _load_contracts(args)
    n_contracts = len(contracts)
    n_in = sum(len(c["bars"]) for c in contracts)
    rows = build_continuous(
        contracts,
        symbol=args.symbol,
        timeframe=args.timeframe,
        method=args.method,
        out_symbol=args.out_symbol,
    )

    if args.out == "-":
        for r in rows:
            sys.stdout.write(json.dumps(r) + "\n")
    else:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    span = f"{rows[0]['ts']} → {rows[-1]['ts']}" if rows else "(empty)"
    print(f"[ok] {n_contracts} contract(s), {n_in} in → {len(rows)} continuous "
          f"rows ({args.method}) {span} → {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
