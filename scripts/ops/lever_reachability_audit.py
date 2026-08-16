#!/usr/bin/env python3
"""Is a declared R-threshold exit lever REACHABLE under the leg's own TP cap?

The donchian/pullback/fade/squeeze live units clamp take-profit to
``min(entry*(1+0.099), entry + tp_r*risk)`` (``_TP_SENTINEL_CAP_PCT``), so with
``tp_r: 50.0`` the venue-sentinel cap always binds and the highest MFE a trade
can print before its TP fills is::

    cap_R = 0.099 * entry / risk = 0.099 / (risk/entry)

A lever that arms on the since-entry favourable extreme reaching ``arm_r`` R
therefore CANNOT FIRE on any leg where ``arm_r > cap_R``. It is declared,
Tier-3-approved, visible in YAML — and inert. Nothing in the repo asserts the
relationship, so an inert lever is indistinguishable from an armed one that
simply has not triggered yet.

Motivating instance (BL-20260816-TRAIL-DECAY-ARM-R-SITS-ABOVE-THE-VENUE-TP-CAP):
``xrp_pullback_2h`` declares ``trail_decay_arm_r: 4.49`` while the live trade
open since 2026-07-29 has ``cap_R = 3.92`` — so the trail ran at its base mult
for ~18 days and ``trail_decay_tight_mult`` never applied.

WHAT THIS SCRIPT DOES NOT DO: it does not decide a value. Every arm_r is Tier-3.
It reports reachability so the operator can decide, and it refuses to grade what
it has not measured.

Usage
-----
    python3 scripts/ops/lever_reachability_audit.py
    python3 scripts/ops/lever_reachability_audit.py --journal-json trades.json
    python3 scripts/ops/lever_reachability_audit.py --json

``--journal-json`` takes the payload of
``/api/diag/journal?table=trades&limit=1000`` (or any list of rows carrying
``entry_price`` / ``stop_loss`` / ``strategy_name``). WITHOUT it every leg
grades ``unmeasured`` — the script reports the risk/entry ratio each lever
NEEDS, never a verdict it cannot support.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "ml"))

# The live clamp. Mirrored from src/units/strategies/{trend_donchian,
# htf_pullback_trend_2h}.py::_TP_SENTINEL_CAP_PCT; a test pins the agreement so
# this constant cannot drift away from the one production clamps with.
LIVE_TP_CAP_PCT = 0.099

# Families whose live units carry the clamp (the sweep harness's own set —
# imported below when available so there is exactly one definition).
FALLBACK_CAPPED_FAMILIES = {"donchian", "pullback", "fade", "squeeze"}

# Levers that arm by REACHING an MFE-in-R threshold. A "below R" gate
# (stale_exit_below_r) is the opposite direction and is deliberately absent.
REACH_GATE_KEYS = ("trail_decay_arm_r", "giveback_min_mfe_r")


def _capped_families() -> set:
    try:
        sys.path.insert(0, str(REPO / "scripts" / "research"))
        from m20_fleet_exit_sweep import LIVE_TP_CAPPED_FAMILIES  # noqa
        return set(LIVE_TP_CAPPED_FAMILIES)
    except Exception:
        return set(FALLBACK_CAPPED_FAMILIES)


def _family_of(strategy: str) -> str:
    try:
        from build_exit_head_dataset import family_of
        return family_of(strategy)
    except Exception:  # pragma: no cover - import-shape fallback only
        s = (strategy or "").lower()
        for tag in ("donchian", "pullback", "squeeze", "fade"):
            if tag in s:
                return tag
        return s or "unknown"


def required_risk_pct(arm_r: float, cap_pct: float = LIVE_TP_CAP_PCT) -> Optional[float]:
    """The risk/entry ratio at which ``cap_R == arm_r``.

    A trade whose risk/entry is ABOVE this cannot reach the lever; below it,
    it can. Returns None for a non-positive arm (an undeclared lever).
    """
    if not arm_r or arm_r <= 0:
        return None
    return cap_pct / arm_r


def cap_r(risk_over_entry: float, cap_pct: float = LIVE_TP_CAP_PCT) -> Optional[float]:
    """The highest MFE in R a trade can print before its capped TP fills."""
    if not risk_over_entry or risk_over_entry <= 0:
        return None
    return cap_pct / risk_over_entry


def observed_risk_ratios(rows: Iterable[dict], strategy: str) -> List[float]:
    """risk/entry for every row of ``strategy`` that carries BOTH prices.

    A row missing either price is DROPPED, never defaulted — a fabricated 0
    risk would make every lever look reachable.
    """
    out: List[float] = []
    for r in rows or []:
        if (r.get("strategy_name") or r.get("strategy")) != strategy:
            continue
        try:
            entry = float(r.get("entry_price"))
            sl = float(r.get("stop_loss"))
        except (TypeError, ValueError):
            continue
        if entry <= 0 or sl <= 0:
            continue
        ratio = abs(entry - sl) / entry
        if ratio > 0:
            out.append(ratio)
    return out


def grade_leg(name: str, cfg: dict, rows: Optional[List[dict]] = None,
              cap_pct: float = LIVE_TP_CAP_PCT) -> List[Dict[str, Any]]:
    """One record per declared reach-gate on ``name``.

    ``reachability`` is never collapsed:
      * ``unmeasured``  — no usable risk observations. NOT "reachable".
      * ``cap_unknown`` — the leg's family is not a known capped one, so there
        may be no ceiling at all. NOT "reachable" either.
      * ``reachable``   — observed cap_R clears arm_r on at least one trade.
      * ``inert``       — every observed trade's cap_R sits BELOW arm_r.
    """
    fam = _family_of(name)
    capped = fam in _capped_families()
    ratios = observed_risk_ratios(rows or [], name)
    caps = [c for c in (cap_r(x, cap_pct) for x in ratios) if c is not None]

    recs: List[Dict[str, Any]] = []
    for key in REACH_GATE_KEYS:
        arm = cfg.get(key)
        try:
            arm = float(arm)
        except (TypeError, ValueError):
            continue
        if arm <= 0:
            continue
        need = required_risk_pct(arm, cap_pct)
        rec: Dict[str, Any] = {
            "strategy": name, "family": fam, "lever": key, "arm_r": arm,
            "cap_applies": capped,
            "required_risk_pct": None if need is None else round(need * 100, 4),
            "atr_stop_mult": cfg.get("atr_stop_mult"),
            "tp_r": cfg.get("tp_r"),
            "observations": len(caps),
            "cap_r_p10": None, "cap_r_p50": None, "cap_r_p90": None,
            "reach_share_pct": None,
        }
        if not capped:
            rec["reachability"] = "cap_unknown"
        elif not caps:
            rec["reachability"] = "unmeasured"
        else:
            srt = sorted(caps)
            rec["cap_r_p10"] = round(srt[max(0, int(0.10 * (len(srt) - 1)))], 3)
            rec["cap_r_p50"] = round(statistics.median(srt), 3)
            rec["cap_r_p90"] = round(srt[int(0.90 * (len(srt) - 1))], 3)
            reach = sum(1 for c in caps if c >= arm)
            rec["reach_share_pct"] = round(100.0 * reach / len(caps), 1)
            rec["reachability"] = "reachable" if reach else "inert"
        recs.append(rec)
    return recs


def audit(strategies: dict, rows: Optional[List[dict]] = None,
          cap_pct: float = LIVE_TP_CAP_PCT) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for name, cfg in (strategies or {}).items():
        if not isinstance(cfg, dict):
            continue
        if not cfg.get("enabled"):
            continue
        if cfg.get("execution", "live") != "live":
            continue
        out.extend(grade_leg(name, cfg, rows, cap_pct))
    return sorted(out, key=lambda r: (-r["arm_r"], r["strategy"]))


def _load_strategies(path: Path) -> dict:
    import yaml
    doc = yaml.safe_load(path.read_text()) or {}
    return doc.get("strategies", doc)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(REPO / "config" / "strategies.yaml"))
    ap.add_argument("--journal-json", default=None,
                    help="/api/diag/journal?table=trades payload (list, or an "
                         "envelope carrying `rows`/`trades`)")
    ap.add_argument("--cap-pct", type=float, default=LIVE_TP_CAP_PCT)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    rows: List[dict] = []
    if a.journal_json:
        payload = json.loads(Path(a.journal_json).read_text())
        if isinstance(payload, dict):
            rows = payload.get("rows") or payload.get("trades") or []
        else:
            rows = payload

    recs = audit(_load_strategies(Path(a.config)), rows, a.cap_pct)
    if a.json:
        print(json.dumps({"cap_pct": a.cap_pct, "journal_rows": len(rows),
                          "levers": recs}, indent=2))
        return 0

    print(f"Reach-gate reachability under a {a.cap_pct:.1%} TP cap "
          f"({len(recs)} declared levers on live+enabled legs; "
          f"{len(rows)} journal rows supplied)\n")
    hdr = (f"{'leg':30} {'lever':22} {'arm_R':>6} {'need risk%':>10} "
           f"{'obs':>4} {'capR p50':>9} {'reach%':>7}  verdict")
    print(hdr)
    print("-" * len(hdr))
    for r in recs:
        p50 = "—" if r["cap_r_p50"] is None else format(r["cap_r_p50"], ".2f")
        reach = "—" if r["reach_share_pct"] is None else format(r["reach_share_pct"], ".0f")
        need = 0.0 if r["required_risk_pct"] is None else r["required_risk_pct"]
        print(f"{r['strategy'][:30]:30} {r['lever'][:22]:22} {r['arm_r']:>6.2f} "
              f"{need:>10.3f} {r['observations']:>4} {p50:>9} {reach:>7}"
              f"  {r['reachability']}")
    inert = [r for r in recs if r["reachability"] == "inert"]
    unmeasured = [r for r in recs if r["reachability"] in ("unmeasured", "cap_unknown")]
    print(f"\ninert: {len(inert)}   ungraded: {len(unmeasured)} "
          f"(unmeasured/cap_unknown are NOT 'reachable')")
    for r in inert:
        print(f"  INERT  {r['strategy']}/{r['lever']}: arm {r['arm_r']}R > cap_R "
              f"p90 {r['cap_r_p90']}R over {r['observations']} trades")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
