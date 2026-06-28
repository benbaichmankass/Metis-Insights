#!/usr/bin/env python3
"""Walk-forward of the Design-A trend_vol CELL SELECTION (not just fixed cells).

The confirmation A/B + the fixed-cell walk-forward
(docs/research/A-vol-gating-OFFcell-design-2026-06-27.md) showed the evidence
OFF-cells help out-of-sample — but the cells themselves were *selected* from the
full-history per-cell attribution, so the selection is in-sample by construction.
This is the stricter test that closes that caveat: for each out-of-sample fold,
RE-DERIVE the OFF-cells from only the prior (in-sample) window, then apply them
OOS and check they still help.

Expanding-window scheme (BTC year-folds):
  OOS fold 2 (2023-07..2024-07): cells authored from data < 2023-07
  OOS fold 3 (2024-07..2025-07): cells authored from data < 2024-07
  OOS fold 4 (2025-07..2026-06): cells authored from data < 2025-07
(fold 1 has no prior in-sample window, so it can't be an OOS fold here.)

Per OOS fold:
  1. run the harness ungated over the in-sample window, read per_cell_attribution
  2. author OFF-cells = meaningful-sample (>= MIN_TRADES) net-negative cells
     (same rule as the evidence policy), written into a temp policy that copies
     the live 1-D blocks verbatim and replaces only trend_vol
  3. run the OOS fold twice: ungated, and ev-ml-gated with the temp policy
  4. report ungated vs ev-ml net/maxDD for the fold + the cells used

Acceptance (the FLIP_POLICY shape, on IN-SAMPLE-DERIVED cells):
  ev-ml net >= ungated net AND ev-ml maxDD <= ungated maxDD, per OOS fold.
If it holds, the cell SELECTION generalizes out-of-sample — not just one fixed
hand-picked set — closing the last in-sample caveat before the live enforce flip.

Tier-1 research tooling — never touches the live order path. Reads the registry
(ML_REGISTRY_ROOT) + the live 1-D policy blocks; runs the existing harness.

  python scripts/ml/walkforward_cell_selection.py [DATA_CSV]
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[2]
LIVE_POLICY = _REPO / "config" / "regime_policy.yaml"
MIN_TRADES = 10  # meaningful-sample threshold (same as the evidence policy)

PY = sys.executable or "python3"
HARNESS = str(_REPO / "scripts" / "backtest_system.py")

# --- per-symbol config (argv): default = BTC (back-compat with the original) ---
import argparse  # noqa: E402

_ap = argparse.ArgumentParser(description="cell-selection walk-forward")
_ap.add_argument("data", nargs="?", default="data/backtest_BTCUSDT_5m.csv")
_ap.add_argument("--symbol", default="BTCUSDT")
_ap.add_argument("--roster",
                 default="trend_donchian,squeeze_breakout_4h,htf_pullback_trend_2h")
_ap.add_argument("--model-id", dest="model_id", default="btc-regime-15m-lgbm-v2")
_ap.add_argument("--clock-tf", dest="clock_tf", default="15m")
_args = _ap.parse_args()
DATA = _args.data
SYMBOL = _args.symbol
ROSTER = _args.roster
MODEL_ID = _args.model_id
CLOCK_TF = _args.clock_tf

# Expanding-window folds: (oos_start, oos_end). In-sample = everything < oos_start.
OOS_FOLDS = [
    ("2023-07-01", "2024-07-01"),
    ("2024-07-01", "2025-07-01"),
    ("2025-07-01", "2026-06-01"),
]


def _run(args: list[str]) -> dict:
    """Run the harness with --json to a temp file; return the parsed dict (or {})."""
    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as fh:
        out = fh.name
    cmd = [PY, HARNESS, "--data", DATA, "--symbol", SYMBOL, "--roster", ROSTER,
           "--clock-tf", CLOCK_TF, "--json", out, *args]
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", ".")
    env.setdefault("ML_REGISTRY_ROOT", "ml/registry-store")
    proc = subprocess.run(cmd, cwd=str(_REPO), env=env,
                          capture_output=True, text=True)
    try:
        with open(out) as fh:
            data = json.load(fh)
    except Exception:
        data = {}
    finally:
        try:
            os.unlink(out)
        except OSError:
            pass
    if not data:
        sys.stderr.write(f"[warn] no JSON for {' '.join(args)}\n{proc.stderr[-800:]}\n")
    return data


def _author_cells(per_cell: dict) -> dict:
    """OFF-cells = meaningful-sample (>= MIN_TRADES) net-negative cells.

    per_cell keys are 'owner|trend|vol|side' -> {trades, pnl, wins}. Skip rows
    with an unknown/None trend or vol (can't place them on the 2-D grid)."""
    trend_vol: dict = {}
    for key, agg in sorted(per_cell.items(), key=lambda kv: kv[1].get("pnl", 0.0)):
        try:
            owner, trend, vol, side = key.split("|")
        except ValueError:
            continue
        if trend in ("None", "unknown", "") or vol in ("None", "unknown", ""):
            continue
        if side not in ("long", "short"):
            continue
        if agg.get("trades", 0) < MIN_TRADES or agg.get("pnl", 0.0) >= 0:
            continue
        trend_vol.setdefault(trend, {}).setdefault(vol, {}).setdefault(owner, {})[side] = "off"
    return trend_vol


def _write_policy(trend_vol: dict) -> str:
    """Temp policy: live 1-D blocks verbatim, trend_vol replaced with `trend_vol`."""
    base = yaml.safe_load(LIVE_POLICY.read_text()) or {}
    pol = copy.deepcopy(base)
    pol["trend_vol"] = trend_vol
    fh = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yaml.safe_dump(pol, fh, sort_keys=False)
    fh.close()
    return fh.name


def _headline(d: dict) -> str:
    if not d:
        return "<no-output>"
    return (f"net=${d.get('net_pnl', 0):.0f} "
            f"maxDD=${d.get('max_drawdown_usd', 0):.0f} "
            f"trades={d.get('total_trades', 0)}")


def main() -> None:
    print("== walk-forward CELL SELECTION (re-derive cells per in-sample window) ==")
    print(f"data={DATA}  head={MODEL_ID}  min_trades={MIN_TRADES}")
    for oos_start, oos_end in OOS_FOLDS:
        print(f"\n== OOS fold {oos_start} .. {oos_end}  (cells authored from < {oos_start}) ==")

        # 1) in-sample ungated -> per-cell attribution -> author cells
        ins = _run(["--end", oos_start, "--vol-verdict", "ml", "--ml-model-id", MODEL_ID])
        per_cell = ins.get("per_cell_attribution") or {}
        cells = _author_cells(per_cell)
        n_off = sum(len(s) for v in cells.values() for st in v.values() for s in st.values())
        print(f"  in-sample: {_headline(ins)}  ->  authored {n_off} OFF-side(s):")
        for tr, vols in cells.items():
            for vol, strats in vols.items():
                for owner, sides in strats.items():
                    for side in sides:
                        c = per_cell.get(f"{owner}|{tr}|{vol}|{side}", {})
                        print(f"      OFF {owner}|{tr}|{vol}|{side}  "
                              f"(in-sample ${c.get('pnl',0):.0f}/{c.get('trades',0)}t)")
        if not cells:
            print("      (no meaningful-sample net-negative cells in-sample — OOS = ungated)")

        policy = _write_policy(cells)

        # 2) OOS: ungated vs ev-ml-gated with the in-sample-derived cells
        oos_un = _run(["--start", oos_start, "--end", oos_end,
                       "--vol-verdict", "ml", "--ml-model-id", MODEL_ID])
        oos_ml = _run(["--start", oos_start, "--end", oos_end,
                       "--regime-router", "on", "--regime-policy", policy,
                       "--vol-verdict", "ml", "--ml-model-id", MODEL_ID])
        try:
            os.unlink(policy)
        except OSError:
            pass

        un_net = oos_un.get("net_pnl", 0.0)
        un_dd = oos_un.get("max_drawdown_usd", 0.0)
        ml_net = oos_ml.get("net_pnl", 0.0)
        ml_dd = oos_ml.get("max_drawdown_usd", 0.0)
        net_ok = ml_net >= un_net
        dd_ok = ml_dd <= un_dd
        print(f"  OOS ungated : {_headline(oos_un)}")
        print(f"  OOS ev-ml   : {_headline(oos_ml)}")
        print(f"  VERDICT: net {'PASS' if net_ok else 'FAIL'} "
              f"(ml ${ml_net:.0f} vs ungated ${un_net:.0f}) | "
              f"maxDD {'PASS' if dd_ok else 'FAIL'} "
              f"(ml ${ml_dd:.0f} vs ungated ${un_dd:.0f})")
    print("\nWF_CELLSEL_DONE")


if __name__ == "__main__":
    main()
