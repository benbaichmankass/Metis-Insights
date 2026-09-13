#!/usr/bin/env python3
# wiring: manual-only CLI plus a CI-registrable check. Its state of record is
# docs/research/exit-lever-map.json; this file is the thing that stops that
# record going stale as harness flags are added.
"""Does every exit-relevant harness flag have a recorded verdict?

WHY THIS EXISTS
---------------
``BL-20260810-EXIT-LEVER-SPACE-UNDER-ENUMERATED``'s first criterion: *"Every
exit-relevant harness flag maps to a matrix column or a recorded n/a with a
reason."* The flags live in five argparse blocks, the columns live in a 765 KB
JSON artifact, and nothing connected them - so the answer was re-derived by hand
each time it was asked, and the three hand answers on that row disagree
(``21 flags / 8 columns / 3 uncovered`` on 2026-08-10, ``3 gaps`` on 2026-09-02
over a different and smaller population, and ``4`` here over the matrix's own).

THE HARNESS SET IS DERIVED, NEVER DECLARED
------------------------------------------
It is whatever the matrix's own rows route to, resolved through
``m20_fleet_exit_sweep.classify`` - the same resolver the round driver uses to
pick a harness. Hardcoding a list is how the 2026-09-02 answer came to cover 42
of 52 legs without saying so.

⚠️ **THE VOCABULARY RULE IS THE WEAK LINK, AND IT IS USED FOR ONE THING ONLY.**
:data:`EXIT_VOCAB` decides which flags must have a curated verdict. It is a
token match and it will miss a flag named in a vocabulary nobody anticipated. It
therefore never produces a VERDICT - only a work list - and the non-candidate
remainder is reported as a COUNT, which is *we did not look*, never a clean bill.

⚠️ **`needs_column` IS NOT `recorded_na`.** The map's three states stay apart
because collapsing them is how a real gap disappears by wording: an exit lever
the matrix cannot express is a finding, and a flag that is not a lever is an
answer. This checker grades presence, and deliberately does NOT fail on
``needs_column`` - the criterion asks that every flag have a recorded verdict,
and "this needs a column" IS one. Failing on it would pressure the next session
into re-labelling a gap as an n/a to get green.
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import pathlib
import sys
from typing import Dict, Set

REPO = pathlib.Path(__file__).resolve().parents[2]
MAP_PATH = REPO / "docs" / "research" / "exit-lever-map.json"
MATRIX_PATH = REPO / "docs" / "research" / "exit-refinement-coverage.json"
CLASSIFIER = REPO / "scripts" / "research" / "m20_fleet_exit_sweep.py"

#: A flag whose NAME contains one of these must carry a curated verdict.
#: Deliberately generous: a false candidate costs one line of curation, a missed
#: one costs a silent gap.
EXIT_VOCAB = (
    "exit", "trail", "stop", "tp", "take-profit", "giveback", "give-back",
    "bank", "stale", "timeout", "flip", "breakeven", "be-", "rr", "pierce",
    "target", "hold", "time-stop", "atr",
)

MAPPED, NA, NEEDS = "mapped_to_column", "recorded_na", "needs_column"
STATES = (MAPPED, NA, NEEDS)


def harness_paths(matrix: dict) -> Dict[str, str]:
    """family -> harness path, taken from the map's declared table but VERIFIED
    against the families the matrix's rows actually route to."""
    spec = importlib.util.spec_from_file_location("_m20_for_lever_map", CLASSIFIER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fams = {mod.classify(r["strategy"]) for r in matrix["rows"]}
    fams.discard(None)
    declared = json.loads(MAP_PATH.read_text())["population"]["harnesses"]
    missing = sorted(fams - set(declared))
    if missing:
        raise SystemExit(
            f"exit-lever-map: the matrix routes to families {missing} that the map "
            f"declares no harness for. Add them to population.harnesses rather than "
            f"letting their flags go unexamined."
        )
    return {f: declared[f] for f in sorted(fams)}


def flags_of(path: pathlib.Path) -> Set[str]:
    out: Set[str] = set()
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "add_argument":
            for a in n.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str) \
                        and a.value.startswith("--"):
                    out.add(a.value)
    return out


def is_candidate(flag: str) -> bool:
    low = flag.lower()
    return any(tok in low for tok in EXIT_VOCAB)


def audit(root: pathlib.Path = REPO) -> dict:
    matrix = json.loads((root / "docs/research/exit-refinement-coverage.json").read_text())
    doc = json.loads((root / "docs/research/exit-lever-map.json").read_text())
    paths = harness_paths(matrix)
    all_flags: Set[str] = set()
    for p in paths.values():
        all_flags |= flags_of(root / p)
    candidates = {f for f in all_flags if is_candidate(f)}
    recorded = doc["flags"]
    bad_state = {f: v.get("state") for f, v in recorded.items()
                 if v.get("state") not in STATES}
    missing_reason = [f for f, v in recorded.items()
                      if v.get("state") in (NA, NEEDS) and not str(v.get("reason") or "").strip()]
    missing_column = [f for f, v in recorded.items()
                      if v.get("state") == MAPPED and v.get("column") not in doc["lever_columns"]]
    return {
        "harnesses": paths,
        "flags_total": len(all_flags),
        "candidates": sorted(candidates),
        "unrecorded": sorted(candidates - set(recorded)),
        "recorded_but_gone": sorted(set(recorded) - all_flags),
        "not_candidates_count": len(all_flags - candidates),
        "bad_state": bad_state,
        "missing_reason": missing_reason,
        "missing_column": missing_column,
        "by_state": {s: sorted(f for f, v in recorded.items() if v.get("state") == s)
                     for s in STATES},
    }


def _self_test() -> int:
    ok = True

    def check(label, cond):
        nonlocal ok
        ok &= bool(cond)
        print(f"  self-test {'ok  ' if cond else 'FAIL'}: {label}")

    check("the exit vocabulary matches a real lever flag",
          is_candidate("--trail-mult") and is_candidate("--giveback-r"))
    check("and does NOT match a plainly non-exit flag",
          not is_candidate("--data") and not is_candidate("--symbol")
          and not is_candidate("--out-dir"))
    r = audit()
    check("every candidate flag has a recorded verdict", not r["unrecorded"])
    check("no recorded flag has vanished from the harnesses", not r["recorded_but_gone"])
    check("every verdict is one of the three states", not r["bad_state"])
    check("every na / needs_column verdict carries a reason", not r["missing_reason"])
    check("every mapped verdict names a REAL lever column", not r["missing_column"])
    check("the three states are all populated (none is decorative)",
          all(r["by_state"][s] for s in STATES))
    check("the non-candidate remainder is REPORTED, not assumed clean",
          r["not_candidates_count"] > 0)
    print("exit-lever-map self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv[1:])
    if a.self_test:
        return _self_test()
    r = audit()
    if a.json:
        print(json.dumps(r, indent=2, ensure_ascii=False))
    else:
        print("exit-lever map audit")
        print(f"  harnesses (derived from the matrix's own rows): "
              f"{', '.join(sorted(r['harnesses']))}")
        print(f"  {r['flags_total']} distinct flags · {len(r['candidates'])} exit-relevant "
              f"candidates · {r['not_candidates_count']} non-candidates NOT examined "
              f"(*we did not look*, not a clean bill)")
        for s in STATES:
            print(f"  {s:18} {len(r['by_state'][s]):3}  {', '.join(r['by_state'][s])}")
    problems = (r["unrecorded"] or r["bad_state"] or r["missing_reason"]
                or r["missing_column"] or r["recorded_but_gone"])
    if problems:
        print("\n::error::exit-lever-map is incomplete or malformed:", file=sys.stderr)
        for k in ("unrecorded", "recorded_but_gone", "bad_state", "missing_reason",
                  "missing_column"):
            if r[k]:
                print(f"  {k}: {r[k]}", file=sys.stderr)
        return 1
    print("\nexit-lever-map: OK — every exit-relevant harness flag carries a recorded verdict.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
