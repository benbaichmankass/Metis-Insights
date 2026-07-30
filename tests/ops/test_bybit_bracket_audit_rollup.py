"""The bracket audit's ROLL-UP must not assert more than its verdicts measured.

2026-07-30, diagnostic-provenance sub-class A2 (narrowed/widened claim). The
roll-up bucketed only under-coverage and then printed

    "every audited symbol is fully SL-covered at the broker."

while a **444.7% over-coverage** sat in the body above it. That sentence is
*literally true* at 444.7% (``covered >= size``), which is exactly what makes it
dangerous: it is not a wrong number, it is a summary asserting a clean bill of
health far stronger than the verdict measured. A reader who stops at the
roll-up — which is the point of a roll-up — walks past live leg
over-accumulation, the very condition the runtime's own
``_check_broker_naked_bybit_positions`` flags as ``over_covered``
(BL-20260730-BYBIT1-XRP-LEG-OVERACCUM-WORSENING). The audit and the runtime
disagreed about the same position, and only the audit was being read.

These tests pin the classification without a broker, so the logic is verified
in CI and the live run only has to confirm integration.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "bybit_bracket_audit", _ROOT / "scripts" / "ops" / "bybit_bracket_audit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


audit = _load()


def _summary(*symbols):
    return {"accounts": [{"account_id": "bybit_1", "symbols": list(symbols)}]}


def _sym(symbol, verdict, pct, uncovered=0.0, covered=0.0):
    return {"symbol": symbol, "verdict": verdict, "coverage_pct": pct,
            "uncovered_qty": uncovered, "sl_covered_qty": covered}


def test_the_444_percent_case_is_no_longer_an_all_clear():
    """The exact live shape that produced the false all-clear."""
    bad, over, audited = audit.classify_rollup(
        _summary(_sym("XRPUSDT", "PROTECTED", 444.7, covered=4447.0)))
    assert bad == []
    assert len(over) == 1, "over-coverage must get its own bucket, not the else-branch"
    assert over[0][1] == "XRPUSDT"
    assert audited == 1


def test_genuinely_clean_coverage_is_still_clean():
    """The guard must not turn every PROTECTED symbol into a finding."""
    bad, over, audited = audit.classify_rollup(
        _summary(_sym("BTCUSDT", "PROTECTED", 100.0),
                 _sym("ETHUSDT", "PROTECTED", 100.3)))
    assert (bad, over, audited) == ([], [], 2)


def test_under_coverage_still_reported():
    bad, over, audited = audit.classify_rollup(
        _summary(_sym("SOLUSDT", "PARTIALLY_NAKED", 62.0, uncovered=38.0)))
    assert len(bad) == 1 and over == [] and audited == 1


def test_unreliable_leg_qty_counts_as_under_covered_not_clean():
    """An ungradeable coverage must never fall through to the all-clear."""
    bad, over, _ = audit.classify_rollup(
        _summary(_sym("XRPUSDT", "PROTECTED_UNRELIABLE_LEG_QTY", None)))
    assert len(bad) == 1 and over == []


def test_flat_symbols_are_excluded_from_the_denominator():
    """A symbol with no position is not evidence of coverage either way.

    Counting it would inflate the reassurance the summary line gives — the
    denominator has to mean what the claim ranges over.
    """
    _, _, audited = audit.classify_rollup(
        _summary(_sym("BTCUSDT", "PROTECTED", 100.0),
                 _sym("DOGEUSDT", "FLAT", None),
                 _sym("ADAUSDT", "FLAT", None)))
    assert audited == 1


@pytest.mark.parametrize("pct,expect_over", [
    (100.0, False),
    (100.4, False),   # inside the 0.5% float-noise epsilon
    (100.6, True),    # outside it — a real extra leg
    (444.7, True),
])
def test_over_coverage_threshold_respects_the_float_noise_epsilon(pct, expect_over):
    """Bybit echoes leg qty as a string at the instrument's qty step; a hair of
    float noise must not read as over-accumulation (that would be the
    alarm-fatigue failure mode in the other direction)."""
    _, over, _ = audit.classify_rollup(_summary(_sym("BTCUSDT", "PROTECTED", pct)))
    assert bool(over) is expect_over


def test_the_all_clear_branch_requires_both_buckets_empty():
    """Mixed state must not print the all-clear."""
    bad, over, audited = audit.classify_rollup(
        _summary(_sym("BTCUSDT", "PROTECTED", 100.0),
                 _sym("XRPUSDT", "PROTECTED", 444.7)))
    assert not (not bad and not over), "all-clear branch would fire on a real anomaly"
    assert audited == 2
