"""The diag relay renderer must say WHICH list elements survive a cut.

E50 / PI-20260924-JN54P2HH-0004: #12820 read the 100-row tail of
arbitration_fanout_soak, the renderer kept the OLDEST 35 rows, and the 35th
was reported as the newest -- a soak that was writing all along was filed as
dark for 32 hours. These pin the two properties that make that misread
impossible to make silently.
"""
import json

from scripts.ops.diag_relay_render import _self_test, fit_bulk, render_section


def _tail(n: int) -> dict:
    return {
        "name": "arbitration_fanout_soak",
        "present": True,
        "size_bytes": 1,
        "lines": [json.dumps({"logged_at_utc": f"row-{i:03d}", "pad": "x" * 200})
                  for i in range(n)],
    }


def test_self_test_passes():
    assert _self_test() == 0


def test_cut_tail_names_the_newest_rows_as_missing():
    env = _tail(100)
    out = render_section(json.dumps(env), 5000)
    assert "row-099" not in out  # the newest row really is cut
    assert "ONLY THE FIRST" in out and "of 100 element(s)" in out
    assert "including the final one" in out


def test_shown_rows_are_whole_and_counted():
    env = _tail(100)
    body, shown = fit_bulk({"lines": env["lines"]}, 3000)
    parsed = json.loads(body)  # a whole-element cut is valid JSON
    ((key, _value, n),) = shown
    assert key == "lines" and len(parsed["lines"]) == n and 0 < n < 100


def test_under_budget_is_untouched():
    env = _tail(3)
    out = render_section(json.dumps(env), 100_000)
    assert "TRUNCATED" not in out and "ONLY THE FIRST" not in out
