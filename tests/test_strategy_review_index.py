"""Tests for the fleet-wide selector + the committed INDEX (Phase F / C3).

The repair C3 makes is *a cron and a committed path*. Both new pieces are pure,
so they are tested here rather than against the live journal — `build_packet`
itself is unchanged and already covered by `test_strategy_review_gate.py`.

⚠️ The load-bearing property is the DENOMINATOR. Packets are committed
selectively (only where an action is proposed) while the index is committed
always, so a reader can tell "48 graded and held" from "only 4 were graded at
all". A test that only checked the actionable rows would pass on an index that
had silently dropped every HOLD — which is the exact failure the index exists to
prevent, so it is asserted explicitly below.
"""
from __future__ import annotations

import json
import pathlib

from scripts.ml.strategy_review_packet import (
    NO_ACTION_VERDICT,
    _strategies_all,
    is_actionable,
    write_index,
)


def test_selects_enabled_and_drops_explicitly_disabled():
    cfg = {"a": {"enabled": True}, "b": {"enabled": False}, "c": {"enabled": True}}
    assert _strategies_all(cfg) == ["a", "c"]


def test_omitted_enabled_defaults_true():
    """Declared-permissive, matching the repo's two execution gates: a strategy
    is demoted by an EXPLICIT flag, never by omission. A selector that dropped
    strategies missing the key would silently stop grading most of the fleet."""
    assert _strategies_all({"a": {}, "b": {"symbols": ["BTCUSDT"]}}) == ["a", "b"]


def test_non_mapping_block_is_skipped_not_crashed():
    """A malformed row must not take the whole cron down — grading 51 of 52 and
    saying so beats grading none."""
    assert _strategies_all({"a": {"enabled": True}, "bad": None, "c": {}}) == ["a", "c"]


def test_selector_is_sorted_so_the_index_diff_is_stable():
    assert _strategies_all({"z": {}, "a": {}, "m": {}}) == ["a", "m", "z"]


def _rows():
    """Fixture rows in the vocabulary ``Decision.action`` ACTUALLY emits.

    ⚠️ This fixture used UPPERCASE ("HOLD"/"KILL"/"TUNE") until 2026-09-01, and
    that is why every test here passed while the live selection filter matched
    nothing and opened a 105-file PR: the tests asserted a fiction, exactly the
    shape of the pairs-soak tests that declared a schema production does not
    have. The lowercase values below are what ``Decision`` writes, and
    ``NO_ACTION_VERDICT`` is imported rather than spelled so the fixture cannot
    drift from the generator again.
    """
    return [
        {
            "strategy": "s_hold_1",
            "proposed_action": NO_ACTION_VERDICT,
            "actionable": False,
            "n_closed": 12,
        },
        {
            "strategy": "s_hold_2",
            "proposed_action": NO_ACTION_VERDICT,
            "actionable": False,
            "n_closed": 3,
        },
        {"strategy": "s_kill", "proposed_action": "kill", "actionable": True, "n_closed": 60},
        {"strategy": "s_tune", "proposed_action": "tune", "actionable": True, "n_closed": 30},
    ]


def test_no_action_verdict_matches_what_the_decision_matrix_emits():
    """The constant must equal a value the gate can actually produce.

    A positive control on the whole class: if this drifts to "HOLD" again, the
    filter silently classifies every graded strategy as actionable.
    """
    src = (
        pathlib.Path(__file__).resolve().parents[1]
        / "scripts"
        / "ml"
        / "strategy_review_packet.py"
    ).read_text()
    assert f'decision.action = "{NO_ACTION_VERDICT}"' in src
    assert 'decision.action = "HOLD"' not in src


def test_is_actionable_is_case_insensitive_and_treats_ungraded_as_actionable():
    assert is_actionable("kill") is True
    assert is_actionable(NO_ACTION_VERDICT) is False
    # The uppercase spelling the docs use must NOT read as actionable.
    assert is_actionable("HOLD") is False
    assert is_actionable("  Hold ") is False
    # `None` is "we did not grade it", which is not the same fact as "hold";
    # surfacing it is the safe direction.
    assert is_actionable(None) is True


def test_index_publishes_the_actionable_count_and_the_verdict_it_used(tmp_path):
    """A consumer must never have to re-derive (and mis-spell) the rule."""
    write_index(_rows(), tmp_path)
    day = sorted(tmp_path.iterdir())[0]
    idx = json.loads((day / "INDEX.json").read_text())
    assert idx["no_action_verdict"] == NO_ACTION_VERDICT
    assert idx["actionable"] == 2
    assert idx["graded"] == 4


def test_index_keeps_every_graded_row_including_holds(tmp_path):
    """THE DENOMINATOR. HOLD rows are the ones whose packets are NOT committed,
    so if the index dropped them the committed record would imply the fleet is
    4 strategies rather than 52."""
    write_index(_rows(), tmp_path)
    day = next(tmp_path.iterdir())
    payload = json.loads((day / "INDEX.json").read_text())
    assert payload["graded"] == 4
    assert {r["strategy"] for r in payload["rows"]} == {
        "s_hold_1", "s_hold_2", "s_kill", "s_tune"
    }
    assert payload["by_action"] == {NO_ACTION_VERDICT: 2, "kill": 1, "tune": 1}


def test_index_rows_sorted_by_action_then_strategy(tmp_path):
    """Actionable rows must not be buried under an arbitrary ordering, and a
    stable order keeps the daily commit diff readable."""
    write_index(_rows(), tmp_path)
    day = next(tmp_path.iterdir())
    rows = json.loads((day / "INDEX.json").read_text())["rows"]
    assert [r["strategy"] for r in rows] == ["s_hold_1", "s_hold_2", "s_kill", "s_tune"]


def test_empty_run_writes_an_index_saying_zero_not_nothing(tmp_path):
    """A run that graded nothing must still leave a record. An ABSENT index and
    an index reading zero are different facts: the first means the cron did not
    run, the second means it ran and found nothing to grade."""
    write_index([], tmp_path)
    day = next(tmp_path.iterdir())
    payload = json.loads((day / "INDEX.json").read_text())
    assert payload["graded"] == 0
    assert payload["rows"] == []
    assert payload["by_action"] == {}
