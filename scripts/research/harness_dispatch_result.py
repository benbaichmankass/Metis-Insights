#!/usr/bin/env python3
"""Map `research-harness-dispatch.yml`'s own output onto the ONE result contract.

WHY THIS IS A SCRIPT AND NOT A HEREDOC IN THE WORKFLOW
------------------------------------------------------
Same reasoning as `scripts/research/exit_head_result.py`: the schema, the
commit and the landing assertion are owned once by
`.github/actions/research-result`, but *"which of my fields is n"* is
producer-specific, and a mapping embedded in YAML cannot be planted with a
broken input and asserted against — the first mis-read lands a wrong record,
green. This is that mapping for the E7 orphan-harness dispatcher.

WHAT THIS RUN IS, AND WHAT IT IS NOT
-------------------------------------
`research-harness-dispatch.yml` exists to answer ONE question per dispatch:
*"does this harness now run end-to-end against a REAL, non-fixture corpus and
produce a numeric summary?"* — checklist row E7, "a harness with no workflow
runs when a human remembers it, which is never." It is a WIRING PROOF, not a
strategy verdict: the harness's own net-R does not say whether the strategy is
good, only that the harness ran. That is why every admissible record from this
mapping carries `verdict=no_action_warranted` on success — there is no action
to decide from a dispatch existing — never `pass`/`fail`, which would imply a
decision rule about the STRATEGY that this run was never registered to answer.

THE ONE DECISION RULE, REGISTERED HERE BEFORE ANY DISPATCH
-------------------------------------------------------------
RULE-E7-HARNESS-DISPATCHABLE (registered 2026-09-27, in this file, before the
first of the ten dispatches it grades): *if a `scripts/backtest_*.py` harness
named by checklist row E7 is dispatched through
`research-harness-dispatch.yml` against a real (non-fixture) corpus and exits
0 with a readable JSON summary, that harness is WIRED — dispatchable and
proven against real data.* A non-zero exit or an unreadable/unrecognized
summary means the opposite: the harness is NOT yet soundly wired, and the run
lands `read_state=producer_failed` rather than being silently swallowed as a
green step with nothing to show for it.

THREE READ-STATES THIS PRODUCER CAN BE IN, NEVER COLLAPSED
------------------------------------------------------------
  producer_failed   the `build` job did not conclude `success`, or its JSON
                    summary is missing / unreadable / carries no recognized
                    population field for the dispatched harness — WE LOOKED
                    AND IT BROKE (or we cannot tell it ran).
  measured          the build succeeded and a population count (`n`) could be
                    read off its own JSON, whatever that count is — including
                    zero, which is a real measurement ("ran, found no trades
                    in this window"), not a failure.

WHERE `n` COMES FROM, PER HARNESS SHAPE
------------------------------------------
Eight of the ten E7 harnesses (chop_scalp, fade, fvg_range, orb, pullback,
squeeze, funding_carry, pairs) share ONE `--json` payload shape with a
top-level `total_trades` integer — read that first, unconditionally, so it
also covers any future harness added to this family with the same shape.
`xsec_momentum`'s payload nests its population as `summary.n_days` (rebalance
days measured, not a trade count — a cross-sectional book has no single-trade
concept). `vol_target` nests it as `baseline.n_days` (the overlay is computed
over another harness's own `--emit-trades` daily series from the SAME
dispatch run, per the workflow's own comment). A harness whose shape matches
none of these yields `n=None`, which THIS MODULE never fabricates into 0 —
that would assert a measured empty sample nobody observed.

Tier-1: reads one JSON file, writes one JSON file. No live path, no network.

Run:
    python3 scripts/research/harness_dispatch_result.py --self-test
    python3 scripts/research/harness_dispatch_result.py \\
        --harness squeeze --symbol BTCUSDT --timeframe 1h \\
        --build-result success --result-json result.json --out-dir rr
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

DECISION_RULE_ID = "RULE-E7-HARNESS-DISPATCHABLE"
DECISION_RULE_REGISTERED_AT = "2026-09-27"

#: harnesses added to the E7 family later inherit this shape automatically —
#: the check is "does the payload carry `total_trades`", not a name allowlist.
_XSEC = "xsec_momentum"
_VOL_TARGET = "vol_target"


def extract_n(harness: str, result: Dict[str, Any]) -> Optional[int]:
    """The population count this dispatch measured, or None if unreadable.

    Checked in this order so a harness that happens to carry BOTH a
    `total_trades` field and (say) a `summary` block is still read as a
    standard-shape harness — `total_trades` is the more specific claim.
    """
    if isinstance(result.get("total_trades"), (int, float)) and not isinstance(
            result.get("total_trades"), bool):
        return int(result["total_trades"])
    if harness == _XSEC:
        v = (result.get("summary") or {}).get("n_days")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return int(v)
        return None
    if harness == _VOL_TARGET:
        v = (result.get("baseline") or {}).get("n_days")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return int(v)
        return None
    return None


def derive(*, harness: str, symbol: str, timeframe: str, build_result: str,
          result: Optional[Dict[str, Any]], read_error: str = "",
          ) -> Tuple[str, str, str, Optional[int], Dict[str, Any], str]:
    """Returns (verdict, read_state, population_description, n, measurement, note)."""
    population = (
        f"{harness} dispatched via research-harness-dispatch.yml against "
        f"{symbol or '(n/a)'} {timeframe or '(n/a)'} — a real, non-fixture "
        f"corpus fetched by the same run (E7 wiring proof: does the harness "
        f"run end-to-end against real data, not a strategy verdict)")
    if build_result != "success":
        note = f"build job concluded {build_result!r}, not success"
        return "not_applicable", "producer_failed", population, None, {}, note
    if result is None:
        note = read_error or "no readable JSON summary was produced by the build job"
        return "not_applicable", "producer_failed", population, None, {}, note
    n = extract_n(harness, result)
    if n is None:
        note = (f"build succeeded but no recognized population field "
                f"(total_trades / summary.n_days / baseline.n_days) was found "
                f"in its JSON summary — treating as unwired, not measured")
        return "not_applicable", "producer_failed", population, None, {}, note
    measurement = {k: result[k] for k in (
        "total_trades", "net_total_r", "win_rate_pct", "net_expectancy_r",
        "max_drawdown_r", "data_start", "data_end") if k in result}
    if "summary" in result and isinstance(result["summary"], dict):
        s = result["summary"]
        measurement.update({k: s[k] for k in (
            "sharpe_annualized", "total_return", "n_days",
            "data_start", "data_end") if k in s})
    if "baseline" in result and isinstance(result["baseline"], dict):
        measurement["baseline"] = result["baseline"]
    if "targeted" in result and isinstance(result["targeted"], dict):
        measurement["targeted"] = result["targeted"]
    note = "E7 wiring-proof dispatch: this run's only claim is that the harness ran"
    return "no_action_warranted", "measured", population, n, measurement, note


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
    """Planted POSITIVE and NEGATIVE controls, graded in both directions."""
    failures = 0

    def case(label: str, harness: str, build_result: str,
             result: Optional[Dict[str, Any]], want_verdict: str,
             want_state: str, want_n: Optional[int]) -> None:
        nonlocal failures
        verdict, state, _pop, n, _meas, _note = derive(
            harness=harness, symbol="BTCUSDT", timeframe="1h",
            build_result=build_result, result=result)
        ok = (verdict, state, n) == (want_verdict, want_state, want_n)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failures += 1
            print(f"        got=({verdict!r}, {state!r}, {n!r}) "
                  f"want=({want_verdict!r}, {want_state!r}, {want_n!r})")

    case("positive control: a standard-shape harness with real trades measures",
         "squeeze", "success", {"total_trades": 118, "net_total_r": -4.2},
         "no_action_warranted", "measured", 118)
    case("positive control: n=0 IS a measurement, not a failure",
         "fade", "success", {"total_trades": 0},
         "no_action_warranted", "measured", 0)
    case("negative: a failed build job is producer_failed, no verdict fabricated",
         "squeeze", "failure", {"total_trades": 118},
         "not_applicable", "producer_failed", None)
    case("negative: a cancelled build job is producer_failed",
         "squeeze", "cancelled", None,
         "not_applicable", "producer_failed", None)
    case("negative: a success build with no readable JSON is producer_failed",
         "squeeze", "success", None,
         "not_applicable", "producer_failed", None)
    case("negative: a boolean total_trades is NOT read as a count",
         "squeeze", "success", {"total_trades": True},
         "not_applicable", "producer_failed", None)
    case("xsec_momentum reads its population from summary.n_days",
         _XSEC, "success", {"summary": {"n_days": 157}},
         "no_action_warranted", "measured", 157)
    case("vol_target reads its population from baseline.n_days",
         _VOL_TARGET, "success", {"baseline": {"n_days": 900}},
         "no_action_warranted", "measured", 900)
    case("negative: xsec_momentum with no summary block is producer_failed",
         _XSEC, "success", {"daily": []},
         "not_applicable", "producer_failed", None)
    case("negative: an unrecognized harness shape is producer_failed, not n=0",
         "some_future_harness", "success", {"weird_field": 1},
         "not_applicable", "producer_failed", None)

    # Every derived record must be admissible to research_result's own schema —
    # a mapping that produces a record the validator refuses turns a
    # successful dispatch into a failed landing, at the last step.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from research_result import build, validate  # noqa: E402
    for label, harness, br, result in [
        ("measured", "squeeze", "success", {"total_trades": 42}),
        ("producer_failed", "squeeze", "failure", None),
        ("measured n=0", "fade", "success", {"total_trades": 0}),
    ]:
        verdict, state, population, n, measurement, note = derive(
            harness=harness, symbol="BTCUSDT", timeframe="1h",
            build_result=br, result=result)
        rec = build(research_unit=None,
                    decision_rule_id=DECISION_RULE_ID,
                    decision_rule_registered_at=DECISION_RULE_REGISTERED_AT,
                    verdict=verdict, read_state=state,
                    population_description=population, n=n,
                    workflow="research-harness-dispatch.yml", run_id="1",
                    commit_sha="abc", tool=f"scripts/backtest_{harness}.py",
                    measurement=measurement,
                    artifact_store="research/results/", artifact_locator="x",
                    note=note)
        problems = validate(rec)
        ok = not problems
        print(f"  {'PASS' if ok else 'FAIL'}  every {label} mapping is ADMISSIBLE "
              f"to research_result.validate()")
        if not ok:
            failures += 1
            print(f"        {problems!r}")

    print(f"harness_dispatch_result --self-test: {failures} failure(s)")
    return 1 if failures else 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--harness", default="")
    ap.add_argument("--symbol", default="")
    ap.add_argument("--timeframe", default="")
    ap.add_argument("--build-result", default="success")
    ap.add_argument("--result-json", default="result.json")
    ap.add_argument("--out-dir", default="rr")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    result: Optional[Dict[str, Any]] = None
    read_error = ""
    src = Path(args.result_json)
    if src.exists():
        try:
            loaded = json.loads(src.read_text(encoding="utf-8"))
            result = loaded if isinstance(loaded, dict) else None
            if result is None:
                read_error = f"{src} did not contain a JSON object"
        except (json.JSONDecodeError, OSError) as exc:
            read_error = f"{src} unreadable — {type(exc).__name__}: {exc}"
            print(f"::warning::harness_dispatch_result: {read_error}")
    else:
        read_error = f"{src} does not exist"

    verdict, read_state, population, n, measurement, note = derive(
        harness=args.harness, symbol=args.symbol, timeframe=args.timeframe,
        build_result=args.build_result, result=result, read_error=read_error)
    measurement = dict(measurement)
    measurement["note"] = note
    _write_outputs(verdict, read_state, n, measurement, population,
                   Path(args.out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
