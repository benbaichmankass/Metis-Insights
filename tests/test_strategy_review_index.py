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

from scripts.ml.strategy_review_packet import _strategies_all, write_index


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
    return [
        {"strategy": "s_hold_1", "proposed_action": "HOLD", "n_closed": 12},
        {"strategy": "s_hold_2", "proposed_action": "HOLD", "n_closed": 3},
        {"strategy": "s_kill", "proposed_action": "KILL", "n_closed": 60},
        {"strategy": "s_tune", "proposed_action": "TUNE", "n_closed": 30},
    ]


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
    assert payload["by_action"] == {"HOLD": 2, "KILL": 1, "TUNE": 1}


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
