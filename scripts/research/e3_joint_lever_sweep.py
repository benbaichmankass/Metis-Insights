#!/usr/bin/env python3
"""E3 — decision-time exit levers over the in-trade panel, swept JOINTLY.

The process (`docs/design/exit-mechanism-construction-PROCESS.md` § E3) specifies:

    Levers are swept **jointly**, not one at a time: the single-lever sweeps cannot
    see an interaction ... *Falsifier:* a combined cell must beat the best single
    cell by more than the added degrees of freedom buy. State the comparison
    explicitly.

⚠️ **WHY THIS IS A SCREEN AND NOT THE M20 GATE — READ BEFORE QUOTING A PASS.** This
replays levers over the EXISTING trade set: each trade's entry is fixed and only its
exit moves. That makes the comparison cost-NEUTRAL by construction (both arms take
exactly one exit per trade, so the per-exit fee cancels) rather than net-of-cost. It
therefore cannot see the effect the M27 sweep calls out explicitly: an earlier exit
frees earlier re-entries, so a banking lever raises turnover and pays MORE fees than
the baseline in a real book.

The asymmetry that follows is the whole basis for reading a result here:

  * a **NEGATIVE is strong** — the cell failed under assumptions that flatter it;
  * a **POSITIVE is provisional** — it must be re-run through
    `m20_fleet_exit_sweep.py`, where entries re-sequence and fees are charged, before
    it is evidence of anything.

Nothing here may be quoted as an M20 verdict, and no cell here licenses a Tier-3
declare.

WHY THE LEVERS ARE READ AT DECISION TIME ONLY. `docs/research/e3-barrier-geometry-2026-08-20.md`
measured that E2's `label_hold` hits are barrier COMPOSITION — the pooled association
reverses inside every stratum, and the stratified view conditions on a barrier the
trade only reaches LATER. So an information score on that label cannot license a
lever, and the licence has to come from a rule that uses only what is knowable at the
bar it fires on. Every condition here is a function of the current row.

THE EXIT PRICE IS THE BAR'S OWN MARK, NOT A LATER ONE. A firing lever realises
`feat_upnl_r` at the triggering row — `(close_t - entry) * dir / R`, the
mark-to-market at that bar's close. That is the same anchoring rule
`src/runtime/exit_anchor.py` enforces on the live side: a confirmed exit is priced at
its own bar, never at a mark read at some later time.

Observe-only, Tier-1: reads a panel, writes a report, touches nothing live.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in [here.parent] + list(here.parents):
        if (p / "scripts").is_dir() and (p / "src").is_dir():
            return p
    return here.parent.parent


_ROOT = _repo_root()
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.research.e2_feature_information import load_panel  # noqa: E402

# The three features the E2 horizon arm named, and nothing else. Widening the grid
# after seeing a result is the move the pre-registration discipline exists to stop.
BANK_AT = (0.25, 0.50, 0.75, 1.00, 1.25)      # upnl_r >= a          -> take the profit
MAE_STOP = (0.30, 0.50, 0.70)                 # running_mae_r >= b   -> adverse-excursion cut
NEAR_STOP = (0.25, 0.50, 1.00)                # dist_to_stop_atr <= c -> proximity cut


def _cond_bank(a: float) -> Callable[[Dict[str, Any]], bool]:
    def f(r):
        v = r.get("feat_upnl_r")
        return v is not None and v >= a
    return f


def _cond_mae(b: float) -> Callable[[Dict[str, Any]], bool]:
    def f(r):
        v = r.get("feat_running_mae_r")
        return v is not None and v >= b
    return f


def _cond_near(c: float) -> Callable[[Dict[str, Any]], bool]:
    def f(r):
        v = r.get("feat_dist_to_stop_atr")
        return v is not None and v <= c
    return f


def build_grid() -> List[Dict[str, Any]]:
    """Singles, then every OR- and AND-composition of the three families.

    `n_params` is carried per cell because the falsifier is stated in degrees of
    freedom: a combined cell must beat the best single by more than the extra
    parameters buy, and that comparison needs the count, not an adjective.
    """
    cells: List[Dict[str, Any]] = []
    for a in BANK_AT:
        cells.append({"tag": f"bank{a}", "kind": "single", "n_params": 1,
                      "conds": [_cond_bank(a)], "mode": "or"})
    for b in MAE_STOP:
        cells.append({"tag": f"mae{b}", "kind": "single", "n_params": 1,
                      "conds": [_cond_mae(b)], "mode": "or"})
    for c in NEAR_STOP:
        cells.append({"tag": f"near{c}", "kind": "single", "n_params": 1,
                      "conds": [_cond_near(c)], "mode": "or"})

    fams = [("bank", BANK_AT, _cond_bank), ("mae", MAE_STOP, _cond_mae),
            ("near", NEAR_STOP, _cond_near)]
    for k in (2, 3):
        for combo in itertools.combinations(fams, k):
            for vals in itertools.product(*[c[1] for c in combo]):
                conds = [c[2](v) for c, v in zip(combo, vals)]
                tag = "+".join(f"{c[0]}{v}" for c, v in zip(combo, vals))
                # OR = any trigger fires (the natural composition for exit rules).
                cells.append({"tag": f"OR({tag})", "kind": "joint", "n_params": k,
                              "conds": conds, "mode": "or"})
                # AND = fire only when every condition holds at the SAME bar.
                cells.append({"tag": f"AND({tag})", "kind": "joint", "n_params": k,
                              "conds": conds, "mode": "and"})
    return cells


def trades_from_panel(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[r.get("trade_id")].append(r)
    out = []
    for tid, rs in by.items():
        rs.sort(key=lambda r: (r.get("feat_bars_in_trade") or 0.0))
        base = rs[-1].get("trade_realized_r")
        if base is None:
            continue
        out.append({"trade_id": tid, "rows": rs, "base_r": float(base),
                    "t0": rs[0].get("decision_time")})
    out.sort(key=lambda t: (str(t["t0"] or "")))
    return out


def apply_cell(trades: Sequence[Dict[str, Any]], cell: Dict[str, Any],
               cost_r: float) -> Dict[str, Any]:
    """Total R under the lever, and how often it actually fired.

    `fired` is reported beside every delta because a cell that never fires has a
    delta of exactly 0.0 and is indistinguishable, in the number alone, from one
    that fired constantly and broke even. The M20 sweep makes the same point:
    "the lever must have been able to fire before any delta means anything."

    ``cost_r`` is charged ONLY when the lever fires, and the baseline is left
    untouched. That asymmetry is deliberate and is NOT a net-of-cost model: it is a
    BREAK-EVEN PROBE for how much extra cost per early exit the edge can absorb. A
    symmetric per-exit fee would cancel between the arms and measure nothing.
    """
    tot = 0.0
    fired = 0
    for t in trades:
        hit = None
        for r in t["rows"]:
            vs = [c(r) for c in cell["conds"]]
            ok = all(vs) if cell["mode"] == "and" else any(vs)
            if ok:
                hit = r
                break
        if hit is None:
            tot += t["base_r"]
        else:
            v = hit.get("feat_upnl_r")
            if v is None:
                tot += t["base_r"]
                continue
            fired += 1
            tot += float(v) - cost_r
    n = len(trades)
    return {"total_r": tot, "n_trades": n, "fired": fired,
            "fired_frac": (fired / n) if n else None,
            "mean_r": (tot / n) if n else None}


def _folds(trades: Sequence[Dict[str, Any]], n_folds: int
           ) -> List[Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]]:
    """Walk-forward: each fold trains on everything BEFORE its test block.

    Anchored/expanding rather than rolling, and strictly forward — a cell selected
    on data that includes its own test window is not an out-of-sample number.
    """
    n = len(trades)
    if n_folds < 2 or n < n_folds * 2:
        return []
    size = n // (n_folds + 1)
    out = []
    for k in range(1, n_folds + 1):
        tr = list(trades[: size * k])
        te = list(trades[size * k: size * (k + 1)])
        if len(tr) >= 10 and len(te) >= 10:
            out.append((tr, te))
    return out


def sweep(rows: List[Dict[str, Any]], manifest: Optional[Dict[str, Any]], *,
          cost_r: float = 0.0, n_folds: int = 4) -> Dict[str, Any]:
    trades = trades_from_panel(rows)
    grid = build_grid()
    base_all = sum(t["base_r"] for t in trades)

    folds = _folds(trades, n_folds)
    rep: Dict[str, Any] = {
        "step": "E3-joint-lever-screen",
        "symbol": (manifest or {}).get("symbol"),
        "timeframe": (manifest or {}).get("timeframe"),
        "is_the_m20_gate": False,
        "caveat": (
            "Entries are held fixed, so the per-exit fee cancels and this is "
            "cost-NEUTRAL, not net-of-cost. It cannot see the turnover an earlier "
            "exit creates, which only ever hurts a banking lever. A NEGATIVE here is "
            "strong; a POSITIVE is provisional and must be re-run through "
            "m20_fleet_exit_sweep.py before it is evidence."
        ),
        "population": {
            "n_trades": len(trades), "n_rows": len(rows),
            "baseline_total_r": base_all,
            "baseline_mean_r": (base_all / len(trades)) if trades else None,
        },
        "config": {"cost_r": cost_r, "n_folds": n_folds, "grid_size": len(grid),
                   "n_singles": sum(1 for c in grid if c["kind"] == "single"),
                   "n_joint": sum(1 for c in grid if c["kind"] == "joint"),
                   "bank_at": list(BANK_AT), "mae_stop": list(MAE_STOP),
                   "near_stop": list(NEAR_STOP)},
        "folds_formed": len(folds),
    }
    if not folds:
        rep["error"] = "not enough trades to form walk-forward folds"
        return rep

    # In-sample selection, out-of-sample evaluation, separately for the SINGLE grid
    # and the JOINT grid. Selecting over a bigger grid inflates the in-sample number,
    # so the honest comparison is OOS-vs-OOS with the grid sizes stated.
    def _select_and_eval(kind_filter) -> Dict[str, Any]:
        pool = [c for c in grid if kind_filter(c)]
        per_fold = []
        for tr, te in folds:
            base_tr = sum(t["base_r"] for t in tr)
            best, best_d = None, None
            for c in pool:
                d = apply_cell(tr, c, cost_r)["total_r"] - base_tr
                if best_d is None or d > best_d:
                    best, best_d = c, d
            base_te = sum(t["base_r"] for t in te)
            oos = apply_cell(te, best, cost_r)
            per_fold.append({
                "selected": best["tag"], "n_params": best["n_params"],
                "is_delta_r": best_d,
                "oos_delta_r": oos["total_r"] - base_te,
                "oos_base_r": base_te, "oos_fired_frac": oos["fired_frac"],
                "oos_n": oos["n_trades"],
            })
        return {
            "pool_size": len(pool),
            "per_fold": per_fold,
            "oos_delta_r_total": sum(f["oos_delta_r"] for f in per_fold),
            "oos_folds_positive": sum(1 for f in per_fold if f["oos_delta_r"] > 0),
            "n_folds": len(per_fold),
        }

    singles = _select_and_eval(lambda c: c["kind"] == "single")
    joint = _select_and_eval(lambda c: c["kind"] in ("single", "joint"))
    rep["singles"] = singles
    rep["joint"] = joint

    # THE FALSIFIER, stated as the process requires.
    d_single = singles["oos_delta_r_total"]
    d_joint = joint["oos_delta_r_total"]
    extra_params = max(
        (f["n_params"] for f in joint["per_fold"]), default=1) - max(
        (f["n_params"] for f in singles["per_fold"]), default=1)
    rep["falsifier"] = {
        "rule": ("a combined cell must beat the best single cell OUT OF SAMPLE by "
                 "more than the added degrees of freedom buy"),
        "best_single_oos_delta_r": d_single,
        "best_joint_oos_delta_r": d_joint,
        "joint_minus_single_oos_r": d_joint - d_single,
        "extra_params": extra_params,
        "joint_pool_size": joint["pool_size"],
        "single_pool_size": singles["pool_size"],
        "grid_ratio": (joint["pool_size"] / singles["pool_size"]
                       if singles["pool_size"] else None),
        # The joint grid is selected over ~10x more cells, so it wins in-sample by
        # construction. Requiring it to also win OOS, and by more than zero, is the
        # weakest honest form of the rule; it is reported with the ratio so a reader
        # can see how much selection pressure produced any margin.
        "joint_beats_single_oos": bool(d_joint > d_single),
    }
    verdict = "no_cell_beats_baseline"
    if d_joint > 0 and joint["oos_folds_positive"] == joint["n_folds"]:
        verdict = "joint_positive_all_folds"
    elif d_joint > 0:
        verdict = "joint_positive_some_folds"
    rep["verdict"] = verdict
    return rep


def _selftest() -> int:
    checks = []

    def check(n, ok, d=""):
        checks.append((n, bool(ok), d))

    # A panel where banking at +0.5R is UNAMBIGUOUSLY right: every trade runs to
    # +0.6R and then reverses to -1R. If the sweep cannot find that, it cannot find
    # anything.
    rows = []
    for t in range(120):
        for b in range(1, 6):
            rows.append({"trade_id": t, "feat_bars_in_trade": float(b),
                         "feat_upnl_r": 0.6 if b >= 2 else 0.1,
                         "feat_running_mae_r": 0.0,
                         "feat_dist_to_stop_atr": 5.0,
                         "trade_realized_r": -1.0,
                         "decision_time": f"2024-01-{1 + t % 28:02d}T00:00:00Z"})
    rep = sweep(rows, {"symbol": "SYNTH"}, cost_r=0.0, n_folds=4)
    check("planted_bank_lever_found", rep["singles"]["oos_delta_r_total"] > 0,
          str(rep["singles"]["oos_delta_r_total"]))
    check("planted_selects_a_bank_cell",
          all(f["selected"].startswith("bank") for f in rep["singles"]["per_fold"]),
          str([f["selected"] for f in rep["singles"]["per_fold"]]))

    # ...and a panel where NO lever helps: every trade rises monotonically to +2R.
    # An exit rule can only ever cut that short, so the sweep must NOT report a win.
    rows2 = []
    for t in range(120):
        for b in range(1, 6):
            rows2.append({"trade_id": t, "feat_bars_in_trade": float(b),
                          "feat_upnl_r": 0.4 * b,
                          "feat_running_mae_r": 0.0,
                          "feat_dist_to_stop_atr": 5.0,
                          "trade_realized_r": 2.0,
                          "decision_time": f"2024-01-{1 + t % 28:02d}T00:00:00Z"})
    rep2 = sweep(rows2, {"symbol": "SYNTH2"}, cost_r=0.0, n_folds=4)
    check("no_lever_helps_on_monotone_panel",
          rep2["joint"]["oos_delta_r_total"] <= 1e-9,
          str(rep2["joint"]["oos_delta_r_total"]))
    check("monotone_panel_verdict_is_negative",
          rep2["verdict"] == "no_cell_beats_baseline", str(rep2["verdict"]))

    # A never-firing cell must be reported as never-firing, not as a break-even win.
    never = {"tag": "x", "kind": "single", "n_params": 1, "mode": "or",
             "conds": [_cond_bank(999.0)]}
    tr = trades_from_panel(rows)
    res = apply_cell(tr, never, 0.0)
    check("never_fires_is_visible", res["fired"] == 0 and res["fired_frac"] == 0.0,
          str(res))
    check("never_fires_equals_baseline",
          abs(res["total_r"] - sum(t["base_r"] for t in tr)) < 1e-9, str(res))

    # Cost must reduce a firing cell's total, or "net of cost" means nothing.
    c1 = apply_cell(tr, {"tag": "b", "kind": "single", "n_params": 1, "mode": "or",
                         "conds": [_cond_bank(0.5)]}, 0.0)
    c2 = apply_cell(tr, {"tag": "b", "kind": "single", "n_params": 1, "mode": "or",
                         "conds": [_cond_bank(0.5)]}, 0.05)
    check("cost_reduces_total", c2["total_r"] < c1["total_r"],
          f"{c1['total_r']} vs {c2['total_r']}")

    # Folds must be strictly forward: no test trade may precede its train block.
    fs = _folds(tr, 4)
    check("folds_are_forward",
          all(max(str(x["t0"]) for x in trn) <= min(str(x["t0"]) for x in tst)
              for trn, tst in fs) if fs else False,
          f"{len(fs)} folds")

    passed = sum(1 for _, ok, _ in checks if ok)
    for n, ok, d in checks:
        if not ok:
            print(f"FAIL {n}: {d}")
    print(f"e3_joint_lever_sweep selftest: {passed}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--panel")
    p.add_argument("--cost-r", type=float, default=0.0)
    p.add_argument("--n-folds", type=int, default=4)
    p.add_argument("--out")
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args(argv)
    if a.selftest:
        return _selftest()
    if not a.panel:
        p.error("--panel is required unless --selftest")

    rows, man = load_panel(Path(a.panel))
    rep = sweep(rows, man, cost_r=a.cost_r, n_folds=a.n_folds)
    rep["panel_path"] = str(a.panel)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(rep, indent=2, sort_keys=True))

    pop = rep["population"]
    print(f"E3 screen [{rep.get('symbol')}] {pop['n_trades']} trades · "
          f"baseline {pop['baseline_total_r']:+.2f}R "
          f"(mean {pop['baseline_mean_r']:+.4f}R) · cost_r={a.cost_r}")
    if rep.get("error"):
        print("  ERROR:", rep["error"]); return 0
    for name in ("singles", "joint"):
        b = rep[name]
        print(f"  {name:8s} pool={b['pool_size']:4d}  OOS dR {b['oos_delta_r_total']:+8.3f}  "
              f"folds+ {b['oos_folds_positive']}/{b['n_folds']}")
        for f in b["per_fold"]:
            print(f"      fold sel={f['selected']:26s} IS {f['is_delta_r']:+7.2f}  "
                  f"OOS {f['oos_delta_r']:+7.2f}  fired {f['oos_fired_frac']:.2f}  n={f['oos_n']}")
    fa = rep["falsifier"]
    print(f"  FALSIFIER: joint {fa['best_joint_oos_delta_r']:+.3f} vs single "
          f"{fa['best_single_oos_delta_r']:+.3f} -> {fa['joint_minus_single_oos_r']:+.3f}R "
          f"over {fa['grid_ratio']:.1f}x the grid · joint_beats_single={fa['joint_beats_single_oos']}")
    print(f"  VERDICT: {rep['verdict']}   (SCREEN, not the M20 gate)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
