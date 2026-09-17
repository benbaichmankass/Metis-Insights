"""The backlog TRIAGE GATE must fail a payload that declares untriaged rows.

`BL-20260814-STRICT-GUARD-DOES-NOT-ENFORCE-THE-TRIAGE-GATE`. The
``/system-review`` skill calls the backlog drive *"a HARD COMPLETION GATE,
every open item, not a sample"*, says each domain's ``count_untriaged`` MUST be
0, and names ``--strict`` as *"the mechanical coverage backstop"* so *"a
skipped assessment can't quietly ship"*.

**Measured on the run that filed the row: ``--strict`` passed a report
declaring count_untriaged of 201 / 39 / 26.** The honest disclosure survived
only because the payload was written honestly — a run that omitted the counts,
or set them to 0, would have rendered *identically*. That is "green is not
evidence" applied to the review process itself, so the failure path is what
these tests exercise.

Three properties, and the second and third are the ones a careless fix loses:

1. a declared shortfall FAILS;
2. the ``--allow-partial-triage`` opt-out lets a shortfall PUBLISH and stamps
   a banner the reader cannot miss;
3. the banner rides the PAYLOAD, so every caller of ``write_report()`` renders
   it rather than only the CLI.

⚠️ **An ABSENT count is deliberately NOT a violation yet, and that is a gap
rather than a judgement that it is fine.** ``count_untriaged`` is named four
times in the skill and was named ZERO times in
``comms/schema/system_report_response.template.json`` — so the field the gate
is written against was not in the schema authors fill in, and every report
rendered from it omits the count. Failing on absence would red every review run
on day one, which is how a guard gets switched off rather than satisfied. This
change adds the field to the template; tightening absence is a second step,
tracked at
``BL-20260912-THE-TRIAGE-GATE-CANNOT-TELL-AN-UNSTATED-COUNT-FROM-A-ZERO-ONE-UNTIL-REPORTS-CARRY-THE-FIELD``.
``test_an_absent_count_is_not_yet_refused_and_that_is_recorded`` pins the
current behaviour so the gap cannot close by accident and go unnoticed.
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _renderer():
    spec = importlib.util.spec_from_file_location(
        "render_system_report_triage", REPO / "scripts/reports/render_system_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _blk(**kw):
    return {"drained": ["x"], "deferred": [], "snoozed": [], "promoted": [], **kw}


def _report(health, perf, ml):
    mod = _renderer()
    rc = {"backlog_drive": {"health": _blk(**health), "performance": _blk(**perf),
                            "ml": _blk(**ml), "summary": "s" * 100}}
    for key in mod._REQUIRED_COVERAGE_KEYS:
        rc.setdefault(key, {} if key == "execution_capture" else "stated")
    return {"consolidated": {"review_coverage": rc}}


def _triage_violations(report, **kw):
    mod = _renderer()
    return [v for v in mod._validate_review_coverage(report, **kw) if "triaged" in v]


def test_a_fully_triaged_run_is_clean():
    """The control. Without it a gate that fails everything would look correct."""
    rep = _report({"count_untriaged": 0, "triaged": 5, "open_at_start": 5},
                  {"count_untriaged": 0}, {"count_untriaged": 0})
    assert _triage_violations(rep) == []


def test_the_measured_201_39_26_payload_fails():
    """The exact shape that passed --strict on 2026-08-14."""
    rep = _report({"count_untriaged": 201}, {"count_untriaged": 39}, {"count_untriaged": 26})
    violations = _triage_violations(rep)
    assert len(violations) == 3, violations
    blob = " ".join(violations)
    for domain, n in (("health", 201), ("performance", 39), ("ml", 26)):
        assert f"backlog_drive.{domain}" in blob and str(n) in blob


def test_an_absent_count_is_not_yet_refused_and_that_is_recorded():
    """Pins the GAP so it cannot close by accident, or widen unnoticed.

    Absence is "we did not look" and should eventually fail. It does not yet,
    because no report carries the field — see the module docstring. If a future
    change makes absence a violation, this test fails and its author is sent to
    the follow-up row rather than left wondering whether the looseness was
    deliberate.
    """
    assert _triage_violations(_report({}, {}, {})) == []


def test_the_opt_out_publishes_a_declared_shortfall():
    rep = _report({"count_untriaged": 201}, {"count_untriaged": 39}, {"count_untriaged": 26})
    assert _triage_violations(rep, allow_partial_triage=True) == []


def test_a_triaged_shortfall_against_open_at_start_fails_even_at_zero_untriaged():
    """Two different counters can disagree; the gate is every open row."""
    rep = _report({"count_untriaged": 0, "triaged": 3, "open_at_start": 5},
                  {"count_untriaged": 0}, {"count_untriaged": 0})
    violations = _triage_violations(rep)
    assert len(violations) == 1 and "open_at_start" in violations[0]


@pytest.mark.parametrize("marker,expected", [(None, False), ({"untriaged_by_domain": {"health": 201}}, True)])
def test_the_partial_review_banner_rides_the_payload_not_a_render_flag(marker, expected):
    """So every caller of write_report() renders it, not just the CLI."""
    mod = _renderer()
    rep = {"window": "daily", "reviewed_at": "2026-09-12T19:00:00Z",
           "consolidated": {"roll_up_grade": "B", "headline": "h", "review_coverage": {}}}
    rep = copy.deepcopy(rep)
    if marker is not None:
        rep["consolidated"]["review_coverage"]["partial_triage"] = marker
    out = mod.render_html(rep)
    assert ("PARTIAL REVIEW" in out) is expected
    if expected:
        assert out.index("PARTIAL REVIEW") < out.index("<h1>"), "the banner must LEAD"
        assert "health: 201" in out, "the banner must name what was skipped"
