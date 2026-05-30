#!/usr/bin/env python3
"""Walk-forward driver for the flip-policy conflict-resolution investigation.

Implements the scope in
`docs/sprint-plans/CONFLICT-POLICY-WALKFORWARD-SCOPE-2026-05-30.md`:

  Two anchored folds × {4-member, 6-member} × {reverse, hold, flat}
  = 12 system/portfolio backtests, each in train + OOS half.

  Fold A: train 2020-06..2023-12   OOS 2024-01..2026-02   (5.7y / 2y)
  Fold B: train 2022-01..2024-06   OOS 2024-07..2026-02   (2.5y / 1.7y)

Reads the per-(strategy, window) signal cache built by
`scripts/backtest_system.py`'s `generate_signal_stream`; pre-warm with
`--prebuild-cache` (recommended) before doing the policy comparison.

Emits a combined JSON at `runtime_logs/system_backtest/walkforward/
walkforward_<UTC>.json` and prints a Markdown summary table.

Pass criteria (from the scope doc, also checked here):
  1. 4-member: hold > reverse in net AND maxDD% in BOTH train AND OOS in
     BOTH folds (4 cells).
  2. 6-member: hold not worse than reverse in OOS for both folds (2 cells).

Tier-1 research tooling — composition over scripts/backtest_system.py's
engine; no engine change, no live-path imports beyond the existing
aggregate_intents call.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Import after sys.path adjustment so the live aggregator import chain works.
from scripts.backtest_system import (  # noqa: E402
    _load_candles,
    generate_signal_stream,
    run_system_backtest,
)

ROSTERS = {
    "4mem": [
        "trend_donchian",
        "fade_breakout_4h",
        "squeeze_breakout_4h",
        "fvg_range_15m",
    ],
    "6mem": [
        "trend_donchian",
        "fade_breakout_4h",
        "squeeze_breakout_4h",
        "fvg_range_15m",
        "turtle_soup",
        "ict_scalp_5m",
    ],
}

# Folds as anchored (train begin -> train end / OOS begin -> OOS end).
# Each tuple covers one fold; the windows match the scope doc.
FOLDS: Dict[str, Dict[str, Tuple[str, str]]] = {
    "A": {
        "train": ("2020-06-01", "2023-12-31"),
        "oos": ("2024-01-01", "2026-02-28"),
    },
    "B": {
        "train": ("2022-01-01", "2024-06-30"),
        "oos": ("2024-07-01", "2026-02-28"),
    },
}

POLICIES = ("reverse", "hold", "flat")
OUT_DIR = _REPO_ROOT / "runtime_logs" / "system_backtest" / "walkforward"


@dataclass
class Cell:
    fold: str
    half: str  # "train" | "oos"
    roster: str
    policy: str
    start: str
    end: str
    summary: Dict[str, Any] = field(default_factory=dict)


def _prebuild_cache(base5m, roster: List[str], folds: Dict[str, Dict[str, Tuple[str, str]]],
                    refresh: bool) -> None:
    """Generate the signal cache for every (strategy, window) the run needs.

    Cache keys are `(strategy, base_path, start, end, overrides)` — so each
    fold half is its own cache file. Doing this up front lets the 12 policy
    runs reuse the streams without re-running the per-bar order_package.
    """
    seen: set[Tuple[str, str, str]] = set()
    for fold_id, halves in folds.items():
        for half_id, (s, e) in halves.items():
            for strat in roster:
                key = (strat, s, e)
                if key in seen:
                    continue
                seen.add(key)
                print(f"[cache] {fold_id}/{half_id}/{strat}  {s} -> {e}", flush=True)
                generate_signal_stream(strat, base5m, start=s, end=e,
                                       overrides={}, refresh=refresh)


def _run_cell(base5m, *, fold: str, half: str, roster_name: str, policy: str,
              start: str, end: str, balance: float, risk_pct: float,
              daily_loss_pct: float, ttl: int) -> Cell:
    roster = ROSTERS[roster_name]
    print(f"[run ] fold={fold} half={half} roster={roster_name} policy={policy} ...", flush=True)
    out = run_system_backtest(
        base5m, roster=roster, start=start, end=end,
        initial_balance=balance, risk_pct=risk_pct,
        daily_loss_pct=daily_loss_pct, signal_ttl_bars=ttl,
        overrides={}, refresh=False, clock_tf="15m",
        flip_policy=policy,
    )
    cell = Cell(fold=fold, half=half, roster=roster_name, policy=policy,
                start=start, end=end, summary=out)
    s = out
    print(f"[done] {fold}/{half}/{roster_name}/{policy}  "
          f"net=${s['net_pnl']:.0f}  maxDD={s['max_drawdown_pct']:.2f}%  "
          f"ret/DD={s.get('return_dd_ratio')}  trades={s['total_trades']}  "
          f"flips={s['by_exit_reason'].get('flip', 0)}", flush=True)
    return cell


def _evaluate_pass_criteria(cells: List[Cell]) -> Dict[str, Any]:
    """Apply the scope doc's pass / fail criteria to the result grid."""
    by_key = {(c.fold, c.half, c.roster, c.policy): c for c in cells}

    # Criterion 1: 4-member hold > reverse in NET AND maxDD% across all
    # (fold, half) cells.
    crit1_cells: List[Dict[str, Any]] = []
    crit1_pass = True
    for fold_id in FOLDS:
        for half_id in ("train", "oos"):
            h = by_key.get((fold_id, half_id, "4mem", "hold"))
            r = by_key.get((fold_id, half_id, "4mem", "reverse"))
            if h is None or r is None:
                crit1_pass = False
                crit1_cells.append({"fold": fold_id, "half": half_id,
                                    "ok": False, "reason": "missing_cell"})
                continue
            ok_net = h.summary["net_pnl"] > r.summary["net_pnl"]
            ok_dd = h.summary["max_drawdown_pct"] < r.summary["max_drawdown_pct"]
            ok = ok_net and ok_dd
            crit1_pass = crit1_pass and ok
            crit1_cells.append({
                "fold": fold_id, "half": half_id, "ok": ok,
                "hold_net": h.summary["net_pnl"],
                "reverse_net": r.summary["net_pnl"],
                "hold_maxDD_pct": h.summary["max_drawdown_pct"],
                "reverse_maxDD_pct": r.summary["max_drawdown_pct"],
            })

    # Criterion 2: 6-member hold not worse than reverse in NET for OOS in
    # both folds (looser test because the 6-member book bleeds anyway).
    crit2_cells: List[Dict[str, Any]] = []
    crit2_pass = True
    for fold_id in FOLDS:
        h = by_key.get((fold_id, "oos", "6mem", "hold"))
        r = by_key.get((fold_id, "oos", "6mem", "reverse"))
        if h is None or r is None:
            crit2_pass = False
            crit2_cells.append({"fold": fold_id, "ok": False,
                                "reason": "missing_cell"})
            continue
        ok = h.summary["net_pnl"] >= r.summary["net_pnl"]
        crit2_pass = crit2_pass and ok
        crit2_cells.append({
            "fold": fold_id, "ok": ok,
            "hold_oos_net": h.summary["net_pnl"],
            "reverse_oos_net": r.summary["net_pnl"],
        })

    return {
        "criterion_1_4member_hold_dominates_reverse": {
            "pass": crit1_pass, "cells": crit1_cells,
        },
        "criterion_2_6member_hold_not_worse_than_reverse_oos": {
            "pass": crit2_pass, "cells": crit2_cells,
        },
        "overall_pass": crit1_pass and crit2_pass,
    }


def _markdown_summary(cells: List[Cell], verdict: Dict[str, Any]) -> str:
    lines: List[str] = ["# Walk-forward — flip-policy conflict resolution\n"]
    lines.append(f"Generated {datetime.now(tz=timezone.utc).isoformat()}\n")
    for roster_name in ROSTERS:
        lines.append(f"\n## Roster = {roster_name}\n")
        lines.append("| fold | half | policy | net | maxDD% | ret/DD | trades | flips |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for fold_id in FOLDS:
            for half_id in ("train", "oos"):
                for pol in POLICIES:
                    c = next((x for x in cells
                              if x.fold == fold_id and x.half == half_id
                              and x.roster == roster_name and x.policy == pol),
                             None)
                    if c is None:
                        lines.append(f"| {fold_id} | {half_id} | {pol} | n/a | n/a | n/a | n/a | n/a |")
                        continue
                    s = c.summary
                    flips = s["by_exit_reason"].get("flip", 0)
                    lines.append(
                        f"| {fold_id} | {half_id} | {pol} | "
                        f"${s['net_pnl']:.0f} | {s['max_drawdown_pct']:.2f}% | "
                        f"{s.get('return_dd_ratio')} | {s['total_trades']} | {flips} |"
                    )
    lines.append("\n## Verdict\n")
    c1 = verdict["criterion_1_4member_hold_dominates_reverse"]
    c2 = verdict["criterion_2_6member_hold_not_worse_than_reverse_oos"]
    lines.append(f"- Criterion 1 (4-member hold dominates reverse, all 4 cells): "
                 f"**{'PASS' if c1['pass'] else 'FAIL'}**")
    lines.append(f"- Criterion 2 (6-member hold not worse than reverse OOS, both folds): "
                 f"**{'PASS' if c2['pass'] else 'FAIL'}**")
    lines.append(f"- Overall: **{'PASS' if verdict['overall_pass'] else 'FAIL'}**")
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--data", required=True,
                   help="5m OHLCV parquet/CSV (resampled per strategy TF internally).")
    p.add_argument("--rosters", default="4mem,6mem",
                   help="Comma list of rosters to test (subset of {4mem,6mem}).")
    p.add_argument("--folds", default="A,B",
                   help="Comma list of folds to test (subset of {A,B}).")
    p.add_argument("--initial-balance", type=float, default=10_000.0)
    p.add_argument("--risk-pct", type=float, default=0.3)
    p.add_argument("--daily-loss-pct", type=float, default=3.0)
    p.add_argument("--signal-ttl-bars", type=int, default=1)
    p.add_argument("--prebuild-cache", action="store_true",
                   help="Pre-generate signal caches for every (strategy, window). "
                        "Recommended for the first run.")
    p.add_argument("--refresh-cache", action="store_true",
                   help="Force regenerate caches (implies --prebuild-cache).")
    p.add_argument("--json-out", default=None,
                   help="Override the default walkforward_<UTC>.json output path.")
    p.add_argument("--md-out", default=None,
                   help="Override the default walkforward_<UTC>.md output path.")
    args = p.parse_args(argv[1:])

    rosters = [r for r in args.rosters.split(",") if r in ROSTERS]
    folds = {f: FOLDS[f] for f in args.folds.split(",") if f in FOLDS}
    if not rosters or not folds:
        print("ERROR: --rosters and --folds must each pick at least one valid value",
              file=sys.stderr)
        return 1

    try:
        base5m = _load_candles(args.data)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: data load failed: {exc}", file=sys.stderr)
        return 1
    print(f"loaded base 5m: rows={len(base5m):,} "
          f"({base5m['timestamp'].iloc[0]} -> {base5m['timestamp'].iloc[-1]})", flush=True)

    if args.prebuild_cache or args.refresh_cache:
        all_strats = sorted({s for r in rosters for s in ROSTERS[r]})
        _prebuild_cache(base5m, all_strats, folds, refresh=args.refresh_cache)

    cells: List[Cell] = []
    for fold_id, halves in folds.items():
        for half_id, (s, e) in halves.items():
            for roster_name in rosters:
                for policy in POLICIES:
                    cells.append(_run_cell(
                        base5m, fold=fold_id, half=half_id,
                        roster_name=roster_name, policy=policy,
                        start=s, end=e,
                        balance=args.initial_balance,
                        risk_pct=args.risk_pct,
                        daily_loss_pct=args.daily_loss_pct,
                        ttl=args.signal_ttl_bars,
                    ))

    verdict = _evaluate_pass_criteria(cells)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = Path(args.json_out) if args.json_out else OUT_DIR / f"walkforward_{ts}.json"
    md_path = Path(args.md_out) if args.md_out else OUT_DIR / f"walkforward_{ts}.md"
    payload = {
        "generated_at_utc": datetime.now(tz=timezone.utc).isoformat(),
        "rosters": rosters,
        "folds": {k: {h: list(v) for h, v in halves.items()} for k, halves in folds.items()},
        "params": {
            "initial_balance": args.initial_balance,
            "risk_pct": args.risk_pct,
            "daily_loss_pct": args.daily_loss_pct,
            "signal_ttl_bars": args.signal_ttl_bars,
        },
        "cells": [
            {"fold": c.fold, "half": c.half, "roster": c.roster, "policy": c.policy,
             "start": c.start, "end": c.end, "summary": c.summary}
            for c in cells
        ],
        "verdict": verdict,
    }
    json_path.write_text(json.dumps(payload, indent=2, default=str))
    md = _markdown_summary(cells, verdict)
    md_path.write_text(md)
    print(f"\nJSON -> {json_path}", file=sys.stderr)
    print(f"MD   -> {md_path}", file=sys.stderr)
    print("\n" + md)
    return 0 if verdict["overall_pass"] else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
