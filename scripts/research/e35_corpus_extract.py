#!/usr/bin/env python3
"""Flatten e35 bracket-geometry `report.json` files into ONE durable per-cell corpus.

WHY THIS EXISTS
---------------
`BL-20260823-E35-SWEEP-EVIDENCE-HAS-NO-DURABLE-PATH`. The 2026-08-20 sweep
measured **19 legs x 199 grid cells = 3,781 cells**, and a later session could
not read a single one of them back:

* `.github/workflows/e35-bracket-sweep.yml` ends in two `upload-artifact` steps
  and a step summary. No extract, no commit.
* Root `CLAUDE.md` is explicit that a PM-side session has **no artifact
  download**.
* `docs/research/m20-sweep-corpus.jsonl` holds only M20 **lever** cells --
  measured 2026-08-23 at 1,379 rows, 331 of them donchian, and **zero** cells
  naming a stop.

So the sweep's own evidence was write-only, and re-interrogating any cell (a
dispersion test, a different gate, `wins_effective` beside `wins`) meant
re-running a multi-hour sweep from candles. One session did exactly that.

This is the same defect `m20_corpus_extract.py` was built to cure for the LEVER
sweep -- its docstring: *"each run restarted the population from zero and no
threshold could be derived from more than one sweep"* -- reproduced in the
newer bracket sweep.

⚠️ **A SEPARATE FILE, DELIBERATELY, NOT `m20-sweep-corpus.jsonl`.**
The two corpora describe different objects: a lever cell answers *"does this
exit lever help?"* and carries `lever`; a bracket cell answers *"does this
(tp, stop, timeout) geometry help?"* and carries `axis` + the three geometry
values. Merging them into one file would make a bare row count wrong for both
and hand every consumer the job of discriminating -- and this repo has already
paid for exactly that hazard, recorded in `m20_corpus_extract.py`: *"TWO SWEEPS
WRITE A FILE CALLED verdicts.json AND THE SCHEMAS ARE [different]"*. Two files
with two names is the cheap way not to repeat it. Every row here also carries
`corpus: "e35_bracket"` so a row that is ever copied elsewhere still says what
it is.

⚠️ **RE-EXTRACTING A RUN SUPERSEDES ITS ROWS, NEVER APPENDS THEM.** Identity is
`(leg, cell, tp_cap_pct, split_mode, split_target_oos)` and recency is the
run's own `generated_at`. A post-fix re-run must not leave the pre-fix vintage
in the population beside it -- the failure `m20_corpus_extract` names.

⚠️ **A FOREIGN `report.json` IS REFUSED LOUDLY**, not coerced. The markers are
taken from the reader below rather than guessed, so the refusal cannot drift
away from what this module actually needs.

Tier-1: research tooling. Reads a run directory, writes a docs corpus. Touches
no config and no order path.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

REPO = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = REPO / "docs" / "research" / "e35-bracket-corpus.jsonl"
CORPUS_TAG = "e35_bracket"

# Taken from `rows_from_report` below — the fields it actually dereferences.
_REQUIRED_TOP = ("legs", "generated_at", "tp_cap_pct")
_REQUIRED_LEG = ("leg", "symbol", "tf", "cells")


class ForeignReport(Exception):
    """This `report.json` was not written by the e35 bracket sweep."""


def _assert_e35(doc: Any, path: Path) -> None:
    if not isinstance(doc, dict):
        raise ForeignReport(f"{path}: top level is {type(doc).__name__}, not an object")
    missing = [k for k in _REQUIRED_TOP if k not in doc]
    if missing:
        raise ForeignReport(
            f"{path}: missing top-level {missing} — this is not an e35 bracket "
            f"report.json (the fleet/lever sweep writes verdicts.json with a "
            f"different schema; use m20_corpus_extract.py for that one)"
        )
    legs = doc.get("legs")
    if not isinstance(legs, list):
        raise ForeignReport(f"{path}: 'legs' is {type(legs).__name__}, not a list")
    for leg in legs:
        if not isinstance(leg, dict):
            raise ForeignReport(f"{path}: a legs[] entry is not an object")
        miss = [k for k in _REQUIRED_LEG if k not in leg]
        if miss:
            raise ForeignReport(f"{path}: legs[] entry missing {miss}")


def measurement_key(row: Dict[str, Any]) -> str:
    """Identity of a measurement. Re-running the same cell supersedes it."""
    return "|".join(str(row.get(k)) for k in
                    ("leg", "cell", "tp_cap_pct", "split_mode", "split_target_oos"))


def rows_from_report(doc: Dict[str, Any], source: str) -> List[Dict[str, Any]]:
    """One row per measured CELL, with the gate verdict joined on when present."""
    gen = doc.get("generated_at")
    cap = doc.get("tp_cap_pct")
    fee = doc.get("fee_bps_roundtrip")
    out: List[Dict[str, Any]] = []
    for leg in doc["legs"]:
        # gate rows are keyed by cell; a cell that was never gated keeps None,
        # which is NOT the same as a cell that was gated and failed.
        gate_by_cell = {g.get("cell"): g for g in (leg.get("gate") or [])
                        if isinstance(g, dict)}
        base = leg.get("base") or {}
        for cell in leg.get("cells") or []:
            if not isinstance(cell, dict):
                continue
            name = cell.get("cell")
            g = gate_by_cell.get(name)
            split_meta = (g or {}).get("split_meta") or {}
            row = {
                "corpus": CORPUS_TAG,
                "source": source,
                "sweep_generated_at": gen,
                "leg": leg.get("leg"),
                "symbol": leg.get("symbol"),
                "timeframe": leg.get("tf"),
                "family": leg.get("family"),
                "execution": leg.get("execution"),
                "cell": name,
                "axis": cell.get("axis"),
                # the geometry the cell actually ran
                "tp_r": cell.get("tp_r"),
                "stop_mult": cell.get("stop_mult"),
                "timeout": cell.get("timeout"),
                # the measurement
                "state": cell.get("state"),
                "net_total_r": cell.get("net_total_r"),
                "max_drawdown_r": cell.get("max_drawdown_r"),
                "net_expectancy_r": cell.get("net_expectancy_r"),
                "d_net_r": cell.get("d_net_r"),
                "d_max_dd": cell.get("d_max_dd"),
                "leverage": cell.get("leverage"),
                # base it was measured against
                "base_net_total_r": base.get("net_total_r"),
                "base_max_drawdown_r": base.get("max_drawdown_r"),
                # run settings that change what the number MEANS
                "tp_cap_pct": cap,
                "fee_bps_roundtrip": fee,
                "split_mode": split_meta.get("split_mode"),
                "split_target_oos": split_meta.get("split_target_oos"),
                # ⚠️ ACHIEVED OOS SAMPLE SIZE — NOT `split_target_oos`.
                #
                # `split_target_oos` is a TARGET and, measured over the whole
                # corpus on 2026-08-31, it is non-null on 377 of 8,321 rows
                # (4.5%) with exactly ONE distinct value: 50. It is a run
                # setting, not a measurement, so it cannot answer "was this
                # cell judged on enough data?" — and R4's power gate needs
                # exactly that. Without an achieved n every e35 unit is
                # UNGRADEABLE, which is where 28 of them sat.
                #
                # The producer has emitted the real counts all along
                # (`e35_bracket_geometry_sweep.gate`: `base_is_trades` /
                # `base_oos_trades`, both `run_cell(...)["total_trades"]`) and
                # this extractor simply never read them. Written-and-never-read
                # is the `exit_price_source` shape this repo already pays for.
                #
                # These are the BASE arm's counts, which is deliberate: the base
                # is the denominator every cell in the leg is judged against, so
                # it is constant per (leg, split) and comparable across cells.
                # `None` where a report predates the field — never 0, because a
                # zero would assert a measured empty sample.
                # Read off the GATE record (`g`), which is where the sweep
                # writes them — not the cell dict.
                "base_is_trades": (g or {}).get("base_is_trades"),
                "base_oos_trades": (g or {}).get("base_oos_trades"),
                # gate verdict — None means NOT GATED, never "gated and passed"
                "gate_verdict": (g or {}).get("verdict"),
                "gate_is_passed": ((g or {}).get("is") or {}).get("passed"),
                "gate_oos_passed": ((g or {}).get("oos") or {}).get("passed"),
                "gate_is_d_net_r": ((g or {}).get("is") or {}).get("d_net_r"),
                "gate_oos_d_net_r": ((g or {}).get("oos") or {}).get("d_net_r"),
                "gate_is_reason": ((g or {}).get("is") or {}).get("reason"),
                "gate_oos_reason": ((g or {}).get("oos") or {}).get("reason"),
                # PATH + WALK-FORWARD (added 2026-08-23). These were absent
                # because they could not exist: Path B was unreachable by
                # construction until the same day
                # (BL-20260823-E35-PATH-B-UNREACHABLE-RAW-RUNCELL-DICT), so no
                # e35 cell had ever reached a walk-forward and the corpus had
                # no folds to carry. Fixing the gate without extending the
                # corpus would make the newly-generated Path B evidence
                # write-only -- the exact defect this extractor exists to cure.
                #
                # `wf_ran` is the DENOMINATOR and is never inferred from a null
                # count: False means the cell never reached a walk-forward
                # (*we did not look*), which a `wf_wins: null` alone cannot
                # distinguish from a run that produced nothing.
                "gate_path": (g or {}).get("path"),
                "wf_ran": bool((g or {}).get("wf")),
                "wf_wins": (((g or {}).get("wf") or {}).get("summary") or {}).get("wins")
                           if isinstance(((g or {}).get("wf") or {}).get("summary"), dict)
                           else ((g or {}).get("wf") or {}).get("wins"),
                "wf_usable": (((g or {}).get("wf") or {}).get("summary") or {}).get("usable")
                             if isinstance(((g or {}).get("wf") or {}).get("summary"), dict)
                             else ((g or {}).get("wf") or {}).get("usable"),
                # BESIDE `wf_wins`, NEVER INSTEAD OF IT
                # (BL-20260817-FLEET-SWEEP-WF-COUNTS-INERT-FOLDS-AS-WINS): a
                # fold in which the cell changed nothing satisfies the win test
                # by construction, so the effective tally is what a reader
                # needs to know whether a 5/6 is five real wins or two.
                "wf_wins_effective": ((g or {}).get("wf") or {}).get("wins_effective"),
                "wf_inert_wins": ((g or {}).get("wf") or {}).get("inert_wins"),
                "wf_folds": ((g or {}).get("wf") or {}).get("folds"),
                # The measured per-fold DRAWDOWN COST -- Path B records this
                # rather than gating on it ("measure the axis first, threshold
                # it second"), and it is the distribution any drawdown
                # tolerance must be argued from.
                "capital_oos_d_net_r_per_capital_day":
                    (((g or {}).get("capital") or {}).get("OOS") or {})
                    .get("d_net_r_per_capital_day"),
            }
            row["measurement_key"] = measurement_key(row)
            out.append(row)
    return out


def load_corpus(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows, malformed = [], 0
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            # COUNTED, never swallowed: a dropped corpus row would silently
            # shrink the population and look like a smaller sweep.
            malformed += 1
    if malformed:
        raise ValueError(
            f"{path}: {malformed} unparseable row(s) — refusing to rewrite a "
            f"corpus I cannot read in full, because the rewrite would DROP them"
        )
    return rows


def merge(existing: List[Dict[str, Any]],
          incoming: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], int, int]:
    """(rows, superseded, added). Incoming supersedes on identity."""
    by_key = {r.get("measurement_key"): r for r in existing}
    superseded = added = 0
    for r in incoming:
        k = r.get("measurement_key")
        if k in by_key:
            superseded += 1
        else:
            added += 1
        by_key[k] = r
    return list(by_key.values()), superseded, added


def find_reports(paths: Iterable[str]) -> List[Path]:
    out: List[Path] = []
    for p in paths:
        pp = Path(p)
        if pp.is_file() and pp.name == "report.json":
            out.append(pp)
        elif pp.is_dir():
            out.extend(sorted(pp.rglob("report.json")))
    return out


def selftest() -> int:
    fails: List[str] = []
    def chk(label, got, want):
        if got != want:
            fails.append(f"{label}: got {got!r} want {want!r}")

    doc = {
        "generated_at": "2026-01-01T00:00:00+00:00", "tp_cap_pct": 0.099,
        "fee_bps_roundtrip": "x",
        "legs": [{
            "leg": "leg_a", "symbol": "S", "tf": "1h", "family": "donchian",
            "execution": "live", "base": {"net_total_r": 1.0, "max_drawdown_r": 2.0},
            "cells": [{"cell": "sm1.5", "axis": "stop", "stop_mult": 1.5,
                       "net_total_r": 5.0, "d_net_r": 4.0, "state": "ok"},
                      {"cell": "tp4", "axis": "tp", "tp_r": 4.0,
                       "net_total_r": 2.0, "d_net_r": 1.0, "state": "ok"}],
            "gate": [{"cell": "sm1.5", "verdict": "is_oos_fail",
                      "is": {"passed": False, "d_net_r": -1.0, "reason": "net_r_worse"},
                      "oos": {"passed": False, "d_net_r": -2.0, "reason": "net_r_worse"},
                      "split_meta": {"split_mode": "oos-trades", "split_target_oos": 50}}],
        }],
    }
    rows = rows_from_report(doc, "t")
    chk("one row per cell", len(rows), 2)
    g = [r for r in rows if r["cell"] == "sm1.5"][0]
    ung = [r for r in rows if r["cell"] == "tp4"][0]
    chk("gated cell keeps its verdict", g["gate_verdict"], "is_oos_fail")
    # the distinction this corpus exists to preserve
    chk("UNGATED cell verdict is None, not a pass", ung["gate_verdict"], None)
    chk("ungated cell has no is-passed", ung["gate_is_passed"], None)
    chk("geometry travels", (g["stop_mult"], ung["tp_r"]), (1.5, 4.0))
    chk("corpus tag stamped", g["corpus"], CORPUS_TAG)
    chk("run settings travel", (g["tp_cap_pct"], g["split_target_oos"]), (0.099, 50))
    # supersede, not append
    merged, sup, add = merge(rows, rows)
    chk("re-extract supersedes", (len(merged), sup, add), (2, 2, 0))
    doc2 = json.loads(json.dumps(doc))
    doc2["legs"][0]["leg"] = "leg_b"
    merged2, sup2, add2 = merge(rows, rows_from_report(doc2, "t"))
    chk("a different leg is ADDED", (len(merged2), sup2, add2), (4, 0, 2))
    # foreign schema refused LOUDLY
    for bad, why in ((["not", "a", "dict"], "list"),
                     ({"verdicts": {}}, "lever verdicts.json"),
                     ({"legs": [{"leg": "x"}], "generated_at": "t", "tp_cap_pct": 0.0}, "leg missing keys")):
        try:
            _assert_e35(bad, Path("p"))
            fails.append(f"foreign schema NOT refused: {why}")
        except ForeignReport:
            pass
    for f in fails:
        print("FAIL " + f)
    print("selftest: %d/%d passed" % (12 - len(fails), 12))
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="*", help="report.json files or dirs to scan")
    ap.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.paths:
        print("nothing to extract: pass report.json files or run dirs", file=sys.stderr)
        return 2
    reports = find_reports(a.paths)
    if not reports:
        # An empty scan is a REFUSAL, not a clean no-op — a silent success here
        # would look exactly like "the sweep produced nothing worth keeping".
        print(f"::error::no report.json found under {a.paths}", file=sys.stderr)
        return 2
    incoming: List[Dict[str, Any]] = []
    for r in reports:
        doc = json.loads(r.read_text())
        _assert_e35(doc, r)
        rows = rows_from_report(doc, str(r))
        print(f"  {r}: {len(rows)} cell(s), {len(doc['legs'])} leg(s)")
        incoming.extend(rows)
    # ---- RUN-SCOPED LANDING STAMPS -------------------------------------
    # Emitted so the caller can assert THIS RUN's rows landed, rather than
    # asserting the store is non-empty. `docs/research/e35-bracket-corpus.jsonl`
    # is CUMULATIVE, so a predicate like `--field cell --contains sm` is
    # satisfied by history and can never fail: measured 2026-08-30, the landing
    # assertion reported `landed - 6624 rows` for run 33306805155 whose rows
    # were ALL on an unmerged side branch (every one of the 6,624 predates it).
    #
    # `sweep_generated_at` is the report's own `generated_at`, one per SHARD, so
    # a stamp identifies the shard that produced it. A caller asserting one
    # stamp proves THAT shard's rows are readable -- which is what `min_rows: 1`
    # claims -- not that every shard landed. Stated because the two are
    # different claims and only the narrower one is supported.
    stamps = sorted({str(r["sweep_generated_at"]) for r in incoming
                     if r.get("sweep_generated_at")})
    for s in stamps:
        print(f"run-stamp: {s}")
    if not stamps:
        # NOT a warning. A caller that cannot scope its assertion must fail
        # rather than fall back to the cumulative predicate, which is the
        # defect this block exists to remove.
        print("::error::extracted rows carry no `sweep_generated_at` - a landing "
              "assertion cannot be scoped to this run", file=sys.stderr)
        return 2

    corpus = Path(a.corpus)
    existing = load_corpus(corpus)
    merged, sup, add = merge(existing, incoming)
    print(f"corpus {corpus}: {len(existing)} -> {len(merged)} rows "
          f"({add} added, {sup} superseded)")
    if a.dry_run:
        print("(dry-run: nothing written)")
        return 0
    corpus.parent.mkdir(parents=True, exist_ok=True)
    with corpus.open("w") as fh:
        for row in sorted(merged, key=lambda r: (str(r.get("leg")), str(r.get("cell")))):
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    # Verify what landed rather than trusting the write.
    back = load_corpus(corpus)
    if len(back) != len(merged):
        print(f"::error::wrote {len(merged)} rows but read back {len(back)}", file=sys.stderr)
        return 1
    print(f"verified {len(back)} rows on disk")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
