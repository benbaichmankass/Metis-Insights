#!/usr/bin/env python3
"""Adapt an e35 bracket sweep's per-half rows into the `verdicts.json` shape the
canonical `m20_banking_risk_adjusted.py` reads.

WHY THIS EXISTS
---------------
The e35 bracket sweep and the M20 lever sweep answer the same *kind* of
question and are gated by the same `fleet.beats` (net_R **AND** maxDD, both
halves), so the same risk-adjusted lens applies to both. But only the lever
sweep writes the `verdicts.json` that `m20_banking_risk_adjusted.py` consumes.

The alternative was to re-implement MAR and the trade ratio against the e35
schema. That would be a **second definition of MAR**, free to drift from the
one the repo already reasons with -- the exact hazard
`_regime_score_semantics.py` exists to stop ("two probes re-derived it
independently and both got it wrong on the same day"). So this adapter reshapes
the DATA and imports the ARITHMETIC by leaving it where it lives.

WHERE THE PER-HALF NUMBERS COME FROM
------------------------------------
`e35_bracket_geometry_sweep.py::gate` runs four backtests per gated cell
(base/cell x IS/OOS) and `log(...)`s each pair to `results.jsonl` as
`{"window": "is"|"oos", "base": {...}, "lever": {...}}`. `report.json`'s `gate`
block keeps only the **deltas** (`d_net_r`, `d_max_dd`), and a delta cannot
produce a ratio of levels -- MAR needs the absolutes. So the absolutes are read
from `results.jsonl` and the *verdict strings* from `report.json`, each from
the file that owns it. **The verdict is never re-derived here.**

⚠️ **ONLY GATED CELLS HAVE HALVES.** The sweep grades a 199-cell surface on
FULL history and runs the split only on the shortlist it forwards to the gate
(measured: 7 per leg). A cell absent from `results.jsonl`'s `is`/`oos` rows was
never split -- that is *"we did not look"*, not *"it has no out-of-sample
edge"* -- so it is skipped and **counted** in `cells_without_halves`, never
emitted with a zero or a null half.

⚠️ **THE MAPPING IS NET, AND IT IS STATED.** e35's `net_total_r` (net of fee +
slippage + funding) lands in the tool's `total_r` slot, because that is the
basis `fleet.beats` gates on and the basis the tool prints as "netR". e35 also
carries a GROSS `total_r`; putting that one in the slot would print a gross
number under the label of the gated one -- the semantic substitution
`diagnostic-provenance-guard` is about.

⚠️ **`dd_per_r` IS NULL FOR A RETURN-GAINING CELL, BY CONSTRUCTION, AND THAT IS
CORRECT.** The tool computes it only when the cell COSTS return (the banking
direction: surrender net_R, buy smoothness). A bracket cell that gains return
and pays drawdown is the mirror object; read the ratio the other way up rather
than reading the null as "no trade-off measured".

Usage:
    python3 scripts/research/e35_verdicts_adapter.py --run-glob '/tmp/w/e35/*/2026-08-23' --out /tmp/rap/verdicts
    python3 scripts/research/e35_verdicts_adapter.py --selftest

Tier-1 research tooling. Reads a sweep's own output, writes verdicts.json
files for a reading tool. Ships nothing, gates nothing, changes no strategy.

# wiring: manual-only -- an adapter invoked by an operator/analyst between a
# sweep run and `m20_banking_risk_adjusted.py`. It has no scheduled caller by
# design (a sweep is dispatched, not cron'd), so `--selftest` is wired into CI
# via `artifact-validity-guard` instead: a self-test nobody invokes is worse
# than a missing one.
"""
from __future__ import annotations

import argparse
import glob as globmod
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

# The e35 keys this adapter depends on. A run missing any of them is a schema
# it does not understand, and it says so loudly rather than emitting a
# plausible-looking file built from `.get(...)` defaults.
_REQUIRED_HALF_KEYS = ("net_total_r", "max_drawdown_r", "total_trades")


class SchemaRefusal(RuntimeError):
    """The input is not an e35 sweep output. Refuse; never coerce."""


def _half_block(rec: Dict[str, Any], where: str) -> Dict[str, Any]:
    missing = [k for k in _REQUIRED_HALF_KEYS if k not in rec]
    if missing:
        raise SchemaRefusal(f"{where}: missing e35 keys {missing} -- "
                            f"this is not an e35 per-half row")
    return {"total_r": rec["net_total_r"],      # NET, see docstring
            "max_dd_r": rec["max_drawdown_r"],
            "trades": rec["total_trades"]}


def adapt_run(run_dir: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """One sweep run directory -> (verdicts-shaped doc, provenance)."""
    results = run_dir / "results.jsonl"
    report = run_dir / "report.json"
    for p in (results, report):
        if not p.exists():
            raise SchemaRefusal(f"{run_dir}: {p.name} absent -- not an e35 run dir")

    halves: Dict[str, Dict[str, Dict[str, Any]]] = {}
    full_cells: set[str] = set()
    leg_name = None
    for line in results.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        # A malformed line is a CORRUPT SAMPLE, not a smaller one. Refuse.
        try:
            d = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SchemaRefusal(f"{results}: unreadable row -- {exc}") from exc
        w = d.get("window")
        if w == "full":
            full_cells.add(d.get("cell"))
            continue
        if w not in ("is", "oos"):
            continue
        leg_name = d.get("leg") or leg_name
        halves.setdefault(d["cell"], {})[w] = d

    rep = json.loads(report.read_text())
    if "legs" not in rep:
        raise SchemaRefusal(f"{report}: no `legs` key -- not an e35 report.json")
    gate_by_cell: Dict[str, Dict[str, Any]] = {}
    meta: Dict[str, Any] = {}
    for leg in rep["legs"]:
        for g in leg.get("gate") or []:
            gate_by_cell[g["cell"]] = g
        meta = {"leg": leg.get("leg"), "symbol": leg.get("symbol"),
                "tf": leg.get("tf"),
                "geom": leg.get("base_geometry") or {}}
    leg_name = leg_name or meta.get("leg")

    baseline: Dict[str, Dict[str, Any]] = {}
    cells: Dict[str, Dict[str, Any]] = {}
    incomplete: List[str] = []
    for cell, hs in sorted(halves.items()):
        if not {"is", "oos"} <= set(hs):
            incomplete.append(cell)
            continue
        for w in ("is", "oos"):
            W = w.upper()
            baseline.setdefault(W, _half_block(hs[w]["base"], f"{cell}/{w}/base"))
            cells.setdefault(cell, {})[W] = _half_block(
                hs[w]["lever"], f"{cell}/{w}/cell")
        g = gate_by_cell.get(cell) or {}
        # Verdicts are the sweep's own strings, never re-derived here.
        cells[cell]["verdict"] = g.get("verdict")
        cells[cell]["walkforward_verdict"] = (g.get("wf") or {}).get("verdict")
        cells[cell]["lever"] = g.get("axis")

    doc = {
        "leg": leg_name,
        "symbol": meta.get("symbol"),
        "timeframe": meta.get("tf"),
        "data": f"e35 bracket sweep {run_dir.name} -- net of fee+slippage+funding",
        "tp_at_r": (meta.get("geom") or {}).get("tp_r"),
        "declared_base": (meta.get("geom") or {}).get("stop_mult"),
        "baseline": baseline,
        "cells": cells,
    }
    prov = {
        "run_dir": str(run_dir),
        "cells_graded_full_history": len(full_cells),
        "cells_with_both_halves": len(cells),
        # NOT a defect: only the gate shortlist is ever split. Stated so a
        # reader never mistakes the shortlist for the population.
        "cells_without_halves": len(full_cells - set(cells)),
        "cells_with_one_half_only": sorted(incomplete),
    }
    return doc, prov


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-glob", default=None,
                    help="glob matching e35 run directories (each holding "
                         "results.jsonl + report.json)")
    ap.add_argument("--out", default=None, help="directory to write verdicts.json into")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv[1:])

    if a.selftest:
        return _selftest()
    if not a.run_glob or not a.out:
        ap.error("--run-glob and --out are required (or --selftest)")

    run_dirs = [Path(p) for p in sorted(globmod.glob(a.run_glob)) if Path(p).is_dir()]
    if not run_dirs:
        # An empty scan is a REFUSAL. Reporting "0 adapted" as a success is
        # the unasserted-denominator shape.
        print(f"REFUSED: --run-glob {a.run_glob!r} matched no directories -- "
              f"nothing adapted (this is NOT a clean result)", file=sys.stderr)
        return 2

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    n_ok = 0
    for rd in run_dirs:
        doc, prov = adapt_run(rd)
        p = out / f"{doc['leg']}__verdicts.json"
        p.write_text(json.dumps(doc, indent=1))
        # Read back and verify -- a write that silently truncated must not
        # report success.
        back = json.loads(p.read_text())
        if back != doc:
            raise RuntimeError(f"{p}: read-back mismatch after write")
        n_ok += 1
        print(f"{doc['leg']:28s} halves={prov['cells_with_both_halves']:>3d} "
              f"of {prov['cells_graded_full_history']:>3d} graded  "
              f"(not split: {prov['cells_without_halves']}) -> {p}")
    print(f"\nadapted {n_ok} run(s). Now read them with:\n"
          f"  python3 scripts/research/m20_banking_risk_adjusted.py "
          f"--glob '{out}/*.json'")
    return 0


# ---------------------------------------------------------------------------
def _selftest() -> int:
    checks: List[Tuple[str, bool]] = []

    def ck(name: str, cond: bool) -> None:
        checks.append((name, bool(cond)))

    with tempfile.TemporaryDirectory() as td:
        rd = Path(td) / "leg_x" / "2026-01-01"
        rd.mkdir(parents=True)
        rows = [
            {"window": "full", "cell": "sm2", "leg": "leg_x"},
            {"window": "full", "cell": "tp4", "leg": "leg_x"},
            {"window": "full", "cell": "to96", "leg": "leg_x"},
            {"window": "is", "cell": "sm2", "leg": "leg_x",
             "base": {"net_total_r": 10.0, "total_r": 99.0,
                      "max_drawdown_r": 5.0, "total_trades": 100},
             "lever": {"net_total_r": 20.0, "total_r": 99.0,
                       "max_drawdown_r": 8.0, "total_trades": 102}},
            {"window": "oos", "cell": "sm2", "leg": "leg_x",
             "base": {"net_total_r": 2.0, "total_r": 9.0,
                      "max_drawdown_r": 4.0, "total_trades": 50},
             "lever": {"net_total_r": 6.0, "total_r": 9.0,
                       "max_drawdown_r": 3.0, "total_trades": 51}},
            # one-half-only cell -> must be skipped AND counted
            {"window": "is", "cell": "tp4", "leg": "leg_x",
             "base": {"net_total_r": 1.0, "total_r": 1.0,
                      "max_drawdown_r": 1.0, "total_trades": 10},
             "lever": {"net_total_r": 1.0, "total_r": 1.0,
                       "max_drawdown_r": 1.0, "total_trades": 10}},
        ]
        (rd / "results.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n")
        (rd / "report.json").write_text(json.dumps({
            "legs": [{"leg": "leg_x", "symbol": "XUSDT", "tf": "4h",
                      "base_geometry": {"tp_r": 50.0, "stop_mult": 2.5},
                      "gate": [{"cell": "sm2", "axis": "stop",
                                "verdict": "is_oos_fail"}]}]}))

        doc, prov = adapt_run(rd)
        ck("leg name carried", doc["leg"] == "leg_x")
        ck("symbol carried", doc["symbol"] == "XUSDT")
        ck("sentinel tp_at_r carried", doc["tp_at_r"] == 50.0)
        ck("baseline IS uses NET not gross",
           doc["baseline"]["IS"]["total_r"] == 10.0)
        ck("baseline OOS uses NET not gross",
           doc["baseline"]["OOS"]["total_r"] == 2.0)
        ck("cell IS level carried", doc["cells"]["sm2"]["IS"]["total_r"] == 20.0)
        ck("verdict is the sweep's own string",
           doc["cells"]["sm2"]["verdict"] == "is_oos_fail")
        ck("one-half cell NOT emitted", "tp4" not in doc["cells"])
        ck("one-half cell counted", prov["cells_with_one_half_only"] == ["tp4"])
        ck("unsplit cells counted, not silently dropped",
           prov["cells_without_halves"] == 2)   # tp4 + to96
        ck("graded denominator reported",
           prov["cells_graded_full_history"] == 3)

        # MAR computed by the CANONICAL tool must match hand arithmetic.
        vp = Path(td) / "v.json"
        vp.write_text(json.dumps(doc))
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import m20_banking_risk_adjusted as bank  # noqa: E402
        r = bank.analyse(vp)
        row = next(x for x in r["rows"] if x["tag"] == "sm2")
        ck("canonical tool sees IS dR=+10", abs(row["IS"]["dR"] - 10.0) < 1e-9)
        ck("canonical tool sees OOS dDD=-1", abs(row["OOS"]["dDD"] + 1.0) < 1e-9)
        ck("canonical MAR base IS = 10/5", abs(row["IS"]["mar_base"] - 2.0) < 1e-9)
        ck("canonical MAR cell IS = 20/8", abs(row["IS"]["mar_cell"] - 2.5) < 1e-9)
        # The documented null: a return-GAINING cell has no banking-direction ratio.
        ck("dd_per_r null for a return-gaining cell",
           row["IS"]["dd_per_r"] is None and row["OOS"]["dd_per_r"] is None)

        # Schema refusals
        bad = Path(td) / "bad" / "2026-01-01"
        bad.mkdir(parents=True)
        # BOTH halves present, so the completeness skip cannot mask the
        # schema check -- the refusal must come from the key check itself.
        (bad / "results.jsonl").write_text("\n".join(json.dumps(r) for r in [
            {"window": "is", "cell": "c", "leg": "l",
             "base": {"nope": 1}, "lever": {"nope": 1}},
            {"window": "oos", "cell": "c", "leg": "l",
             "base": {"nope": 1}, "lever": {"nope": 1}},
        ]) + "\n")
        (bad / "report.json").write_text(json.dumps({"legs": []}))
        try:
            adapt_run(bad)
            ck("refuses foreign half schema", False)
        except SchemaRefusal:
            ck("refuses foreign half schema", True)

        missing = Path(td) / "missing" / "2026-01-01"
        missing.mkdir(parents=True)
        try:
            adapt_run(missing)
            ck("refuses a dir with no sweep output", False)
        except SchemaRefusal:
            ck("refuses a dir with no sweep output", True)

        corrupt = Path(td) / "corrupt" / "2026-01-01"
        corrupt.mkdir(parents=True)
        (corrupt / "results.jsonl").write_text("{not json\n")
        (corrupt / "report.json").write_text(json.dumps({"legs": []}))
        try:
            adapt_run(corrupt)
            ck("refuses a corrupt row (never a smaller sample)", False)
        except SchemaRefusal:
            ck("refuses a corrupt row (never a smaller sample)", True)

    ok = sum(1 for _, c in checks if c)
    for name, c in checks:
        print(f"  [{'ok' if c else 'FAIL'}] {name}")
    print(f"selftest: {ok}/{len(checks)}")
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
