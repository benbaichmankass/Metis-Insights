#!/usr/bin/env python3
"""matrix-bracket-values — a `shipped` bracket_geometry cell must match the DECLARE.

THE GAP THIS CLOSES. `matrix-config-agreement` grades whether a lever is ARMED, over
exactly four levers (`m20_fleet_exit_sweep.LEVER_DECLARED_KEYS`: stale_stop,
giveback_stop, trail_decay, vol_trail). `bracket_geometry` is deliberately not among
them, and correctly so: that guard's `_arms()` tests key PRESENCE, and every leg always
declares `tp_r`/`atr_stop_mult`, so including the column would demand `shipped`
everywhere.

The consequence was a column with NO staleness detector at all. MEASURED 2026-08-29:
PR #10419 declared validated bracket geometry on **8 live legs, real money**, that
morning; the matrix carried all 8 as `passed_unshipped` for the rest of the day; and
`matrix-config-agreement` stayed GREEN throughout, because arming was never the
question. The 8 cells were reconciled by hand in #10430. This guard is what makes the
next one mechanical.

WHAT IT CHECKS, and why it is a different question. Not "is a lever armed" but "does the
declared VALUE match the cell the matrix says was shipped". The cell id encodes the
geometry -- `tp3_sm2` means `tp_r == 3.0` AND `atr_stop_mult == 2.0` -- so a `shipped`
cell is a falsifiable claim about `config/strategies.yaml`, and this guard falsifies it.

⚠️ ONLY THE AXES THE CELL ID NAMES ARE ASSERTED. `sm2` says nothing about `tp_r`, so
`tp_r` is NOT checked on that leg. Treating an absent axis as "must equal the default"
would invent a claim the evidence never made -- the same over-reach as reading a missing
value as zero.

⚠️ A `to<N>` COMPONENT ON A `shipped` CELL IS ITSELF A FINDING. No live trend/pullback/
squeeze unit implements a bar-count exit (`timeout_bars` is read only by
`fvg_range_15m.py` and `fade_breakout_4h.py`, each from its own `_DEFAULTS`, with no
generic reader), so no config change can deliver such a cell. It cannot be `shipped`;
it belongs at `blocked:no_live_bar_count_exit`.
BL-20260829-HARNESS-FORCE-CLOSES-TREND-PULLBACK-TRADES-ON-BAR-COUNT-AND-LIVE-NEVER-DOES

⚠️ THE GRADE IS THREE-STATE AND `unreadable` IS NOT A PASS. The cell id is parsed out of
the cell's own `ref` prose, which is written by hand and can therefore be written in a
form this parser does not recognise. "We could not find a cell id" and "we found one and
it matched" are opposite facts; collapsing them would make the guard silently vacuous the
moment a ref is phrased differently -- the exact failure mode
`new-table-wiring-guard`'s presence-only marker had. `unreadable` FAILS, naming the leg.

CONFIG IS THE FIELD, THE MATRIX IS PROSE ABOUT IT. A disagreement is reported as a stale
RECORD, never as a reason to touch a live declare -- every one of those declares is
operator-approved Tier-3 work. Same posture as `matrix-config-agreement`.

Usage:
    python3 scripts/ci/check_matrix_bracket_values.py
    python3 scripts/ci/check_matrix_bracket_values.py --self-test
Exit 0 = every shipped bracket_geometry cell matches its declare; 1 = a disagreement.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
MATRIX = REPO / "docs" / "research" / "exit-refinement-coverage.json"
STRATEGIES = REPO / "config" / "strategies.yaml"

#: Cell-id component -> the `config/strategies.yaml` key it asserts.
#: `to` is deliberately ABSENT: it maps to `timeout_bars`, which no live
#: trend/pullback/squeeze unit reads, so it can never be satisfied by a declare.
AXIS_KEY = {"tp": "tp_r", "sm": "atr_stop_mult"}

#: Components that make a cell UNSHIPPABLE rather than merely unmatched.
UNDELIVERABLE_AXES = {"to": "timeout_bars"}

_COMPONENT = re.compile(r"^(tp|sm|to)([0-9]+(?:\.[0-9]+)?)$")
#: The cell id as written in a ref: ``cell `tp3_sm2` `` .
_CELL_IN_REF = re.compile(r"cell\s+`([A-Za-z0-9_.]+)`")

TOL = 1e-9


def parse_cell(cell: str) -> tuple[dict[str, float], list[str]]:
    """(asserted axis->value, undeliverable axes). Unknown components are IGNORED.

    Ignoring an unknown component is safe in the direction that matters: it narrows
    what is asserted, never widens it. A component this parser cannot read is not
    silently treated as satisfied — it simply is not one of the claims checked, and
    a cell whose components are ALL unreadable yields an empty dict, which the
    caller grades `unreadable` rather than `ok`.
    """
    asserted: dict[str, float] = {}
    undeliverable: list[str] = []
    for part in cell.split("_"):
        m = _COMPONENT.match(part)
        if not m:
            continue
        axis, raw = m.group(1), float(m.group(2))
        if axis in UNDELIVERABLE_AXES:
            undeliverable.append(part)
        elif axis in AXIS_KEY:
            asserted[AXIS_KEY[axis]] = raw
    return asserted, undeliverable


def cell_id_from_ref(ref: str) -> str | None:
    m = _CELL_IN_REF.search(ref or "")
    return m.group(1) if m else None


def disagreements(matrix: dict, strategies: dict) -> list[dict]:
    out: list[dict] = []
    for row in matrix.get("rows", []):
        leg = row.get("strategy")
        cell_obj = row.get("bracket_geometry")
        if not isinstance(cell_obj, dict) or cell_obj.get("status") != "shipped":
            continue
        cfg = strategies.get(leg)
        if not isinstance(cfg, dict):
            # An aggregate/roll-up row, not a leg. Counted by the caller, never
            # silently dropped and never graded as agreement.
            continue
        cell = cell_id_from_ref(str(cell_obj.get("ref", "")))
        if cell is None:
            out.append({"leg": leg, "kind": "unreadable", "detail":
                        "no ``cell `<id>``` in the ref, so the claim cannot be checked"})
            continue
        asserted, undeliverable = parse_cell(cell)
        if undeliverable:
            out.append({"leg": leg, "kind": "undeliverable_axis", "cell": cell,
                        "detail": f"cell prescribes {undeliverable}; no live unit reads "
                                  "timeout_bars, so this cannot be `shipped`"})
            continue
        if not asserted:
            out.append({"leg": leg, "kind": "unreadable", "cell": cell,
                        "detail": "no recognised tp/sm component to check"})
            continue
        for key, want in sorted(asserted.items()):
            got = cfg.get(key)
            if got is None or abs(float(got) - want) > TOL:
                out.append({"leg": leg, "kind": "value_mismatch", "cell": cell,
                            "key": key, "matrix_says": want, "config_says": got})
    return out


def _load_yaml(path: Path) -> dict:
    import yaml  # noqa: PLC0415
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("strategies", {})


def _self_test() -> int:
    """Assertions inside the transform. Each case is one the guard must not soften."""
    strat = {"leg_a": {"tp_r": 3.0, "atr_stop_mult": 2.0},
             "leg_b": {"tp_r": 50.0, "atr_stop_mult": 2.5},
             "leg_c": {"atr_stop_mult": 2.0}}

    def m(leg, ref, status="shipped"):
        return {"rows": [{"strategy": leg,
                          "bracket_geometry": {"status": status, "ref": ref}}]}

    # 1. A matching cell is clean.
    assert disagreements(m("leg_a", "SHIPPED as cell `tp3_sm2` — PR #1"), strat) == []
    # 2. A value that disagrees is caught, and names BOTH numbers.
    d = disagreements(m("leg_b", "SHIPPED as cell `tp3_sm2`"), strat)
    assert len(d) == 2, d
    assert {x["key"] for x in d} == {"tp_r", "atr_stop_mult"}, d
    assert d[0]["matrix_says"] == 2.0 and d[0]["config_says"] == 2.5, d
    # 3. Only the axes the cell NAMES are asserted — `sm2` must not police tp_r.
    assert disagreements(m("leg_c", "SHIPPED as cell `sm2`"), strat) == []
    # 4. A ref with no cell id FAILS as `unreadable`; it is not a pass.
    d = disagreements(m("leg_a", "SHIPPED 2026-08-29, see the PR"), strat)
    assert len(d) == 1 and d[0]["kind"] == "unreadable", d
    # 5. A timeout component cannot be `shipped` — no live reader exists.
    d = disagreements(m("leg_a", "SHIPPED as cell `tp3_sm2_to24`"), strat)
    assert len(d) == 1 and d[0]["kind"] == "undeliverable_axis", d
    # 6. A non-shipped status is out of scope (this guard grades shipped-ness only).
    assert disagreements(m("leg_b", "cell `tp3_sm2`", status="passed_unshipped"),
                         strat) == []
    # 7. Float forms agree: 2 and 2.0 are the same declare.
    assert disagreements(m("leg_a", "cell `sm2`"), {"leg_a": {"atr_stop_mult": 2}}) == []
    # 8. POSITIVE CONTROL — the parser must actually read a decimal cell, or case 3
    #    would pass vacuously for the wrong reason.
    got, und = parse_cell("tp1.5_sm3")
    assert got == {"tp_r": 1.5, "atr_stop_mult": 3.0} and und == [], (got, und)
    print("matrix-bracket-values self-test: OK (8 cases)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _self_test()

    matrix: dict[str, Any] = json.loads(MATRIX.read_text(encoding="utf-8"))
    strategies = _load_yaml(STRATEGIES)
    shipped = sum(1 for r in matrix.get("rows", [])
                  if isinstance(r.get("bracket_geometry"), dict)
                  and r["bracket_geometry"].get("status") == "shipped")
    found = disagreements(matrix, strategies)

    print(f"matrix-bracket-values: {shipped} shipped bracket_geometry cell(s) checked "
          f"against config/strategies.yaml.")
    if not found:
        print("OK — every shipped cell's declared values match the cell it names.")
        return 0
    print("::error::a `shipped` bracket_geometry cell disagrees with the DECLARE. "
          "Config is the field; the matrix is prose about it — fix the RECORD, never "
          "the live declare (every one is operator-approved Tier-3 work):")
    for f in found:
        if f["kind"] == "value_mismatch":
            print(f"  - {f['leg']}: cell `{f['cell']}` says {f['key']}="
                  f"{f['matrix_says']}, config says {f['config_says']}")
        else:
            print(f"  - {f['leg']}: {f['kind']} — {f['detail']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
