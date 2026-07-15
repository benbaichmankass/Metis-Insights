#!/usr/bin/env python3
"""Session / killzone frequency-reduction re-scorer (M22 wave-2 D1, research/Tier-1).

The maker study (M22 P1) confirmed **fee-drag is the universal small-TF killer**:
nearly every small-TF cell is gross-positive but taker fees on both legs flip it
negative, and maker execution can't rescue a stop-based scalp (its exit is a
taker event). This tool tests the OTHER lever against fee-drag — **cut frequency,
not fees** — by asking: *if we only take the trades entered inside the
high-liquidity killzones, does the surviving subset flip net-positive?* Fewer
trades = less total fee drag on the same per-trade gross edge; the ICT thesis is
that killzone entries are also higher-quality.

No harness edit needed: every research emit already carries ``entry_time``, so we
bucket the emitted trades by UTC hour / named killzone and re-aggregate. The fee
arithmetic is the SAME exact re-pricing as ``maker_economics.py``
(``fee_r_taker = gross_r - net_r``; ``net_r(X) = gross_r - fee_r_taker*X/taker_bps``)
so the two tools agree at the taker setting.

Killzones (UTC, DST-agnostic approximations — crypto trades 24/7, so these mark
when TradFi/FX liquidity is active, not an exchange session):
  * ``asian``        00:00–06:00
  * ``london``       07:00–10:00  (London open KZ)
  * ``ny_am``        12:00–15:00  (NY AM KZ)
  * ``london_close`` 15:00–17:00
  * ``ny_pm``        17:00–21:00
  * ``off``          everything else (06–07, 10–12, 21–24)

Reported per cell (a cell = one emit file, tf parsed from ``*_<tf>.jsonl``):
  * the ALL-hours baseline (reproduces the harness net at the taker setting),
  * a per-killzone breakdown (trades / net_R taker / net_R maker-both),
  * the BEST net-positive killzone subset — the "restrict to these hours" result
    that answers whether a session gate flips the cell.

A cell "flips" via session gating if some killzone subset is net-positive at the
REALISTIC execution setting (taker, since a stop-based scalp exit stays taker).
If nothing flips even here, the small-TF cell is dead to frequency-reduction too.
Research only; proposes nothing.
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_TF_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
               "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400}

# (name, start_hour_inclusive, end_hour_exclusive) in UTC.
_KILLZONES = [
    ("asian", 0, 6),
    ("london", 7, 10),
    ("ny_am", 12, 15),
    ("london_close", 15, 17),
    ("ny_pm", 17, 21),
]


def _tf_from_name(path: str) -> str:
    m = re.search(r"_(\d+[mhd])\.jsonl$", Path(path).name)
    return m.group(1) if m else ""


def _read(path: str) -> List[dict]:
    out = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _hour_of(entry_time: Any) -> Optional[int]:
    """UTC hour-of-day from an emit's entry_time (ISO string, epoch, or pandas
    'YYYY-MM-DD HH:MM:SS+00:00'). Returns None if unparseable."""
    if entry_time is None:
        return None
    # epoch seconds / ms
    if isinstance(entry_time, (int, float)):
        ts = float(entry_time)
        if ts > 1e12:  # ms
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).hour
        except (OverflowError, OSError, ValueError):
            return None
    s = str(entry_time).strip()
    if not s:
        return None
    # numeric string?
    try:
        ts = float(s)
        if ts > 1e12:
            ts /= 1000.0
        if ts > 1e8:  # plausibly an epoch, not an hour
            return datetime.fromtimestamp(ts, tz=timezone.utc).hour
    except ValueError:
        pass
    iso = s.replace("Z", "+00:00").replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        m = re.search(r"[T ](\d{2}):", s)
        return int(m.group(1)) if m else None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).hour


def _kz_of_hour(hour: Optional[int]) -> str:
    if hour is None:
        return "unknown"
    for name, lo, hi in _KILLZONES:
        if lo <= hour < hi:
            return name
    return "off"


def _net_at(rows: List[dict], bps: float, taker_bps: float) -> Dict[str, Any]:
    """Aggregate net_R re-priced at round-trip ``bps`` (same math as maker_economics)."""
    n = len(rows)
    if n == 0:
        return {"trades": 0, "net_total_r": 0.0, "net_exp_r": None,
                "win_pct": None, "gross_total_r": 0.0}
    scale = bps / taker_bps if taker_bps else 0.0
    per = []
    gross = 0.0
    for r in rows:
        g = float(r.get("gross_r", 0.0))
        net_taker = float(r.get("net_r", g))
        per.append(g - (g - net_taker) * scale)
        gross += g
    wins = sum(1 for x in per if x > 0)
    return {"trades": n, "net_total_r": round(sum(per), 2),
            "net_exp_r": round(sum(per) / n, 4), "win_pct": round(100 * wins / n, 1),
            "gross_total_r": round(gross, 2)}


def _score_cell(path: str, taker_bps: float, maker_both_bps: float) -> Dict[str, Any]:
    rows = _read(path)
    label = Path(path).stem
    tf = _tf_from_name(path)
    # bucket
    by_kz: Dict[str, List[dict]] = {}
    parsed = 0
    for r in rows:
        h = _hour_of(r.get("entry_time"))
        if h is not None:
            parsed += 1
        by_kz.setdefault(_kz_of_hour(h), []).append(r)
    all_taker = _net_at(rows, taker_bps, taker_bps)
    kz_stats = {}
    for name in [k[0] for k in _KILLZONES] + ["off", "unknown"]:
        if name in by_kz:
            kz_stats[name] = {
                "taker": _net_at(by_kz[name], taker_bps, taker_bps),
                "maker_both": _net_at(by_kz[name], maker_both_bps, taker_bps),
            }
    # best net-positive killzone SUBSET at taker (union of KZs whose taker net > 0)
    positive_kzs = [name for name, st in kz_stats.items()
                    if name not in ("off", "unknown") and st["taker"]["net_total_r"] > 0]
    subset_rows = [r for name in positive_kzs for r in by_kz.get(name, [])]
    best_subset = {
        "killzones": positive_kzs,
        "taker": _net_at(subset_rows, taker_bps, taker_bps),
        "maker_both": _net_at(subset_rows, maker_both_bps, taker_bps),
    }
    return {"cell": label, "tf": tf, "trades": all_taker["trades"],
            "entry_time_coverage_pct": round(100 * parsed / len(rows), 1) if rows else 0.0,
            "all_hours": all_taker, "by_killzone": kz_stats, "best_subset": best_subset}


def _fmt(results: List[Dict[str, Any]], taker_bps: float, maker_both_bps: float) -> str:
    lines = [
        f"# Session / killzone frequency-reduction — taker={taker_bps}bps | maker_both~{maker_both_bps}bps",
        "",
        "Per-cell: ALL-hours baseline vs the best net-positive killzone SUBSET "
        "(the 'restrict to these hours' result). A cell flips via session gating "
        "if the subset is net-positive at the REALISTIC (taker) bound.",
        "",
        "| cell | tf | all-hrs trades | all-hrs net_R taker | subset KZs | subset trades | subset net_R taker | subset net_R maker | flips? |",
        "|---|--:|--:|--:|---|--:|--:|--:|:-:|",
    ]
    for r in results:
        a = r["all_hours"]
        s = r["best_subset"]
        st = s["taker"]
        flips = "✅" if st["net_total_r"] > 0 and a["net_total_r"] <= 0 else (
            "=" if st["net_total_r"] > 0 else "❌")
        kzs = "+".join(s["killzones"]) if s["killzones"] else "—"
        lines.append(
            f"| {r['cell']} | {r['tf']} | {a['trades']} | {a['net_total_r']} | {kzs} | "
            f"{st['trades']} | {st['net_total_r']} | {s['maker_both']['net_total_r']} | {flips} |")
    lines += ["",
              "Legend: ✅ the killzone subset is net-positive where all-hours was NOT "
              "(session gating flips it) · = already positive all-hours (gate not needed) · "
              "❌ no killzone subset is net-positive (frequency-reduction can't save it).",
              "'subset net_R maker' = the same subset re-priced at maker-both (the extra "
              "headroom IF the strategy could also be maker-executed).",
              "Faithfulness: 'all-hrs net_R taker' must equal the harness's own net for the run."]
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description="Re-score emitted backtest trades restricted to killzone entry windows.")
    p.add_argument("--emits", nargs="+", required=True,
                   help="Emit JSONL paths or globs (must carry entry_time; tf parsed from *_<tf>.jsonl).")
    p.add_argument("--taker-bps", type=float, default=7.5)
    p.add_argument("--maker-both-bps", type=float, default=0.0)
    p.add_argument("--json", dest="json_out", default=None)
    args = p.parse_args(argv[1:])

    paths: List[str] = []
    for e in args.emits:
        paths.extend(sorted(glob.glob(e)) or ([e] if Path(e).exists() else []))
    if not paths:
        print("ERROR: no emit files matched.", file=sys.stderr)
        return 1
    results = [_score_cell(pp, args.taker_bps, args.maker_both_bps) for pp in paths]
    print(_fmt(results, args.taker_bps, args.maker_both_bps))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, indent=2))
        print(f"\nJSON -> {args.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
