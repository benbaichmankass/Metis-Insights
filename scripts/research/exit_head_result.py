#!/usr/bin/env python3
"""Map `research-exit-head-build`'s own output onto the ONE result contract.

WHY THIS IS A SCRIPT AND NOT A HEREDOC IN THE WORKFLOW
------------------------------------------------------
This is the piece that CANNOT be shared: the schema, the commit and the landing
assertion are owned once by `.github/actions/research-result`, but *"which of my
fields is the verdict, and what is my n"* is producer-specific by construction.

It still must not live inline. A mapping embedded in YAML is a transform with no
test: nothing can plant a broken producer output and assert this reads it
correctly, so the first time it mis-reads a verdict the result record is wrong
and green. `docs/CLAUDE-RULES-CANONICAL.md` § "WHAT ENFORCES THIS RULE" puts
*"put the assertion inside the transform"* second only to a guard, and records
that it was the only self-verification that worked at all.

THE THREE READ-STATES THIS PRODUCER CAN BE IN, NEVER COLLAPSED
---------------------------------------------------------------
  producer_failed   the `build` job did not conclude `success`, or wrote no
                    readable `exit_head.json` — WE LOOKED AND IT BROKE
  no_data           `exit_head.json` exists and neither the discrimination nor
                    the exit-policy block was computed — an empty population,
                    which is a measurement of the world, not a broken tool
  measured          at least one block computed; the verdict is READ off the
                    workflow's own pre-registered two-clause bar
                    (`verdict.clears_bar`), never re-derived here

⚠️ `clears_bar` MISSING IS `indeterminate`, NOT `fail`. The bar not having been
evaluated and the bar having been failed are opposite facts about the run, and
defaulting a missing flag to `fail` would manufacture a negative finding out of
a plumbing gap.

⚠️ n IS THE POLICY TRADE COUNT, because that is the denominator the headline
net-R improvement is actually made over. It falls back to the panel's
`trades_used` only when the policy block did not run, and to `0` only when the
producer genuinely reported no trades — never to a fabricated figure. With
`read_state != measured` it is `null`, because "we could not look" and "we
looked and there were none" are opposite statements.

Tier-1: reads one JSON file, writes one JSON file. No live path, no network.

Run:
    python3 scripts/research/exit_head_result.py --self-test
    python3 scripts/research/exit_head_result.py \\
        --exit-head collected/exit_head.json --build-result success \\
        --out-dir rr --population "..."
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

#: Read off `exit_head.json`'s own verdict block. Registered in this file,
#: before any run — that is what makes it a pre-registration rather than a
#: description of the answer.
DECISION_RULE_ID = "RULE-EXIT-HEAD-CLEARS-BAR"
DECISION_RULE_REGISTERED_AT = "2026-09-22"


def _as_int(value: Any) -> Optional[int]:
    """int(value) for a real number, None for anything else — including bool.

    `isinstance(True, int)` is True in Python, and a boolean silently becoming
    n=1 is exactly the fabricated-denominator failure this repo files as its own
    bug class.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def derive(head: Optional[Dict[str, Any]], *, build_result: str,
           manifest: Optional[Dict[str, Any]] = None
           ) -> Tuple[str, str, Optional[int], Dict[str, Any], str]:
    """Return (verdict, read_state, n, measurement, note). Pure — no I/O.

    ⚠️ `head` IS `exit_head.json` AND `manifest` IS
    `exit_panel.jsonl.manifest.json`, AND THEY CARRY DIFFERENT FIELDS. The
    trade count lives on the MANIFEST (`trades_used`); `exit_head.json` carries
    `n_rows_total` / `n_rows_usable`, which are PANEL ROWS — several per trade
    (the workflow's own verdict renderer prints `rows_per_trade`). Reading a
    row count as a trade denominator would overstate n by that factor, so the
    two files are passed separately rather than merged.
    """
    manifest = manifest or {}
    if build_result != "success" or head is None:
        return (
            "not_applicable", "producer_failed", None,
            {"build_job_result": build_result, "exit_head_json_present": head is not None},
            "The build job did not conclude success, or wrote no readable "
            "exit_head.json. This record exists so the run is not silent: it "
            "says WE LOOKED AND IT BROKE, which is a different and more "
            "actionable fact than no record at all.",
        )

    # ⚠️ THE DISCRIMINATION BLOCK IS `regression`, NOT `oos_discrimination`.
    # VERIFIED 2026-09-22 by reading `scripts/research/analyze_exit_head.py`
    # (`report["regression"]`, `report["exit_policy"]`, `report["verdict"]`) and
    # the workflow's own VERDICT.md renderer, which does
    # `reg = r.get("regression", {})`. A plausible-sounding key name here would
    # have read an empty dict on every real run, graded `computed` False, and
    # landed `no_data` on a healthy build — a wrong record, green, forever.
    reg = head.get("regression") or {}
    pol = head.get("exit_policy") or {}
    v = head.get("verdict") or {}
    measurement = {
        "harness": head.get("harness") or manifest.get("harness"),
        "n_rows_total": head.get("n_rows_total"),
        "n_rows_usable": head.get("n_rows_usable"),
        "manifest_row_count": manifest.get("row_count"),
        "manifest_trades_used": manifest.get("trades_used"),
        "base_hold_rate": head.get("base_hold_rate"),
        "label_config": head.get("label_config"),
        "config": head.get("config"),
        "oos_auc": reg.get("oos_auc"),
        "oos_auc_by_fold": reg.get("oos_auc_by_fold"),
        "folds_above_half": reg.get("folds_above_half"),
        "n_folds_used": reg.get("n_folds_used"),
        "mean_net_r_improvement": pol.get("mean_net_r_improvement"),
        "mean_head_r": pol.get("mean_head_r"),
        "mean_baseline_r": pol.get("mean_baseline_r"),
        "n_policy_trades": pol.get("n_policy_trades"),
        "pct_trades_early_exited": pol.get("pct_trades_early_exited"),
        "head_sharpe": pol.get("head_sharpe"),
        "head_psr": pol.get("head_psr"),
        "verdict_block": v,
    }

    if not (reg.get("computed") or pol.get("computed")):
        return (
            "not_applicable", "no_data", None, measurement,
            "exit_head.json exists but neither the OOS-discrimination nor the "
            "exit-policy block was computed. That is an EMPTY POPULATION — a "
            "measurement of the world — not a broken producer, and the two are "
            "deliberately not collapsed.",
        )

    clears = v.get("clears_bar")
    if clears is True:
        verdict = "pass"
    elif clears is False:
        verdict = "fail"
    else:
        verdict = "indeterminate"

    # n IS TRADES, NEVER PANEL ROWS. The policy simulation's own trade count
    # first, because that is the denominator the headline net-R improvement is
    # actually made over; the manifest's `trades_used` second. `0` only when
    # the producer genuinely reported neither — never a row count standing in
    # for a trade count.
    n = _as_int(pol.get("n_policy_trades"))
    if n is None:
        n = _as_int(manifest.get("trades_used"))
    if n is None:
        n = 0
    return verdict, "measured", n, measurement, str(v.get("note") or "")


def _write_outputs(verdict: str, read_state: str, n: Optional[int],
                   measurement: Dict[str, Any], population: str,
                   out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "measurement.json").write_text(
        json.dumps(measurement, indent=2, sort_keys=True), encoding="utf-8")
    n_out = "null" if n is None else str(n)
    gh = os.environ.get("GITHUB_OUTPUT")
    lines = [f"verdict={verdict}", f"read_state={read_state}", f"n={n_out}",
             f"population={population}"]
    if gh:
        with open(gh, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))


def _self_test() -> int:
    failures = 0

    def case(label: str, head, build_result: str, want_verdict: str,
             want_state: str, want_n, man=None) -> None:
        nonlocal failures
        verdict, state, n, _m, _note = derive(
            head, build_result=build_result,
            manifest=manifest if man is None else man)
        ok = (verdict, state, n) == (want_verdict, want_state, want_n)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failures += 1
            print(f"        got ({verdict!r}, {state!r}, {n!r}) "
                  f"want ({want_verdict!r}, {want_state!r}, {want_n!r})")

    # ⚠️ THE FIXTURE USES THE PRODUCER'S REAL KEY NAMES, read off
    # `analyze_exit_head.py` rather than invented. A fixture that agrees with a
    # wrong mapping is a self-test that proves the bug.
    full = {
        "harness": "backtest_system", "n_rows_total": 4400, "n_rows_usable": 4400,
        "regression": {"computed": True, "oos_auc": 0.58,
                       "folds_above_half": 5, "n_folds_used": 5},
        "exit_policy": {"computed": True, "mean_net_r_improvement": 0.31,
                        "n_policy_trades": 118},
        "verdict": {"clears_bar": True, "note": "both criteria met"},
    }
    manifest = {"harness": "backtest_system", "row_count": 4400, "trades_used": 220}

    # POSITIVE CONTROLS — a clean pass and a clean fail must both be reachable.
    case("positive: clears_bar=True -> pass/measured/n=118",
         full, "success", "pass", "measured", 118)
    case("positive: clears_bar=False -> fail/measured",
         {**full, "verdict": {"clears_bar": False}}, "success",
         "fail", "measured", 118)

    # NEGATIVE CONTROLS — each planted defect must land in its OWN state.
    case("negative: a failed build -> producer_failed, n=null (never 0)",
         full, "failure", "not_applicable", "producer_failed", None)
    case("negative: no artifact at all -> producer_failed, n=null",
         None, "success", "not_applicable", "producer_failed", None)
    case("negative: neither block computed -> no_data, n=null",
         {**full, "regression": {"computed": False},
          "exit_policy": {"computed": False}}, "success",
         "not_applicable", "no_data", None)
    case("negative: clears_bar MISSING -> indeterminate, NOT fail",
         {**full, "verdict": {}}, "success", "indeterminate", "measured", 118)
    case("negative: no policy denominator falls back to the MANIFEST's "
         "trades_used (220), not to a panel ROW count (4400)",
         {**full, "exit_policy": {"computed": True}}, "success",
         "pass", "measured", 220)
    case("negative: a boolean where a count belongs is NOT read as n=1",
         {**full, "exit_policy": {"computed": True, "n_policy_trades": True}},
         "success", "pass", "measured", 220)
    case("negative: no denominator ANYWHERE -> n=0, never a row count",
         {**full, "exit_policy": {"computed": True}}, "success",
         "pass", "measured", 0, man={})
    # THE KEY-NAME CONTROL. If someone renames the mapping back to a key the
    # producer does not write, this fires: a healthy, computed run must never
    # grade `no_data`.
    case("negative: the OLD wrong key name (`oos_discrimination`) does NOT "
         "make a computed run read as no_data",
         {**full, "oos_discrimination": full["regression"]}, "success",
         "pass", "measured", 118)

    # The whole point of the mapping: whatever it emits must be ADMISSIBLE to
    # the schema. A mapping that produces a record the validator refuses turns
    # a successful run into a failed landing, at the last step, hours in.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from research_result import build, validate  # noqa: E402
    for label, head, br in [("measured", full, "success"),
                            ("producer_failed", None, "success"),
                            ("no_data", {**full, "regression": {"computed": False},
                                         "exit_policy": {"computed": False}}, "success")]:
        verdict, state, n, meas, note = derive(head, build_result=br,
                                               manifest=manifest)
        rec = build(research_unit="RQ-20260922-007",
                    decision_rule_id=DECISION_RULE_ID,
                    decision_rule_registered_at=DECISION_RULE_REGISTERED_AT,
                    verdict=verdict, read_state=state,
                    population_description="a stated population", n=n,
                    workflow="research-exit-head-build.yml", run_id="1",
                    commit_sha="abc", tool="t", measurement=meas,
                    artifact_store="research/results/", artifact_locator="x",
                    note=note)
        problems = validate(rec)
        ok = not problems
        print(f"  {'PASS' if ok else 'FAIL'}  every {label} mapping is ADMISSIBLE "
              f"to research_result.validate()")
        if not ok:
            failures += 1
            print(f"        {problems!r}")

    print(f"exit_head_result --self-test: {failures} failure(s)")
    return 1 if failures else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--exit-head", default="collected/exit_head.json")
    ap.add_argument("--manifest",
                    default="collected/exit_panel.jsonl.manifest.json",
                    help="The PANEL manifest — where `trades_used` lives. It is "
                         "NOT in exit_head.json, and a panel row count is not a "
                         "trade count.")
    ap.add_argument("--build-result", default="success")
    ap.add_argument("--out-dir", default="rr")
    ap.add_argument("--population", default="")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    src = Path(args.exit_head)
    head: Optional[Dict[str, Any]] = None
    if src.exists():
        try:
            loaded = json.loads(src.read_text(encoding="utf-8"))
            head = loaded if isinstance(loaded, dict) else None
        except (json.JSONDecodeError, OSError) as exc:
            # NARROW, and the cause is PRINTED rather than swallowed: the run
            # still lands a `producer_failed` record, which is the point, but a
            # reader of the log must be able to see why.
            print(f"::warning::exit_head_result: {src} unreadable — {type(exc).__name__}: {exc}")
    man: Optional[Dict[str, Any]] = None
    mpath = Path(args.manifest)
    if mpath.exists():
        try:
            loaded = json.loads(mpath.read_text(encoding="utf-8"))
            man = loaded if isinstance(loaded, dict) else None
        except (json.JSONDecodeError, OSError) as exc:
            print(f"::warning::exit_head_result: {mpath} unreadable — "
                  f"{type(exc).__name__}: {exc}")
    verdict, read_state, n, measurement, note = derive(
        head, build_result=args.build_result, manifest=man)
    measurement["note"] = note
    _write_outputs(verdict, read_state, n, measurement, args.population,
                   Path(args.out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
