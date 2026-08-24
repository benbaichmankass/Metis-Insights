"""`structural_health` must actually FAIL a bad payload.

The 2026-08-24 operator directive: the review has to look for the bigger
structural problems, not only the per-item defects — *"bugs that are not really
resolving themselves over time because we're just putting on band-aids"*.

`backlog_classes` finds patterns in the BACKLOG. This key finds them in the
RUNNING SYSTEM, where the biggest defects have no backlog row at all. On the day
it was added, over ALL 1324 closed non-backtest trades: 64.7% of closes came
from cleanup machinery rather than a decision, and the M20 exit levers had fired
17 times ever (1.3%). Neither fact was a backlog row, and eight consecutive
reviews had reported execution-capture as a flat metric without asking what it
was a symptom of.

Same reason as test_review_coverage_build_verification: a guard whose failure
path is never exercised is indistinguishable from one that always passes.
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _renderer():
    spec = importlib.util.spec_from_file_location(
        "render_system_report", REPO / "scripts/reports/render_system_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _violations(structural_health) -> list[str]:
    rep = {"consolidated": {"review_coverage": {"structural_health": structural_health}}}
    return [v for v in _renderer()._validate_review_coverage(rep)
            if "structural_health" in v]


GOOD = {
    "population": "all 1324 closed non-backtest trades — whole history, not the window",
    "findings": [
        {
            "finding": "exits are performed by cleanup, not by decisions",
            "measured": "857 of 1324 closes (64.7%) from reconciler/sweep paths",
            "trend": "flat",
            "structural_fix": "make the declared exit fire at the venue",
        }
    ],
    "hypothesis_tested": {
        "hypothesis": "the provenance gap is downstream of janitor closes",
        "verdict": "refuted",
        "evidence": "janitor closes 52.0% measured vs decided 27.0% — the opposite",
    },
}


def test_key_is_required():
    """Declared-but-unenforced is the failure mode this family keeps repeating."""
    assert "structural_health" in _renderer()._REQUIRED_COVERAGE_KEYS


def test_well_formed_block_passes():
    assert _violations(GOOD) == []


def test_missing_block_fails():
    assert _violations(None)


def test_population_must_be_stated():
    bad = copy.deepcopy(GOOD)
    bad["population"] = ""
    assert any("population missing" in v for v in _violations(bad))


def test_findings_may_not_be_empty():
    bad = copy.deepcopy(GOOD)
    bad["findings"] = []
    assert any("findings missing" in v for v in _violations(bad))


def test_finding_without_a_number_is_an_opinion():
    bad = copy.deepcopy(GOOD)
    bad["findings"][0].pop("measured")
    assert any("no measured" in v for v in _violations(bad))


def test_finding_without_a_structural_fix_fails():
    bad = copy.deepcopy(GOOD)
    bad["findings"][0]["structural_fix"] = "  "
    assert any("no structural_fix" in v for v in _violations(bad))


@pytest.mark.parametrize("trend", ["falling", "flat", "rising", "first_measurement"])
def test_every_declared_trend_value_is_accepted(trend):
    ok = copy.deepcopy(GOOD)
    ok["findings"][0]["trend"] = trend
    assert _violations(ok) == []


@pytest.mark.parametrize("trend", ["improving", "worse", "", "unknown"])
def test_trend_outside_the_vocabulary_fails(trend):
    """'Is this class shrinking?' is the whole question — a free-text trend
    cannot answer it, and 'unknown' would let a flat class read as assessed."""
    bad = copy.deepcopy(GOOD)
    bad["findings"][0]["trend"] = trend
    assert any("trend must be one of" in v for v in _violations(bad))


def test_a_hypothesis_must_be_stated():
    bad = copy.deepcopy(GOOD)
    bad.pop("hypothesis_tested")
    assert any("hypothesis missing" in v for v in _violations(bad))


@pytest.mark.parametrize("verdict", ["supported", "refuted"])
def test_both_verdicts_are_accepted(verdict):
    """REFUTED must be as legal as supported — on 2026-08-24 the refutation was
    the most valuable output of the pass. A guard that only tolerated
    'supported' would quietly select for reviews that confirm themselves."""
    ok = copy.deepcopy(GOOD)
    ok["hypothesis_tested"]["verdict"] = verdict
    assert _violations(ok) == []


@pytest.mark.parametrize("verdict", ["inconclusive", "", "partially"])
def test_non_binary_verdict_fails(verdict):
    bad = copy.deepcopy(GOOD)
    bad["hypothesis_tested"]["verdict"] = verdict
    assert any("supported|refuted" in v for v in _violations(bad))


def test_verdict_without_evidence_fails():
    bad = copy.deepcopy(GOOD)
    bad["hypothesis_tested"]["evidence"] = ""
    assert any("evidence missing" in v for v in _violations(bad))
