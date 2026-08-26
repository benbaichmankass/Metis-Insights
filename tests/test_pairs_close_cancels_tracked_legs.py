"""The pairs close path must cancel its own tracked Bybit TP/SL legs.

BL-20260721-BYBIT2-XRP-TPSL-LEGCAP shipped leg-id tracking plus a close-side
cancel in ``close_open_position``. Measured 2026-08-25, that fix was wired at
**1 of 10** ``close_open_position`` call sites, and the pairs sleeve was not one
of them — so every pairs close left its legs resting on the venue forever.

MEASURED, on the live demo book, before this fix:
  bybit_1/ETHUSDT position 5.59, resting SL legs 9.33 across 9 = **167%**.
  14 visible resting legs; 2 belonged to the one OPEN row, **12 to SIX CLOSED
  rows** (5003, 4998, 4974, 4937, 4932, 4909 — every one a pairs_revert /
  pairs_stop / pairs_half_open_cleanup). **Zero orphans**: every resting leg
  mapped to a journal row, so the leaked-close path is the whole cause.

On a netted one-way book those legs accumulate against the SURVIVING position,
which is what the trader's own alert says: *"a trip would over-close and strand
the rest."*
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_PAIRS = pathlib.Path(__file__).resolve().parents[1] / "src/units/strategies/pairs_executor.py"


def _close_calls(path: pathlib.Path):
    """(lineno, kwarg-names) for every close_open_position call in *path*."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name == "close_open_position":
            out.append((node.lineno, {k.arg for k in node.keywords if k.arg}))
    return out


def test_the_pair_close_passes_its_own_tracked_leg_ids():
    """The regression. Without the kwargs the venue keeps the legs forever."""
    calls = _close_calls(_PAIRS)
    assert calls, "probe found no close_open_position call at all — it cannot fail"
    with_row = [c for c in calls if "sl_order_id" in c[1]]
    assert with_row, (
        "no pairs close_open_position call passes sl_order_id; the closed trade's "
        "Bybit Partial-tpsl legs will rest on the venue forever and accumulate "
        "against the surviving netted position"
    )
    for _, kw in with_row:
        assert "tp_order_id" in kw, (
            "passing only the stop leg strands the take-profit leg — the same "
            "leak, half the size"
        )


def test_the_unwind_path_is_deliberately_exempt_and_stays_that_way():
    """A control that keeps the assertion above honest.

    ``_unwind_placed_legs`` iterates ``(symbol, direction, qty)`` tuples from a
    partial-placement failure and holds NO journal row, so it has no leg id to
    pass. If a future edit gives it one, this test should be updated rather than
    the exemption silently widened — the point is that "does not pass" is a
    measured fact about scope here, not an oversight like the close path was.
    """
    src = _PAIRS.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and "unwind" in n.name)
    body = ast.get_source_segment(src, fn) or ""
    assert "close_open_position" in body
    assert "sl_order_id" not in body, (
        "the unwind path now references a leg id — if it genuinely has a row in "
        "scope it SHOULD pass it, and this control should be replaced by the "
        "same assertion as the close path"
    )


@pytest.mark.parametrize("field", ["sl_order_id", "tp_order_id"])
def test_the_row_was_always_in_scope(field):
    """This was never a missing-context problem, which is why it is a defect.

    ``_close_pair`` already reads ``direction`` / ``position_size`` /
    ``entry_price`` off the same row three lines above the call. A fix that
    needed a new query would be a design change; this one needed two kwargs.
    """
    src = _PAIRS.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_close_pair")
    body = ast.get_source_segment(src, fn) or ""
    assert 'row.get("position_size")' in body or "row.get('position_size')" in body, (
        "control failed: _close_pair no longer reads the row at all, so this "
        "test is no longer asserting what it claims"
    )
    assert f'row.get("{field}")' in body or f"row.get('{field}')" in body
