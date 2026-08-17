#!/usr/bin/env python3
"""Which exit MECHANISMS can each live leg actually use?

`check_lever_reachability.py` (M31 P1) asks whether a DECLARED R-threshold lever
can arm under its own TP cap. This asks the prior question, which nothing asked:
**does the leg's own unit module implement that lever at all?**

Two failure shapes, and they are not the same:

* **orphaned declare** — the YAML declares a key the leg's unit module never
  reads. Silently inert, and INVISIBLE to the reachability guard (which only
  compares arm_r against cap_R). Measured 2026-08-16: **zero instances** over
  18 declaring legs — but that is a result, not an assumption, and it needs
  re-checking whenever a lever moves between modules.
* **family coverage gap** — the module implements no such lever, so the leg
  cannot use the mechanism however it is configured. Measured 2026-08-16: the
  `htf_pullback_trend_2h` family (18 of 47 live legs, 38%) implements exactly
  ONE of the four M20 mechanisms.

Why it matters, concretely: the live XRP short that motivated M31 runs
`xrp_pullback_2h` → `htf_pullback_trend_2h`, whose only mechanism is
`trail_decay` — and on that leg `trail_decay_arm_r: 4.49` sits above its
`cap_R 3.92` for most entries. So the trade had **no working M20 exit
mechanism at all** for 18 days. That is not a mis-declaration (the leg declares
only what its module reads, so it grades `ok` here); it is a coverage gap, and
the two are worth telling apart.

**Reports; decides nothing.** Every arm value and every new declare is Tier-3.

Usage
-----
    python3 scripts/ops/exit_mechanism_coverage.py
    python3 scripts/ops/exit_mechanism_coverage.py --json
    python3 scripts/ops/exit_mechanism_coverage.py --orphans-only   # exit 1 if any
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
_BUILDERS = REPO / "src" / "runtime" / "strategy_signal_builders.py"
_UNITS = REPO / "src" / "units" / "strategies"
_STRATEGIES = REPO / "config" / "strategies.yaml"
_REGISTRY = REPO / "config" / "lever_reachability.json"

# The four M20 exit mechanisms, each by the cfg key(s) its unit module must read
# for the mechanism to exist there at all. A module that reads NONE of a
# mechanism's keys cannot run it, whatever the YAML says.
MECHANISMS: Dict[str, Tuple[str, ...]] = {
    "stale_stop": ("stale_exit_bars",),
    "giveback_stop": ("giveback_r", "giveback_min_mfe_r"),
    "exit_head": ("exit_head", "exit_head_threshold"),
    "trail_decay": ("trail_decay_tight_mult", "trail_decay_arm_r"),
}

# Per (leg, mechanism) state. FIVE values, never collapsed — in particular
# `not_implemented` and `undeclared` are opposite statements about whose choice
# it was, and `unresolved` is "we could not look", never "no".
NOT_IMPLEMENTED = "not_implemented"   # module has no such lever
UNDECLARED = "undeclared"             # module implements it; this leg opts out
DECLARED = "declared"                 # declared; see `reachability` for whether it can fire
ORPHANED = "orphaned"                 # declared, module cannot read it — the defect
UNRESOLVED = "unresolved"             # we could not resolve the leg's unit module


def builder_unit_map(src: str) -> Dict[str, str]:
    """The explicit ``(builder, "unit")`` registration table — authoritative."""
    return {m.group(1): m.group(2) for m in re.finditer(
        r'\(\s*([a-z0-9_]+)_signal_builder\s*,\s*"([A-Za-z0-9_]+)"\s*\)', src)}


def builder_unit_import(src: str, strategy: str) -> Optional[str]:
    """The unit this leg's builder body imports from — the second witness."""
    m = re.search(rf"^def {re.escape(strategy)}_signal_builder\b", src, re.M)
    if not m:
        return None
    nxt = re.search(r"^def ", src[m.end():], re.M)
    span = src[m.start(): m.end() + (nxt.start() if nxt else len(src))]
    found = re.findall(
        r"from src\.units\.strategies\.([A-Za-z0-9_]+) import", span)
    return found[0] if found else None


def unit_of(src: str, strategy: str) -> Tuple[Optional[str], str]:
    """(unit, basis). Two independent witnesses; a CONFLICT resolves to None.

    Guessing between two disagreeing resolutions is how a probe reports a
    confident wrong module — better to say we could not establish it.
    """
    table = builder_unit_map(src).get(strategy)
    imported = builder_unit_import(src, strategy)
    if table and imported and table != imported:
        return None, f"conflict:{table}!={imported}"
    if table:
        return table, "registration_table"
    if imported:
        return imported, "builder_import"
    return None, "no_builder_found"


def module_implements(unit_src: str, mechanism: str) -> bool:
    return any(f'"{k}"' in unit_src for k in MECHANISMS[mechanism])


def _live_strategies(cfg: dict) -> List[Tuple[str, dict]]:
    strats = cfg.get("strategies", cfg)
    return [(n, c) for n, c in sorted(strats.items())
            if isinstance(c, dict) and c.get("enabled", True)
            and str(c.get("execution", "live")).lower() == "live"]


def _reachability() -> Dict[str, str]:
    try:
        data = json.loads(_REGISTRY.read_text())
    except (OSError, ValueError):
        return {}
    return {lev["strategy"]: lev.get("verdict") for lev in data.get("levers", [])
            if lev.get("strategy")}


def audit() -> Dict[str, Any]:
    src = _BUILDERS.read_text()
    unit_src = {p.stem: p.read_text() for p in _UNITS.glob("*.py")}
    import yaml  # local: keeps the module importable for a self-test without cfg
    cfg = yaml.safe_load(_STRATEGIES.read_text())
    reach = _reachability()

    legs = []
    for name, c in _live_strategies(cfg):
        unit, basis = unit_of(src, name)
        row: Dict[str, Any] = {"strategy": name, "unit": unit,
                               "unit_basis": basis, "mechanisms": {}}
        for mech, keys in MECHANISMS.items():
            declared = any(c.get(k) is not None for k in keys)
            if unit is None or unit not in unit_src:
                state = UNRESOLVED
            elif not module_implements(unit_src[unit], mech):
                state = ORPHANED if declared else NOT_IMPLEMENTED
            else:
                state = DECLARED if declared else UNDECLARED
            cell: Dict[str, Any] = {"state": state}
            if state == DECLARED:
                # `None` is honest: the registry only covers R-threshold gates,
                # so a stall-armed trail_decay legitimately has no verdict.
                cell["reachability"] = reach.get(name)
            row["mechanisms"][mech] = cell
        legs.append(row)

    orphans = [(r["strategy"], m) for r in legs
               for m, cell in r["mechanisms"].items()
               if cell["state"] == ORPHANED]
    unresolved = sorted({r["strategy"] for r in legs
                         if any(c["state"] == UNRESOLVED
                                for c in r["mechanisms"].values())})
    return {"legs": legs, "orphans": orphans, "unresolved": unresolved,
            "live_leg_count": len(legs)}


def _self_test() -> int:
    """Plant controls. A probe that cannot find a known positive proves nothing.

    `trend_donchian` implements stale_stop; `htf_pullback_trend_2h` does not.
    If either flips, this file's every "clean" result is worthless.
    """
    ok = 0
    checks = [
        ("positive: trend_donchian implements stale_stop",
         module_implements((_UNITS / "trend_donchian.py").read_text(), "stale_stop")),
        ("negative: htf_pullback_trend_2h does NOT implement stale_stop",
         not module_implements(
             (_UNITS / "htf_pullback_trend_2h.py").read_text(), "stale_stop")),
        ("positive: htf_pullback_trend_2h DOES implement trail_decay",
         module_implements(
             (_UNITS / "htf_pullback_trend_2h.py").read_text(), "trail_decay")),
    ]
    src = _BUILDERS.read_text()
    checks.append(("xrp_pullback_2h resolves to htf_pullback_trend_2h",
                   unit_of(src, "xrp_pullback_2h")[0] == "htf_pullback_trend_2h"))
    checks.append(("trend_donchian_xrp_4h resolves to trend_donchian",
                   unit_of(src, "trend_donchian_xrp_4h")[0] == "trend_donchian"))
    for label, passed in checks:
        print(f"  {'ok ' if passed else 'FAIL'} {label}")
        ok += bool(passed)
    print(f"self-test: {ok}/{len(checks)} passed")
    return 0 if ok == len(checks) else 1


_GLYPH = {NOT_IMPLEMENTED: "—", UNDECLARED: "off", DECLARED: "DECL",
          ORPHANED: "ORPHAN", UNRESOLVED: "?"}
_REACH_GLYPH = {"reachable": "LIVE", "vol_conditional": "PART",
                "inert": "INERT", "unmeasured": "DECL?"}


def _render(res: Dict[str, Any]) -> None:
    mechs = list(MECHANISMS)
    print(f"live+enabled legs: {res['live_leg_count']}\n")
    hdr = f"{'strategy':<26}{'unit':<24}" + "".join(f"{m:<13}" for m in mechs)
    print(hdr)
    print("-" * len(hdr))
    for r in res["legs"]:
        cells = []
        for m in mechs:
            cell = r["mechanisms"][m]
            g = _GLYPH[cell["state"]]
            if cell["state"] == DECLARED and cell.get("reachability"):
                g = _REACH_GLYPH.get(cell["reachability"], g)
            cells.append(f"{g:<13}")
        print(f"{r['strategy']:<26}{str(r['unit'] or r['unit_basis']):<24}"
              + "".join(cells))
    print("\nlegend: —  module has no such lever  |  off  implemented, leg opts out")
    print("        DECL declared (no R-gate verdict) | LIVE declared+reachable")
    print("        PART vol-conditional | INERT cannot fire | DECL? never measured")
    print("        ORPHAN declared but the module cannot read it | ? unit unresolved\n")

    for m in mechs:
        tally: Dict[str, int] = {}
        for r in res["legs"]:
            cell = r["mechanisms"][m]
            k = (_REACH_GLYPH.get(cell.get("reachability"), "DECL")
                 if cell["state"] == DECLARED else cell["state"])
            tally[k] = tally.get(k, 0) + 1
        print(f"  {m:<15}{tally}")

    graded = res["live_leg_count"] - len(res["unresolved"])
    if res["unresolved"]:
        print(f"\n⚠️  unit UNRESOLVED for {len(res['unresolved'])} leg(s): "
              f"{', '.join(res['unresolved'])}")
        print(f"    Not graded. The orphan verdict below ranges over "
              f"{graded} of {res['live_leg_count']} live legs — not all of them.")
    if res["orphans"]:
        print(f"\n❌ ORPHANED declares ({len(res['orphans'])}): "
              "declared in YAML, unreadable by the leg's own module")
        for s, m in res["orphans"]:
            print(f"     {s}  {m}")
    else:
        print(f"\n✅ no orphaned declares over "
              f"{res['live_leg_count'] - len(res['unresolved'])} resolved legs")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--orphans-only", action="store_true",
                    help="print only orphaned declares; exit 1 if any exist")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    res = audit()
    if a.json:
        print(json.dumps(res, indent=2, sort_keys=True))
    elif a.orphans_only:
        for s, m in res["orphans"]:
            print(f"{s}\t{m}")
    else:
        _render(res)
    return 1 if (a.orphans_only and res["orphans"]) else 0


if __name__ == "__main__":
    sys.exit(main())
