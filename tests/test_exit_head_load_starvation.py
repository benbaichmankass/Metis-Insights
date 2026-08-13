"""A family starved at the LOAD stage must be attributable, not just counted.

`BL-20260813-E0-LOAD-STAGE-DROPS-INVISIBLE-ON-PARTIAL-FAILURE`.

THE SHAPE WORTH REMEMBERING. `build_exit_head_dataset.py` drops rows at two
stages — LOAD (rejected on shape) and CANDLE (no candles / unresolvable). After
the 2026-08-12 ict_scalp incident (1170 rows dropped 100%, reported only as "no
trades loaded") the load counters were added — but printed ONLY inside the
`if not trades:` total-failure branch. So the fix covered the failure that had
already happened and not the one that had not: a PARTIAL drop, which is the case
the counters are most needed for, still reported nothing.

Measured 2026-08-13 on the 1d round: **371 trend rows dropped at load (100% of
that family) beside 578 pullback rows that loaded fine**, and `build_report.json`
said only `{"no_candles": 697, "unresolvable": 63}` — no trace of the 371.
`trades_in` counted the survivors, so even the denominator gave nothing away.

Surfacing the aggregate was then necessary and STILL not sufficient: the number
was computable all along and nobody looked, because nothing said which family it
belonged to. These tests pin the attribution — the part that makes a starved
family visible by contrast rather than by a reader knowing to go looking.

The numbers below are the real ones from the incident, so a regression fails
against the case that actually occurred rather than a toy.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "build_exit_head_dataset", REPO / "scripts" / "ml" / "build_exit_head_dataset.py")
bd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bd)


def _emit(path: Path, rows: list[dict]) -> Path:
    with path.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return path


def _complete(strategy: str) -> dict:
    return {"strategy": strategy, "symbol": "ETHUSDT", "entry_time": 1,
            "exit_time": 2, "entry": 10.0, "sl": 9.0}


def _missing_exit_time(strategy: str) -> dict:
    """The exact defect: `entry_time` written, `exit_time` absent."""
    r = _complete(strategy)
    del r["exit_time"]
    return r


def test_partial_drop_is_attributed_to_its_family(tmp_path):
    """THE regression: the 1d round, at its real counts."""
    f = _emit(tmp_path / "emit.jsonl",
              [_missing_exit_time("trend_donchian_eth")] * 371
              + [_complete("eth_pullback_2h")] * 578)
    report: dict = {}
    trades = bd.load_harness_trades([f], report)

    assert len(trades) == 578, "the healthy family must still load"
    assert report["rows_seen"] == 949 and report["rows_loaded"] == 578
    # The aggregate alone was what hid this — the per-family split is the fix.
    assert report["per_family_load"]["donchian"] == {"seen": 371, "loaded": 0}
    assert report["per_family_load"]["pullback"] == {"seen": 578, "loaded": 578}
    assert report["families_starved"] == ["donchian"]


def test_healthy_round_reports_no_starvation(tmp_path):
    """A check that fires on everything is as useless as one that fires never."""
    f = _emit(tmp_path / "ok.jsonl",
              [_complete("trend_donchian_eth"), _complete("eth_pullback_2h")])
    report: dict = {}
    bd.load_harness_trades([f], report)
    assert report["families_starved"] == []
    assert all(c["loaded"] == c["seen"] for c in report["per_family_load"].values())


def test_total_drop_still_reports_every_family_starved(tmp_path):
    """The 2026-08-12 case must not regress while fixing the 2026-08-13 one."""
    f = _emit(tmp_path / "all_bad.jsonl", [_missing_exit_time("ict_scalp_5m")] * 1170)
    report: dict = {}
    assert bd.load_harness_trades([f], report) == []
    # NOTE the bucket is `ict_scalp_5m`, NOT `scalp` — see the docstring on
    # test_scalp_does_not_resolve_to_a_scalp_family below. Asserting the real
    # value rather than the intended one, so this test measures the loader and
    # the drift stays visible in exactly one place.
    assert report["families_starved"] == ["ict_scalp_5m"]
    assert report["per_family_load"]["ict_scalp_5m"] == {"seen": 1170, "loaded": 0}


def test_row_without_a_strategy_keeps_its_own_bucket(tmp_path):
    """`<no_strategy>` must not hide inside a legitimate family.

    Three harnesses emitted rows with no `strategy` at all until #8889. Folding
    those into `unknown` — a real `family_of` return value — would put a defect
    behind a bucket that also has honest members.
    """
    r = _complete("x")
    del r["strategy"]
    report: dict = {}
    bd.load_harness_trades([_emit(tmp_path / "ns.jsonl", [r])], report)
    assert "<no_strategy>" in report["per_family_load"]


def test_unparseable_row_is_counted_not_dropped_from_the_denominator(tmp_path):
    """A bad-JSON line has no strategy to attribute it to — it gets its own
    bucket rather than vanishing (which would silently shrink `seen`) or being
    charged to a neighbouring family (which would slander one)."""
    p = tmp_path / "bad.jsonl"
    p.write_text('{"strategy": "eth_pullback_2h", not json\n'
                 + json.dumps(_complete("eth_pullback_2h")) + "\n")
    report: dict = {}
    bd.load_harness_trades([p], report)
    assert report["per_family_load"]["<unparseable>"]["seen"] == 1
    assert report["skipped"]["bad_json"] == 1
    assert report["rows_seen"] == 2, "the bad line must stay in the denominator"


@pytest.mark.parametrize("strategy,family", [
    ("trend_donchian_eth", "donchian"), ("eth_pullback_2h", "pullback"),
    ("squeeze_breakout_4h", "squeeze"), ("fade_breakout_4h", "fade"),
])
def test_family_resolution_survives_a_row_that_is_otherwise_unusable(
        tmp_path, strategy, family):
    """Attribution must work on the rows that FAILED, which is the whole point:
    `family_of` reads only `strategy`, and that key survives every rejection
    reason the loader has."""
    report: dict = {}
    bd.load_harness_trades(
        [_emit(tmp_path / f"{family}.jsonl", [_missing_exit_time(strategy)])], report)
    assert report["families_starved"] == [family]


def test_scalp_does_not_resolve_to_a_scalp_family(tmp_path):
    """PINS A KNOWN DEFECT, deliberately.

    `BL-20260813-FAMILY-RESOLVER-DRIFT-SCALP-NEVER-POOLED`.

    `family_of` has branches for donchian / pullback / squeeze / fade and NONE
    for scalp, so every `ict_scalp_*` leg falls through to its own name. The
    sibling resolver the round driver uses to pick the HARNESS
    (`m20_fleet_exit_sweep.classify`) returns `scalp` for the same legs — two
    definitions of "which family is this", disagreeing on 24 of 55 legs.

    The measured consequence is that the 8 ict_scalp legs have NEVER trained as
    the pooled family the design describes: every scalp exit-head verdict in the
    coverage matrix came from a model trained on ONE leg.

    This test asserts the CURRENT behaviour, not the intended one. Changing the
    pooling unit changes what every future exit-head model trains on, so it is a
    research-design decision for the operator, not a mechanical repair — and the
    obvious fix is measurably wrong (delegating to `classify` would silently drop
    the vwap / fade / pairs legs, which return None there yet have live rows and
    their own dirs today). When that decision lands, this test is the one to
    update, and its failure is the reminder that the matrix's scalp verdicts need
    re-grading.
    """
    report: dict = {}
    bd.load_harness_trades(
        [_emit(tmp_path / "scalp.jsonl", [_missing_exit_time("ict_scalp_5m")])], report)
    assert report["families_starved"] == ["ict_scalp_5m"]
    assert bd.family_of("ict_scalp_5m") == "ict_scalp_5m"
