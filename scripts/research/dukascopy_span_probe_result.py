#!/usr/bin/env python3
# wiring: .github/workflows/dukascopy-span-probe.yml, via .github/actions/research-result
"""Map `dukascopy_span_probe.py --json-out`'s `by_instrument` rows onto ONE
records-file line per instrument, for `research_result.py --emit --records-file`.

WHY A MAPPER AND NOT INLINE SHELL, following the exit_head_result.py /
m20_sweep_result.py precedent: the mapping from a producer's own output shape to
the five per-record fields (`verdict`, `read_state`, `population`, `n`,
`measurement`) is a real translation with real judgement calls, and judgement
calls belong in a tested `.py` file, not in a YAML `run:` block nobody self-tests.

THE MAPPING, PER INSTRUMENT (`depth_claim` -> `read_state`/`verdict`)
----------------------------------------------------------------------
`depth_claim` is already a GRADED claim (`summarize()` in the probe itself
downgrades an unknown_instrument or all-errored row before this file ever sees
it) — this module does not re-grade the probe's own judgement, only carries it
across the schema boundary:

  measured                          -> read_state=measured, verdict=no_action_warranted
      A clean depth reading. `no_action_warranted` because this probe answers a
      DEPTH question for a downstream feed-source decision (BL-20260824-E35-
      SWEEP-FEED-IS-BINANCE-ONLY-FOR-A-MOSTLY-NON-CRYPTO-MATRIX); it is not
      itself a pass/fail test of anything.
  no_bars_at_any_anchor             -> read_state=measured, verdict=indeterminate
      We looked at every anchor and got a real answer (zero bars) — that is
      `measured`, not `producer_failed` (§ collapsed states: "we looked and
      found nothing" is a real answer). The DEPTH question is left open, so
      `indeterminate` rather than `no_action_warranted`.
  lower_bound_some_anchors_errored  -> read_state=measured, verdict=indeterminate
      A real reading exists (bars, at some anchor) but is a LOWER bound, not the
      true depth — the probe's own docstring is explicit that this must never be
      read as a completed measurement.
  not_established_probe_errored     -> read_state=producer_failed, verdict=not_applicable
      Every anchor errored — we did not get a single real read for this
      instrument. "We did not look" (network/venue), not "the venue has
      nothing".
  unknown_instrument                -> read_state=producer_failed, verdict=not_applicable
      A mapping bug in this repo's own INSTRUMENTS table, not a fact about the
      venue — still "we could not look" from this record's point of view.

`n` is `anchors_probed` (the real denominator for this instrument's row),
never `anchors_with_bars` — § "Always state the population" asks for the
denominator that was actually attempted, not the subset that succeeded.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

_MEASURED_CLAIMS = {
    "measured": "no_action_warranted",
    "no_bars_at_any_anchor": "indeterminate",
    "lower_bound_some_anchors_errored": "indeterminate",
}
_FAILED_CLAIMS = {"not_established_probe_errored", "unknown_instrument"}


def map_row(b: Dict[str, Any]) -> Dict[str, Any]:
    claim = b.get("depth_claim")
    inst = b.get("instrument")
    n = b.get("anchors_probed")
    population = (
        f"Dukascopy span probe for `{inst}` (relation={b.get('relation')}, "
        f"serves={b.get('serves')}) across {n} anchor(s)"
    )
    measurement = {
        "depth_claim": claim,
        "earliest_bars_anchor": b.get("earliest_bars_anchor"),
        "anchors_with_bars": b.get("anchors_with_bars"),
        "anchors_empty": b.get("anchors_empty"),
        "anchors_errored": b.get("anchors_errored"),
        "anchors_unknown": b.get("anchors_unknown"),
    }
    if claim in _MEASURED_CLAIMS:
        return {
            "verdict": _MEASURED_CLAIMS[claim],
            "read_state": "measured",
            "population": population,
            "n": n,
            "measurement": measurement,
        }
    if claim in _FAILED_CLAIMS:
        return {
            "verdict": "not_applicable",
            "read_state": "producer_failed",
            "population": population,
            "n": None,
            "measurement": measurement,
        }
    raise ValueError(f"unknown depth_claim {claim!r} for instrument {inst!r} — "
                      f"the probe's own summarize() must have added a new state "
                      f"this mapper does not know about yet")


def build_records(probe_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    by_instrument = probe_json.get("by_instrument")
    if not isinstance(by_instrument, list) or not by_instrument:
        raise ValueError("probe JSON carries no by_instrument rows — "
                          "nothing to land")
    return [map_row(b) for b in by_instrument]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe-json", help="Path to dukascopy_span_probe.py --json-out")
    ap.add_argument("--out", help="Path to write the records-file JSONL")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    if not args.probe_json or not args.out:
        ap.error("--probe-json and --out are required unless --self-test")

    data = json.loads(Path(args.probe_json).read_text(encoding="utf-8"))
    records = build_records(data)
    with open(args.out, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    print(f"wrote {len(records)} record(s) to {args.out}")
    return 0


def _self_test() -> int:
    failures = 0

    def case(label: str, claim: str, expect_verdict: str, expect_read_state: str) -> None:
        nonlocal failures
        row = {"instrument": "X", "relation": "same", "serves": ["FOO"],
               "anchors_probed": 6, "anchors_with_bars": 1, "anchors_empty": 0,
               "anchors_errored": 0, "anchors_unknown": 0,
               "earliest_bars_anchor": 365, "depth_claim": claim}
        rec = map_row(row)
        ok = rec["verdict"] == expect_verdict and rec["read_state"] == expect_read_state
        if claim in _FAILED_CLAIMS:
            ok = ok and rec["n"] is None
        else:
            ok = ok and rec["n"] == 6
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failures += 1
            print(f"        got={rec!r}")

    case("measured -> no_action_warranted/measured",
         "measured", "no_action_warranted", "measured")
    case("no_bars_at_any_anchor -> indeterminate/measured (a real empty answer)",
         "no_bars_at_any_anchor", "indeterminate", "measured")
    case("lower_bound_some_anchors_errored -> indeterminate/measured",
         "lower_bound_some_anchors_errored", "indeterminate", "measured")
    case("not_established_probe_errored -> not_applicable/producer_failed",
         "not_established_probe_errored", "not_applicable", "producer_failed")
    case("unknown_instrument -> not_applicable/producer_failed",
         "unknown_instrument", "not_applicable", "producer_failed")

    try:
        map_row({"instrument": "X", "depth_claim": "some_future_state",
                 "anchors_probed": 1})
        print("  FAIL  an unrecognised depth_claim must raise, not silently pass")
        failures += 1
    except ValueError:
        print("  PASS  an unrecognised depth_claim raises rather than silently passing")

    try:
        build_records({"by_instrument": []})
        print("  FAIL  an empty by_instrument list must raise")
        failures += 1
    except ValueError:
        print("  PASS  an empty by_instrument list raises rather than landing nothing")

    good = {"by_instrument": [
        {"instrument": "A", "relation": "same", "serves": ["A"], "anchors_probed": 2,
         "anchors_with_bars": 2, "anchors_empty": 0, "anchors_errored": 0,
         "anchors_unknown": 0, "earliest_bars_anchor": 730, "depth_claim": "measured"},
        {"instrument": "B", "relation": "proxy", "serves": ["B"], "anchors_probed": 2,
         "anchors_with_bars": 0, "anchors_empty": 0, "anchors_errored": 2,
         "anchors_unknown": 0, "earliest_bars_anchor": None,
         "depth_claim": "not_established_probe_errored"},
    ]}
    recs = build_records(good)
    ok = len(recs) == 2
    print(f"  {'PASS' if ok else 'FAIL'}  build_records fans out one record per instrument")
    if not ok:
        failures += 1

    print(f"dukascopy_span_probe_result --self-test: {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
