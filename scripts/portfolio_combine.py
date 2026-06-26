#!/usr/bin/env python3
"""Combine per-strategy --emit-trades JSONL into ONE book-level daily series.

The strategy backtest harnesses (``scripts/backtest_{trend,fade,squeeze}.py``,
``backtest_ict_scalp.py`` …) each emit a per-trade JSONL via ``--emit-trades``
with the shape ``{strategy, entry_time, direction, gross_r, net_r, confidence}``
(``exit_time`` when a harness records it). This tool stacks several of those
files into a single BOOK: treat each trade as **1 unit of risk**, so a day's
book return is the SUM of every trade's ``net_r`` whose close lands on that UTC
calendar day.

Output: the daily book return series (date -> summed net_r + trade count), the
headline book stats (total net_r, n_trades, n_days, daily mean/std, annualized
Sharpe, max drawdown in R on the cumulative daily curve, by-year net_r), and a
per-source breakdown (which input file contributed what). ``--json`` writes the
full payload; ``--emit-daily PATH`` writes the daily series as a CSV
(``date,book_r,n_trades``) so ``scripts/backtest_vol_target.py`` can consume it.

The combine logic is exposed as importable functions (``load_trades`` /
``combine_daily`` / ``summarize_book``) so the vol-target overlay can reuse them
without re-implementing the parse.

Research only (Tier-1): reads JSONL, writes JSON/CSV/text. Never touches the
order path or live config. Pure + robust: malformed lines are skipped, an empty
book never raises.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

TRADING_DAYS_PER_YEAR = 252


def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse an ISO-ish timestamp to a tz-aware UTC datetime; None on failure."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # Heuristic: ms epoch if very large, else seconds.
        secs = value / 1000.0 if value > 1e11 else float(value)
        try:
            return datetime.fromtimestamp(secs, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip()
    if not s:
        return None
    # pandas/ISO style: "2024-01-02 03:00:00+00:00" or with a trailing Z.
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # Last resort: strip a fractional/space tail and try a date-only parse.
        try:
            dt = datetime.fromisoformat(s.split(" ")[0])
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _resolve_inputs(specs: Sequence[str]) -> List[str]:
    """Expand a mix of files, globs, and directories into a sorted file list."""
    out: List[str] = []
    for spec in specs:
        p = Path(spec)
        if p.is_dir():
            out.extend(sorted(str(q) for q in p.glob("*.jsonl")))
        elif any(ch in spec for ch in "*?["):
            out.extend(sorted(glob.glob(spec)))
        else:
            out.append(spec)
    # De-dup, preserve order.
    seen, uniq = set(), []
    for f in out:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq


def load_trades(specs: Sequence[str], *, basis: str = "auto") -> List[Dict[str, Any]]:
    """Load + normalize trades from emit-trades JSONL files.

    ``basis`` selects the day a trade is attributed to:
      - ``entry``: always use ``entry_time``
      - ``exit``:  use ``exit_time`` (falls back to ``entry_time`` if absent)
      - ``auto``:  prefer ``exit_time`` when the line carries it, else entry

    Each returned trade is ``{net_r, day (date|None), source, basis_used}``.
    Malformed lines / missing net_r are skipped silently (robustness mandate).
    """
    trades: List[Dict[str, Any]] = []
    for path in _resolve_inputs(specs):
        src = os.path.basename(path)
        try:
            fh = open(path, "r", encoding="utf-8")
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(rec, dict):
                    continue
                net_r = rec.get("net_r")
                if net_r is None:
                    # Fall back to gross_r only if net is genuinely absent.
                    net_r = rec.get("gross_r")
                try:
                    net_r = float(net_r)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(net_r):
                    continue
                entry_dt = _parse_ts(rec.get("entry_time"))
                exit_dt = _parse_ts(rec.get("exit_time"))
                if basis == "entry":
                    chosen, used = entry_dt, "entry"
                elif basis == "exit":
                    chosen = exit_dt or entry_dt
                    used = "exit" if exit_dt else "entry"
                else:  # auto: prefer exit when present
                    if exit_dt is not None:
                        chosen, used = exit_dt, "exit"
                    else:
                        chosen, used = entry_dt, "entry"
                day = chosen.date() if chosen is not None else None
                trades.append({"net_r": net_r, "day": day, "source": src,
                               "basis_used": used})
    return trades


def combine_daily(trades: Sequence[Dict[str, Any]]
                  ) -> List[Tuple[date, float, int]]:
    """Sum net_r per UTC calendar day -> sorted [(day, book_r, n_trades), ...].

    Trades with an unparseable day are dropped from the daily series (but they
    are still counted in the per-source/total trade tallies upstream).
    """
    by_day_r: Dict[date, float] = defaultdict(float)
    by_day_n: Dict[date, int] = defaultdict(int)
    for t in trades:
        d = t["day"]
        if d is None:
            continue
        by_day_r[d] += t["net_r"]
        by_day_n[d] += 1
    return [(d, round(by_day_r[d], 6), by_day_n[d]) for d in sorted(by_day_r)]


def _max_drawdown_r(daily_r: Sequence[float]) -> float:
    """Max drawdown (in R) on the cumulative daily-return curve."""
    cum = peak = mdd = 0.0
    for r in daily_r:
        cum += r
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return round(mdd, 6)


def sharpe_annualized(daily_r: Sequence[float]) -> Optional[float]:
    """Daily mean/std Sharpe annualized by sqrt(252). None if < 2 days."""
    if len(daily_r) < 2:
        return None
    mean = statistics.fmean(daily_r)
    std = statistics.pstdev(daily_r)  # population std of the realized series
    if std <= 0:
        return None
    return round((mean / std) * math.sqrt(TRADING_DAYS_PER_YEAR), 4)


def summarize_book(trades: Sequence[Dict[str, Any]],
                   daily: Sequence[Tuple[date, float, int]]) -> Dict[str, Any]:
    """Headline book stats + per-source + by-year breakdowns."""
    daily_r = [r for (_d, r, _n) in daily]
    n_trades = len(trades)
    n_dropped = sum(1 for t in trades if t["day"] is None)
    total_r = round(sum(t["net_r"] for t in trades), 6)

    by_source: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        slot = by_source.setdefault(t["source"], {"trades": 0, "net_r": 0.0})
        slot["trades"] += 1
        slot["net_r"] = round(slot["net_r"] + t["net_r"], 6)

    by_year: Dict[str, Dict[str, Any]] = {}
    for d, r, n in daily:
        slot = by_year.setdefault(str(d.year), {"days": 0, "trades": 0, "net_r": 0.0})
        slot["days"] += 1
        slot["trades"] += n
        slot["net_r"] = round(slot["net_r"] + r, 6)

    summary: Dict[str, Any] = {
        "n_trades": n_trades,
        "n_trades_dropped_no_day": n_dropped,
        "n_days": len(daily),
        "total_net_r": total_r,
        "daily_mean_r": round(statistics.fmean(daily_r), 6) if daily_r else 0.0,
        "daily_std_r": round(statistics.pstdev(daily_r), 6) if len(daily_r) >= 2 else 0.0,
        "sharpe_annualized": sharpe_annualized(daily_r),
        "max_drawdown_r": _max_drawdown_r(daily_r),
        "data_start": str(daily[0][0]) if daily else None,
        "data_end": str(daily[-1][0]) if daily else None,
        "by_source": by_source,
        "by_year": by_year,
        "run_date": str(date.today()),
    }
    return summary


def build_book(specs: Sequence[str], *, basis: str = "auto"
               ) -> Tuple[List[Tuple[date, float, int]], Dict[str, Any]]:
    """One-shot: load -> combine -> summarize. The importable entry point."""
    trades = load_trades(specs, basis=basis)
    daily = combine_daily(trades)
    summary = summarize_book(trades, daily)
    summary["inputs"] = list(_resolve_inputs(specs))
    summary["basis"] = basis
    return daily, summary


def write_daily_csv(path: str, daily: Sequence[Tuple[date, float, int]]) -> None:
    out = sys.stdout if path == "-" else open(path, "w", newline="", encoding="utf-8")
    close = path != "-"
    if path != "-":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    try:
        w = csv.writer(out)
        w.writerow(["date", "book_r", "n_trades"])
        for d, r, n in daily:
            w.writerow([str(d), f"{r:.6f}", n])
    finally:
        if close:
            out.close()


def _fmt(summary: Dict[str, Any]) -> str:
    lines = [
        f"portfolio_combine — book over {summary['n_days']} days "
        f"({summary.get('data_start')} -> {summary.get('data_end')}), "
        f"basis={summary.get('basis')}",
        f"  trades={summary['n_trades']} "
        f"(dropped_no_day={summary['n_trades_dropped_no_day']})  "
        f"total_net_r={summary['total_net_r']}",
        f"  daily mean={summary['daily_mean_r']} std={summary['daily_std_r']}  "
        f"Sharpe(ann)={summary['sharpe_annualized']}  "
        f"maxDD_r={summary['max_drawdown_r']}",
        "  by_source:",
    ]
    for src, s in summary["by_source"].items():
        lines.append(f"    {src:<40} trades={s['trades']:>5}  net_r={s['net_r']}")
    lines.append("  by_year:")
    for yr, s in sorted(summary["by_year"].items()):
        lines.append(f"    {yr}  days={s['days']:>4} trades={s['trades']:>5} "
                     f"net_r={s['net_r']}")
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(
        description="Combine per-strategy emit-trades JSONL into one book-level "
                    "daily return series.")
    p.add_argument("--trades", nargs="+", required=True, metavar="PATH",
                   help="emit-trades JSONL files (or globs / directories).")
    p.add_argument("--basis", choices=["entry", "exit", "auto"], default="auto",
                   help="Attribute a trade's net_r to its entry_time, exit_time, "
                        "or (auto) exit_time-if-present-else-entry. Default auto.")
    p.add_argument("--json", dest="json_out", default=None,
                   help="Write the full JSON payload here ('-' for stdout).")
    p.add_argument("--emit-daily", default=None, metavar="PATH",
                   help="Write the daily series as CSV (date,book_r,n_trades); "
                        "'-' for stdout. Consumed by backtest_vol_target.py.")
    a = p.parse_args(argv[1:])

    daily, summary = build_book(a.trades, basis=a.basis)
    print(_fmt(summary))

    if a.emit_daily:
        write_daily_csv(a.emit_daily, daily)
        if a.emit_daily != "-":
            print(f"daily CSV -> {a.emit_daily}", file=sys.stderr)

    if a.json_out:
        payload = json.dumps({"summary": summary,
                              "daily": [{"date": str(d), "book_r": r, "n_trades": n}
                                        for (d, r, n) in daily]},
                             indent=2, default=str)
        if a.json_out == "-":
            print(payload)
        else:
            Path(a.json_out).write_text(payload)
            print(f"JSON -> {a.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
