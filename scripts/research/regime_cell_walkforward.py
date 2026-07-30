#!/usr/bin/env python3
"""Walk-forward the temporal stability of ONE (regime, direction) debt-matrix cell.

The regime-debt matrix (`regime_debt_matrix.py`, rec #5) grades each debt
strategy's FULL-SAMPLE per-(regime, direction) net-R. But a full-sample cell —
even a powered one — can be an artifact of *when* that regime happened to occur:
`direction_walkforward.py` refuted the 2h-pullback long-drag on exactly that
basis (#7915). So before a losing cell can become a Tier-3 OFF-cell draft it
must survive an out-of-sample walk-forward. This driver runs that gate for one
cell end-to-end:

  1. resolve the strategy's exact live params + candle feed (reuses
     `regime_debt_matrix`: Binance-vision for `*USDT`, Yahoo for equities/ETFs
     + `ES=F`/`GC=F`/`HG=F` continuous futures),
  2. run the classified harness `--emit-trades`,
  3. regime-tag each trade and keep ONLY the target regime
     (`regime_tag_emitted.py --emit-tagged --only-regime`),
  4. fold the target-regime trades into N contiguous time-folds and report
     per-fold per-direction net-R + the stability verdict
     (`direction_walkforward.analyze`).

The verdict for a SHORT-side OFF-cell candidate (e.g. gld_pullback_1h trending
short) is `short_stable_drag` — the short side is < 0 in a strict majority of
folds AND pooled short net-R < 0. Only then is the cell a real (still Tier-3,
still operator-gated) draft candidate; otherwise it's regime-of-sample noise
and stays tracked debt.

Yahoo needs network the sandbox firewalls, so this runs on a free GitHub-hosted
runner (see .github/workflows/regime-cell-walkforward.yml). The crypto path is
exercisable in-sandbox for verification.

Usage:
  python scripts/research/regime_cell_walkforward.py \
      --strategy gld_pullback_1h --regime trending --folds 4 --json
  # in-sandbox verification on a crypto cell (Binance-reachable):
  python scripts/research/regime_cell_walkforward.py \
      --strategy ada_pullback_2h --regime trending --folds 3 --json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import direction_walkforward as dwf  # type: ignore  # noqa: E402
import regime_debt_matrix as rdm  # type: ignore  # noqa: E402

REPO = rdm.REPO


def run_cell(strategy: str, regime: str, folds: int, workdir: str, days: int) -> dict:
    roster = rdm.load_roster()
    # Fall back to strategies.yaml so an ALREADY-CELLED strategy can still be
    # re-audited — authoring a cell pays it down out of coverage_debt, which used to
    # make it permanently unmeasurable here. BL-20260730-REGIME-CELL-UNAUDITABLE.
    cfg = roster.get(strategy) or rdm.resolve_strategy(strategy)
    out: dict = {"strategy": strategy, "regime": regime, "folds": folds, "days": days}
    if cfg is None:
        out["error"] = "not declared in strategies.yaml"
        return out
    harness = rdm.classify(cfg)
    sym = (cfg.get("symbols") or [None])[0]
    tf = cfg.get("timeframe")
    if harness is None or not sym or not tf:
        out["error"] = "unclassifiable (no donchian/pullback/squeeze params or no symbol/timeframe)"
        return out
    out.update({"symbol": sym, "timeframe": tf, "harness": harness})

    feed = rdm.resolve_feed(sym, tf)
    out["feed"] = feed
    csv = os.path.join(workdir, f"{strategy}__wf_data.csv")
    emit = os.path.join(workdir, f"{strategy}__wf_trades.jsonl")
    jout = os.path.join(workdir, f"{strategy}__wf_bt.json")
    tagged = os.path.join(workdir, f"{strategy}__wf_{regime}.jsonl")

    try:
        rdm._fetch_csv(feed, days, csv)
    except Exception as e:  # noqa: BLE001
        out["error"] = f"fetch failed: {type(e).__name__}: {e}"
        return out

    argv, faithful, omitted = rdm.build_harness_cmd(
        strategy, cfg, harness, csv, feed["resample"], emit, jout)
    unreplayable = sorted(k for k in cfg if k in rdm._UNREPLAYABLE)
    if unreplayable:
        faithful = False
        omitted = sorted(set(omitted) | set(unreplayable))
    out["fidelity"] = "faithful" if faithful else "approximate"
    out["omitted_levers"] = omitted
    try:
        subprocess.run(argv, check=True, cwd=REPO,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        out["error"] = f"harness failed: {(e.stderr or b'').decode()[-300:]}"
        return out

    # regime-tag + keep only the target regime's trades
    try:
        subprocess.run(
            [sys.executable, os.path.join(REPO, "scripts/research/regime_tag_emitted.py"),
             "--trades", emit, "--data", csv, "--resample", feed["resample"],
             "--label", strategy, "--emit-tagged", tagged, "--only-regime", regime,
             "--json"],
            check=True, cwd=REPO, capture_output=True)
    except subprocess.CalledProcessError as e:
        out["error"] = f"regime-tag failed: {(e.stderr or b'').decode()[-300:]}"
        return out

    # walk-forward the target-regime subset
    wf = dwf.analyze([tagged], max(2, folds), f"{strategy}:{regime}")
    out["walkforward"] = wf
    out["cell_verdict"] = cell_verdict(wf, regime)
    return out


def cell_verdict(wf: dict, regime: str) -> dict:
    """Reduce a walk-forward result to the per-side OOS-stability verdict for one
    regime cell. A SHORT-side OFF cell is justified ONLY when the short side is
    < 0 in a strict majority of folds AND pooled short net-R < 0 (mirrors #7915's
    `stable_drag` test, applied to the short leg within one regime); the LONG-side
    analogue is reported symmetrically. Neither is a trade decision — both are the
    evidence gate a Tier-3 OFF-cell draft must pass first."""
    p = wf.get("pooled") or {}
    folds = wf.get("folds", 0) or 0
    by_fold = wf.get("by_fold", [])
    short_neg = sum(1 for s in by_fold if s.get("short_n") and s.get("short_r", 0) < 0)
    long_neg = sum(1 for s in by_fold if s.get("long_n") and s.get("long_r", 0) < 0)
    return {
        "target_regime": regime,
        "regime_trades": wf.get("total_trades", 0),
        "short_folds_negative": short_neg,
        "long_folds_negative": long_neg,
        "of_folds": folds,
        "pooled_short_r": p.get("short_r"),
        "pooled_long_r": p.get("long_r"),
        "short_stable_drag": short_neg > folds / 2 and (p.get("short_r") or 0) < 0,
        "long_stable_drag": long_neg > folds / 2 and (p.get("long_r") or 0) < 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strategy", required=True, help="debt-roster strategy name")
    ap.add_argument("--regime", default="trending",
                    choices=["trending", "transitional", "chop"],
                    help="the regime whose cell is walk-forwarded (default trending)")
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--days", type=int, default=730)
    ap.add_argument("--workdir", default="/tmp/regime_cell_wf")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    os.makedirs(args.workdir, exist_ok=True)
    res = run_cell(args.strategy, args.regime, args.folds, args.workdir, args.days)
    if args.json:
        print(json.dumps(res))
        return 0
    print(f"== walk-forward {res.get('strategy')} [{res.get('regime')} cell] ==")
    if res.get("error"):
        print(f"  ERROR: {res['error']}")
        return 0
    print(f"  {res['symbol']} {res['timeframe']} · {res['harness']} · fidelity={res['fidelity']}")
    for s in res.get("walkforward", {}).get("by_fold", []):
        print(f"  fold {s['fold']} [{s.get('from')}..{s.get('to')}]: "
              f"n={s['trades']} long_r={s['long_r']}(n{s['long_n']}) "
              f"short_r={s['short_r']}(n{s['short_n']}) net={s['net_r']}")
    cv = res.get("cell_verdict", {})
    print(f"  CELL VERDICT [{cv.get('target_regime')}]: n={cv.get('regime_trades')} "
          f"short_neg {cv.get('short_folds_negative')}/{cv.get('of_folds')} "
          f"pooled_short_r={cv.get('pooled_short_r')} · "
          f"short_stable_drag={cv.get('short_stable_drag')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
