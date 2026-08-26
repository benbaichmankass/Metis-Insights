"""Pin `stated-population-guard`'s measured limits so they stay DELIBERATE.

The guard (GATE 0 item G4) suppresses when the context carries a second number,
because a population is a count. That rule has a known false-negative edge: an
INCIDENTAL integer — an unrelated count, a date, a backlog id — also satisfies
it.

⚠️ These tests assert the CURRENT behaviour including the misses. That is the
point. Measured 2026-08-26 over all 875 watched files (3,549 lines containing a
percentage), exactly **9 — 0.25%** — have a date/backlog-id/PR-number as their
only denominator, and most of those are legitimate: a year used as a data label
(`2011 (3.327%)`), a citation year, a config threshold. Tightening would fire on
those correct lines, and this repo's documented failure mode is the alarm nobody
reads.

So if a future change makes a `should_still_miss` case FIRE, that is not
automatically a fix — re-measure the false-POSITIVE cost against the corpus
before keeping it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "ci"))

from check_stated_population import findings  # noqa: E402


def _diff(line: str, path: str = "docs/research/probe.md") -> str:
    return (f"diff --git a/{path} b/{path}\n"
            f"--- a/{path}\n+++ b/{path}\n@@ -1,0 +1,2 @@\n+{line}\n")


def _fires(line: str, **kw) -> bool:
    return bool(findings(_diff(line, **kw)))


# The guard's REASON TO EXIST. If any of these stops firing the guard is dead.
SHOULD_FIRE = [
    "Coverage is 42.9% across the journal.",
    "The exit labels are 25.6% correct.",
    "Coverage fell to 42.9% from 60.8%.",
    "Fabricated share of closed trades reached 65.3% in July.",
]

# Correctly quiet — a population IS stated.
SHOULD_PASS = [
    "Real-money coverage is 42.9% of 1151 rows.",
    "Coverage is 42.9%, n=1151.",
    "494 measured / 1151 total — coverage 42.9%.",
]

# The measured blind spot. Quiet today, on purpose. See the module docstring.
SHOULD_STILL_MISS = [
    ("an unrelated integer", "Coverage is 42.9% and the win rate moves 9 points."),
    ("a date", "Measured on 2026-08-26, coverage is 42.9%."),
    ("a backlog id", "Coverage is 42.9% (see BL-20260817 for context)."),
]


@pytest.mark.parametrize("line", SHOULD_FIRE)
def test_a_bare_percentage_is_caught(line):
    assert _fires(line), (
        "the guard stopped catching a population-less claim; this is the "
        "behaviour it exists for")


@pytest.mark.parametrize("line", SHOULD_PASS)
def test_a_stated_population_is_quiet(line):
    assert not _fires(line), (
        "the guard fired on a line that DOES state its population — a false "
        "positive here is what gets a guard switched off")


@pytest.mark.parametrize("label,line", SHOULD_STILL_MISS,
                         ids=[a for a, _ in SHOULD_STILL_MISS])
def test_the_incidental_number_blind_spot_is_deliberate(label, line):
    assert not _fires(line), (
        f"{label}: this case now FIRES. That may be an improvement, but it is "
        "not automatically one — re-measure the false-positive cost against "
        "the corpus (a year used as a data label, e.g. '2011 (3.327%)', is a "
        "CORRECT line) before keeping the change, and update the docstring's "
        "measured 9/3,549 figure.")


def test_a_legitimate_year_labelled_datum_must_not_fire():
    """The concrete line that makes tightening expensive.

    From `docs/research/m20-arm-reachability-is-a-vol-threshold-2026-08-16.md`.
    A naive "ignore year-shaped integers" tightening turns this into a finding.
    """
    assert not _fires("2011 (3.327%) and 2013 (3.169%) also sit above the band.")


def test_code_paths_are_not_watched():
    """The guard reads docs and backlog rows, never source.

    A percentage in code is a computation, not a claim addressed to a reader.
    """
    assert not _fires("    if pct > 42.9:  # threshold",
                      path="src/runtime/probe.py")
