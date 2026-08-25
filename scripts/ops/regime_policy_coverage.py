#!/usr/bin/env python3
# wiring: manual-only - a one-question AUDIT — how much of the live fleet the
#     regime hard gate governs. The answer changes only when a cell is
#     authored or a leg is demoted, both Tier-3 events with their own review,
#     so it is run as part of that review rather than on a cadence.
"""How much of the live fleet does the regime hard gate actually govern?

THE QUESTION, AND WHY NOTHING COULD ANSWER IT
---------------------------------------------
`config/regime_policy.yaml` is described everywhere as a load-bearing live
order-routing control: BASELINE-ON since 2026-06-28, Tier-3 to edit a cell,
with a kill-switch (`REGIME_ROUTER_DISABLED`) and a documented failure story
about money-losing cells trading again if it silently stopped enforcing.

Two facts make its REACH unobservable from every existing surface:

  1. The table is keyed on strategy-leg names, and the permissive default is
     correct and deliberate — "a regime profile we haven't measured yet defaults
     to ON, not OFF." So an unlisted leg gets `cell: "default-on"`.

  2. **The gate writes an audit row ONLY when it refuses.** Both
     `intents.py::_emit_regime_shadow_rows` and the hard-gate path
     `continue` without logging when the verdict is permissive. So the audit
     stream contains the gate's positives and nothing else.

Together those mean the gate LOOKS like it is working precisely because the
only evidence it produces is the evidence of it firing. A leg that is in the
table and allowed, and a leg that no one has ever measured, are indistinguishable
downstream — and the second population's size has never been stated.

That is diagnostic-provenance sub-class C (an unasserted denominator): an empty
or quiet result read as a clean negative. This script supplies the denominator.

WHAT IT MEASURES, AND WHAT IT DELIBERATELY DOES NOT
---------------------------------------------------
It is a STATIC read of two committed files — `config/strategies.yaml` and
`config/regime_policy.yaml`. It reports, per live leg, whether the gate has any
cell that could ever apply to it, and if so which sides are OFF in which regime.

It does NOT claim an uncovered leg is a bug. An unauthored cell is the
documented permissive default, and CLAUDE.md records a worked case
(SOL `trend_vol`) where the evidence says a cell SHOULD NOT be authored. The
finding this script produces is about the DENOMINATOR, not about any one cell.

It also does not read the live audit stream, because the audit stream cannot
answer this — see fact (2) above. Anyone tempted to "just check the logs" will
find only refusals and conclude coverage is total.

  Verdicts, per leg, never collapsed:
    ``governed``    — the leg has a cell in >= 1 regime block AND at least one
                      of those cells is `off` for some side. The gate can
                      actually refuse this leg.
    ``listed_open`` — the leg has cells, but every one is permissive. The gate
                      evaluated it and never refuses it. Distinct from
                      `unlisted`: someone measured this leg and decided ON.
    ``unlisted``    — no cell in any regime block. `cell: "default-on"`. Nobody
                      has measured this leg; the gate is inert for it.

`listed_open` and `unlisted` produce the same runtime behaviour and are still
kept apart, because the difference between "assessed and allowed" and "never
assessed" is the entire point of the exercise.

Observe-only. Reads two YAML files. No DB, no socket, no order path.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

#: The 1-D trend blocks the gate evaluates. The 2-D `trend_vol` block is a
#: separate axis with its own enforce preconditions and is reported apart —
#: folding it in would overstate 1-D coverage.
TREND_BLOCKS = ("chop", "transitional", "trending")

GOVERNED = "governed"
LISTED_OPEN = "listed_open"
UNLISTED = "unlisted"


def _load(path: Path) -> Dict[str, Any]:
    import yaml  # local import: keeps `--self-test` runnable without a repo read
    with path.open() as fh:
        return yaml.safe_load(fh) or {}


def live_legs(strategies_doc: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Legs that can actually place an order: enabled AND `execution: live`.

    A `shadow` leg logs order packages and places nothing, so gate coverage over
    it is not a live-routing fact. Stating the population is the point of the
    script; quietly including shadow legs would inflate the denominator.
    """
    block = strategies_doc.get("strategies", strategies_doc)
    out: Dict[str, Dict[str, Any]] = {}
    for name, cfg in (block or {}).items():
        if not isinstance(cfg, dict):
            continue
        if not cfg.get("enabled", True):
            continue
        if str(cfg.get("execution", "live")).strip().lower() != "live":
            continue
        out[str(name)] = cfg
    return out


def cells_for(policy: Dict[str, Any], leg: str) -> Dict[str, Dict[str, Any]]:
    """Every 1-D cell naming `leg`, keyed by regime. Empty dict => unlisted."""
    found: Dict[str, Dict[str, Any]] = {}
    for regime in TREND_BLOCKS:
        block = policy.get(regime)
        if not isinstance(block, dict):
            continue
        cell = block.get(leg)
        if isinstance(cell, dict):
            found[regime] = cell
    return found


def _off_sides(policy: Dict[str, Any], regime: str, leg: str) -> List[str]:
    """Sides the LIVE gate would refuse for this (regime, leg).

    Delegates to `policy._evaluate_trend_cell` — the same function
    `intents.py` calls — rather than reading the cell dict here. That is not
    fastidiousness; the first draft of this script DID re-implement the
    comparison as `str(cell.get(side)).lower() == "off"` and reported
    **0 governed legs out of 47**, because PyYAML resolves the YAML 1.1
    literals `on`/`off` to Python `True`/`False`, so the string never matched.
    The live gate is correct (`value is False or value == "off"`); the copy was
    wrong, and it was wrong in the reassuring direction — it under-reported the
    gate's reach, which is the same shape as reporting a quiet probe as a clean
    negative.

    A second implementation of a decision predicate is the drift this repo
    keeps paying for. The remedy is to collapse the two, not to test the copy
    against the original forever.
    """
    from src.runtime.regime.policy import _evaluate_trend_cell
    return sorted(
        side for side in ("long", "short")
        if bool(_evaluate_trend_cell(
            strategy=leg, side=side, regime=regime, policy=policy,
        ).get("gated"))
    )


def classify(
    policy: Dict[str, Any], leg: str, cells: Dict[str, Dict[str, Any]],
) -> Tuple[str, Dict[str, List[str]]]:
    """`(verdict, {regime: [off sides]})` for one leg."""
    if not cells:
        return UNLISTED, {}
    offs = {r: _off_sides(policy, r, leg) for r in cells}
    offs = {r: s for r, s in offs.items() if s}
    return (GOVERNED if offs else LISTED_OPEN), offs


def audit(strategies_doc: Dict[str, Any], policy: Dict[str, Any]) -> Dict[str, Any]:
    legs = live_legs(strategies_doc)
    rows = []
    for leg in sorted(legs):
        cells = cells_for(policy, leg)
        verdict, offs = classify(policy, leg, cells)
        rows.append({
            "strategy": leg,
            "verdict": verdict,
            "regimes_listed": sorted(cells),
            "off_sides_by_regime": offs,
        })
    counts = {v: sum(1 for r in rows if r["verdict"] == v)
              for v in (GOVERNED, LISTED_OPEN, UNLISTED)}
    total = len(rows) or 1
    policy_keys = sorted({
        k for b in TREND_BLOCKS
        if isinstance(policy.get(b), dict)
        for k in policy[b]
    })
    # Keys in the table that match NO live leg. A stale key is not a coverage
    # gap, but it IS evidence the table has drifted from the roster, and it
    # reads as coverage to anyone counting rows in the YAML.
    orphan_keys = [k for k in policy_keys if k not in legs]
    return {
        "live_legs": len(rows),
        "counts": counts,
        "governed_pct": round(100.0 * counts[GOVERNED] / total, 1),
        "unlisted_pct": round(100.0 * counts[UNLISTED] / total, 1),
        "policy_keys": policy_keys,
        "orphan_policy_keys": orphan_keys,
        "rows": rows,
    }


def render(report: Dict[str, Any]) -> str:
    c = report["counts"]
    out = [
        "REGIME-POLICY COVERAGE OVER THE LIVE FLEET",
        "=" * 62,
        f"live legs (enabled AND execution: live) : {report['live_legs']}",
        f"  governed    (>=1 explicit OFF cell)   : {c[GOVERNED]}"
        f"  ({report['governed_pct']}%)",
        f"  listed_open (cells, none OFF)         : {c[LISTED_OPEN]}",
        f"  unlisted    (no cell; cell=default-on): {c[UNLISTED]}"
        f"  ({report['unlisted_pct']}%)",
        "",
        "The gate can only ever REFUSE a `governed` leg. `listed_open` and",
        "`unlisted` behave identically at runtime and are reported apart on",
        "purpose: one was measured and allowed, the other was never measured.",
        "",
        "NOTE: the gate emits an audit row ONLY on a refusal, so none of this",
        "is recoverable from the audit stream — a permissive verdict logs",
        "nothing at all.",
        "",
    ]
    if report["orphan_policy_keys"]:
        out += ["policy keys matching NO live leg (table drift, not a gap):",
                "  " + ", ".join(report["orphan_policy_keys"]), ""]
    for verdict in (GOVERNED, LISTED_OPEN, UNLISTED):
        legs = [r for r in report["rows"] if r["verdict"] == verdict]
        if not legs:
            continue
        out.append(f"--- {verdict} ({len(legs)}) ---")
        for r in legs:
            detail = ""
            if r["off_sides_by_regime"]:
                detail = "   OFF: " + "; ".join(
                    f"{k}={'/'.join(v)}" for k, v in sorted(
                        r["off_sides_by_regime"].items()))
            out.append(f"  {r['strategy']}{detail}")
        out.append("")
    return "\n".join(out)


def _self_test() -> int:
    """Planted controls, including a negative the classifier must NOT pass."""
    fails = []

    def check(name, got, want):
        if got != want:
            fails.append(f"{name}: got {got!r} want {want!r}")

    pol = {
        # Booleans, because that is what safe_load() hands the gate for the
        # YAML literals `on` / `off`. Planting strings here would test a
        # document shape production never produces.
        "trending": {"a": {"long": True, "short": False},
                     "b": {"long": True, "short": True}},
        "chop": {"a": {"long": False, "short": False}},
        # A 2-D block must never be mistaken for a 1-D one.
        "trend_vol": {"chop": {"calm": {"c": {"long": False}}}},
    }
    strat = {"strategies": {
        "a": {"enabled": True, "execution": "live"},
        "b": {"enabled": True, "execution": "live"},
        "c": {"enabled": True, "execution": "live"},   # only in trend_vol
        "d": {"enabled": True, "execution": "live"},   # nowhere
        "e": {"enabled": True, "execution": "shadow"}, # excluded: not live
        "f": {"enabled": False, "execution": "live"},  # excluded: disabled
    }}
    rep = audit(strat, pol)
    check("population excludes shadow+disabled", rep["live_legs"], 4)
    by = {r["strategy"]: r["verdict"] for r in rep["rows"]}
    check("a governed", by["a"], GOVERNED)
    check("b listed_open (cells but no OFF)", by["b"], LISTED_OPEN)
    # THE NEGATIVE CONTROL: `c` appears in the file, but only under trend_vol.
    # A classifier that scanned the document instead of the three 1-D blocks
    # would report it covered.
    check("c unlisted despite a trend_vol mention", by["c"], UNLISTED)
    check("d unlisted", by["d"], UNLISTED)
    check("orphan keys empty here", rep["orphan_policy_keys"], [])

    # A numeric weight is permissive, not an OFF (mirrors _evaluate_trend_cell).
    v, _ = classify({"chop": {"z": {"long": 0.5, "short": "on"}}},
                    "z", {"chop": {"long": 0.5, "short": "on"}})
    check("weight cell is not an OFF", v, LISTED_OPEN)
    # Case/whitespace tolerance on the one literal that matters.
    ypol = {"chop": {"z": {"long": False, "short": True}}}
    v2, offs = classify(ypol, "z", {"chop": ypol["chop"]["z"]})
    check("YAML bool off is an OFF", (v2, offs), (GOVERNED, {"chop": ["long"]}))
    # An orphan key is reported without being counted as coverage.
    rep2 = audit({"strategies": {"a": {"enabled": True, "execution": "live"}}},
                 {"chop": {"a": {"long": False}, "gone": {"long": False}}})
    check("orphan key surfaced", rep2["orphan_policy_keys"], ["gone"])
    check("orphan not in population", rep2["live_legs"], 1)

    for f in fails:
        print("FAIL", f)
    print(f"self-test: {9 - len(fails)}/9 passed")
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="emit the raw report")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--strategies", default=str(REPO / "config/strategies.yaml"))
    ap.add_argument("--policy", default=str(REPO / "config/regime_policy.yaml"))
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    report = audit(_load(Path(args.strategies)), _load(Path(args.policy)))
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
