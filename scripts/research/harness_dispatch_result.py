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

WHEN A QUEUE UNIT IS ATTACHED, ITS OWN RULE IS APPLIED (PI-20261004-FOJGFIZF-0004)
-----------------------------------------------------------------------------------
Until 2026-10-04 every successful dispatch landed `no_action_warranted` even
when it carried a `research_unit` whose pre-registered rule said PASS/FAIL --
RQ-20260929-106 measured net_total_r -121.96R over n=178 against a rule
reading "FAIL IF net_total_r <= 0 with n_trades >= 39", and its record said
the same thing as RQ-20260929-105's +18.72R. `queue_grade.py` parses no rule
of its own (an E5 row's verdict IS the producer's application of the rule; the
grader reads unanimity), so such a unit closed `done` whatever it measured.

Now, with `--research-unit RQ-...`, this module reads that unit's
`decision_rule.rule` and:

  * if it is the mechanical shape this module can evaluate exactly --
        PASS IF net_total_r > 0 AND n_trades >= N
        FAIL IF net_total_r <= 0 with n_trades >= N
        indeterminate IF n_trades < N
    (one N in all three clauses; `n` / `n_trades` / `total_trades` accepted
    for the count) -- it applies it to the harness's own `net_total_r` /
    `total_trades` and emits pass / fail / indeterminate under the UNIT's
    rule id and registration date;
  * otherwise (unit file missing, rule is prose, names another statistic such
    as "net-of-cost OOS R", or the payload lacks `net_total_r`/`total_trades`)
    it emits `verdict=wiring_only`: measured, but NOT a decision.
    `queue_grade.grade_e5` treats any verdict outside pass / fail /
    no_action_warranted / indeterminate as non-mechanical and sets
    `needs_review`, so such a row can never close a unit as if it were graded.

Without a unit (a bare E7 wiring dispatch) RULE-E7-HARNESS-DISPATCHABLE still
applies and a success is `no_action_warranted`, as registered.

Tier-1: reads one JSON file (+ the named queue unit), writes one JSON file. No
live path, no network.

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
import re
import sys
from pathlib import Path
from typing import Any

DECISION_RULE_ID = "RULE-E7-HARNESS-DISPATCHABLE"
DECISION_RULE_REGISTERED_AT = "2026-09-27"

_REPO = Path(__file__).resolve().parents[2]
QUEUE_DIR = _REPO / "research" / "queue"

#: Measured, but NOT a decision. Legal only with read_state=measured
#: (research_result.validate); the grader routes it to needs_review.
WIRING_ONLY = "wiring_only"

_COUNT = r"(?:n_trades|total_trades|n)"
_PASS_RE = re.compile(
    r"\bPASS\s+IF\s+net_total_r\s*>\s*0\s+AND\s+" + _COUNT + r"\s*>=\s*(\d+)\b", re.I)
_FAIL_RE = re.compile(
    r"\bFAIL\s+IF\s+net_total_r\s*<=\s*0\s+(?:with|AND)\s+" + _COUNT + r"\s*>=\s*(\d+)\b", re.I)
_IND_RE = re.compile(
    r"\bindeterminate\s+IF\s+" + _COUNT + r"\s*<\s*(\d+)\b", re.I)


def parse_rule(rule_text: Any) -> int | None:
    """The n-floor N of a `net_total_r > 0 AND n_trades >= N` rule, or None.

    All three clauses (PASS / FAIL / indeterminate) must each appear exactly
    once and agree on ONE N. Anything else -- another statistic ("net-of-cost
    OOS R"), a missing branch, two floors -- is not this mapper's to evaluate
    on the rule's behalf, and returns None (-> wiring_only).
    """
    if not isinstance(rule_text, str):
        return None
    text = " ".join(rule_text.split())
    found = [rx.findall(text) for rx in (_PASS_RE, _FAIL_RE, _IND_RE)]
    if not all(len(f) == 1 for f in found):
        return None
    floors = {int(f[0]) for f in found}
    if len(floors) != 1:
        return None
    floor = floors.pop()
    return floor if floor > 0 else None


def load_unit(unit_id: str, queue_dir: Path = QUEUE_DIR) -> dict[str, Any] | None:
    """The queue unit's parsed YAML, or None if absent/unreadable."""
    if not unit_id:
        return None
    p = queue_dir / f"{unit_id}.yaml"
    if not p.is_file():
        return None
    try:
        import yaml
        u = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001  # allow-silent: unreadable unit -> logged ::warning:: and the result is stamped wiring_only (never a decisive verdict)
        print(f"::warning::harness_dispatch_result: {p} unreadable — "
              f"{type(exc).__name__}: {exc}")
        return None
    return u if isinstance(u, dict) else None


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def apply_unit_rule(unit: dict[str, Any] | None, result: dict[str, Any]
                    ) -> tuple[str, str, str, str]:
    """(verdict, rule_id, registered_at, note) for a MEASURED run with a unit.

    Never returns no_action_warranted: with a unit attached, a success is
    either the unit's own rule applied, or explicitly NOT a decision.
    """
    e7 = (DECISION_RULE_ID, DECISION_RULE_REGISTERED_AT)
    if unit is None:
        return (WIRING_ONLY, *e7,
                "research unit named but its queue file is missing/unreadable, so "
                "its rule could not be applied: wiring_only, NOT a decision")
    uid = unit.get("id")
    dr = unit.get("decision_rule") if isinstance(unit.get("decision_rule"), dict) else {}
    floor = parse_rule(dr.get("rule"))
    if floor is None:
        return (WIRING_ONLY, *e7,
                f"{uid}'s decision_rule is not the mechanical 'net_total_r > 0 AND "
                f"n_trades >= N' shape this mapper evaluates: wiring_only, NOT a "
                f"decision (queue_grade -> needs_review)")
    net, trades = result.get("net_total_r"), result.get("total_trades")
    if not (_num(net) and _num(trades)):
        return (WIRING_ONLY, *e7,
                f"{uid}'s rule needs net_total_r and total_trades; the harness "
                f"payload lacks one: wiring_only, NOT a decision")
    rule_id = str(dr.get("id") or "").strip()
    reg = str(dr.get("registered_at") or "").strip()
    if not rule_id or not reg:
        return (WIRING_ONLY, *e7,
                f"{uid}'s decision_rule lacks an id or registered_at: wiring_only, "
                f"NOT a decision")
    trades = int(trades)
    if trades < floor:
        verdict = "indeterminate"
    elif net > 0:
        verdict = "pass"
    else:
        verdict = "fail"
    return (verdict, rule_id, reg,
            f"{rule_id} applied mechanically by harness_dispatch_result.py: "
            f"net_total_r={net} n_trades={trades} floor={floor} -> {verdict}")


#: harnesses added to the E7 family later inherit this shape automatically —
#: the check is "does the payload carry `total_trades`", not a name allowlist.
_XSEC = "xsec_momentum"
_VOL_TARGET = "vol_target"


def extract_n(harness: str, result: dict[str, Any]) -> int | None:
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
          result: dict[str, Any] | None, read_error: str = "",
          research_unit: str = "", unit: dict[str, Any] | None = None,
          ) -> tuple[str, str, str, int | None, dict[str, Any], str]:
    """Returns (verdict, read_state, population_description, n, measurement, note).

    `derive_full` additionally returns the rule the verdict was graded under.
    """
    return derive_full(harness=harness, symbol=symbol, timeframe=timeframe,
                       build_result=build_result, result=result,
                       read_error=read_error, research_unit=research_unit,
                       unit=unit)[:6]


def derive_full(*, harness: str, symbol: str, timeframe: str, build_result: str,
                result: dict[str, Any] | None, read_error: str = "",
                research_unit: str = "", unit: dict[str, Any] | None = None,
                ) -> tuple[str, str, str, int | None, dict[str, Any], str, str, str]:
    """derive() + (decision_rule_id, decision_rule_registered_at)."""
    e7 = (DECISION_RULE_ID, DECISION_RULE_REGISTERED_AT)
    population = (
        f"{harness} dispatched via research-harness-dispatch.yml against "
        f"{symbol or '(n/a)'} {timeframe or '(n/a)'} — a real, non-fixture "
        f"corpus fetched by the same run (E7 wiring proof: does the harness "
        f"run end-to-end against real data, not a strategy verdict)")
    if build_result != "success":
        note = f"build job concluded {build_result!r}, not success"
        return ("not_applicable", "producer_failed", population, None, {}, note) + e7
    if result is None:
        note = read_error or "no readable JSON summary was produced by the build job"
        return ("not_applicable", "producer_failed", population, None, {}, note) + e7
    n = extract_n(harness, result)
    if n is None:
        note = ("build succeeded but no recognized population field "
                "(total_trades / summary.n_days / baseline.n_days) was found "
                "in its JSON summary — treating as unwired, not measured")
        return ("not_applicable", "producer_failed", population, None, {}, note) + e7
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
    if research_unit:
        verdict, rule_id, reg, note = apply_unit_rule(unit, result)
        if verdict != WIRING_ONLY:
            population = (
                f"{harness} dispatched via research-harness-dispatch.yml against "
                f"{symbol or '(n/a)'} {timeframe or '(n/a)'} for {research_unit}, "
                f"graded against that unit's pre-registered rule {rule_id} "
                f"(net_total_r and total_trades from the harness's own JSON summary)")
        return verdict, "measured", population, n, measurement, note, rule_id, reg
    note = "E7 wiring-proof dispatch: this run's only claim is that the harness ran"
    return ("no_action_warranted", "measured", population, n, measurement, note) + e7


def _write_outputs(verdict: str, read_state: str, n: int | None,
                   measurement: dict[str, Any], population: str,
                   out_dir: Path, rule_id: str = DECISION_RULE_ID,
                   rule_at: str = DECISION_RULE_REGISTERED_AT) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "measurement.json").write_text(
        json.dumps(measurement, indent=2, sort_keys=True), encoding="utf-8")
    n_out = "null" if n is None else str(n)
    gh = os.environ.get("GITHUB_OUTPUT")
    lines = [f"verdict={verdict}", f"read_state={read_state}", f"n={n_out}",
             f"population={population}", f"decision_rule_id={rule_id}",
             f"decision_rule_registered_at={rule_at}"]
    if gh:
        with open(gh, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))


def _self_test() -> int:
    """Planted POSITIVE and NEGATIVE controls, graded in both directions."""
    failures = 0

    def case(label: str, harness: str, build_result: str,
             result: dict[str, Any] | None, want_verdict: str,
             want_state: str, want_n: int | None) -> None:
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

    # ── a unit's own pre-registered rule is applied (PI-20261004-FOJGFIZF-0004)
    rule39 = ("PASS IF net_total_r > 0 AND n_trades >= 39 -- Stage-0 evidence only. "
              "FAIL IF net_total_r <= 0 with n_trades >= 39 -- re-queues. "
              "indeterminate IF n_trades < 39.")
    unit39 = {"id": "RQ-20300101-001", "decision_rule": {
        "id": "RULE-TEST-39", "registered_at": "2030-01-01", "rule": rule39}}
    oos = {"id": "RQ-20300101-002", "decision_rule": {
        "id": "RULE-TPL-OOS", "registered_at": "2030-01-01",
        "rule": "PASS IF net-of-cost OOS R > 0 with n >= 39. FAIL IF net-of-cost "
                "OOS R <= 0 with n >= 39. indeterminate IF n < 39."}}

    def ucase(label: str, unit: dict[str, Any] | None, result: dict[str, Any],
              want: str, want_rule: str) -> None:
        nonlocal failures
        out = derive_full(harness="pairs", symbol="SPY", timeframe="1h",
                          build_result="success", result=result,
                          research_unit="RQ-20300101-001", unit=unit)
        ok = (out[0], out[1], out[6]) == (want, "measured", want_rule)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failures += 1
            print(f"        got=({out[0]!r}, {out[1]!r}, {out[6]!r}) want=({want!r}, {want_rule!r})")

    ucase("PLANTED: net_total_r<0 with n>=N grades FAIL under the unit's rule",
          unit39, {"total_trades": 178, "net_total_r": -121.96}, "fail", "RULE-TEST-39")
    ucase("net_total_r==0 with n>=N grades FAIL (rule says <= 0)",
          unit39, {"total_trades": 39, "net_total_r": 0.0}, "fail", "RULE-TEST-39")
    ucase("PLANTED: net_total_r>0 with n>=N grades PASS",
          unit39, {"total_trades": 144, "net_total_r": 18.72}, "pass", "RULE-TEST-39")
    ucase("PLANTED: n<N grades INDETERMINATE whatever the sign",
          unit39, {"total_trades": 10, "net_total_r": 5.0}, "indeterminate", "RULE-TEST-39")
    ucase("a rule on another statistic (OOS R) is wiring_only, never a decision",
          oos, {"total_trades": 178, "net_total_r": -121.96}, WIRING_ONLY, DECISION_RULE_ID)
    ucase("a missing unit file is wiring_only",
          None, {"total_trades": 178, "net_total_r": 3.0}, WIRING_ONLY, DECISION_RULE_ID)
    ucase("a payload without net_total_r is wiring_only",
          unit39, {"total_trades": 178}, WIRING_ONLY, DECISION_RULE_ID)
    for label, txt, want_floor in [
        ("floor 20 parses", rule39.replace("39", "20"), 20),
        ("mismatched floors refuse", rule39.replace("< 39", "< 40"), None),
        ("missing indeterminate branch refuses", rule39.split("indeterminate")[0], None),
        ("prose refuses", "PASS if it looks good", None),
    ]:
        got = parse_rule(txt)
        ok = got == want_floor
        print(f"  {'PASS' if ok else 'FAIL'}  parse_rule: {label}")
        if not ok:
            failures += 1
            print(f"        got={got!r} want={want_floor!r}")
    # the fix's whole point: no unit-attached success may read no_action_warranted
    for u in (unit39, oos, None):
        v = derive(harness="pairs", symbol="SPY", timeframe="1h", build_result="success",
                   result={"total_trades": 50, "net_total_r": 1.0},
                   research_unit="RQ-20300101-001", unit=u)[0]
        if v == "no_action_warranted":
            failures += 1
            print(f"  FAIL  unit-attached success read no_action_warranted ({u and u['id']})")

    # Every derived record must be admissible to research_result's own schema —
    # a mapping that produces a record the validator refuses turns a
    # successful dispatch into a failed landing, at the last step.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from research_result import build, validate
    for label, harness, br, result, unit in [
        ("measured", "squeeze", "success", {"total_trades": 42}, None),
        ("producer_failed", "squeeze", "failure", None, None),
        ("measured n=0", "fade", "success", {"total_trades": 0}, None),
        ("unit-rule fail", "pairs", "success", {"total_trades": 178, "net_total_r": -1.0}, unit39),
        ("unit-rule indeterminate", "pairs", "success", {"total_trades": 3, "net_total_r": 1.0}, unit39),
        ("wiring_only", "pairs", "success", {"total_trades": 178, "net_total_r": -1.0}, oos),
    ]:
        verdict, state, population, n, measurement, note, rid, rat = derive_full(
            harness=harness, symbol="BTCUSDT", timeframe="1h",
            build_result=br, result=result,
            research_unit="RQ-20300101-001" if unit else "", unit=unit)
        rec = build(research_unit="RQ-20300101-001" if unit else None,
                    decision_rule_id=rid,
                    decision_rule_registered_at=rat,
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


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--harness", default="")
    ap.add_argument("--symbol", default="")
    ap.add_argument("--timeframe", default="")
    ap.add_argument("--build-result", default="success")
    ap.add_argument("--result-json", default="result.json")
    ap.add_argument("--out-dir", default="rr")
    ap.add_argument("--research-unit", default="",
                    help="queue unit id (RQ-...); when given, that unit's own "
                         "decision_rule is applied, or the run lands wiring_only")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    result: dict[str, Any] | None = None
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

    unit_id = args.research_unit.strip()
    unit = load_unit(unit_id) if unit_id else None
    (verdict, read_state, population, n, measurement, note,
     rule_id, rule_at) = derive_full(
        harness=args.harness, symbol=args.symbol, timeframe=args.timeframe,
        build_result=args.build_result, result=result, read_error=read_error,
        research_unit=unit_id, unit=unit)
    measurement = dict(measurement)
    measurement["note"] = note
    _write_outputs(verdict, read_state, n, measurement, population,
                   Path(args.out_dir), rule_id, rule_at)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
