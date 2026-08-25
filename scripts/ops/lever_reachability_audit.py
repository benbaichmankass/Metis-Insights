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
import re
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "ml"))

# The live clamp. Mirrored from src/units/strategies/{trend_donchian,
# htf_pullback_trend_2h}.py::_TP_SENTINEL_CAP_PCT; a test pins the agreement so
# this constant cannot drift away from the one production clamps with.
# The venue TP clamp -- ONE owner: src/runtime/tp_venue_cap.py. IMPORTED, not
# mirrored. This file used to carry its own `LIVE_TP_CAP_PCT = 0.099`, one of
# thirteen such literals with nothing binding them -- and this repo's own note
# on that was right: "if the live constant moves, this silently keeps measuring
# the OLD book, and the sweep will look correct while doing it". The owner
# imports only `typing`, and src/__init__.py + src/runtime/__init__.py are
# empty, so this adds no heavy dependency.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.runtime.tp_venue_cap import (  # noqa: E402
    CLAMPING_FAMILIES, TP_VENUE_CAP_PCT as LIVE_TP_CAP_PCT)


# Levers that arm by REACHING an MFE-in-R threshold. A "below R" gate
# (stale_exit_below_r) is the opposite direction and is deliberately absent.
REACH_GATE_KEYS = ("trail_decay_arm_r", "giveback_min_mfe_r")


def _capped_families() -> set:
    """The clamping families, from the ONE owner.

    Was a try/except import of the sweep harness's set with a private
    `FALLBACK_CAPPED_FAMILIES` copy behind it -- so a failed import silently
    swapped in a second declaration that nothing checked. Both are gone.
    """
    return set(CLAMPING_FAMILIES)


_BUILDERS_SRC = REPO / "src" / "runtime" / "strategy_signal_builders.py"
_UNITS_DIR = REPO / "src" / "units" / "strategies"


def cap_applies(strategy: str) -> tuple:
    """(capped: bool|None, basis: str) — does this leg's live unit clamp TP?

    THREE outcomes, never two. `None` means "we could not establish it", which
    is NOT "uncapped" — an unproven absence would let a genuinely inert lever
    grade as un-checkable and drop out of the finding.

    Two resolution paths, both MEASURED:

    * ``family`` — the leg's family string is in `LIVE_TP_CAPPED_FAMILIES`.
    * ``builder_unit`` — the family string does not resolve (the equity legs are
      named `qqq_trend_long_1d`, `scha_trend_long_1d`, …), so read which unit
      module the leg's signal builder actually imports `order_package` from and
      check whether THAT module CARRIES `_TP_SENTINEL_CAP_PCT` (it is
      imported from src/runtime/tp_venue_cap.py, not declared, since
      2026-08-25 -- the source-anchored probe is unchanged and still
      correct, but it no longer finds a literal). Verified 2026-08-16:
      `qqq_trend_long_1d_signal_builder` imports it from
      `src.units.strategies.trend_donchian`, which clamps — so those legs are
      capped and the family-only test was under-claiming on all of them.

    Source-text resolution, deliberately: importing `strategy_signal_builders`
    pulls the live runtime into an ops script. A parse miss falls to `None`.
    """
    fam = _family_of(strategy)
    if fam in _capped_families():
        return True, "family"
    try:
        src = _BUILDERS_SRC.read_text()
    except OSError:
        return None, "builders_unreadable"
    m = re.search(rf"^def {re.escape(strategy)}_signal_builder\b", src, re.M)
    if not m:
        return None, "no_builder_found"
    # Scope to this function: up to the next top-level `def`.
    nxt = re.search(r"^def ", src[m.end():], re.M)
    span = src[m.start(): m.end() + (nxt.start() if nxt else len(src))]
    units = re.findall(
        r"from src\.units\.strategies\.([A-Za-z0-9_]+) import[^\n]*order_package",
        span)
    if not units:
        return None, "no_unit_import"
    for unit in units:
        path = _UNITS_DIR / f"{unit}.py"
        try:
            if "_TP_SENTINEL_CAP_PCT" in path.read_text():
                return True, f"builder_unit:{unit}"
        except OSError:
            return None, "unit_unreadable"
    return None, f"unit_has_no_cap_constant:{units[0]}"


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


def observed_risk_ratios(rows: Iterable[dict],
                         strategy: str) -> List[Dict[str, Any]]:
    """DECISION-TIME risk/entry per row, each stamped with the field it came from.

    ⚠️ **`entry - stop_loss` IS NOT RELIABLY THE ENTRY RISK.** A stop is trailed
    and amended over a trade's life, and both the journal `trades.stop_loss` and
    the `order_packages.sl` a consumer reads back can be the CURRENT stop, not the
    one the sizer used. Measured on the live fleet 2026-08-16 (diag #9587): for
    `gld_pullback_1d` and `qqq_trend_long_1d` the two agree to 1.00, for
    `xrp_pullback_2h` the ratio is 0.71, and one `trend_donchian` package read
    5.7x tighter than its own recorded risk. Nothing distinguishes the agreeing
    rows from the diverging ones without checking, and the error runs the
    DANGEROUS way — an understated risk inflates cap_R and makes an inert lever
    look reachable.

    So prefer ``signalLogic.risk_per_unit``, the value the strategy actually
    sized with, and fall back to the price difference only when it is absent —
    recording WHICH on every observation (`basis`), never averaging the two
    silently. A row with neither is DROPPED, never defaulted: a fabricated 0
    risk makes every cap_R infinite and every lever look fine.
    """
    out: List[Dict[str, Any]] = []
    for r in rows or []:
        if (r.get("strategy_name") or r.get("strategy")) != strategy:
            continue
        try:
            entry = float(r.get("entry_price") if r.get("entry_price") is not None
                          else r.get("entry"))
        except (TypeError, ValueError):
            continue
        if entry <= 0:
            continue

        risk, basis = None, None
        logic = r.get("signalLogic") or r.get("signal_logic") or {}
        if isinstance(logic, dict):
            try:
                rpu = float(logic.get("risk_per_unit"))
                if rpu > 0:
                    risk, basis = rpu, "risk_per_unit"
            except (TypeError, ValueError):
                pass
        if risk is None:
            try:
                sl = float(r.get("stop_loss") if r.get("stop_loss") is not None
                           else r.get("sl"))
                if sl > 0 and abs(entry - sl) > 0:
                    risk, basis = abs(entry - sl), "entry_minus_stop"
            except (TypeError, ValueError):
                pass
        if risk is None:
            continue
        out.append({"ratio": risk / entry, "basis": basis})
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
    capped, cap_basis = cap_applies(name)
    obs = observed_risk_ratios(rows or [], name)
    caps = [c for c in (cap_r(o["ratio"], cap_pct) for o in obs) if c is not None]
    # Report the basis MIX, not just a count. A leg measured entirely off the
    # `entry_minus_stop` fallback is a weaker claim than one off `risk_per_unit`
    # (see observed_risk_ratios) and must not read identically.
    n_authoritative = sum(1 for o in obs if o["basis"] == "risk_per_unit")

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
            "cap_applies": capped, "cap_basis": cap_basis,
            "required_risk_pct": None if need is None else round(need * 100, 4),
            "atr_stop_mult": cfg.get("atr_stop_mult"),
            "tp_r": cfg.get("tp_r"),
            "observations": len(caps),
            "risk_basis_risk_per_unit": n_authoritative,
            "risk_basis_entry_minus_stop": len(obs) - n_authoritative,
            "cap_r_p10": None, "cap_r_p50": None, "cap_r_p90": None,
            "reach_share_pct": None,
        }
        if capped is not True:
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
                    help="a rows payload — PREFER /api/bot/order-packages "
                         "(carries signalLogic.risk_per_unit, the decision-time "
                         "risk); /api/diag/journal?table=trades also works but "
                         "its stop_loss may be the TRAILED stop. List, or an "
                         "envelope carrying `rows`/`trades`.")
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
           f"{'obs':>4} {'basis':>9} {'capR p50':>9} {'reach%':>7}  verdict")
    print(hdr)
    print("-" * len(hdr))
    for r in recs:
        p50 = "—" if r["cap_r_p50"] is None else format(r["cap_r_p50"], ".2f")
        reach = "—" if r["reach_share_pct"] is None else format(r["reach_share_pct"], ".0f")
        need = 0.0 if r["required_risk_pct"] is None else r["required_risk_pct"]
        # "rpu/fallback" — a leg measured wholly off the fallback is a weaker
        # claim and must not print identically to one off the sized risk.
        basis = (f"{r['risk_basis_risk_per_unit']}/"
                 f"{r['risk_basis_entry_minus_stop']}")
        print(f"{r['strategy'][:30]:30} {r['lever'][:22]:22} {r['arm_r']:>6.2f} "
              f"{need:>10.3f} {r['observations']:>4} {basis:>9} {p50:>9} {reach:>7}"
              f"  {r['reachability']}")
    print("\nbasis = risk_per_unit / entry_minus_stop  (the fallback can be a "
          "TRAILED or AMENDED stop; its error has no fixed sign, so it can "
          "inflate OR deflate capR — prefer risk_per_unit)")
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
