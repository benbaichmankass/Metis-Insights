#!/usr/bin/env python3
"""Walk-forward the temporal stability of ONE debt-matrix cell.

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
  3. regime-tag each trade and keep ONLY the target cell
     (`regime_tag_emitted.py --emit-tagged --only-regime [--vol-labels --only-vol]`),
  4. fold the target-cell trades into contiguous time-folds and report
     per-fold per-direction net-R + the stability verdict.

**The live router gates on 2-D `(trend, vol)` cells.** Passing only `--regime`
walk-forwards the 1-D trend cell — a DIFFERENT population than the six authored
`trend_vol` cells the router actually enforces (BL-20260730-WALKFORWARD-NO-VOL-AXIS).
To gate a 2-D cell, pass BOTH `--regime` and `--vol {calm,volatile}` together with
`--vol-labels <jsonl>` (per-bar ML vol labels from `ml_vol_label_replay.py`, which
replays the SAME advisory head the router reads — NOT the frozen `vol_detector`,
whose label behaves oppositely). The vol-labels file is produced trainer-side and
injected here; requesting `--vol` WITHOUT `--vol-labels` is a hard error, never a
silent 1-D fallback (mirrors `regime_tag_emitted.py`'s `--only-vol requires
--vol-labels`).

**The verdict is fold-count invariant** (BL-20260730-WF-FOLDCOUNT-VERDICT-FLIP):
`*_stable_drag` used to be `neg > folds/2`, so at identical pooled net-R a 4-fold
2/4 read FALSE while a 3-fold 2/3 read TRUE — the PASS/FAIL flipped on the
caller's `--folds`. The verdict is now computed over a FIXED internal fold panel
`FOLD_PANEL` (independent of `--folds`) and requires pooled<0 AND a strict
per-fold-count majority-negative in EVERY panel member; `*_fold_sensitive` reports
when the panel members disagree. `--folds` still controls only the human-readable
`by_fold` breakdown, never the verdict.

Yahoo needs network the sandbox firewalls, so this runs on a free GitHub-hosted
runner (see .github/workflows/regime-cell-walkforward.yml). The crypto path is
exercisable in-sandbox for verification.

Usage:
  # 1-D trend cell (legacy):
  python scripts/research/regime_cell_walkforward.py \
      --strategy gld_pullback_1h --regime trending --folds 4 --json
  # 2-D (trend, vol) cell — the population the router enforces:
  python scripts/research/regime_cell_walkforward.py \
      --strategy gld_pullback_1h --regime trending --vol volatile \
      --vol-labels /tmp/gld_vol_labels.jsonl --json
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

# Fixed internal fold panel the verdict is evaluated over. Independent of the
# caller's --folds so the PASS/FAIL cannot flip on the fold count
# (BL-20260730-WF-FOLDCOUNT-VERDICT-FLIP). Odd + even members so a genuine
# stable drag must hold under both parities.
FOLD_PANEL = (3, 4, 5)


def run_cell(strategy: str, regime: str, folds: int, workdir: str, days: int,
             vol: str | None = None, vol_labels: str | None = None) -> dict:
    roster = rdm.load_roster()
    # Fall back to strategies.yaml so an ALREADY-CELLED strategy can still be
    # re-audited — authoring a cell pays it down out of coverage_debt, which used to
    # make it permanently unmeasurable here. BL-20260730-REGIME-CELL-UNAUDITABLE.
    cfg = roster.get(strategy) or rdm.resolve_strategy(strategy)
    out: dict = {"strategy": strategy, "regime": regime, "folds": folds, "days": days}
    if vol:
        out["vol"] = vol
    out["cell"] = f"{regime}/{vol}" if vol else regime
    if cfg is None:
        out["error"] = "not declared in strategies.yaml"
        return out
    # A 2-D cell is refused without vol labels rather than silently graded on the
    # 1-D trend population (the wrong-population trap this axis exists to close).
    if vol and not vol_labels:
        out["error"] = ("2-D cell requested (--vol) but no --vol-labels supplied; "
                        "refusing a 1-D wrong-population fallback")
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
    cell_slug = f"{regime}_{vol}" if vol else regime
    tagged = os.path.join(workdir, f"{strategy}__wf_{cell_slug}.jsonl")

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

    # regime-tag + keep only the target cell's trades (2-D when --vol given).
    tag_cmd = [
        sys.executable, os.path.join(REPO, "scripts/research/regime_tag_emitted.py"),
        "--trades", emit, "--data", csv, "--resample", feed["resample"],
        "--label", strategy, "--emit-tagged", tagged, "--only-regime", regime,
        "--json",
    ]
    if vol:
        tag_cmd += ["--vol-labels", vol_labels, "--only-vol", vol]
    try:
        subprocess.run(tag_cmd, check=True, cwd=REPO, capture_output=True)
    except subprocess.CalledProcessError as e:
        out["error"] = f"regime-tag failed: {(e.stderr or b'').decode()[-300:]}"
        return out

    # Display walk-forward at the caller's --folds (human-readable by_fold only).
    disp_k = max(2, folds)
    panel_ks = sorted(set(FOLD_PANEL) | {disp_k})
    label = f"{strategy}:{cell_slug}"
    analyses = {k: dwf.analyze([tagged], k, label) for k in panel_ks}
    out["walkforward"] = analyses[disp_k]
    # Verdict over the FIXED panel only — never the caller's --folds.
    out["cell_verdict"] = cell_verdict({k: analyses[k] for k in FOLD_PANEL}, regime, vol)
    return out


def cell_verdict(panel: dict[int, dict], regime: str, vol: str | None = None) -> dict:
    """Reduce a FIXED-panel walk-forward to the per-side OOS-stability verdict for
    one cell. A SHORT-side OFF cell is justified ONLY when the pooled short net-R
    < 0 AND the short side is negative in a strict majority of folds under EVERY
    fold count in ``panel`` (the long-side analogue is symmetric). Because the
    panel is fixed (`FOLD_PANEL`) and not the caller's ``--folds``, the boolean is
    fold-count invariant (BL-20260730-WF-FOLDCOUNT-VERDICT-FLIP); ``*_fold_sensitive``
    flags when the per-fold-count majority reads disagree. Neither boolean is a
    trade decision — both are the evidence gate a Tier-3 OFF-cell draft passes first.
    """
    ks = sorted(panel)
    # Pooled stats are identical across fold counts (same trades) — read from any.
    ref = panel[ks[-1]] if ks else {}
    p = ref.get("pooled") or {}
    per_fold_count: dict = {}
    short_maj: dict = {}
    long_maj: dict = {}
    for k in ks:
        by_fold = panel[k].get("by_fold", [])
        short_neg = sum(1 for s in by_fold if s.get("short_n") and (s.get("short_r") or 0) < 0)
        long_neg = sum(1 for s in by_fold if s.get("long_n") and (s.get("long_r") or 0) < 0)
        short_maj[k] = short_neg > k / 2
        long_maj[k] = long_neg > k / 2
        per_fold_count[k] = {
            "short_folds_negative": short_neg,
            "long_folds_negative": long_neg,
            "of_folds": k,
            "short_majority_negative": short_maj[k],
            "long_majority_negative": long_maj[k],
        }
    pooled_short_neg = (p.get("short_r") or 0) < 0
    pooled_long_neg = (p.get("long_r") or 0) < 0
    return {
        "target_regime": regime,
        "target_vol": vol,
        "cell": f"{regime}/{vol}" if vol else regime,
        "regime_trades": ref.get("total_trades", 0),
        "fold_panel": list(ks),
        "per_fold_count": per_fold_count,
        "pooled_short_r": p.get("short_r"),
        "pooled_long_r": p.get("long_r"),
        # pooled<0 AND strict majority-negative under EVERY panel fold count.
        "short_stable_drag": pooled_short_neg and all(short_maj.values()) and bool(ks),
        "long_stable_drag": pooled_long_neg and all(long_maj.values()) and bool(ks),
        # True when the per-fold-count majority reads are not unanimous (the exact
        # instability the fold-count-flip bug hid) — a transparency flag, not a gate.
        "short_fold_sensitive": len(set(short_maj.values())) > 1,
        "long_fold_sensitive": len(set(long_maj.values())) > 1,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strategy", required=True, help="debt-roster strategy name")
    ap.add_argument("--regime", default="trending",
                    choices=["trending", "transitional", "chop"],
                    help="the trend regime whose cell is walk-forwarded (default trending)")
    ap.add_argument("--vol", default=None, choices=["calm", "volatile"],
                    help="the vol half of a 2-D (trend, vol) cell; requires --vol-labels")
    ap.add_argument("--vol-labels", default=None,
                    help="per-bar ML vol labels JSONL (from ml_vol_label_replay.py); "
                         "REQUIRED when --vol is set — never a silent 1-D fallback")
    ap.add_argument("--folds", type=int, default=4,
                    help="fold count for the human-readable by_fold breakdown only "
                         "(the verdict uses the fixed FOLD_PANEL, not this)")
    ap.add_argument("--days", type=int, default=730)
    ap.add_argument("--workdir", default="/tmp/regime_cell_wf")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.vol and not args.vol_labels:
        print("ERROR: --vol requires --vol-labels (refusing a 1-D wrong-population "
              "fallback for a 2-D cell).", file=sys.stderr)
        return 2
    os.makedirs(args.workdir, exist_ok=True)
    res = run_cell(args.strategy, args.regime, args.folds, args.workdir, args.days,
                   vol=args.vol, vol_labels=args.vol_labels)
    if args.json:
        print(json.dumps(res))
        return 0
    print(f"== walk-forward {res.get('strategy')} [{res.get('cell')} cell] ==")
    if res.get("error"):
        print(f"  ERROR: {res['error']}")
        return 0
    print(f"  {res['symbol']} {res['timeframe']} · {res['harness']} · fidelity={res['fidelity']}")
    for s in res.get("walkforward", {}).get("by_fold", []):
        print(f"  fold {s['fold']} [{s.get('from')}..{s.get('to')}]: "
              f"n={s['trades']} long_r={s['long_r']}(n{s['long_n']}) "
              f"short_r={s['short_r']}(n{s['short_n']}) net={s['net_r']}")
    cv = res.get("cell_verdict", {})
    panel = cv.get("per_fold_count", {})
    maj = " ".join(f"k{k}:{v['short_folds_negative']}/{k}" for k, v in panel.items())
    print(f"  CELL VERDICT [{cv.get('cell')}]: n={cv.get('regime_trades')} "
          f"panel={cv.get('fold_panel')} short_maj[{maj}] "
          f"pooled_short_r={cv.get('pooled_short_r')} · "
          f"short_stable_drag={cv.get('short_stable_drag')} "
          f"(fold_sensitive={cv.get('short_fold_sensitive')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
