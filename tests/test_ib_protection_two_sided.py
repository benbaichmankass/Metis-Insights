"""Two-sided IB protection coverage — BL-20260816-COVERAGE-IS-ONE-SIDED.

`protection_coverage` graded a stop and a take-profit as interchangeable
(`"STP" in t or "LMT" in t or "TRAIL" in t`), so a position holding a full stop
and NO target reported `covered_qty == size` and the broker-naked sweep never
fired. Measured on `ib_paper` 2026-08-16: MGC 105 long with one stop and no
limit, MES 15 long with TWO stops and no limit — zero limit orders on the whole
account, and nothing had ever alerted.

These tests pin the axis itself. The classifier is a pure function, so it is
exercised directly rather than through a broker mock.
"""
from __future__ import annotations

import ast
import types

import pytest

_SRC = open("src/units/accounts/ib_client.py").read()


def _load_helper():
    """Exec just `_protective_leg_side` — the module imports ccxt/ib_insync,
    which are not installed in every environment, and the classifier is pure."""
    tree = ast.parse(_SRC)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_protective_leg_side":
            mod = types.ModuleType("h")
            mod.__dict__["Optional"] = __import__("typing").Optional
            exec(compile(ast.Module(body=[node], type_ignores=[]), "h", "exec"),
                 mod.__dict__)
            return mod.__dict__["_protective_leg_side"]
    raise AssertionError("_protective_leg_side not found")


side = _load_helper()


@pytest.mark.parametrize("order_type,expected", [
    ("STP", "stop"),
    ("TRAIL", "stop"),
    ("TRAIL LIMIT", "stop"),
    ("LMT", "target"),
    ("MKT", None),
    ("", None),
    (None, None),
    ("stp", "stop"),      # case-insensitive
    ("lmt", "target"),
])
def test_leg_side_classification(order_type, expected):
    assert side(order_type) == expected


def test_stop_limit_is_a_STOP_not_a_target():
    """The precedence trap, and the one that would be worse than the bug.

    "STP LMT" contains the substring "LMT". A naive LMT-first test files every
    stop-limit as a take-profit — MANUFACTURING target coverage that does not
    exist, which is strictly worse than the one-sided grading being fixed:
    the old bug hid a real gap, this would invent a fake fill.
    """
    assert side("STP LMT") == "stop"
    assert side("STOP LIMIT") == "stop"


def test_a_stop_contributes_nothing_to_the_target_side():
    """The whole finding in one assertion: stop-only is NOT covered."""
    assert side("STP") == "stop"
    assert side("STP") != "target"


def test_coverage_returns_both_sides():
    """`protection_coverage` must publish stop_qty and target_qty as separate
    keys — grading them together is the defect, so a single combined number
    cannot be the only output."""
    src = _SRC[_SRC.index("def _locked_protection_coverage"):]
    src = src[:src.index("\n    def ", 10)]
    for key in ('"stop_qty"', '"target_qty"', '"covered_qty"'):
        assert key in src, f"protection_coverage must return {key}"
    # and it must classify via the shared helper, not a re-derived substring test
    assert "_protective_leg_side(" in src, (
        "coverage must classify legs through _protective_leg_side — a second "
        "inline substring test would be free to drift from the helper"
    )


def test_the_old_one_sided_test_is_gone_from_the_COVERAGE_path():
    """Guard against reintroduction — scoped to `protection_coverage`.

    The combined membership test deliberately SURVIVES in
    `has_protective_orders`, and that is correct: that method answers "does ANY
    protective leg rest?", for which a stop-only book truthfully answers yes.
    Its docstring already forbids using it for naked-detection and routes
    callers to `protection_coverage`. Widening this assertion to the whole file
    would force a change to a call site that is not wrong — so it is scoped to
    the method whose job is the QUANTITY, where combining the sides is the bug.
    """
    cov = _SRC[_SRC.index("def _locked_protection_coverage"):]
    cov = cov[:cov.index("\n    def ", 10)]
    assert '"STP" in otype or "LMT" in otype or "TRAIL" in otype' not in cov
    # ...and confirm the probe this test deliberately exempts still exists,
    # so the carve-out above never silently becomes vacuous.
    assert "_locked_has_protective_orders" in _SRC
