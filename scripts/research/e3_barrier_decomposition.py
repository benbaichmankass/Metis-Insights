#!/usr/bin/env python3
"""Is a `label_hold` association EDGE, or a restatement of the triple barrier?

The E2 horizon arm licensed E3 on `label_hold` at a long horizon and attached this
limit to the licence (`docs/research/e2-horizon-arm-2026-08-20.md` § 8.2):

    Part of this may be barrier geometry rather than edge. At a longer horizon more
    trades reach a barrier, and `dist_to_stop_atr` mechanically bears on WHICH
    barrier: far from the stop => SL less likely, TP more likely => P(hold beats
    exiting now) rises. Whether that is an exploitable lever or a restatement of the
    triple-barrier structure is not a question E2 can answer.

This module answers it, because a lever must not be built on the unresolved version.
`label_hold = 1[advantage_r > 0]` and `advantage_r` is dominated by which barrier the
trade runs into, so the pooled association between any feature and `label_hold` mixes
two things that a lever needs kept apart:

  * BETWEEN strata -- how the feature shifts which barrier gets hit. Mechanical: it
    is a statement about where price sits relative to two fixed levels.
  * WITHIN a stratum -- whether the feature says anything once the barrier outcome is
    held fixed. This is the part that is not implied by the geometry.

⚠️ **THE STRATIFIED NUMBER IS A DIAGNOSTIC, NOT A LEVER LICENCE.** `touch` is the
barrier the trade LATER reached, so conditioning on it conditions on the future. No
live lever can do that, and a within-stratum coefficient is therefore not a tradeable
effect size. What the stratification IS good for is the mechanism question: if the
pooled association reverses or vanishes inside every stratum, then the pooled signal
is composition -- it carries the geometry and nothing else -- and a lever reading it
is a re-parameterisation of the bracket rather than an exit mechanism. That
distinction decides whether E3 builds a lever or sends the finding back to entry
geometry, which is why it runs BEFORE any lever.

Reports, per feature: the pooled Spearman, the per-stratum Spearman, the
size-weighted mean of the within-stratum values, and a `simpson` flag when the pooled
sign disagrees with every stratum that carries enough rows to speak. Also the
entropy decomposition -- how much of `label_hold` the barrier alone accounts for --
so the share of the label that is geometry is stated rather than argued.

Observe-only, Tier-1: reads a panel, writes a report, touches nothing live.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in [here.parent] + list(here.parents):
        if (p / "scripts").is_dir() and (p / "src").is_dir():
            return p
    return here.parent.parent


_ROOT = _repo_root()
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.research.e2_feature_information import (  # noqa: E402
    _dense_feats,
    load_panel,
    spearman,
)

STRATUM_COL = "touch"
MIN_STRATUM_ROWS = 50


def _binary_entropy(q: float) -> float:
    if q <= 0.0 or q >= 1.0:
        return 0.0
    return -(q * math.log2(q) + (1.0 - q) * math.log2(1.0 - q))


def entropy_decomposition(rows: Sequence[Dict[str, Any]], label: str,
                          stratum: str = STRATUM_COL) -> Dict[str, Any]:
    """How much of the label does the terminal barrier alone account for?

    Reported because "the label is mostly geometry" is a quantitative claim and
    deserves a number rather than an adjective.
    """
    vals = [r for r in rows if r.get(label) is not None and r.get(stratum) is not None]
    n = len(vals)
    if not n:
        return {"n": 0}
    p = sum(float(r[label]) for r in vals) / n
    h0 = _binary_entropy(p)
    by: Dict[Any, List[float]] = defaultdict(list)
    for r in vals:
        by[r[stratum]].append(float(r[label]))
    h_cond = sum(len(v) / n * _binary_entropy(sum(v) / len(v)) for v in by.values())
    return {
        "n": n,
        "base_rate": p,
        "H_label_bits": h0,
        "H_label_given_stratum_bits": h_cond,
        "explained_fraction": ((h0 - h_cond) / h0) if h0 > 0 else None,
        "strata": {
            str(k): {"n": len(v), "base_rate": sum(v) / len(v)} for k, v in sorted(
                by.items(), key=lambda kv: -len(kv[1]))
        },
    }


def decompose(rows: List[Dict[str, Any]], manifest: Optional[Dict[str, Any]], *,
              label: str = "label_hold", stratum: str = STRATUM_COL,
              min_stratum_rows: int = MIN_STRATUM_ROWS,
              features: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    feats = list(features) if features else [
        f for f in _dense_feats(rows, manifest) if f.startswith("feat_")
    ]
    usable = [r for r in rows if r.get(label) is not None and r.get(stratum) is not None]

    by_stratum: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    for r in usable:
        by_stratum[r[stratum]].append(r)

    def _sp(rs: Sequence[Dict[str, Any]], f: str) -> Optional[float]:
        xs, ys = [], []
        for r in rs:
            v, y = r.get(f), r.get(label)
            if v is None or y is None:
                continue
            try:
                xs.append(float(v)); ys.append(float(y))
            except (TypeError, ValueError):
                continue
        if len(xs) < max(3, min_stratum_rows):
            return None
        return spearman(xs, ys)

    out: List[Dict[str, Any]] = []
    for f in feats:
        pooled = _sp(usable, f)
        per: Dict[str, Optional[float]] = {}
        wsum = 0.0
        wtot = 0.0
        for k, rs in by_stratum.items():
            v = _sp(rs, f)
            per[str(k)] = v
            if v is not None:
                wsum += v * len(rs)
                wtot += len(rs)
        within = (wsum / wtot) if wtot else None
        speaking = [v for v in per.values() if v is not None]
        # A `simpson` flag needs the pooled sign to disagree with EVERY stratum that
        # carries enough rows to speak -- one dissenting stratum is not a reversal,
        # and calling it one would be the same over-reading this module exists to
        # stop. Near-zero pooled values are excluded: a sign that is noise has no
        # disagreement to report.
        simpson = bool(
            pooled is not None and abs(pooled) > 0.02 and len(speaking) >= 2
            and all((v < 0) if pooled > 0 else (v > 0)
                    for v in speaking if abs(v) > 0.02)
            and any(abs(v) > 0.02 for v in speaking)
        )
        out.append({
            "feature": f,
            "pooled": pooled,
            "per_stratum": per,
            "within_stratum_weighted": within,
            "abs_within_exceeds_abs_pooled": bool(
                pooled is not None and within is not None
                and abs(within) > abs(pooled)),
            "sign_reversal_vs_all_strata": simpson,
        })
    out.sort(key=lambda d: (d["pooled"] is None, -abs(d["pooled"] or 0.0)))

    return {
        "step": "E3-precondition-barrier-decomposition",
        "label": label,
        "stratum": stratum,
        "symbol": (manifest or {}).get("symbol"),
        "timeframe": (manifest or {}).get("timeframe"),
        "population": {
            "n_rows_total": len(rows),
            "n_rows_usable": len(usable),
            "n_trades": len({r.get("trade_id") for r in usable}),
            "n_features": len(feats),
        },
        "caveat": (
            "per_stratum conditions on the barrier the trade LATER reached, which no "
            "live lever can observe. These are mechanism diagnostics, never effect "
            "sizes for a lever."
        ),
        "entropy": entropy_decomposition(usable, label, stratum),
        "features": out,
        "n_sign_reversals": sum(1 for d in out if d["sign_reversal_vs_all_strata"]),
    }


def _selftest() -> int:
    checks = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    # A PLANTED Simpson's paradox: within each stratum x and y move together
    # NEGATIVELY, while the strata sit at opposite corners so the pooled sign is
    # POSITIVE. If the detector cannot find a planted one it cannot find a real one.
    rows = []
    for i in range(400):
        rows.append({"trade_id": f"a{i}", "touch": "sl", "label_hold": 0,
                     "feat_x": 0.0 + (i % 40) * 0.001, "feat_flat": 1.0})
    # stratum sl: label all 0 -> no variance, cannot speak (guards the min-rows path)
    for i in range(400):
        y = 1 if i % 2 == 0 else 0
        # within 'time': higher x -> lower label
        rows.append({"trade_id": f"b{i}", "touch": "time", "label_hold": y,
                     "feat_x": 10.0 - y * 2.0 + (i % 7) * 0.01, "feat_flat": 1.0})
    for i in range(400):
        y = 1 if i % 10 else 0
        rows.append({"trade_id": f"c{i}", "touch": "tp", "label_hold": y,
                     "feat_x": 20.0 - y * 2.0 + (i % 7) * 0.01, "feat_flat": 1.0})
    man = {"dense_feature_cols": ["feat_x", "feat_flat"], "symbol": "SYNTH",
           "timeframe": "15m"}
    rep = decompose(rows, man, features=["feat_x", "feat_flat"])
    fx = next(d for d in rep["features"] if d["feature"] == "feat_x")
    check("planted_pooled_is_positive", (fx["pooled"] or 0) > 0.1, str(fx["pooled"]))
    check("planted_within_is_negative", (fx["within_stratum_weighted"] or 0) < 0,
          str(fx["within_stratum_weighted"]))
    check("planted_reversal_detected", fx["sign_reversal_vs_all_strata"], str(fx))

    # A constant feature must not be reported as a reversal.
    ff = next(d for d in rep["features"] if d["feature"] == "feat_flat")
    check("constant_feature_no_reversal", not ff["sign_reversal_vs_all_strata"], str(ff))

    # Entropy: a label perfectly determined by the stratum is 100% explained.
    det = [{"touch": "sl", "label_hold": 0} for _ in range(100)] + \
          [{"touch": "tp", "label_hold": 1} for _ in range(100)]
    e = entropy_decomposition(det, "label_hold")
    check("entropy_fully_explained", abs((e["explained_fraction"] or 0) - 1.0) < 1e-9,
          str(e))
    # ...and one independent of it is 0% explained.
    ind = [{"touch": "sl" if i % 2 else "tp", "label_hold": i % 4 // 2}
           for i in range(400)]
    e2 = entropy_decomposition(ind, "label_hold")
    check("entropy_independent_explains_nothing",
          abs(e2["explained_fraction"] or 0) < 0.02, str(e2))

    passed = sum(1 for _, ok, _ in checks if ok)
    for n, ok, d in checks:
        if not ok:
            print(f"FAIL {n}: {d}")
    print(f"e3_barrier_decomposition selftest: {passed}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--panel")
    p.add_argument("--label", default="label_hold")
    p.add_argument("--stratum", default=STRATUM_COL)
    p.add_argument("--min-stratum-rows", type=int, default=MIN_STRATUM_ROWS)
    p.add_argument("--top", type=int, default=8)
    p.add_argument("--out")
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args(argv)
    if a.selftest:
        return _selftest()
    if not a.panel:
        p.error("--panel is required unless --selftest")

    rows, man = load_panel(Path(a.panel))
    rep = decompose(rows, man, label=a.label, stratum=a.stratum,
                    min_stratum_rows=a.min_stratum_rows)
    rep["panel_path"] = str(a.panel)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(rep, indent=2, sort_keys=True))

    e = rep["entropy"]
    print(f"{rep['symbol']} {a.label} · {rep['population']['n_rows_usable']} rows / "
          f"{rep['population']['n_trades']} trades")
    print(f"  barrier explains {100 * (e.get('explained_fraction') or 0):.1f}% of "
          f"{a.label} entropy · strata: " +
          " ".join(f"{k}={v['n']}({v['base_rate']:.3f})" for k, v in e["strata"].items()))
    keys = list(e["strata"].keys())
    print(f"  {'feature':32s} {'pooled':>8s} {'within':>8s} " +
          " ".join(f"{k:>8s}" for k in keys) + "  flag")
    for d in rep["features"][:a.top]:
        per = d["per_stratum"]
        cells = " ".join(
            (f"{per[k]:8.4f}" if per.get(k) is not None else f"{'—':>8s}") for k in keys)
        print(f"  {d['feature']:32s} {(d['pooled'] or 0):8.4f} "
              f"{(d['within_stratum_weighted'] or 0):8.4f} {cells}  "
              f"{'REVERSAL' if d['sign_reversal_vs_all_strata'] else ''}")
    print(f"  sign reversals: {rep['n_sign_reversals']}/{len(rep['features'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
