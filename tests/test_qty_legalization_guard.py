"""Phase 4 of the sizing/qty-legalization consolidation
(``docs/sizing-legalization-DESIGN.md``).

Self-test for the ``qty-legalization`` CI guard
(``scripts/check_qty_legalization_guard.py``): the machine check that keeps the
venue lot rule / step-alignment single-homed in the ``qty_legalize`` seam. A
deliberately-planted out-of-seam ``get_lot_rule`` / ``quantize_qty`` call must
fail the guard; the seam files themselves never do; and — the live invariant —
the real ``src/`` tree must currently be clean.
"""
from __future__ import annotations

from pathlib import Path

from scripts.check_qty_legalization_guard import (
    FORBIDDEN_CALLS,
    SEAM_ALLOWLIST,
    find_violations_in_source,
    scan_paths,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


# --- the planted-violation self-test (the design's CI-guard self-test) -------

def test_planted_out_of_seam_call_is_flagged():
    src = (
        "from src.units.accounts.precision import get_lot_rule\n"
        "def f(client, symbol):\n"
        "    return get_lot_rule(client, symbol, 'linear')\n"
    )
    hits = find_violations_in_source(src, "src/core/somewhere.py")
    assert hits == [(3, "get_lot_rule")]


def test_planted_quantize_qty_call_is_flagged():
    src = (
        "from src.units.accounts.precision import quantize_qty\n"
        "def f(q, step):\n"
        "    return quantize_qty(q, step)\n"
    )
    hits = find_violations_in_source(src, "src/units/accounts/execute.py")
    assert hits == [(3, "quantize_qty")]


def test_attribute_call_form_is_flagged():
    # `precision.get_lot_rule(...)` (Attribute call) must also be caught.
    src = (
        "from src.units.accounts import precision\n"
        "def f(client, symbol):\n"
        "    return precision.get_lot_rule(client, symbol, 'linear')\n"
    )
    hits = find_violations_in_source(src, "src/core/other.py")
    assert hits == [(3, "get_lot_rule")]


# --- non-violations: mentions, seam allowlist, opt-out marker ----------------

def test_mention_in_comment_or_string_is_not_flagged():
    # AST-based: only a real Call counts, never a comment/docstring/string.
    src = (
        '"""This module used to call get_lot_rule directly."""\n'
        "# get_lot_rule / quantize_qty resolve the Bybit lot.\n"
        "NAME = 'get_lot_rule'\n"
        "def f():\n"
        "    return NAME\n"
    )
    assert find_violations_in_source(src, "src/core/history.py") == []


def test_seam_files_are_allowlisted():
    # The seam itself and the defining module may call the primitives freely.
    src = "def f(client, symbol):\n    return get_lot_rule(client, symbol, 'linear')\n"
    for allowed in SEAM_ALLOWLIST:
        assert find_violations_in_source(src, allowed) == []


def test_inline_allow_marker_suppresses():
    src = (
        "def f(client, symbol):\n"
        "    return get_lot_rule(client, symbol, 'linear')  # qty-legalize-allow: legacy diag\n"
    )
    assert find_violations_in_source(src, "src/core/diag.py") == []


def test_forbidden_set_is_the_two_primitives():
    assert FORBIDDEN_CALLS == frozenset({"get_lot_rule", "quantize_qty"})


# --- the live invariant: the real src/ tree is clean today -------------------

def test_real_src_tree_is_clean():
    violations = scan_paths([_REPO_ROOT / "src"], _REPO_ROOT)
    assert violations == [], (
        "qty-legalization guard found out-of-seam venue-lot calls:\n  "
        + "\n  ".join(violations)
    )
