"""MI-157 — no committed candle file may be a flat line, and the readers refuse one.

THE FINDING (2026-09-07). Five committed fixtures under ``data/ohlcv/`` held
300 rows with exactly **one distinct close** each — btc 95000.0, eth 3200.0,
spy 580.0, qqq 490.0 — with ``open``/``high``/``low`` constant too. A backtest
over a series that does not move is arithmetically confident and empirically
empty: every return is exactly 0.0, so expectancy, correlation and rho are
*undefined* rather than merely weak.

Nothing said so. The placeholder-ness lived only in the commit message that
added them (``29014899``), while ``.gitignore:75`` already declared
``data/ohlcv/*.csv`` should not be committed at all — and
``tests/test_backtest_ict_cli.py`` ran the full ICT backtester across all four
manifest pairs and asserted success, passing green over a flat line for months.

WHY A TEST AND NOT JUST A FIX. ``CLAUDE.md`` is explicit that a finding
without a permanent detector recurs, and this one already proved it: the same
five files were measured and written up on 2026-08-20
(``BL-20260820-PLACEHOLDER-CANDLE-FIXTURES-CARRY-NO-MARKER``) and were still
flat, still committed and still being read 18 days later. A record is not a
detector.

There are two layers on purpose, because they fail differently:

* ``scripts/ci/check_candle_fixture_variance.py`` — CI census over every
  *tracked* file. Catches a flat corpus being **committed**.
* this module — catches a regression in the *readers*, i.e. a flat corpus
  being **read** without complaint, which is the half that produces a number
  somebody acts on.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from candle_variance import (  # noqa: E402
    GRADE_STATES,
    DegenerateSeriesError,
    grade_close_variance,
)

GUARD = REPO_ROOT / "scripts" / "ci" / "check_candle_fixture_variance.py"


# ---------------------------------------------------------------------------
# The grader itself — three states, never collapsed
# ---------------------------------------------------------------------------


def test_flat_series_grades_degenerate():
    grade = grade_close_variance([95000.0] * 300)
    assert grade.state == "degenerate"
    assert grade.distinct_close == 1
    assert grade.nonzero_returns == 0


def test_varying_series_grades_non_degenerate():
    """POSITIVE CONTROL — a grader that fails everything discriminates nothing."""
    grade = grade_close_variance([100.0, 101.5, 99.25, 103.0, 102.5])
    assert grade.state == "non_degenerate"
    assert grade.nonzero_returns == 4


@pytest.mark.parametrize("closes", [[], [100.0], ["nonsense", None]])
def test_too_short_grades_not_gradeable_never_clean(closes):
    """'We could not look' is its own state and must never pass as clean.

    A series with fewer than two usable closes has no return distribution to
    be degenerate *or* healthy. Folding it into ``non_degenerate`` is how a
    one-row corpus reads as measured — the collapsed-state failure
    ``docs/CLAUDE-RULES-CANONICAL.md`` names.
    """
    grade = grade_close_variance(closes)
    assert grade.state == "not_gradeable"
    assert grade.state != "non_degenerate"


def test_all_three_states_are_reachable():
    """Every declared state must be produced by some input, or it is decoration."""
    produced = {
        grade_close_variance([1.0] * 10).state,
        grade_close_variance([1.0, 2.0, 3.0]).state,
        grade_close_variance([1.0]).state,
    }
    assert produced == set(GRADE_STATES), (
        f"declared states {GRADE_STATES} but only {sorted(produced)} are reachable"
    )


def test_non_numeric_rows_are_dropped_not_counted_as_variance():
    """An unparseable price is not evidence of movement."""
    assert grade_close_variance([5.0, "x", 5.0, None, 5.0]).state == "degenerate"


# ---------------------------------------------------------------------------
# The census — no committed candle file may be flat
# ---------------------------------------------------------------------------


def test_no_committed_candle_file_is_degenerate():
    """CENSUS over every tracked file, not a sample of the ones we remember."""
    result = subprocess.run(
        [sys.executable, str(GUARD)],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        "A committed candle file has no price variation:\n"
        f"{result.stdout}\n{result.stderr}"
    )
    # The guard must say how many files it examined. A run that graded nothing
    # and a run that graded everything cleanly both exit 0, and only the
    # printed population tells them apart.
    assert "graded" in result.stdout


def test_guard_self_test_passes():
    """The detector must prove it can FAIL before its silence means anything."""
    result = subprocess.run(
        [sys.executable, str(GUARD), "--self-test"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_guard_actually_fails_on_a_flat_file(tmp_path):
    """NEGATIVE CONTROL, end to end through the real CLI.

    Reconstructs the exact shape of the deleted fixtures — 300 rows, one
    distinct close — and asserts the guard exits non-zero and names the file.
    Without this, "the guard is green" would be indistinguishable from "the
    guard cannot see anything".
    """
    flat = tmp_path / "btc_5m_placeholder.csv"
    flat.write_text(
        "timestamp,open,high,low,close,volume\n"
        + "".join(
            f"2026-01-01T{i // 60:02d}:{i % 60:02d}:00Z,"
            "95000.0,95000.0,95000.0,95000.0,1.0\n"
            for i in range(300)
        )
    )
    result = subprocess.run(
        [sys.executable, str(GUARD), str(flat)],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 1, (
        f"guard passed a 300-row constant-price file:\n{result.stdout}"
    )
    assert "btc_5m_placeholder.csv" in result.stdout, "guard failed without naming the file"
    assert "1 distinct close" in result.stdout


def test_guard_passes_the_real_committed_corpus():
    """POSITIVE CONTROL — the guard must not fail a genuine candle corpus.

    ``BL-20260820-PLACEHOLDER-CANDLE-FIXTURES-CARRY-NO-MARKER``'s own
    done_condition requires exactly this pairing: shown to fire on a
    placeholder AND shown to stay silent on ``data/backtest_candles.csv``.
    """
    corpus = REPO_ROOT / "data" / "backtest_candles.csv"
    if not corpus.exists():
        pytest.skip("data/backtest_candles.csv absent from this checkout")
    result = subprocess.run(
        [sys.executable, str(GUARD), str(corpus)],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"guard failed a REAL 5000-row corpus:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# The readers refuse — the half that stops a number being produced
# ---------------------------------------------------------------------------


def test_load_candles_refuses_a_flat_series(tmp_path):
    pytest.importorskip("pandas")
    import candle_io

    flat = tmp_path / "flat.csv"
    flat.write_text(
        "timestamp,open,high,low,close,volume\n"
        + "".join(
            f"2026-01-01T00:{i:02d}:00Z,3200.0,3200.0,3200.0,3200.0,1.0\n"
            for i in range(30)
        )
    )
    with pytest.raises(DegenerateSeriesError) as excinfo:
        candle_io.load_candles(str(flat))
    # The refusal must be actionable, not just loud.
    assert "DEGENERATE" in str(excinfo.value)
    assert "fetch_backtest_candles" in str(excinfo.value)


def test_load_candles_still_reads_a_real_series(tmp_path):
    """POSITIVE CONTROL — the refusal must not have broken the reader."""
    pytest.importorskip("pandas")
    import candle_io

    real = tmp_path / "real.csv"
    real.write_text(
        "timestamp,open,high,low,close,volume\n"
        + "".join(
            f"2026-01-01T00:{i:02d}:00Z,{100 + i},{101 + i},{99 + i},{100.5 + i},1.0\n"
            for i in range(30)
        )
    )
    assert len(candle_io.load_candles(str(real))) == 30


def test_the_five_deleted_fixtures_have_not_come_back():
    """The specific files MI-157 removed, named so a revert is caught by name.

    The generic census above would catch them anyway. This is deliberately
    redundant: it makes the failure message say *which* known-bad artefact
    returned, instead of only that something somewhere is flat.
    """
    returned = [
        name
        for name in (
            "btc_5m_2026.csv",
            "eth_5m_2026.csv",
            "qqq_15m_2026.csv",
            "spy_15m_2026.csv",
            "spy_5m_2026.csv",
        )
        if (REPO_ROOT / "data" / "ohlcv" / name).exists()
    ]
    if not returned:
        return
    # Present is only a failure if it is STILL FLAT — a real fetch to these
    # paths is the documented workflow and must not be punished.
    import csv as _csv

    flat_again = []
    for name in returned:
        with (REPO_ROOT / "data" / "ohlcv" / name).open(newline="") as handle:
            rows = list(_csv.DictReader(handle))
        if grade_close_variance(r.get("close") for r in rows).state == "degenerate":
            flat_again.append(name)
    assert not flat_again, (
        f"MI-157 placeholder fixtures are back and still flat: {flat_again}. "
        "These paths are gitignored and fetched on demand — see data/ohlcv/README.md."
    )
