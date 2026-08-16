"""A failed round must name the stage that failed, not the stage that noticed.

Two diagnoses cost a real round on 2026-08-16 (trainer relay #9531), and both
are the same class one level apart:

  * the per-harness `--help` probe died on `ModuleNotFoundError: pandas` and
    reported *"could not determine whether <harness> supports --strategy-name …
    fix the harness probe"*. The refusal was right — unattributable rows are
    worse than a missing leg — but the sentence names a cause no code path
    tested. The harness was fine; the round had been launched with bare
    `python3` instead of `.venv/bin/python3`, and since every harness runs
    under `sys.executable`, no leg could ever have run.
  * the round then printed *"no emitted trades — nothing to build"*, which is
    the message for a leg population that genuinely produced no trades. Nothing
    had been measured at all.

CLAUDE.md § "Diagnostic provenance": sub-class A (a failure message naming an
untested cause) and sub-class C (an empty result reading as a clean negative).
What is pinned here is that each diagnosis appears for its own cause AND NOT
for the others — a message that fires on everything carries no information.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "m20_exit_head_round", REPO / "scripts/research/m20_exit_head_round.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["m20_exit_head_round"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


R = _load()

# Verbatim from #9531's log.
REAL_STDERR = (
    "Traceback (most recent call last):\n"
    '  File "/home/ubuntu/ict-trading-bot/scripts/backtest_trend.py", '
    "line 38, in <module>\n"
    "    import pandas as pd\n"
    "ModuleNotFoundError: No module named 'pandas'\n"
)


# ------------------------------------------------- the interpreter diagnosis

def test_the_real_failure_is_identified_as_the_interpreter() -> None:
    assert R.interpreter_defect(REAL_STDERR) == "pandas"


def test_a_plain_import_error_counts_too() -> None:
    assert R.interpreter_defect(
        "ImportError: No module named 'yaml'") == "yaml"


def test_a_dotted_module_keeps_its_full_name() -> None:
    assert R.interpreter_defect(
        "ModuleNotFoundError: No module named 'sklearn.ensemble'"
    ) == "sklearn.ensemble"


def test_a_genuine_harness_failure_is_NOT_blamed_on_the_interpreter() -> None:
    """The half that makes the other half worth having."""
    for stderr in (
        "argparse error: unrecognized arguments: --strategy-name",
        "Traceback…\nValueError: no candle data for SLV\n",
        "",
    ):
        assert R.interpreter_defect(stderr) is None, stderr


def test_a_mere_mention_of_a_module_is_not_a_missing_module() -> None:
    """Substring-matching 'pandas' would fire on any traceback through it."""
    assert R.interpreter_defect(
        '  File "/x/pandas/core/frame.py", line 1, in <module>\n'
        "KeyError: 'close'\n") is None


# ------------------------------------------------------- the empty-round read

def test_nothing_ran_is_not_reported_as_an_empty_result() -> None:
    msg = R.empty_round_reason(2, 0, 0)
    assert "MEASURED NOTHING" in msg
    assert "do not record this as an empty result" in msg


def test_all_harnesses_failing_is_its_own_state() -> None:
    msg = R.empty_round_reason(3, 3, 3)
    assert "NOTHING RAN CLEANLY" in msg
    assert "MEASURED NOTHING" not in msg


def test_a_genuine_zero_trade_round_says_so_plainly() -> None:
    msg = R.empty_round_reason(3, 3, 0)
    assert "a real empty result" in msg
    for alarm in ("MEASURED NOTHING", "NOTHING RAN"):
        assert alarm not in msg


def test_a_partial_failure_still_reports_the_denominator() -> None:
    """1 of 3 ran and emitted nothing: honest, but the reader needs the count."""
    msg = R.empty_round_reason(3, 1, 0)
    assert "1 of 3" in msg
