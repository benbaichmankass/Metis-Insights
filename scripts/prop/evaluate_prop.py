#!/usr/bin/env python3
"""Prop-firm strategy evaluation tool — combo search + ruleset evaluation.

Enumerates strategy-combo rosters (all non-empty subsets of the cleanly
backtestable BTCUSDT roster, or an explicit csv), runs the EXISTING portfolio
engine (scripts/backtest_system.py) in-process for each, feeds the full output
(equity curve + closed-trade ledger) to the prop-firm evaluator (src/prop/),
ranks the verdicts per design §6, and writes a Markdown matrix + JSON under
runtime_logs/prop_eval/<UTC-date>/.

Design: docs/research/prop-firm-testing-tool-DESIGN.md.

Tier-1 research tooling — NO live order path, NO trading. The engine itself
does not import or alter any live-order path; this tool only consumes its
in-memory output and reuses the (read-only) ruleset YAML.

CLI conventions mirror scripts/backtest_system.py (--data/--start/--end/
--initial-balance/--risk-pct/--daily-loss-pct/...).
"""
from __future__ import annotations

import argparse
import itertools
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import scripts.backtest_system as bt  # noqa: E402
from src.prop.evaluator import TradeRecord, evaluate  # noqa: E402
from src.prop.report import render_json, render_markdown  # noqa: E402
from src.prop.ruleset import PropRuleset, load_ruleset  # noqa: E402

# The cleanly-backtestable BTCUSDT roster the portfolio engine supports
# (design §6 / backtest_system COVERAGE note — ict_scalp_5m + turtle_soup
# deferred). Combo search enumerates non-empty subsets of this list.
DEFAULT_COMBO_ROSTER = [
    "trend_donchian",
    "fade_breakout_4h",
    "squeeze_breakout_4h",
    "fvg_range_15m",
]


def _all_combos(members: Sequence[str]) -> List[List[str]]:
    """All non-empty subsets of `members`, ordered by size then name."""
    combos: List[List[str]] = []
    for r in range(1, len(members) + 1):
        for c in itertools.combinations(members, r):
            combos.append(list(c))
    return combos


def _parse_combos(spec: str, members: Sequence[str]) -> List[List[str]]:
    """Resolve the --combos argument into a list of rosters.

    "all"  → every non-empty subset of `members`.
    else   → a ';'-separated list of csv rosters
             (e.g. "trend_donchian; trend_donchian,fvg_range_15m").
    """
    spec = (spec or "").strip()
    if not spec or spec.lower() == "all":
        return _all_combos(members)
    rosters: List[List[str]] = []
    for group in spec.split(";"):
        names = [n.strip() for n in group.split(",") if n.strip()]
        names = [n for n in names if n in bt.ROSTER]
        if names:
            rosters.append(names)
    return rosters


def _trade_records(summary: Dict[str, Any]) -> List[TradeRecord]:
    """Convert the engine's closed-trade ledger (_ClosedTrade objects) into the
    evaluator's TradeRecord shape."""
    recs: List[TradeRecord] = []
    for t in summary.get("closed_trades", []) or []:
        recs.append(
            TradeRecord(
                owner=getattr(t, "owner", ""),
                entry_ts=getattr(t, "entry_ts", None),
                exit_ts=getattr(t, "exit_ts", None),
                pnl=float(getattr(t, "pnl", 0.0) or 0.0),
                notional=float(getattr(t, "notional", 0.0) or 0.0),
            )
        )
    return recs


def _engine_metrics(summary: Dict[str, Any]) -> Dict[str, Any]:
    """The subset of _summarize fields the verdict surfaces (design §5)."""
    return {
        "net_pnl": summary.get("net_pnl"),
        "return_pct": summary.get("return_pct"),
        "max_drawdown_pct": summary.get("max_drawdown_pct"),
        "max_drawdown_usd": summary.get("max_drawdown_usd"),
        "total_trades": summary.get("total_trades"),
        "win_rate_pct": summary.get("win_rate_pct"),
    }


def _rank_key(v: Dict[str, Any], ruleset: PropRuleset):
    """Design §6 ranking key (sorted ascending → best first).

    primary  : passes eval AND survives funded soak (best)
    then     : funded-soak drawdown margin (bigger margin to the limit = better)
    then     : days-to-target (fewer = better)
    then     : consistency margin (bigger margin to the cap = better)
    Higher-is-better fields are negated so a plain ascending sort puts the best
    combo first.
    """
    ev = v.get("eval", {})
    fs = v.get("funded_soak", {})
    m = v.get("metrics", {})

    passes = bool(ev.get("passed")) and bool(fs.get("survived"))
    primary = 0 if passes else 1  # 0 sorts first

    max_dd_pct = m.get("max_drawdown_pct")
    dd_limit_pct = (ruleset.limits.max_drawdown_pct or 0.0) * 100.0
    if isinstance(max_dd_pct, (int, float)) and dd_limit_pct:
        dd_margin = dd_limit_pct - max_dd_pct  # larger = safer
    else:
        dd_margin = -1e9  # unknown DD ranks worst
    dd_rank = -dd_margin  # ascending → larger margin first

    dtt = ev.get("days_to_target")
    dtt_rank = dtt if isinstance(dtt, (int, float)) else 1e9  # fewer days first

    worst_share = m.get("consistency_worst_day_share")
    cons_cap = ruleset.consistency.max_single_day_profit_share
    if isinstance(worst_share, (int, float)):
        cons_margin = cons_cap - worst_share  # larger = safer
    else:
        cons_margin = cons_cap  # no trades → maximal margin
    cons_rank = -cons_margin  # ascending → larger margin first

    return (primary, dd_rank, dtt_rank, cons_rank)


def run(args: argparse.Namespace) -> int:
    ruleset = load_ruleset(args.ruleset)
    account_size = args.initial_balance

    members = [m.strip() for m in args.members.split(",") if m.strip() in bt.ROSTER]
    if not members:
        members = list(DEFAULT_COMBO_ROSTER)
    rosters = _parse_combos(args.combos, members)
    if not rosters:
        print("ERROR: no valid rosters resolved from --combos/--members", file=sys.stderr)
        return 2

    try:
        base5m = bt._load_candles(args.data)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: candle load failed ({args.data}): {exc}", file=sys.stderr)
        return 1

    verdicts: List[Dict[str, Any]] = []
    data_window: Optional[Dict[str, Any]] = None
    for roster in rosters:
        roster_str = ",".join(roster)
        print(f"[prop] running roster: {roster_str}", file=sys.stderr)
        try:
            summary = bt.run_system_backtest(
                base5m, roster=roster, start=args.start, end=args.end,
                initial_balance=account_size, risk_pct=args.risk_pct,
                daily_loss_pct=args.daily_loss_pct, signal_ttl_bars=args.signal_ttl_bars,
                overrides={}, refresh=args.refresh_signals, clock_tf=args.clock_tf,
                flip_policy=args.flip_policy, reentry_policy=args.reentry_policy,
                attach_full=True,
            )
        except Exception as exc:  # noqa: BLE001 — one bad roster must not abort the sweep
            print(f"[prop] roster {roster_str} failed: {exc}", file=sys.stderr)
            continue
        if data_window is None:
            data_window = {
                "start": args.start, "end": args.end,
                "data_start": summary.get("data_start"),
                "data_end": summary.get("data_end"),
            }
        verdict = evaluate(
            ruleset,
            equity_curve=summary.get("full_equity_curve", []),
            trades=_trade_records(summary),
            account_size=account_size,
            metrics=_engine_metrics(summary),
            roster=roster_str,
        )
        verdicts.append(verdict)

    if not verdicts:
        print("ERROR: no roster produced a verdict", file=sys.stderr)
        return 1

    verdicts.sort(key=lambda v: _rank_key(v, ruleset))

    generated_at = datetime.now(timezone.utc).isoformat()
    ruleset_view = ruleset.to_dict()
    md = render_markdown(verdicts, ruleset_view=ruleset_view,
                         data_window=data_window, generated_at=generated_at)
    js = render_json(verdicts, ruleset_view=ruleset_view,
                     data_window=data_window, generated_at=generated_at)

    out_dir = Path(args.out_dir) if args.out_dir else (
        _REPO_ROOT / "runtime_logs" / "prop_eval" / date.today().isoformat()
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "matrix.md").write_text(md)
    (out_dir / "matrix.json").write_text(js)

    print(md)
    print(f"\nwrote {out_dir / 'matrix.md'} + matrix.json", file=sys.stderr)
    return 0


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(
        description="Prop-firm strategy evaluation tool — combo search vs a ruleset."
    )
    p.add_argument("--ruleset", default=str(_REPO_ROOT / "config" / "prop_rulesets" / "breakout.yaml"),
                   help="Ruleset YAML (config/prop_rulesets/*.yaml).")
    p.add_argument("--data", default=os.environ.get("BACKTEST_DATA_PATH", "data/backtest_candles.csv"),
                   help="5m OHLCV CSV/parquet (resampled per strategy TF internally).")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--members", default=",".join(DEFAULT_COMBO_ROSTER),
                   help="Comma list of strategies to enumerate combos from "
                        "(default: the cleanly-backtestable BTCUSDT roster).")
    p.add_argument("--combos", default="all",
                   help="'all' (every non-empty subset of --members) or a "
                        "';'-separated list of csv rosters.")
    p.add_argument("--initial-balance", type=float, default=25_000.0,
                   help="Account size (also the prop drawdown/position reference).")
    p.add_argument("--risk-pct", type=float, default=0.3,
                   help="Per-trade risk %% of balance (the shared account's risk_pct).")
    p.add_argument("--daily-loss-pct", type=float, default=3.0,
                   help="In-sim daily-loss cap %% of day-start balance (engine halt).")
    p.add_argument("--signal-ttl-bars", type=int, default=1)
    p.add_argument("--clock-tf", default="15m", choices=list(bt._PANDAS_TF.keys()))
    p.add_argument("--flip-policy", default="hold", choices=["reverse", "hold", "flat"])
    p.add_argument("--reentry-policy", default="suppress", choices=["suppress", "net"])
    p.add_argument("--refresh-signals", action="store_true", help="Ignore the signal cache.")
    p.add_argument("--out-dir", default=None,
                   help="Output dir (default: runtime_logs/prop_eval/<UTC-date>/).")
    args = p.parse_args(argv[1:])
    return run(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
