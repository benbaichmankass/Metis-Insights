#!/usr/bin/env python3
"""Flatten M20 fleet-sweep `verdicts.json` files into ONE durable per-cell corpus.

WHY THIS EXISTS. `m20_fleet_exit_sweep.py` already emits everything a Path B
threshold needs — per-cell IS/OOS deltas, capital rates, the derived drawdown
allowance, walk-forward folds — into `verdicts.json`. That file goes to a GitHub
Actions **artifact**, which no Claude session can download (`CLAUDE.md` § PM-side
session capabilities: no artifact download), and the PR comment carries a
**top-30** slice of one table. So the evidence for the operator's standing ask —
*"use capital-utilisation and PnL optimisation to decide what the correct number
is, database decisions and not arbitrary guesses"* — was being produced and then
discarded on every run, and each sweep started the population over from zero.

This turns the artifact into an accumulating, versioned corpus in the repo.

TWO PROPERTIES IT EXISTS TO PRESERVE, both about the denominator:

  1. **A leg that produced no cells still gets a row.** A harness error, a
     missing frame, or a skipped leg is recorded as `kind:"leg_status"`. A corpus
     that silently omits them would let a later analysis report "38 of 40 cells
     generalised" over a fleet where a third of the legs never ran — the
     unasserted-denominator failure, one level up from where it usually bites.

  2. **A cell that was never walk-forwarded is distinguishable from one that was
     and scored 0.** `wf_ran` is an explicit boolean and `wf_wins`/`wf_usable`
     are `None`, never `0`, when no walk-forward ran. Those are opposite
     statements about generalisation.

MERGE SEMANTICS — keyed on the MEASUREMENT, not on the run.

A row's identity is `(leg, cell, split, tp_cap_pct)`: what was measured, over
which windows, against which exit geometry. The newest `sweep_generated_at`
wins. Keying on the run id instead — the obvious choice, and the one this file
shipped with first — is wrong in a way that corrupts the analysis silently:
re-sweeping the same legs produces a NEW run id, so both copies survive and the
population doubles without gaining one bit of information. Tonight's 4th and 5th
dispatches are the worked example — byte-identical numbers on every leg, two run
ids. A floor analysis reading that corpus would see 22 cells over 9 legs instead
of 11 and report a denominator twice its real size.

`tp_cap_pct` is IN the key, not metadata. The same `(leg, cell, split)` measured
at the legacy no-TP geometry and at live parity are two different numbers about
two different books (`BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP`),
and collapsing them would re-commit that defect one level up. A run predating
the field records `tp_cap_pct: null`, which keys DISTINCTLY from any known
geometry — "we do not know which book this measured" is its own bucket, never
silently merged into the current one.

Every row still carries `sweep_generated_at` and `run_id`, so a vintage can be
excluded explicitly rather than by hoping it was overwritten — which is what the
2026-08-10 config-exactness defect needed
(`BL-20260810-SWEEP-BASE-NOT-CONFIG-EXACT-TRAILVOL` — kept on one line: a
backlog id hyphen-broken across a wrap resolves to nothing and reads as tracked
while being tracked by nobody, which `artifact-validity-guard` fails on).

Usage:
    python3 scripts/research/m20_corpus_extract.py \
        --in out/ --corpus docs/research/m20-sweep-corpus.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CORPUS_DEFAULT = "docs/research/m20-sweep-corpus.jsonl"


def _num(d: dict | None, key: str):
    """Read a numeric field, preserving None. Never coerces a missing value to 0."""
    if not isinstance(d, dict):
        return None
    v = d.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _win(block: dict | None, window: str) -> dict:
    if not isinstance(block, dict):
        return {}
    got = block.get(window)
    return got if isinstance(got, dict) else {}


def measurement_key(row: dict) -> tuple:
    """WHAT this row measured — the merge identity. Never includes the run.

    `tp_cap_pct` is part of it: the same cell at the legacy no-TP geometry and at
    live parity are two different measurements. `None` (a run predating the
    field) keys distinctly from every known geometry rather than merging into
    one — an unknown book is its own state, not the current book.
    """
    return (row.get("kind"), row.get("leg"), row.get("cell"),
            row.get("split"), row.get("tp_cap_pct"))


def rows_from_verdicts(doc: dict, run_id: str) -> list[dict]:
    """One row per (leg, cell), plus one per leg that produced no cells."""
    out: list[dict] = []
    gen = doc.get("generated_at")
    split = doc.get("split")
    # A run predating the field records None — NOT a default of the current
    # geometry, which would silently relabel a legacy no-TP measurement as
    # live-parity and merge two different books under one key.
    tp_cap = doc.get("tp_cap_pct")
    verdicts = doc.get("verdicts") or {}

    # Legs the planner skipped never reach `verdicts` at all. They are part of
    # the fleet denominator, so they are rows too.
    for s in doc.get("skipped") or []:
        out.append({"kind": "leg_status", "run_id": run_id,
                    "sweep_generated_at": gen, "split": split, "tp_cap_pct": tp_cap,
                    "leg": s.get("leg"), "cell": None,
                    "leg_status": "skipped", "leg_status_why": s.get("reason")})

    for leg, v in verdicts.items():
        if not isinstance(v, dict):
            continue
        if "levers" not in v:
            out.append({"kind": "leg_status", "run_id": run_id,
                        "sweep_generated_at": gen, "split": split,
                        "tp_cap_pct": tp_cap, "leg": leg, "cell": None,
                        "leg_status": v.get("status") or "no_levers",
                        "leg_status_why": v.get("error")})
            continue

        base = v.get("base_book") or {}
        sel = v.get("selection") or {}
        # A leg whose sweep predates the `base_book` block (added 2026-08-10)
        # carries no rate. That is recorded as its own state rather than left to
        # look like an ungradeable book — an OLD CORPUS and an UNPROFITABLE BOOK
        # would otherwise be indistinguishable, and only one of them is evidence.
        base_present = bool(base)
        leg_common = {
            "run_id": run_id, "sweep_generated_at": gen, "split": split,
            "tp_cap_pct": tp_cap, "leg": leg, "proxy": v.get("proxy"),
            "base_book_present": base_present,
            "cells_tried": sel.get("cells_tried"),
            "cells_withheld_inert": sel.get("cells_withheld_inert"),
        }
        for w in ("IS", "OOS"):
            b = _win(base, w)
            leg_common[f"base_net_r_{w}"] = _num(b, "net_total_r")
            leg_common[f"base_max_dd_{w}"] = _num(b, "max_drawdown_r")
            leg_common[f"base_rate_{w}"] = _num(b, "net_r_per_drawdown_r")
            leg_common[f"base_rate_ungradeable_why_{w}"] = (
                b.get("rate_ungradeable_why") if base_present else "no_base_book_in_run")
            leg_common[f"base_cap_day_{w}"] = _num(b, "net_r_per_capital_day")
            leg_common[f"base_trades_{w}"] = b.get("total_trades")

        if not v.get("levers"):
            out.append({**leg_common, "kind": "leg_status", "cell": None,
                        "leg_status": "no_cells", "leg_status_why": None})
            continue

        for lever, entries in (v.get("levers") or {}).items():
            for e in entries or []:
                if not isinstance(e, dict):
                    continue
                g_is, g_oos = _win(e.get("gate"), "IS"), _win(e.get("gate"), "OOS")
                c_is, c_oos = _win(e.get("capital"), "IS"), _win(e.get("capital"), "OOS")
                x_is, x_oos = (_win(e.get("dd_exchange_rate"), "IS"),
                               _win(e.get("dd_exchange_rate"), "OOS"))
                wf = e.get("walkforward")
                wins = usable = None
                if isinstance(wf, str) and "/" in wf:
                    try:
                        a, b_ = wf.split("/", 1)
                        wins, usable = int(a), int(b_)
                    except ValueError:
                        wins = usable = None
                row = {**leg_common, "kind": "cell", "lever": lever,
                       "cell": e.get("cell"), "verdict": e.get("verdict"),
                       "is_oos_pass": e.get("is_oos_pass"),
                       "path_b_candidate": bool(e.get("path_b_candidate")),
                       # THREE-STATE, and NOT `bool(...)`: True / False / None
                       # ("no window was gradeable") are three different findings
                       # and `bool()` would silently turn the third into the
                       # second. Absent on rows written before the sweep emitted
                       # it — which reads as None, correctly, since those rows
                       # genuinely carry no verdict on the rate gate.
                       "path_b_rate_ok": e.get("path_b_rate_ok"),
                       # `wf_ran` is the honest flag: a cell that never reached a
                       # walk-forward is not a cell that failed one.
                       "wf_ran": wf is not None,
                       "wf_summary": wf, "wf_wins": wins, "wf_usable": usable,
                       "wf_folds": e.get("walkforward_folds")}
                for tag, g in (("IS", g_is), ("OOS", g_oos)):
                    row[f"d_net_r_{tag}"] = _num(g, "d_net_r")
                    row[f"d_max_dd_{tag}"] = _num(g, "d_max_dd")
                    row[f"gate_passed_{tag}"] = g.get("passed")
                    row[f"gate_reason_{tag}"] = g.get("reason")
                for tag, c in (("IS", c_is), ("OOS", c_oos)):
                    row[f"d_cap_day_{tag}"] = _num(c, "d_net_r_per_capital_day")
                    row[f"cell_cap_day_{tag}"] = _num(c, "cell_net_r_per_capital_day")
                    row[f"net_r_retained_frac_{tag}"] = _num(c, "net_r_retained_frac")
                    row[f"d_mean_bars_held_{tag}"] = _num(c, "d_mean_bars_held")
                for tag, x in (("IS", x_is), ("OOS", x_oos)):
                    row[f"headroom_{tag}"] = _num(x, "headroom")
                    row[f"allowed_d_max_dd_{tag}"] = _num(x, "allowed_d_max_dd")
                    row[f"rate_ok_{tag}"] = x.get("passes")
                    row[f"rate_reason_{tag}"] = x.get("reason")
                out.append(row)
    return out


def find_verdicts(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted(root.rglob("verdicts.json"))


def run_id_for(path: Path, doc: dict) -> str:
    """Stable id for the run a verdicts file came from.

    Prefers the sweep's own timestamp: two legs of the SAME matrix run land in
    different `out/<leg>/<date>/` directories, so a path-derived id would split
    one run into N and make a per-run row count meaningless.
    """
    gen = doc.get("generated_at")
    if isinstance(gen, str) and gen:
        return gen
    return str(path.parent)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", required=True,
                    help="Directory to search for verdicts.json (recursive), or one file.")
    ap.add_argument("--corpus", default=CORPUS_DEFAULT,
                    help=f"JSONL corpus to merge into (default {CORPUS_DEFAULT}).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would be merged; write nothing.")
    a = ap.parse_args(argv[1:])

    src = Path(a.inp)
    if not src.exists():
        print(f"error: --in path does not exist: {src}", file=sys.stderr)
        return 2
    files = find_verdicts(src)
    if not files:
        # A silent zero here would commit an unchanged corpus and read as "the
        # sweep added nothing", which is a different statement from "no verdicts
        # file was produced". Fail instead.
        print(f"error: no verdicts.json under {src} — nothing to extract. "
              "This is a failed extraction, NOT an empty sweep.", file=sys.stderr)
        return 1

    fresh: list[dict] = []
    runs: set[str] = set()
    for f in files:
        try:
            doc = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: unreadable {f}: {exc}", file=sys.stderr)
            return 1
        rid = run_id_for(f, doc)
        runs.add(rid)
        fresh.extend(rows_from_verdicts(doc, rid))

    corpus = Path(a.corpus)
    kept: list[dict] = []
    superseded = malformed = 0
    fresh_keys = {measurement_key(r) for r in fresh}
    if corpus.exists():
        for line in corpus.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                # COUNTED, not skipped silently. A corpus quietly shedding rows
                # to a parse error would shrink its own denominator invisibly.
                malformed += 1
                continue
            # Supersede by MEASUREMENT, not by run: re-sweeping the same leg
            # re-measures the same cell, and keeping both would double the
            # population without adding information.
            if measurement_key(r) in fresh_keys:
                superseded += 1
                continue
            kept.append(r)

    merged = kept + fresh
    # The invariant the merge exists to hold. Asserting it here means a future
    # edit to the key cannot silently reintroduce duplicates — the failure mode
    # is invisible in the corpus itself (rows look fine; only the COUNT is wrong).
    keys = [measurement_key(r) for r in merged]
    if len(keys) != len(set(keys)):
        from collections import Counter
        dupes = [k for k, n in Counter(keys).items() if n > 1]
        print(f"error: merge produced {len(keys) - len(set(keys))} duplicate "
              f"measurement key(s), e.g. {dupes[:3]}. The corpus would "
              "over-count its own population.", file=sys.stderr)
        return 1
    cells = sum(1 for r in merged if r.get("kind") == "cell")
    statuses = sum(1 for r in merged if r.get("kind") == "leg_status")
    rated = sum(1 for r in merged
                if r.get("kind") == "cell" and r.get("base_rate_IS") is not None)
    print(f"runs merged: {len(runs)}  new rows: {len(fresh)}  "
          f"superseded: {superseded}  malformed-dropped: {malformed}")
    print(f"corpus now: {len(merged)} rows = {cells} cells + {statuses} leg-status; "
          f"{rated}/{cells} cells carry a base rate")
    if a.dry_run:
        print("(dry run — nothing written)")
        return 0
    corpus.parent.mkdir(parents=True, exist_ok=True)
    corpus.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in merged))
    print("wrote", corpus)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
