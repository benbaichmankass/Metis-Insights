#!/usr/bin/env python3
"""Map `research-exit-head-replay-trainer`'s own output onto RQ-20260922-007's
PRE-REGISTERED rule (`RULE-RQ0922-007-EXIT-HEAD-ATTRIBUTION`).

WHY THIS EXISTS
---------------
RQ-20260922-007 asks whether `trend_donchian`'s RULE-D1 fail on bybit_2
(net_r_oos -0.4245, comms/strategy_evidence/trend_donchian.json) is a property
of the STRATEGY or of a HARNESS that cannot see the leg's live exit head. Its
first dispatch (run 36239257289) fired `research-exit-head-build.yml`, which
trains a brand-new discovery exit head from scratch
(`build_intrabar_exit_panel.py` / `analyze_exit_head.py`) and always lands
against its OWN hardcoded `RULE-EXIT-HEAD-CLEARS-BAR` — a different model, a
different statistic (OOS AUC / head Sharpe over CV folds, not a net_r_oos
comparable to -0.4245), and both harness scripts' cost terms (`--cost-r`,
`--exit-fee-r`) default to `0.0` and are never wired from any input the unit
declares, which is why that run's population read
`cost_r: 0.0, exit_fee_r: 0.0` regardless of what run.inputs said.

`scripts/ml/exit_head_replay.py` (driven here by
`research-exit-head-replay-trainer.yml`) is the tool that actually answers
this unit's question: it replays trend_donchian's ACTUAL PUBLISHED
`exit_head_model` over the live-faithful `scripts/backtest_trend.py` harness,
costed via `src.runtime.execution_costs.resolve_cost_policy` (venue-aware
fee+slippage+funding — bybit_2's BTCUSDT perp gets the full stack, not
fee-only), and reports a `baseline_summary_net_total_r` directly comparable to
the evidence record's `net_r_oos`.

THE STATISTIC (mirrors the unit's own `decision_rule.statistic`)
------------------------------------------------------------------
`net_total_r = sum(trade.r_multiple - fee_r(trade))` (scripts/backtest_trend.py
`_summarize`, verified by reading the source rather than assumed). The
replay's own `--emit-trades` net figure uses the SAME per-trade `fee_r(t)`
computed on the ORIGINAL (un-replayed) trade `t` for both arms — it does not
recompute cost for the new exit point (a modelling choice already made and
documented by `exit_head_replay.py`, not introduced here). Under that
identity:

    sum(replayed_net_r_i) = sum(replayed_r_i) - sum(fee_r(t_i))
                           = replayed_gross_r - sum(fee_r(t_i))
                           = (baseline_gross_r + delta_gross_r) - sum(fee_r(t_i))
                           = baseline_summary_net_total_r + delta_gross_r

so `head_simulated_net_r_oos = payload.baseline_summary_net_total_r +
payload.delta_gross_r` exactly, with no need to re-derive per-trade cost here.

THE THREE OUTCOMES THE RULE NAMES, NEVER COLLAPSED
----------------------------------------------------
  attributable_to: omitted_exit_head   recovered_R > 0 -> verdict `pass`
  attributable_to: strategy            recovered_R <= 0 -> verdict `fail`
  attributable_to: unestablishable     no servable head (replay rc=2) or the
                                        replay job did not conclude rc=0 ->
                                        read_state `producer_failed`,
                                        verdict `not_applicable` (the schema's
                                        closed set has no room for a fourth
                                        read_state, so the RQ-007-specific
                                        `attributable_to` classification lives
                                        in `measurement`, layered on top of the
                                        shared schema exactly as
                                        `exit_head_result.py` layers its own
                                        pass/fail mapping over the same schema)
  no baseline in the evidence store    read_state `no_data` -> verdict
                                        `not_applicable` (the replay measured
                                        something, but there is no registered
                                        net_r_oos to recover relative to)

Tier-1: reads two JSON files (a replay payload, a strategy-evidence record),
writes one JSON file. No live path, no network, no VM.

Run:
    python3 scripts/research/exit_head_attribution_result.py --self-test
    python3 scripts/research/exit_head_attribution_result.py \\
        --replay-json collected/replay.json --replay-rc 0 --strategy trend_donchian \\
        --out-dir rr --population "..."
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]

#: Registered in the unit BEFORE any run (research/queue/RQ-20260922-007.yaml
#: `decision_rule.id` / `.registered_at`, `registered_before_run: true`) — this
#: script mirrors that id/date, it does not mint one.
DECISION_RULE_ID = "RULE-RQ0922-007-EXIT-HEAD-ATTRIBUTION"
DECISION_RULE_REGISTERED_AT = "2026-09-22"

_SLIM_DROP = ("trades_detail",)


def _slim(payload: Dict[str, Any]) -> Dict[str, Any]:
    """The payload minus its per-trade detail — that stays in the run artifact
    (not durably retrievable), not duplicated row-by-row into the result."""
    return {k: v for k, v in payload.items() if k not in _SLIM_DROP}


def read_baseline(strategy: str, evidence_dir: Path
                  ) -> Tuple[Optional[float], Dict[str, Any]]:
    """The CURRENT committed `net_r_oos` for `strategy`, read live off its
    evidence record — never hardcoded, so a regenerated record is read as of
    NOW rather than as of whatever git sha last inspected it (`field beats
    comment`)."""
    path = evidence_dir / f"{strategy}.json"
    if not path.exists():
        return None, {"evidence_file": str(path), "present": False}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return None, {"evidence_file": str(path), "present": True,
                       "error": f"{type(exc).__name__}: {exc}"}
    val = doc.get("net_r_oos")
    meta = {"evidence_file": str(path), "present": True, "net_r_oos": val,
             "n_trades_oos": doc.get("n_trades_oos"),
             "generated_at": doc.get("generated_at")}
    return (float(val) if isinstance(val, (int, float)) and not isinstance(val, bool)
            else None, meta)


def derive(payload: Optional[Dict[str, Any]], *, replay_rc: str,
          baseline_net_r_oos: Optional[float], evidence_meta: Dict[str, Any],
          ) -> Tuple[str, str, Optional[int], Dict[str, Any], str]:
    """Return (verdict, read_state, n, measurement, note). Pure -- no I/O."""
    if replay_rc != "0" or payload is None:
        unestablishable = replay_rc == "2"
        return (
            "not_applicable", "producer_failed", None,
            {"replay_rc": replay_rc, "payload_present": payload is not None,
             "attributable_to": "unestablishable" if unestablishable else None,
             "evidence": evidence_meta},
            ("The replay honestly reported it could not score a head "
             "(`COULD NOT MEASURE`, rc=2) -- no trained/servable exit-head "
             "artifact for this (strategy, timeframe, symbol) on the trainer. "
             "Under RULE-RQ0922-007-EXIT-HEAD-ATTRIBUTION that is "
             "`attributable_to: unestablishable`: it blocks any demotion "
             "proposal and the missing replay path IS the finding -- it is "
             "NEITHER a pass nor a fail." if unestablishable else
             f"The replay job did not conclude rc=0 (rc={replay_rc!r}) or "
             f"wrote no readable JSON -- WE LOOKED AND IT BROKE."),
        )

    if baseline_net_r_oos is None:
        return (
            "not_applicable", "no_data", None,
            {"payload": _slim(payload), "evidence": evidence_meta},
            "The replay ran and produced a payload, but "
            "comms/strategy_evidence/<strategy>.json carries no numeric "
            "net_r_oos to recover R relative to -- there is no registered "
            "baseline, which is a fact about the evidence store, not a "
            "broken run.",
        )

    delta_gross_r = payload.get("delta_gross_r")
    baseline_net_total_r = payload.get("baseline_summary_net_total_r")
    if not isinstance(delta_gross_r, (int, float)) or isinstance(delta_gross_r, bool) \
       or not isinstance(baseline_net_total_r, (int, float)) or isinstance(baseline_net_total_r, bool):
        return (
            "not_applicable", "no_data", None,
            {"payload": _slim(payload), "evidence": evidence_meta},
            "The replay's payload is missing `delta_gross_r` or "
            "`baseline_summary_net_total_r` -- the statistic this unit's rule "
            "is graded on cannot be computed from what the producer emitted.",
        )

    head_simulated_net_r_oos = round(baseline_net_total_r + delta_gross_r, 4)
    recovered_r = round(head_simulated_net_r_oos - baseline_net_r_oos, 4)
    attributable_to = "omitted_exit_head" if recovered_r > 0 else "strategy"
    verdict = "pass" if recovered_r > 0 else "fail"

    pop = payload.get("population") or {}
    n = pop.get("trades")
    n = int(n) if isinstance(n, (int, float)) and not isinstance(n, bool) else 0

    fwd = payload.get("in_sample_split") or {}
    measurement = {
        "strategy": payload.get("strategy"), "symbol": payload.get("symbol"),
        "timeframe": payload.get("timeframe"), "model_id": payload.get("model_id"),
        "stage": payload.get("stage"), "exit_head_action": payload.get("exit_head_action"),
        "heads_servable": payload.get("heads_servable"),
        "population": pop, "cost_basis": payload.get("cost_basis"),
        "baseline_gross_r": payload.get("baseline_gross_r"),
        "replayed_gross_r": payload.get("replayed_gross_r"),
        "delta_gross_r": delta_gross_r,
        "baseline_summary_net_total_r": baseline_net_total_r,
        "head_simulated_net_r_oos": head_simulated_net_r_oos,
        "evidence_baseline_net_r_oos": baseline_net_r_oos,
        "recovered_r": recovered_r,
        "attributable_to": attributable_to,
        "in_sample_split_gross": fwd,
        "evidence": evidence_meta,
    }
    note = (
        f"recovered_R = {head_simulated_net_r_oos:+.4f} (head-simulated, full "
        f"cost stack: fee {payload.get('cost_basis', {}).get('fee_bps_roundtrip')}"
        f"bps + slip {payload.get('cost_basis', {}).get('slippage_bps_roundtrip')}"
        f"bps + funding {payload.get('cost_basis', {}).get('funding_bps_per_window')}"
        f"bps/window) - ({baseline_net_r_oos:+.4f}) = {recovered_r:+.4f} -> "
        f"attributable_to: {attributable_to}. "
        + ("A positive train-end is present on the head artifact; see "
           "in_sample_split_gross for the forward-only GROSS delta -- this "
           "record's headline recovered_R is over the full replay window, "
           "matching the unit's own statistic definition, and is not itself "
           "split in/out of sample."
           if fwd.get("train_window_present") else
           "This head artifact records no train_end (pre-BL-20260808 export) "
           "so no in-sample/forward split is available; the headline figure "
           "cannot be qualified further.")
    )
    return verdict, "measured", n, measurement, note


def _write_outputs(verdict: str, read_state: str, n: Optional[int],
                   measurement: Dict[str, Any], population: str,
                   out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "measurement.json").write_text(
        json.dumps(measurement, indent=2, sort_keys=True, default=str), encoding="utf-8")
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

    def case(label: str, payload, rc: str, baseline, want_verdict: str,
             want_state: str, want_n) -> None:
        nonlocal failures
        verdict, state, n, _m, _note = derive(
            payload, replay_rc=rc, baseline_net_r_oos=baseline,
            evidence_meta={"present": baseline is not None})
        ok = (verdict, state, n) == (want_verdict, want_state, want_n)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failures += 1
            print(f"        got ({verdict!r}, {state!r}, {n!r}) "
                  f"want ({want_verdict!r}, {want_state!r}, {want_n!r})")

    full = {
        "strategy": "trend_donchian", "symbol": "BTCUSDT", "timeframe": "1h",
        "model_id": "m1", "stage": "advisory", "exit_head_action": "close",
        "heads_servable": 1, "population": {"trades": 73},
        "baseline_gross_r": 12.87, "replayed_gross_r": 19.52,
        "delta_gross_r": 6.65, "baseline_summary_net_total_r": -0.4245,
        "cost_basis": {"fee_bps_roundtrip": 7.5, "slippage_bps_roundtrip": 3.0,
                       "funding_bps_per_window": 1.0},
        "in_sample_split": {"train_window_present": True, "forward_trades": 10},
    }

    # POSITIVE CONTROLS
    case("positive: recovered_R > 0 -> pass/measured, attributable_to omitted_exit_head",
         full, "0", -0.4245, "pass", "measured", 73)
    case("positive: recovered_R <= 0 -> fail/measured, attributable_to strategy",
         {**full, "delta_gross_r": -0.1}, "0", -0.4245, "fail", "measured", 73)

    # NEGATIVE CONTROLS
    case("negative: rc=2 (no servable head) -> producer_failed/not_applicable, n=null",
         None, "2", -0.4245, "not_applicable", "producer_failed", None)
    case("negative: rc=1 (crash) -> producer_failed/not_applicable, n=null",
         None, "1", -0.4245, "not_applicable", "producer_failed", None)
    case("negative: rc=0 but no evidence baseline -> no_data/not_applicable, n=null",
         full, "0", None, "not_applicable", "no_data", None)
    case("negative: rc=0 but payload missing delta_gross_r -> no_data, n=null",
         {**full, "delta_gross_r": None}, "0", -0.4245, "not_applicable", "no_data", None)
    case("negative: recovered_R exactly 0 reads as fail (not > 0), never dropped",
         {**full, "delta_gross_r": 0.0}, "0", -0.4245, "fail", "measured", 73)

    # ADMISSIBILITY -- whatever this emits must pass research_result.validate().
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from research_result import build, validate  # noqa: E402
    for label, payload, rc, baseline in [
        ("measured/pass", full, "0", -0.4245),
        ("unestablishable", None, "2", -0.4245),
        ("no_data", full, "0", None),
    ]:
        verdict, state, n, meas, note = derive(
            payload, replay_rc=rc, baseline_net_r_oos=baseline,
            evidence_meta={"present": baseline is not None})
        rec = build(research_unit="RQ-20260922-007",
                    decision_rule_id=DECISION_RULE_ID,
                    decision_rule_registered_at=DECISION_RULE_REGISTERED_AT,
                    verdict=verdict, read_state=state,
                    population_description="a stated population", n=n,
                    workflow="research-exit-head-replay-trainer.yml", run_id="1",
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

    print(f"exit_head_attribution_result --self-test: {failures} failure(s)")
    return 1 if failures else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--replay-json", default="collected/replay.json")
    ap.add_argument("--replay-rc", default="1")
    ap.add_argument("--strategy", default="trend_donchian")
    ap.add_argument("--evidence-dir", default="comms/strategy_evidence")
    ap.add_argument("--out-dir", default="rr")
    ap.add_argument("--population", default="")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    src = Path(args.replay_json)
    payload: Optional[Dict[str, Any]] = None
    if src.exists():
        try:
            loaded = json.loads(src.read_text(encoding="utf-8"))
            payload = loaded if isinstance(loaded, dict) else None
        except (json.JSONDecodeError, OSError) as exc:
            print(f"::warning::exit_head_attribution_result: {src} unreadable -- "
                  f"{type(exc).__name__}: {exc}")

    baseline, evidence_meta = read_baseline(args.strategy, REPO / args.evidence_dir)
    verdict, read_state, n, measurement, note = derive(
        payload, replay_rc=str(args.replay_rc),
        baseline_net_r_oos=baseline, evidence_meta=evidence_meta)
    _write_outputs(verdict, read_state, n, measurement, args.population,
                   Path(args.out_dir))
    print(note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
