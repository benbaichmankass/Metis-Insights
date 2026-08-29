#!/usr/bin/env python3
# wiring: manual-only - an on-demand comparison of two COMMITTED corpus revisions.
# It reads git objects, has no scheduled consumer, and is invoked by a session
# doing the D2/N7 old-vs-new reading. Registered in RESEARCH-CAPABILITY-INDEX.md.
"""Compare two revisions of `docs/research/e35-bracket-corpus.jsonl`.

⚠️ **THE OBVIOUS CONTROL IS UNSATISFIABLE, AND THIS SCRIPT EXISTS PARTLY TO SAY SO.**

`WORKPLAN-NIGHT-2026-08-29.md` § 0 specified a correctness control: the 23 legs
graded CLEAN on the timeout axis must come back **numerically identical** after
the base-arm pin, "or the pin changed something it should not have".

That control **cannot pass on any re-run taken on a different day**, and the
reason is not the pin. The sweep fetches a TRAILING window (`days=1830` ending
at run time), so a re-run moves **both** edges of the data:

  * the RIGHT edge grows — the current partial year gains bars;
  * the LEFT edge advances — the earliest year loses bars.

Measured on the 2026-08-26 → 2026-08-29 pair: walk-forward folds **2021-2025
reproduce byte-identically** on `gld_pullback_1h|sm1.5`, and only the **2026**
fold moves. Where a CLOSED fold also moves it is the earliest one (2021, on
`avax_pullback_2h`) — the left edge, not a computation change.

So `--control` reports the identity check AND the fold-level reading that
explains it. A bare "3,515 cells differ" is true and worthless; it is what a
future session would have escalated as a false alarm.

⚠️ **AND `base == to400` IS NOT THE POST-PIN CONTROL EITHER.** Before the pin
the base arm carried the harness default (48/200 bars), so base-vs-`to400`
measured *does the default bind*. After the pin base carries
`NO_BAR_COUNT_EXIT` (1e9), so the same comparison measures *does 400 bind* — a
different question, on which a long-hold leg differs CORRECTLY. Reading a
non-zero residual there as "the pin half-worked" is a false finding; `--pin`
reports the residual **split by the leg's prior grade**, which is what makes it
interpretable.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from typing import Any

CORPUS = "docs/research/e35-bracket-corpus.jsonl"
# Fields whose change would mean the MEASUREMENT moved, not just its metadata.
VALUE_FIELDS = ("net_total_r", "net_expectancy_r", "gate_verdict",
                "wf_wins_effective", "max_drawdown_r", "d_net_r")
EPS = 1e-9


def _load_rev(rev: str, path: str = CORPUS) -> dict[str, dict]:
    blob = subprocess.run(["git", "show", f"{rev}:{path}"],
                          capture_output=True, text=True, check=True).stdout
    out: dict[str, dict] = {}
    for line in blob.splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["measurement_key"]] = r
    return out


def _folds(row: dict) -> dict[str, tuple]:
    return {f["fold"]: (f.get("d_net_r"), f.get("d_max_dd"))
            for f in (row.get("wf_folds") or [])}


def control(before: dict, after: dict, clean: set[str]) -> dict[str, Any]:
    """The § 0 identity check, plus the fold reading that explains its result.

    `only_current_fold` / `also_closed_fold` are counted over the cells that
    CARRY fold data — a much smaller population than the differing cells, and
    the caller must state that denominator rather than quoting the ratio alone.
    """
    compared = differing = 0
    only_current = also_closed = no_folds = 0
    closed_movers: list[tuple[str, list[str]]] = []
    missing: list[str] = []
    for key, b in before.items():
        if b["leg"] not in clean:
            continue
        a = after.get(key)
        if a is None:
            missing.append(key)
            continue
        compared += 1
        if not any(b.get(f) != a.get(f) for f in VALUE_FIELDS):
            continue
        differing += 1
        bf, af = _folds(b), _folds(a)
        if not bf or not af:
            no_folds += 1
            continue
        changed = {f for f in bf.keys() & af.keys() if bf[f] != af[f]}
        newest = max(bf.keys() | af.keys())
        if changed <= {newest}:
            only_current += 1
        else:
            also_closed += 1
            closed_movers.append((key, sorted(changed)))
    return {"legs": len(clean), "compared": compared, "differing": differing,
            "identical": compared - differing, "missing_in_after": missing,
            "fold_data_present": only_current + also_closed,
            "only_current_fold": only_current, "also_closed_fold": also_closed,
            "no_fold_data": no_folds, "closed_movers": closed_movers}


def pin(rows: dict, clean: set[str]) -> dict[str, Any]:
    """base-vs-`to400` divergence, SPLIT by the leg's prior timeout grade.

    Unsplit this is uninterpretable: a long-hold leg differs correctly once the
    base arm is infinite. The load-bearing number is how many PREVIOUSLY-CLEAN
    legs appear — that set must be EMPTY, because a leg with no trade past
    48/200 bars certainly has none past 400.
    """
    base: dict[tuple, dict] = {}
    ref: dict[tuple, dict] = {}
    for r in rows.values():
        if not str(r.get("source", "")).endswith("report.json"):
            continue
        g = (r["leg"], r.get("tp_r"), r.get("stop_mult"), r.get("tp_cap_pct"))
        t = r.get("timeout")
        if t is None:
            base[g] = r
        elif t == 400:
            ref[g] = r
    shared = [g for g in base.keys() & ref.keys()
              if isinstance(base[g].get("net_total_r"), (int, float))
              and isinstance(ref[g].get("net_total_r"), (int, float))]
    diverging = [g for g in shared
                 if abs(base[g]["net_total_r"] - ref[g]["net_total_r"]) > EPS]
    legs = {g[0] for g in diverging}
    return {"pairs": len(shared), "diverging": len(diverging),
            "legs": sorted(legs),
            "legs_previously_clean": sorted(legs & clean),
            "legs_previously_contaminated": sorted(legs - clean)}


def _clean_legs(path: str) -> set[str]:
    out = set()
    for line in open(path):
        parts = line.split()
        if len(parts) == 2 and parts[1] == "clean":
            out.add(parts[0])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--before", help="git rev holding the BEFORE corpus")
    ap.add_argument("--after", help="git rev holding the AFTER corpus")
    ap.add_argument("--clean-legs",
                    help="file of '<leg> clean|contaminated' lines (timeout_binding_audit)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()
    missing = [n for n, v in (("--before", args.before), ("--after", args.after),
                              ("--clean-legs", args.clean_legs)) if not v]
    if missing:
        ap.error("required unless --self-test: " + ", ".join(missing))

    clean = _clean_legs(args.clean_legs)
    B, A = _load_rev(args.before), _load_rev(args.after)

    print(f"corpus  BEFORE {args.before}: {len(B)} keys")
    print(f"corpus  AFTER  {args.after}: {len(A)} keys")
    lost = B.keys() - A.keys()
    print(f"keys LOST between the two revisions: {len(lost)}"
          + ("  ⚠️ a union must never lose a key" if lost else ""))

    c = control(B, A, clean)
    print(f"\n=== CONTROL (run FIRST) — {c['legs']} CLEAN legs ===")
    print(f"  cells compared : {c['compared']}")
    print(f"  identical      : {c['identical']}")
    print(f"  differing      : {c['differing']}")
    if c["differing"]:
        print("  ⚠️ the § 0 control as WRITTEN ('numerically identical') FAILS.")
        print(f"  Of the differing cells, {c['fold_data_present']} carry fold data "
              f"(the rest, {c['no_fold_data']}, were never carried into the gate "
              f"and cannot be checked this way — state that denominator):")
        print(f"     only the CURRENT (partial) fold moved : {c['only_current_fold']}")
        print(f"     a CLOSED fold also moved              : {c['also_closed_fold']}")
        for k, ch in c["closed_movers"][:5]:
            print(f"        {k}  folds={ch}")

    p = pin(A, clean)
    print("\n=== base-vs-to400 on the AFTER corpus ===")
    print(f"  pairs {p['pairs']}  diverging {p['diverging']}  legs {len(p['legs'])}")
    print(f"  previously CONTAMINATED : {len(p['legs_previously_contaminated'])}")
    print(f"  previously CLEAN        : {len(p['legs_previously_clean'])}"
          + ("  <-- MUST BE 0" if not p["legs_previously_clean"] else "  ⚠️ NON-EMPTY — a clean leg regressed"))
    return 0


def _self_test() -> int:
    def row(leg, key, tf=None, net=1.0, folds=None, src="x/report.json", **kw):
        d = {"leg": leg, "measurement_key": key, "timeout": tf, "net_total_r": net,
             "source": src, "tp_r": kw.get("tp_r"), "stop_mult": kw.get("sm"),
             "tp_cap_pct": 0.099, "wf_folds": folds}
        return d

    # 1. identical clean leg -> control clean.
    B = {"k": row("L", "k", net=1.0)}
    A = {"k": row("L", "k", net=1.0)}
    c = control(B, A, {"L"})
    assert c["differing"] == 0 and c["identical"] == 1, c

    # 2. only the newest fold moves -> classified as the window, not a computation change.
    f1 = [{"fold": "2021", "d_net_r": 1, "d_max_dd": 1}, {"fold": "2026", "d_net_r": 2, "d_max_dd": 2}]
    f2 = [{"fold": "2021", "d_net_r": 1, "d_max_dd": 1}, {"fold": "2026", "d_net_r": 9, "d_max_dd": 2}]
    c = control({"k": row("L", "k", net=1.0, folds=f1)},
                {"k": row("L", "k", net=2.0, folds=f2)}, {"L"})
    assert c["differing"] == 1 and c["only_current_fold"] == 1 and c["also_closed_fold"] == 0, c

    # 3. a CLOSED fold moving is counted separately — it is NOT explained by the right edge.
    f3 = [{"fold": "2021", "d_net_r": 7, "d_max_dd": 1}, {"fold": "2026", "d_net_r": 2, "d_max_dd": 2}]
    c = control({"k": row("L", "k", net=1.0, folds=f1)},
                {"k": row("L", "k", net=2.0, folds=f3)}, {"L"})
    assert c["also_closed_fold"] == 1 and c["only_current_fold"] == 0, c

    # 4. a contaminated leg is OUT of the control population.
    c = control({"k": row("C", "k", net=1.0)}, {"k": row("C", "k", net=5.0)}, {"L"})
    assert c["compared"] == 0, c

    # 5. a key missing from AFTER is reported, never silently skipped.
    c = control({"k": row("L", "k")}, {}, {"L"})
    assert c["missing_in_after"] == ["k"], c

    # 6. pin(): a previously-CLEAN leg diverging is the alarming case and is split out.
    rows = {"b": row("L", "b", tf=None, net=1.0), "f": row("L", "f", tf=400, net=2.0)}
    p = pin(rows, {"L"})
    assert p["diverging"] == 1 and p["legs_previously_clean"] == ["L"], p

    # 7. ...and a previously-CONTAMINATED leg diverging is the EXPECTED case.
    p = pin(rows, set())
    assert p["legs_previously_clean"] == [] and p["legs_previously_contaminated"] == ["L"], p

    # 8. POSITIVE CONTROL — equal base/to400 must NOT register, or case 6 passes vacuously.
    rows2 = {"b": row("L", "b", tf=None, net=1.0), "f": row("L", "f", tf=400, net=1.0)}
    assert pin(rows2, set())["diverging"] == 0

    print("e35_resweep_verdict_diff self-test: OK (8 cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
