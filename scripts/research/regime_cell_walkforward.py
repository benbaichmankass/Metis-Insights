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

# Minimum trades IN THE GRADED DIRECTION for a verdict to be decision-grade.
#
# NOT an invented threshold. It is the SAME meaningful-sample floor as the
# evidence policy that AUTHORED these cells in the first place —
# `scripts/ml/walkforward_cell_selection.py::MIN_TRADES = 10` ("OFF-cells =
# meaningful-sample (>= MIN_TRADES) net-negative cells"). Grading a cell on a
# smaller sample than the policy required to author it is not a re-audit.
# (That module parse_args() at import time, so it cannot be imported here; the
# two constants are pinned together by `test_min_direction_trades_matches_
# evidence_policy`, which reads the literal out of the sibling via `ast`.)
#
# WHY THIS EXISTS (measured 2026-08-07). `*_stable_drag` had no sample floor at
# all, and `regime-selectivity` Rule 2 makes it THE gate a Tier-3 OFF-cell must
# clear. Because folds are equal-COUNT by trade order (direction_walkforward.
# analyze), three losing trades spread one-per-fold satisfy "pooled < 0 AND
# strict majority-negative under every panel member" and report
# `long_stable_drag=True, long_fold_sensitive=False` — indistinguishable in the
# output from a verdict over 300 trades. Conversely two trades pooling -80R
# report False, because the majority denominator is the fold COUNT and empty
# folds dilute it. So at the n where the six authored `trend_vol` cells actually
# live (9, 9, 30, 43) the PASS/FAIL was driven by how many folds the trades
# happened to spread across, not by the strength of the evidence.
#
# A re-partition of 9 trades into 3/4/5 contiguous slices is not out-of-sample
# validation — it is the same 9 trades counted three ways.
# `CLAUDE.md` § "Diagnostic provenance", sub-class C (unasserted denominator).
MIN_DIRECTION_TRADES = 10


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
            # How many folds could contribute AT ALL. The majority test above
            # divides by the fold COUNT, not by this — so when a direction's
            # trades occupy fewer than ceil(k/2)+1 folds it cannot pass at that
            # k no matter how negative it is (2 trades pooling -80R read False
            # at k=4 and k=5). That is the intended conservative reading — a
            # drag concentrated in one slice of the timeline has not been shown
            # to PERSIST — but it is only legible if the denominator gap is
            # visible, so it is reported rather than left implicit.
            "short_populated_folds": sum(1 for s in by_fold if s.get("short_n")),
            "long_populated_folds": sum(1 for s in by_fold if s.get("long_n")),
            "short_majority_negative": short_maj[k],
            "long_majority_negative": long_maj[k],
        }
    pooled_short_neg = (p.get("short_r") or 0) < 0
    pooled_long_neg = (p.get("long_r") or 0) < 0
    # Per-direction trade counts. Without these a verdict over ZERO trades in a
    # direction is indistinguishable from a measured False: trending/volatile
    # trend_donchian (2026-08-07) had 9 long trades and 0 short, and reported
    # `short_stable_drag=False` with nothing marking the population as empty.
    ref_by_fold = ref.get("by_fold", [])
    long_trades = sum(int(s.get("long_n") or 0) for s in ref_by_fold)
    short_trades = sum(int(s.get("short_n") or 0) for s in ref_by_fold)
    # A verdict below the evidence policy's own meaningful-sample floor is not a
    # weak finding, it is NO finding — the same distinction the tool already
    # draws for a zero-trade direction, extended to the range where the fold
    # arithmetic is driven by trade spacing rather than by evidence. Kept as an
    # explicit tri-state so a caller can never again read "not enough data" as a
    # measured negative: `*_stable_drag` False now means EITHER, and only
    # `*_verdict` separates them.
    long_insufficient = long_trades < MIN_DIRECTION_TRADES
    short_insufficient = short_trades < MIN_DIRECTION_TRADES
    long_drag = (pooled_long_neg and all(long_maj.values()) and bool(ks)
                 and not long_insufficient)
    short_drag = (pooled_short_neg and all(short_maj.values()) and bool(ks)
                  and not short_insufficient)

    def _verdict(insufficient: bool, drag: bool, n: int) -> str:
        if n == 0:
            return "no_trades"
        if insufficient:
            return "insufficient_n"
        return "stable_drag" if drag else "no_stable_drag"

    return {
        "target_regime": regime,
        "target_vol": vol,
        "cell": f"{regime}/{vol}" if vol else regime,
        "regime_trades": ref.get("total_trades", 0),
        "long_trades": long_trades,
        "short_trades": short_trades,
        "fold_panel": list(ks),
        "per_fold_count": per_fold_count,
        "pooled_short_r": p.get("short_r"),
        "pooled_long_r": p.get("long_r"),
        # The floor this verdict was graded against, stated in the output so the
        # basis travels with the number instead of living only in the source
        # (CLAUDE-RULES-CANONICAL § "Always state the population").
        "min_direction_trades": MIN_DIRECTION_TRADES,
        "long_insufficient_n": long_insufficient,
        "short_insufficient_n": short_insufficient,
        # Tri-state: no_trades | insufficient_n | no_stable_drag | stable_drag.
        # Read THIS, not the boolean, when deciding anything.
        "long_verdict": _verdict(long_insufficient, long_drag, long_trades),
        "short_verdict": _verdict(short_insufficient, short_drag, short_trades),
        # pooled<0 AND strict majority-negative under EVERY panel fold count AND
        # the direction clears MIN_DIRECTION_TRADES.
        "short_stable_drag": short_drag,
        "long_stable_drag": long_drag,
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
    print(f"  CELL VERDICT [{cv.get('cell')}]: n={cv.get('regime_trades')} "
          f"panel={cv.get('fold_panel')}")
    # BOTH directions are printed. This block used to emit only the SHORT
    # verdict under a heading that says "CELL VERDICT", while
    # `long_stable_drag` was computed two lines earlier and dropped at the
    # output layer — the same shape as the `p_volatile` drop in #8553.
    # Four of the six authored `trend_vol` cells are `long: off`
    # (config/regime_policy.yaml), so for those the printed verdict graded a
    # direction the cell does not gate. Observed 2026-08-07 on
    # trending/volatile trend_donchian: all 9 trades LONG, zero short, and the
    # line still read `pooled_short_r=0 · short_stable_drag=False` — a
    # determinate verdict over an empty population (CLAUDE.md § "Diagnostic
    # provenance", sub-class A: the label does not describe what was computed).
    for side in ("long", "short"):
        maj = " ".join(
            f"k{k}:{v[f'{side}_folds_negative']}/{k}(pop{v[f'{side}_populated_folds']})"
            for k, v in panel.items()
        )
        n_side = cv.get(f"{side}_trades")
        verdict = cv.get(f"{side}_verdict")
        # The qualifier must sit ON the verdict line. A floor reported only in
        # the JSON is a floor the reader of the human output never applies —
        # which is how n=9 cells were read as justified/refuted for a day.
        if verdict == "no_trades":
            note = "  [NO TRADES — verdict is vacuous]"
        elif verdict == "insufficient_n":
            note = (f"  [INSUFFICIENT n — {n_side} < {cv.get('min_direction_trades')} "
                    f"(the evidence policy's own floor); NOT a finding either way]")
        else:
            note = ""
        print(f"    {side:<5} n={n_side} {side}_maj[{maj}] "
              f"pooled_{side}_r={cv.get(f'pooled_{side}_r')} · "
              f"{side}_verdict={verdict} "
              f"(stable_drag={cv.get(f'{side}_stable_drag')}, "
              f"fold_sensitive={cv.get(f'{side}_fold_sensitive')}){note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
