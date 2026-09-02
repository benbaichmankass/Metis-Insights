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
  `htf_pullback_trend_2h` family (18 of 47 live legs, 38%) implemented exactly
  ONE of the four M20 mechanisms. **CLOSED 2026-08-18** — `stale_stop` and
  `giveback_stop` were extracted to `src/runtime/exit_levers.py` and the family
  now runs THREE of four. `exit_head` remains genuinely absent, and
  deliberately: it needs an advisory-stage trained head that does not exist for
  this family, so shipping the plumbing would be a capability that can never
  fire. NOTE this detector had to learn to follow the shared import to see any
  of that — a source-only grep reported the DONCHIAN family as having LOST two
  mechanisms it still runs
  (`BL-20260818-CAPABILITY-AUDITS-GREP-ONE-FILE-AND-MISS-SHARED-LEVERS`).

Why it mattered, concretely: the live XRP short that motivated M31 runs
`xrp_pullback_2h` → `htf_pullback_trend_2h`, whose only mechanism WAS
`trail_decay` — and on that leg `trail_decay_arm_r: 4.49` sits above its
`cap_R 3.92` for most entries. So the trade had **no working M20 exit
mechanism at all** for 18 days. The family now has three; whether any is worth
DECLARING on a given leg is a separate Tier-3 question the fleet sweep answers
(on xrp_pullback_2h specifically, all seven cells came back honest negatives —
`BL-20260818-XRP-PULLBACK-LEG-REJECTS-EVERY-DECISION-EXIT-LEVER`). That is not a mis-declaration (the leg declares
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
#
# ⚠️ THIS TABLE ANSWERS "DOES THE MODULE IMPLEMENT IT", NOT "DID THE LEG DECLARE
# IT". Those are different questions over different key sets and conflating them
# is what made the orphan check partial — see `DECLARE_KEYS` below.
MECHANISMS: Dict[str, Tuple[str, ...]] = {
    "stale_stop": ("stale_exit_bars",),
    "giveback_stop": ("giveback_r", "giveback_min_mfe_r"),
    "exit_head": ("exit_head", "exit_head_threshold"),
    "trail_decay": ("trail_decay_tight_mult", "trail_decay_arm_r"),
}

#: The COMPLETE config surface of each mechanism — every key an implementation
#: actually reads out of `meta`/`cfg`. This is what "the leg DECLARED this
#: mechanism" must be tested against.
#:
#: WHY IT IS A SEPARATE TABLE FROM `MECHANISMS`, and why that is not tidiness.
#: One table was serving two questions at once and was too NARROW for one of
#: them, so the orphan check — the whole point of this file — was a partial
#: predicate whose negative said nothing. MEASURED 2026-09-02 by planting a
#: declare on `ict_scalp_sol_5m`, a leg whose unit has **zero** `exit_head`
#: code (`grep -c exit_head src/units/strategies/ict_scalp.py` -> 0):
#:
#:   plant `exit_head_threshold` + `exit_head_action`  -> orphan reported, exit 1
#:   plant `exit_head_model`     + `exit_head_action`  -> SILENT,          exit 0
#:
#: The second plant is the one that ships. `trend_donchian._exit_head_verdict`
#: returns `None` unless `exit_head_action == "close"`, and it falls back to the
#: artifact's own tau when no `exit_head_threshold` is given — so
#: **`exit_head_action` is the key that arms the lever and `exit_head_threshold`
#: is an optional modifier**, and the detector was keyed on the modifier while
#: blind to the arming key. `"exit_head"` is not a key any leg declares at all.
#:
#: Not confined to `exit_head`: the same scan found `stale_exit_below_r` (read by
#: `exit_levers.py` and `ict_scalp.py`) and `trail_decay_stall_bars` (read by
#: `runtime/trail_decay.py`) missing too — 3 of 4 mechanisms, which is why the
#: remedy is the derivation check in `declared_surface_gaps()` rather than four
#: hand-added strings that can go stale again the next time a lever gains a knob.
#:
#: Widening THIS table can only widen orphan detection; it cannot make a module
#: read as implementing a lever it does not have, because `module_implements`
#: deliberately still reads the narrow `MECHANISMS` table above.
DECLARE_KEYS: Dict[str, Tuple[str, ...]] = {
    "stale_stop": ("stale_exit_bars", "stale_exit_below_r"),
    "giveback_stop": ("giveback_r", "giveback_min_mfe_r"),
    "exit_head": ("exit_head", "exit_head_action", "exit_head_model",
                  "exit_head_threshold"),
    "trail_decay": ("trail_decay_tight_mult", "trail_decay_arm_r",
                    "trail_decay_stall_bars"),
}

#: The config-key prefix each mechanism's keys share, used to DERIVE the surface
#: above from the implementations rather than trusting the hand-written tuple.
KEY_PREFIX: Dict[str, str] = {
    "stale_stop": "stale_",
    "giveback_stop": "giveback_",
    "exit_head": "exit_head",
    "trail_decay": "trail_decay",
}

#: How every lever in this repo reads its declare. Deliberately narrow: it is
#: the idiom, not a general string scan, so a key NAME appearing in a comment or
#: a log line does not enter the surface.
_CFG_READ = re.compile(r"\b(?:meta|cfg|cfg_dict|c)\.get\(\s*\"([a-z0-9_]+)\"")

#: Where an implementation may live. Units, plus the shared runtime modules a
#: unit delegates to (`exit_levers.py`, `trail_decay.py`, `exit_head_shadow.py`
#: today). Scanned WHOLESALE rather than by an explicit module list, because a
#: lever extracted to a NEW shared module is exactly the move that broke the
#: source-only greps in `BL-20260818-CAPABILITY-AUDITS-GREP-ONE-FILE-AND-MISS-
#: SHARED-LEVERS`, and an explicit list would reproduce it.
_IMPL_DIRS = (REPO / "src" / "units" / "strategies", REPO / "src" / "runtime")

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


# A lever whose body lives in `src/runtime/exit_levers.py` is still implemented
# by every unit that calls it. Grepping the unit's own source was correct while
# every lever was inline and became a FALSE `not_implemented` the moment
# `stale_stop`/`giveback_stop` were extracted (2026-08-18) — this audit would
# have reported BOTH families as having lost mechanisms they still run.
_SHARED_VERDICT_SYMBOLS = {
    "stale_stop": "stale_stop_verdict",
    "giveback_stop": "giveback_verdict",
}


def module_implements(unit_src: str, mechanism: str) -> bool:
    if any(f'"{k}"' in unit_src for k in MECHANISMS[mechanism]):
        return True
    sym = _SHARED_VERDICT_SYMBOLS.get(mechanism)
    if not sym or not re.search(rf"\b{sym}\b", unit_src):
        return False
    # The unit calls the shared verdict — confirm the SHARED module actually
    # reads this mechanism's keys, rather than trusting the call site's name.
    try:
        shared = (REPO / "src" / "runtime" / "exit_levers.py").read_text()
    except OSError:
        return False
    return any(f'"{k}"' in shared for k in MECHANISMS[mechanism])


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
        for mech in MECHANISMS:
            # THE FULL surface, not the implementation probe's narrow tuple:
            # `declared` is a claim about the YAML, and testing it against a
            # subset makes "no orphans" a statement about the subset only.
            declared = any(c.get(k) is not None
                           for k in DECLARE_KEYS[mech])
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


def read_config_keys() -> Dict[str, Dict[str, List[str]]]:
    """Every `<prefix>*` cfg key an implementation READS, per mechanism.

    `{mechanism: {key: [files that read it]}}`. This is the DERIVATION that
    keeps `DECLARE_KEYS` honest: the surface is a property of the code, and a
    hand-written copy of a property of the code drifts the moment a lever gains
    a knob — which is what `declared_surface_gaps()` exists to catch.
    """
    out: Dict[str, Dict[str, List[str]]] = {m: {} for m in DECLARE_KEYS}
    for d in _IMPL_DIRS:
        for path in sorted(d.glob("*.py")):
            try:
                src = path.read_text()
            except OSError:
                continue
            for key in set(_CFG_READ.findall(src)):
                for mech, prefix in KEY_PREFIX.items():
                    if key.startswith(prefix):
                        out[mech].setdefault(key, []).append(path.name)
    return out


def declared_surface_gaps(
    declare_keys: Optional[Dict[str, Tuple[str, ...]]] = None,
) -> List[Tuple[str, str, List[str]]]:
    """`(mechanism, key, readers)` for a key an implementation reads that
    `DECLARE_KEYS` does not list — i.e. a declare this file would not see.

    ONE-SIDED ON PURPOSE. A key in the table that nothing reads is harmless
    (it only widens orphan detection and costs nothing), so it is not a
    failure; a key that is READ and not in the table makes the orphan check
    silently partial, which is the defect. Reporting both as errors would have
    forced an exception for `"exit_head"` — a key no leg declares and no module
    reads — and an exception mechanism is how a guard becomes cheaper to lie to
    than to satisfy.
    """
    table = DECLARE_KEYS if declare_keys is None else declare_keys
    gaps: List[Tuple[str, str, List[str]]] = []
    for mech, keys in read_config_keys().items():
        for key, readers in sorted(keys.items()):
            if key not in table.get(mech, ()):
                gaps.append((mech, key, sorted(set(readers))))
    return gaps


def _self_test() -> int:
    """Plant controls. A probe that cannot find a known positive proves nothing.

    `trend_donchian` implements stale_stop; `htf_pullback_trend_2h` does not.
    If either flips, this file's every "clean" result is worthless.
    """
    ok = 0
    checks = [
        ("positive: trend_donchian implements stale_stop",
         module_implements((_UNITS / "trend_donchian.py").read_text(), "stale_stop")),
        # Was a negative until 2026-08-18, when stale_stop was extracted to
        # the shared module and this family gained it. Kept as the control
        # that the extraction is DETECTED — the keys are no longer in this
        # unit's own source, so a source-only grep answers no.
        ("positive: htf_pullback_trend_2h implements stale_stop via the shared module",
         module_implements(
             (_UNITS / "htf_pullback_trend_2h.py").read_text(), "stale_stop")),
        # Replacement NEGATIVE, so the detector is not merely answering yes:
        # exit_head is deliberately withheld from this family (it needs an
        # advisory-stage head that does not exist for it).
        ("negative: htf_pullback_trend_2h does NOT implement exit_head",
         not module_implements(
             (_UNITS / "htf_pullback_trend_2h.py").read_text(), "exit_head")),
        ("positive: htf_pullback_trend_2h DOES implement trail_decay",
         module_implements(
             (_UNITS / "htf_pullback_trend_2h.py").read_text(), "trail_decay")),
    ]
    # ---- the DECLARED SURFACE is complete, and the check can find a gap.
    # Both halves are required: an empty gap list from a probe that cannot
    # find a planted positive is the "no orphans" answer this file exists to
    # stop anyone acting on.
    gaps = declared_surface_gaps()
    checks.append((
        f"declared surface complete (no read-but-undeclared lever key); "
        f"gaps={[(m, k) for m, k, _ in gaps]}",
        not gaps))
    _holed = {m: tuple(k for k in ks if k != "exit_head_action")
              for m, ks in DECLARE_KEYS.items()}
    _planted = declared_surface_gaps(_holed)
    checks.append((
        "positive control: removing exit_head_action from the table IS "
        "detected as a gap",
        any(k == "exit_head_action" for _m, k, _r in _planted)))

    # ---- the ARMING key alone counts as a declare.
    # The regression this file shipped with: `exit_head_action: close` is
    # necessary AND sufficient to arm the head (trend_donchian._exit_head_verdict
    # returns None without it and falls back to the artifact tau when no
    # threshold is given), yet it was absent from the implementation tuple — so
    # a leg carrying only model+action on a unit with no exit-head code was
    # graded `not_implemented`, not `orphaned`, and `--orphans-only` exited 0.
    _leg = {"exit_head_action": "close", "exit_head_model": "exit-head-x-v1"}
    checks.append((
        "exit_head_action alone reads as DECLARED",
        any(_leg.get(k) is not None for k in DECLARE_KEYS["exit_head"])))
    checks.append((
        "…and is still NOT taken as evidence a module implements exit_head",
        not any(_leg.get(k) is not None for k in MECHANISMS["exit_head"])))

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
