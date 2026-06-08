"""Walk-forward — position-netting guard (net vs suppress), BL-20260608-DEMOPNL.

Mirrors ``scripts/walkforward_flip_policy.py`` but varies the *re-entry
policy* (``--reentry-policy net|suppress``) instead of the flip policy:

  * ``net``      — models CURRENT live one-way-mode behaviour (same-side
                   re-entry pyramids the shared position + overwrites the
                   single SL/TP). This is the bug Option A removes.
  * ``suppress`` — models the Option-A FIX (one trade = one position; a
                   same-side re-entry while open is ignored).

For each (fold, half) cell it runs both policies on the SAME window and
roster and tabulates trades / win-rate / net P&L / max-DD / return-DD so
the operator can confirm the fix does NOT regress the roster's metrics
versus today's netting behaviour.

Usage:
    python3 scripts/walkforward_netting_guard.py --data /tmp/btc_5m.parquet
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

warnings.filterwarnings("ignore")

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.backtest_system import ROSTER, _load_candles, run_system_backtest  # noqa: E402

ROSTER_4 = ["trend_donchian", "fade_breakout_4h", "squeeze_breakout_4h", "fvg_range_15m"]
POLICIES = ["net", "suppress"]


def _cell(base5m, *, start, end, roster, reentry_policy) -> Dict[str, Any]:
    out = run_system_backtest(
        base5m, roster=roster, start=start, end=end,
        initial_balance=10_000.0, risk_pct=0.3, daily_loss_pct=3.0,
        signal_ttl_bars=1, overrides={}, refresh=False, clock_tf="15m",
        flip_policy="hold", reentry_policy=reentry_policy,
    )
    return {
        "trades": out["total_trades"],
        "win_rate": out["win_rate_pct"],
        "net": round(out["net_pnl"], 2),
        "maxDD_pct": out["max_drawdown_pct"],
        "ret_dd": out["return_dd_ratio"],
    }


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--roster", default=",".join(ROSTER_4))
    p.add_argument("--out-dir", default="runtime_logs/system_backtest/walkforward")
    args = p.parse_args(argv[1:])

    roster = [r.strip() for r in args.roster.split(",") if r.strip() in ROSTER]
    base5m = _load_candles(args.data)
    dmin = str(base5m["timestamp"].min())
    dmax = str(base5m["timestamp"].max())

    # Two anchored folds inside the available 2022-07..2024-12 span.
    folds = {
        "A": {
            "train": ("2022-07-01", "2023-12-31"),
            "oos": ("2024-01-01", "2024-12-31"),
        },
        "B": {
            "train": ("2022-07-01", "2023-06-30"),
            "oos": ("2023-07-01", "2024-12-31"),
        },
    }

    rows: List[Dict[str, Any]] = []
    for fold, halves in folds.items():
        for half, (start, end) in halves.items():
            res = {pol: _cell(base5m, start=start, end=end, roster=roster,
                              reentry_policy=pol) for pol in POLICIES}
            for pol in POLICIES:
                rows.append({"fold": fold, "half": half, "policy": pol, **res[pol]})
                print(f"{fold}/{half:5s} {pol:8s} "
                      f"net=${res[pol]['net']:>9} maxDD%={res[pol]['maxDD_pct']:>6} "
                      f"ret/DD={res[pol]['ret_dd']:>6} trades={res[pol]['trades']:>4} "
                      f"WR={res[pol]['win_rate']}", flush=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "kind": "walkforward_netting_guard",
        "generated_at": ts,
        "data": args.data,
        "data_start": dmin,
        "data_end": dmax,
        "roster": roster,
        "folds": folds,
        "rows": rows,
    }
    out_path = out_dir / f"walkforward_netting_{ts}.json"
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    print(f"\nJSON -> {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
