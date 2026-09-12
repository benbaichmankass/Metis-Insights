#!/usr/bin/env python3
"""M20 U2b — the break-even ratchet amends order_packages.sl, so R off it is
inflated ON WINNERS ONLY. Identify it, recover the entry risk, re-measure.

# wiring: manual-only — a research session runs this against a journal pull.

WHAT THIS IS. MI-277 measured a stop distance of exactly 0.00150000 of entry on
48 of 180 bybit_1 rows, could not find its source in ``src/``, and INFERRED it
was a floor. It is not a floor and it is not applied at entry: it is
``_base.monitor_breakeven_sl`` with ``be_offset_bps: 15`` -- 15 bp IS
0.00150000 -- writing the break-even stop back into ``order_packages.sl``.

WHY IT MATTERS MORE THAN A MISLABELLED CONSTANT. The ratchet fires only once a
trade reaches ``one_r_threshold = 1.0``, so it shrinks the risk denominator
**selectively on winners** and never on losers. Every R computed off
``order_packages.sl`` is therefore inflated on the winner side and correct on
the loser side -- which is exactly the asymmetry a winner-vs-loser comparison
depends on. MI-277 switched TO this field precisely because
``trades.stop_loss`` is the trailed stop; that switch was right in direction
and the destination is also not entry-frozen.

THE RECOVERY, AND ITS CONTROL. ``tp`` is NOT amended, and ict_scalp builds
``tp = entry ± tp_at_r × risk`` from the SAME ``risk`` as the stop, so
``|tp - entry| / tp_at_r`` IS the entry risk. That is a claim this script
CHECKS rather than asserts: on the un-ratcheted rows the recovery must
reproduce ``|entry - sl|``, and if it does not, the run FAILS. Without that
control the recovery would be a second unverified basis replacing the first.

⚠️ SCOPE, STATED BECAUSE IT IS NARROW. The recovery is ict_scalp-specific --
it needs a leg whose target is a fixed multiple of the entry risk. vwap and
turtle_soup ALSO call monitor_breakeven_sl (asserted in U1's inventory) and are
NOT graded here; trend_donchian and htf_pullback_trend_2h do not call it. A leg
absent from this window's ratcheted set is not thereby immune.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

#: `be_offset_bps: 15` on every ict_scalp leg -> the ratcheted stop sits exactly
#: this fraction from entry. Compared with an exact tolerance, not a band: the
#: whole point is that the value is a CONSTANT the ratchet writes, not a
#: distribution, and a band would sweep in genuinely-tight entry stops.
RATCHET_RISK_FRACTION = 0.0015
RATCHET_EPS = 1e-9
#: config-exact for every ict_scalp_* leg (config/strategies.yaml::tp_at_r)
ICT_SCALP_TP_AT_R = 1.5
#: the recovery must reproduce |entry-sl| this closely on UN-ratcheted rows
CONTROL_MAX_REL_ERR = 1e-6
CONTROL_MIN_PASS_FRAC = 0.95


def _pkg_geom(pkg: Dict[str, Any]) -> Optional[Tuple[float, float, float, str]]:
    e, sl, tp = pkg.get("entry"), pkg.get("sl"), pkg.get("tp")
    d = str(pkg.get("direction") or "").lower()
    if not all([e, sl, tp]) or d not in ("long", "short"):
        return None
    return e, sl, tp, d


def is_ratcheted(pkg: Dict[str, Any]) -> Optional[bool]:
    g = _pkg_geom(pkg)
    if not g:
        return None
    e, sl, _tp, _d = g
    if e <= 0:
        return None
    return abs(abs(e - sl) / e - RATCHET_RISK_FRACTION) < RATCHET_EPS


def entry_risk(pkg: Dict[str, Any], tp_at_r: float = ICT_SCALP_TP_AT_R) -> Optional[float]:
    """The ENTRY risk, recovered from the frozen tp. None when ungradeable."""
    g = _pkg_geom(pkg)
    if not g:
        return None
    e, _sl, tp, _d = g
    r = abs(tp - e) / tp_at_r
    return r if r > 0 else None


def run_control(scalp_pkgs: List[Dict[str, Any]]) -> Tuple[bool, str]:
    """Assert the recovery on rows the ratchet has NOT touched. FAILS the run."""
    errs = []
    for pkg in scalp_pkgs:
        if is_ratcheted(pkg):
            continue
        g = _pkg_geom(pkg)
        rec = entry_risk(pkg)
        if not g or rec is None:
            continue
        e, sl, _tp, _d = g
        errs.append(abs(abs(e - sl) - rec) / max(rec, 1e-12))
    if not errs:
        return False, "CONTROL COULD NOT RUN — no un-ratcheted rows. That is not a pass."
    ok = sum(1 for x in errs if x < CONTROL_MAX_REL_ERR)
    frac = ok / len(errs)
    msg = (f"control: |tp-entry|/{ICT_SCALP_TP_AT_R} reproduces |entry-sl| on "
           f"{ok}/{len(errs)} un-ratcheted rows ({frac:.1%}); "
           f"median rel err {statistics.median(errs):.3e}")
    return frac >= CONTROL_MIN_PASS_FRAC, msg


def r_at_exit(trade: Dict[str, Any], pkg: Dict[str, Any], *, repair: bool) -> Optional[float]:
    g = _pkg_geom(pkg)
    px = trade.get("exit_price")
    if not g or not px:
        return None
    e, sl, _tp, _d = g
    d = str(trade.get("direction") or "").lower()
    if d not in ("long", "short"):
        return None
    risk = abs(e - sl)
    if repair and str(trade.get("strategy_name") or "").startswith("ict_scalp") and is_ratcheted(pkg):
        risk = entry_risk(pkg) or risk
    if risk <= 0:
        return None
    return ((px - e) if d == "long" else (e - px)) / risk


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trades", required=True)
    ap.add_argument("--packages", required=True)
    ap.add_argument("--split", default="2026-08-30T08:53:19", help="MI-277's era boundary")
    ap.add_argument("--account", default="bybit_1", help="account for the MI-277 reproduction")
    args = ap.parse_args()

    trades = json.loads(Path(args.trades).read_text())
    pkgs_list = json.loads(Path(args.packages).read_text())
    pkgs = {str(r.get("order_package_id")): r for r in pkgs_list}
    scalp = [r for r in pkgs_list if str(r.get("strategy_name") or "").startswith("ict_scalp")
             and _pkg_geom(r)]

    ok, msg = run_control(scalp)
    print(msg)
    if not ok:
        print("::error::the entry-risk recovery FAILED its own control — do not use these numbers.",
              file=sys.stderr)
        return 2

    rat = [p for p in scalp if is_ratcheted(p)]
    print(f"\nratchet identification over {len(scalp)} ict_scalp packages:")
    print(f"  at risk/entry == {RATCHET_RISK_FRACTION:.8f} exactly : {len(rat)}")
    off = [p for p in scalp if not is_ratcheted(p)]
    def ratio(p):
        e, sl, tp, d = _pkg_geom(p)
        return ((tp - e) if d == "long" else (e - tp)) / abs(e - sl)
    if off:
        print(f"  median (tp-entry)/|entry-sl| off-ratchet : {statistics.median([ratio(p) for p in off]):.3f} "
              f"(declared tp_at_r = {ICT_SCALP_TP_AT_R})")
    if rat:
        print(f"  median (tp-entry)/|entry-sl| ON ratchet  : {statistics.median([ratio(p) for p in rat]):.3f}")
        und = [entry_risk(p) / abs(_pkg_geom(p)[0] - _pkg_geom(p)[1]) for p in rat]
        print(f"  |entry-sl| understates the entry risk by a median {statistics.median(und):.2f}x")

    def population(pred) -> List[Dict[str, Any]]:
        return [r for r in trades
                if r.get("status") == "closed" and not r.get("is_backtest")
                and r.get("pnl") is not None
                and not str(r.get("strategy_name") or "").startswith("pairs_")
                and pred(r)]

    for label, pred in (
        (f"MI-277's population ({args.account}, all legs)", lambda r: r.get("account_id") == args.account),
        ("ict_scalp_* family, ALL accounts", lambda r: str(r.get("strategy_name") or "").startswith("ict_scalp")),
    ):
        rows = population(pred)
        print(f"\n=== {label} — n={len(rows)} ===")
        print(f"{'era/side':14s} {'n':>4s} {'med R (pkg sl)':>15s} {'med R (repaired)':>18s}")
        med = {}
        for era, sel in (("pre", lambda r: (r.get("created_at") or "")[:19] < args.split),
                         ("post", lambda r: (r.get("created_at") or "")[:19] >= args.split)):
            for side, f in (("win", lambda r: r["pnl"] > 0), ("loss", lambda r: r["pnl"] <= 0)):
                sub = [r for r in rows if sel(r) and f(r)]
                a, b = [], []
                for r in sub:
                    pkg = pkgs.get(str(r.get("order_package_id")))
                    if not pkg:
                        continue
                    x, y = r_at_exit(r, pkg, repair=False), r_at_exit(r, pkg, repair=True)
                    if x is not None and y is not None:
                        a.append(x)
                        b.append(y)
                if not a:
                    continue
                med[(era, side)] = (statistics.median(a), statistics.median(b))
                print(f"{era + ' ' + side:14s} {len(a):4d} {statistics.median(a):15.3f} {statistics.median(b):18.3f}")
        if ("pre", "win") in med and ("post", "win") in med:
            p, q = med[("pre", "win")], med[("post", "win")]
            print(f"  winner median R post/pre — pkg sl {q[0]/p[0]:.3f} ({(q[0]/p[0]-1)*100:+.0f}%) "
                  f"| repaired {q[1]/p[1]:.3f} ({(q[1]/p[1]-1)*100:+.0f}%)")
        if ("pre", "loss") in med:
            pl, ql = med[("pre", "loss")], med.get(("post", "loss"), (float("nan"),) * 2)
            same = abs(pl[0] - pl[1]) < 1e-12 and abs(ql[0] - ql[1]) < 1e-12
            print(f"  LOSER control identical under both bases: {same}  "
                  f"({pl[1]:.3f} -> {ql[1]:.3f}) — losers never ratchet, so this must be True")
    return 0


if __name__ == "__main__":
    sys.exit(main())
