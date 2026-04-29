#!/usr/bin/env python3
"""
analyze_ict_results.py — post-backtest analysis for multi-symbol ICT runs.

Reads the JSON report produced by ``bin/backtest_ict.py --output <file>``
and emits a markdown validation report plus a go/no-go verdict.

Go criteria (from sprint-plan-2026-04-28 M7 Phase 3):
  * total_trades  >= GO_MIN_TRADES   (default 50)
  * win_rate_pct  >= GO_MIN_WR_PCT   (default 55.0)
  * avg_r_multiple > 0

Usage
-----

    PYTHONPATH=. python bin/analyze_ict_results.py \\
        --input  reports/ict_multi.json \\
        --output docs/sprint-plans/ict-validation-report.md

    # Override go thresholds:
    PYTHONPATH=. python bin/analyze_ict_results.py \\
        --input  reports/ict_multi.json \\
        --min-trades 30 \\
        --min-wr     52

Exit codes
----------
* 0 — report written, go/no-go computed (even if verdict is NO-GO).
* 1 — input file missing or malformed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

GO_MIN_TRADES = 50
GO_MIN_WR_PCT = 55.0

# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _load_report(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        raise SystemExit(f"ERROR: input file not found: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"ERROR: invalid JSON in {path}: {exc}")


def _aggregate_from_pairs(pairs: List[dict]) -> dict:
    """Re-derive cross-pair aggregate from per-pair summaries.

    We re-derive rather than trusting the stored aggregate so this script
    stays correct even if the report was produced by an older CLI version.
    """
    ok = [p for p in pairs if p.get("ok") and p.get("summary")]
    total_trades = sum(int(p["summary"].get("total_trades", 0)) for p in ok)
    winners = sum(int(p["summary"].get("winners", 0)) for p in ok)
    win_rate = (winners / total_trades * 100.0) if total_trades else 0.0

    r_values = [
        float(p["summary"]["avg_r_multiple"])
        for p in ok
        if "avg_r_multiple" in p["summary"]
    ]
    avg_r = (sum(r_values) / len(r_values)) if r_values else 0.0

    total_pnl = sum(float(p["summary"].get("total_pnl", 0)) for p in ok)
    return_pcts = [
        float(p["summary"]["total_return_pct"])
        for p in ok
        if "total_return_pct" in p["summary"]
    ]
    avg_return = (sum(return_pcts) / len(return_pcts)) if return_pcts else 0.0

    dd_values = [
        float(p["summary"]["max_drawdown_pct"])
        for p in ok
        if "max_drawdown_pct" in p["summary"]
    ]
    avg_dd = (sum(dd_values) / len(dd_values)) if dd_values else 0.0

    return {
        "pairs_total": len(pairs),
        "pairs_ok": len(ok),
        "pairs_failed": len(pairs) - len(ok),
        "total_trades": total_trades,
        "win_rate_pct": round(win_rate, 1),
        "avg_r_multiple": round(avg_r, 2),
        "total_pnl": round(total_pnl, 2),
        "avg_return_pct": round(avg_return, 2),
        "avg_max_dd_pct": round(avg_dd, 2),
    }


def go_verdict(agg: dict, min_trades: int, min_wr: float) -> tuple[bool, List[str]]:
    """Return (go_bool, list_of_failed_criteria)."""
    fails = []
    if agg["total_trades"] < min_trades:
        fails.append(
            f"total_trades {agg['total_trades']} < {min_trades} required"
        )
    if agg["win_rate_pct"] < min_wr:
        fails.append(
            f"win_rate_pct {agg['win_rate_pct']}% < {min_wr}% required"
        )
    if agg["avg_r_multiple"] <= 0:
        fails.append(
            f"avg_r_multiple {agg['avg_r_multiple']} <= 0"
        )
    return (len(fails) == 0, fails)


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

_PAIR_HEADER = (
    "| Symbol | TF | Trades | WR% | Avg R | Return% | Max DD% | PF | Status |"
)
_PAIR_SEP = (
    "|--------|----|---------:|---------:|---------:|---------:|---------:|----:|--------|"
)


def _pair_row(p: dict) -> str:
    sym = p.get("symbol", "?")
    tf = p.get("timeframe", "?")
    if not p.get("ok"):
        err = (p.get("error") or "unknown error")[:60]
        return f"| {sym} | {tf} | — | — | — | — | — | — | ❌ `{err}` |"
    s = p.get("summary") or {}
    trades = s.get("total_trades", 0)
    if not trades:
        return f"| {sym} | {tf} | 0 | — | — | — | — | — | ⚠️ no trades |"
    wr = s.get("win_rate_pct", 0)
    avg_r = s.get("avg_r_multiple", 0)
    ret = s.get("total_return_pct", 0)
    dd = s.get("max_drawdown_pct", 0)
    pf = s.get("profit_factor", 0)
    status = "✅" if (wr >= GO_MIN_WR_PCT and avg_r > 0) else "⚠️"
    return (
        f"| {sym} | {tf} | {trades} | {wr} | {avg_r} | {ret} | {dd} | {pf} | {status} |"
    )


def render_markdown(
    report: dict,
    agg: dict,
    go: bool,
    fails: List[str],
    min_trades: int,
    min_wr: float,
    source_path: str,
) -> str:
    pairs = report.get("pairs", [])
    verdict_line = "## ✅ GO — edge confirmed" if go else "## ❌ NO-GO — criteria not met"
    fail_block = ""
    if fails:
        fail_items = "\n".join(f"- {f}" for f in fails)
        fail_block = f"\n**Failed criteria:**\n{fail_items}\n"

    pair_rows = "\n".join(_pair_row(p) for p in pairs)

    lines = [
        "# ICT Multi-Symbol Validation Report",
        "",
        f"**Source:** `{source_path}`  ",
        f"**Go thresholds:** ≥{min_trades} trades, WR ≥{min_wr}%, avg R > 0",
        "",
        verdict_line,
        fail_block,
        "---",
        "",
        "## Aggregate",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Pairs run | {agg['pairs_total']} |",
        f"| Pairs OK | {agg['pairs_ok']} |",
        f"| Pairs failed | {agg['pairs_failed']} |",
        f"| Total trades | {agg['total_trades']} |",
        f"| Blended WR% | {agg['win_rate_pct']} |",
        f"| Avg R-multiple | {agg['avg_r_multiple']} |",
        f"| Avg return% | {agg['avg_return_pct']} |",
        f"| Avg max DD% | {agg['avg_max_dd_pct']} |",
        "",
        "---",
        "",
        "## Per-pair results",
        "",
        _PAIR_HEADER,
        _PAIR_SEP,
        pair_rows,
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Analyze ICT multi-symbol backtest JSON and produce a markdown report.",
    )
    p.add_argument("--input", type=Path, required=True,
                   help="JSON report from backtest_ict.py --output.")
    p.add_argument("--output", type=Path, default=None,
                   help="Destination markdown file (default: print to stdout).")
    p.add_argument("--min-trades", type=int, default=GO_MIN_TRADES,
                   help=f"Min total trades for GO verdict (default {GO_MIN_TRADES}).")
    p.add_argument("--min-wr", type=float, default=GO_MIN_WR_PCT,
                   help=f"Min blended WR%% for GO verdict (default {GO_MIN_WR_PCT}).")
    return p


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    report = _load_report(args.input)
    pairs = report.get("pairs", [])
    agg = _aggregate_from_pairs(pairs)
    go, fails = go_verdict(agg, args.min_trades, args.min_wr)

    md = render_markdown(
        report, agg, go, fails, args.min_trades, args.min_wr,
        source_path=str(args.input),
    )

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(md)
        print(f"wrote {args.output}")
    else:
        print(md)

    verdict_str = "GO" if go else "NO-GO"
    print(f"\nVerdict: {verdict_str}  |  trades={agg['total_trades']}  "
          f"WR={agg['win_rate_pct']}%  avgR={agg['avg_r_multiple']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
