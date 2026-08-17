#!/usr/bin/env python3
"""Aggregate a harness `--emit-trades` file into a COMMITTABLE per-leg MFE distribution.

WHY THIS EXISTS
---------------
`PB-20260817-NO-COMMITTED-PER-TRADE-HARNESS-MFE`. M31 P4 Check B compares the
LIVE final-MFE distribution against the HARNESS one, and needs both halves. The
live half is a soak-depth problem. The harness half is not, and it was missing
for a duller reason: `scripts/backtest_trend.py` computes `Trade.mfe_r` and
writes it into the `--emit-trades` JSONL, but **that JSONL is committed
nowhere**, and `docs/research/m20-sweep-corpus.jsonl` holds CELL-LEVEL
aggregates only — verified here, not inherited: a key census over all 1,376
corpus rows finds **zero** keys containing `mfe`.

So Check B's harness side had no standing artifact and had to be regenerated
per comparison — and a session waiting only on live depth would have found it
still missing when the depth arrived.

The backlog row accepts either a committed distribution or a documented
one-command re-run, and prefers the distribution: *"it is small, it versions
with the corpus, and it does not need a sweep to be reproducible."* This writes
that distribution.

⚠️ WHY THIS REFUSES AN UNCAPPED SWEEP, AND WHY THAT IS THE POINT
----------------------------------------------------------------
`backtest_trend.py` has NO take-profit exit path unless `--tp-cap-pct > 0`, so
an uncapped book's `mfe_r` runs past the venue ceiling **by construction**. A
distribution built from one is not a weaker measurement of the same thing — it
measures a different book, and committing it would put a permanently wrong
artifact under the name Check B reads. `m31_mfe_parity` already refuses such a
comparison (`harness_uncapped`); this refuses to WRITE one in the first place.

The same reasoning is why the artifact carries `symbol` + `timeframe` per leg
and the consumer checks them. The repo's only committed candles are
`data/backtest_candles.csv` — **BTCUSDT 1-MINUTE**, median (high−low)/close
0.101% — where the 9.9% cap lands at ~37R, against live legs measured at cap_R
2.13–5.83. A distribution generated from that fixture is off by an order of
magnitude, and silently comparing it to a live 4h leg is precisely the defect
family M31 exists to close (*the harness measured a book production does not
run*). Per-leg candles are gitignored (`data/*.csv`), so the real artifact is
produced by a trainer-side sweep.

PERCENTILES ARE IMPORTED, NOT RE-DERIVED
----------------------------------------
`_pct` comes from `m31_mfe_parity`. Two definitions of p80 — one writing the
artifact, one reading it — would drift silently and both would look right in
isolation, which is the same rule that made `backtest_trend.py` import
`r_distances` from the live telemetry module rather than re-implement it.

Usage:
    # write/update one leg from a CAPPED sweep's emit file
    m31_harness_mfe_dist.py --emit emit_sol_4h.jsonl \\
        --symbol SOLUSDT --timeframe 4h --tp-cap-pct 0.099

    # read back what a committed artifact claims
    m31_harness_mfe_dist.py --show

    # prove the tool can fail before trusting a quiet result
    m31_harness_mfe_dist.py --self-test

Tier 1 — research tooling. Reads nothing live, writes no config, changes no
exit. It aggregates a backtest artifact; every lever value stays Tier-3.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[2]
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from m31_mfe_parity import _f, _pct  # noqa: E402  (the ONE percentile definition)

#: Where the committed artifact lives. Small by construction — one row per leg,
#: percentiles + n, never per-trade rows (the corpus must not carry per-trade
#: volume, per the backlog row's own resolution criteria).
DEFAULT_OUT = _REPO / "docs" / "research" / "m31-harness-mfe-dist.jsonl"


def summarize_emit(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per-leg `mfe_r` stats from `--emit-trades` rows, keyed by `strategy`.

    Rows are grouped by the emit file's own `strategy` field — the per-leg
    attribution `backtest_trend.py` stamps. A row without a usable `mfe_r` is
    COUNTED, not silently dropped: `rows_without_mfe` beside `n` is the
    denominator that says whether `n` covers the file.
    """
    by_leg: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        leg = r.get("strategy") or "(unattributed)"
        slot = by_leg.setdefault(leg, {"values": [], "rows_without_mfe": 0})
        v = _f(r.get("mfe_r"))
        if v is None:
            slot["rows_without_mfe"] += 1
        else:
            slot["values"].append(v)
    out: Dict[str, Dict[str, Any]] = {}
    for leg, slot in by_leg.items():
        vals = slot["values"]
        out[leg] = {
            "n": len(vals),
            "rows_without_mfe": slot["rows_without_mfe"],
            "p50": _pct(vals, 0.50),
            "p80": _pct(vals, 0.80),
            "max": (max(vals) if vals else None),
            "min": (min(vals) if vals else None),
        }
    return out


def _read_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def load_dist(path: Path) -> Dict[str, Dict[str, Any]]:
    """Read a committed artifact into `{leg: record}`. Missing file ⇒ empty."""
    if not path.exists():
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for rec in _read_rows(path):
        leg = rec.get("leg")
        if leg:
            out[str(leg)] = rec
    return out


def write_dist(path: Path, records: Dict[str, Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for leg in sorted(records):
            fh.write(json.dumps(records[leg], sort_keys=True) + "\n")


def build_records(
    stats: Dict[str, Dict[str, Any]],
    *,
    symbol: str,
    timeframe: str,
    tp_cap_pct: float,
    source: str,
    now: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """One self-describing record per leg.

    Every field a consumer needs to REFUSE a mismatched comparison travels with
    the numbers: which instrument and bar they came from, and whether the sweep
    was capped. A bare percentile triple would be indistinguishable from one
    generated on the wrong fixture.
    """
    ts = now or datetime.now(timezone.utc).isoformat()
    out: Dict[str, Dict[str, Any]] = {}
    for leg, s in stats.items():
        out[leg] = {
            "leg": leg,
            "symbol": symbol,
            "timeframe": timeframe,
            "tp_cap_pct": tp_cap_pct,
            "n": s["n"],
            "rows_without_mfe": s["rows_without_mfe"],
            "p50": s["p50"],
            "p80": s["p80"],
            "max": s["max"],
            "min": s["min"],
            "generated_at": ts,
            "source": source,
        }
    return out


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--emit", nargs="*", default=[],
                    help="backtest_trend.py --emit-trades JSONL (strategy + mfe_r per row)")
    ap.add_argument("--symbol", help="instrument the sweep ran on (e.g. SOLUSDT)")
    ap.add_argument("--timeframe", help="bar the sweep ran on (e.g. 4h)")
    ap.add_argument("--tp-cap-pct", type=float, default=0.0,
                    help="the sweep's --tp-cap-pct. MUST be > 0: an uncapped "
                         "book's mfe_r runs past the venue ceiling by "
                         "construction and is not comparable to the live book.")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--show", action="store_true",
                    help="print the committed artifact and exit (no write)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv[1:])

    if args.self_test:
        return self_test()

    out_path = Path(args.out)

    if args.show:
        recs = load_dist(out_path)
        if not recs:
            print(f"m31-harness-mfe-dist: NO ARTIFACT at {out_path}")
            print("  Check B's harness half is absent -- not zero, absent.")
            return 0
        print(f"m31-harness-mfe-dist: {len(recs)} leg(s) at {out_path}")
        for leg in sorted(recs):
            r = recs[leg]
            print(f"  {leg:32} {r.get('symbol')}/{r.get('timeframe')} "
                  f"cap={r.get('tp_cap_pct')} n={r.get('n')} "
                  f"p50={r.get('p50')} p80={r.get('p80')} max={r.get('max')}")
        return 0

    if not args.emit:
        print("ERROR: --emit is required (or use --show / --self-test).",
              file=sys.stderr)
        return 2
    # REFUSE rather than write a poisoned artifact. An uncapped sweep measures a
    # different book; committing its distribution under the name Check B reads
    # would be worse than the honest absence it replaces.
    if args.tp_cap_pct <= 0.0:
        print("ERROR: --tp-cap-pct must be > 0 (production uses 0.099). An "
              "uncapped sweep has no take-profit exit path, so mfe_r runs past "
              "the venue ceiling by construction and the distribution is not "
              "comparable to the live book.", file=sys.stderr)
        return 2
    if not args.symbol or not args.timeframe:
        print("ERROR: --symbol and --timeframe are required -- they are what "
              "lets a consumer refuse a distribution generated on the wrong "
              "instrument or bar.", file=sys.stderr)
        return 2

    rows: List[Dict[str, Any]] = []
    for p in args.emit:
        try:
            rows.extend(_read_rows(Path(p)))
        except OSError as exc:
            print(f"ERROR: cannot read {p}: {exc}", file=sys.stderr)
            return 2
    if not rows:
        print("ERROR: emit file(s) held no rows -- nothing to summarize. This "
              "is 'we read an empty file', not 'the leg has no MFE'.",
              file=sys.stderr)
        return 2

    stats = summarize_emit(rows)
    graded = {leg: s for leg, s in stats.items() if s["n"] > 0}
    if not graded:
        print(f"ERROR: {len(rows)} row(s) carried no usable mfe_r. Re-run the "
              "sweep with a build that emits it rather than committing an "
              "empty distribution.", file=sys.stderr)
        return 2

    records = load_dist(out_path)
    new = build_records(graded, symbol=args.symbol, timeframe=args.timeframe,
                        tp_cap_pct=args.tp_cap_pct,
                        source="backtest_trend.py --emit-trades")
    records.update(new)
    write_dist(out_path, records)
    print(f"m31-harness-mfe-dist: wrote {len(new)} leg(s) to {out_path} "
          f"({len(records)} total)")
    for leg in sorted(new):
        r = new[leg]
        print(f"  {leg:32} n={r['n']} p50={r['p50']} p80={r['p80']} "
              f"max={r['max']} (rows_without_mfe={r['rows_without_mfe']})")
    return 0


def self_test() -> int:
    """RULE ONE: show the tool finds a positive AND refuses the bad cases."""
    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        print(f"  {name}: {'PASS' if cond else 'FAIL'}")
        ok = ok and cond

    print("m31-harness-mfe-dist self-test")

    rows = [{"strategy": "leg_a", "mfe_r": float(i)} for i in range(1, 11)]
    s = summarize_emit(rows)["leg_a"]
    check("1 percentiles computed over the emit rows",
          s["n"] == 10 and s["max"] == 10.0 and s["min"] == 1.0)
    # Nearest-rank, matching m31_mfe_parity._pct exactly (imported, not copied).
    check("2 p50/p80 match the imported nearest-rank definition",
          s["p50"] == _pct([float(i) for i in range(1, 11)], 0.50)
          and s["p80"] == _pct([float(i) for i in range(1, 11)], 0.80))

    mixed = rows + [{"strategy": "leg_a"}, {"strategy": "leg_a", "mfe_r": None}]
    s2 = summarize_emit(mixed)["leg_a"]
    check("3 rows without mfe_r are COUNTED, not silently dropped",
          s2["n"] == 10 and s2["rows_without_mfe"] == 2)

    two = summarize_emit(rows + [{"strategy": "leg_b", "mfe_r": 99.0}])
    check("4 legs are separated by the emit row's own strategy field",
          set(two) == {"leg_a", "leg_b"} and two["leg_b"]["n"] == 1)

    rec = build_records(two, symbol="SOLUSDT", timeframe="4h",
                        tp_cap_pct=0.099, source="t", now="2026-01-01T00:00:00Z")
    check("5 every record is self-describing (symbol/timeframe/cap travel)",
          all(rec[k]["symbol"] == "SOLUSDT" and rec[k]["timeframe"] == "4h"
              and rec[k]["tp_cap_pct"] == 0.099 for k in rec))

    # 6-8 — the refusals, exercised against a REAL emit file.
    #
    # ⚠️ These deliberately do NOT point at a nonexistent path. An earlier
    # version passed `x.jsonl` and asserted `rc == 2` — and a mutation test
    # caught that it still "passed" with the cap guard DELETED, because the
    # missing file returns 2 by itself. The test asserted an exit code that a
    # different failure also produces, which is the unasserted-denominator
    # class one level down: a control that cannot fail is not a control.
    # A real file makes the refusal the ONLY reason 2 can come back, and the
    # positive case below proves the fixture is otherwise acceptable.
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        emit = Path(td) / "emit.jsonl"
        emit.write_text('{"strategy": "leg_a", "mfe_r": 1.0}\n', encoding="utf-8")
        out = Path(td) / "dist.jsonl"
        base = ["m31_harness_mfe_dist.py", "--emit", str(emit), "--out", str(out)]

        # The POSITIVE CONTROL first — otherwise the three refusals below prove
        # only that the tool refuses everything.
        rc_ok = main(base + ["--symbol", "S", "--timeframe", "4h",
                             "--tp-cap-pct", "0.099"])
        check("6 a CAPPED sweep with identity is WRITTEN (positive control)",
              rc_ok == 0 and out.exists() and load_dist(out)["leg_a"]["n"] == 1)

        out.unlink()
        rc = main(base + ["--symbol", "S", "--timeframe", "4h",
                          "--tp-cap-pct", "0"])
        check("7 an UNCAPPED sweep is refused, and writes NOTHING",
              rc == 2 and not out.exists())

        rc = main(base + ["--tp-cap-pct", "0.099"])
        check("8 missing symbol/timeframe is refused, and writes NOTHING",
              rc == 2 and not out.exists())

    print("SELF-TEST PASS" if ok else "SELF-TEST FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
