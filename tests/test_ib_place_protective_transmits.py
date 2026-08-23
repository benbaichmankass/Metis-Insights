"""Every leg of a parentless OCA bracket must TRANSMIT.

`BL-20260823-REASSERT-REPORTS-APPLIED-OK-ON-A-HALF-ARMED-BRACKET` — and the
CAUSE of `BL-20260816-COVERAGE-IS-ONE-SIDED`, whose measurement was "zero limit
orders existed account-wide" on `ib_paper`.

`place_protective` set `leg.transmit = i == len(legs) - 1`, described in its own
comment as mirroring "the bracket-transmit discipline in place()". That
discipline works in `place()` ONLY because its children carry `parentId`, so
IBKR holds them until the parent transmits and then releases them. These legs
have **no parent** — the method's own docstring says so — and a parentless order
with `transmit=False` is held and never released. `legs` was built `[TP, SL]`,
so the TP was the non-final leg: constructed, sent, never transmitted. Every
bracket placed through this method was stop-only, and `place_target_in_group`
was built to repair the symptom while the producer kept making them.

Source-level assertions: the module imports `ccxt`/`ib_insync`, which are not
installed everywhere, so the placement block is read rather than executed. The
risk of source inspection is a test that passes over a `NameError` — mitigated
here because each assertion names a specific token whose ABSENCE is the defect,
and the negative controls below fail on the pre-fix source.
"""
from __future__ import annotations

import ast
import re

_SRC = open("src/units/accounts/ib_client.py").read()


def _place_protective_src() -> str:
    """`_locked_place_protective`, NOT the `place_protective` wrapper.

    The public method only takes the lock and delegates — the placement code
    lives in the `_locked_` sibling, the same split `modify_protective` uses.
    Reading the wrapper returns a function containing none of the legs, so every
    "the defect is absent" assertion would pass vacuously. Caught by writing the
    assertions first and watching them fail against the wrong function."""
    tree = ast.parse(_SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_locked_place_protective":
            body = list(node.body)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(getattr(body[0], "value", None), ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body = body[1:]          # drop the docstring
            node.body = body or [ast.Pass()]
            # `ast.unparse`, not the raw segment: the assertions below check that
            # the DEFECT is absent, and the fix's own explanatory comment quotes
            # the defective expression verbatim. Reading raw source makes the
            # documentation fail the test — a check that cannot survive being
            # explained. Unparsing yields executable code only.
            return ast.unparse(node)
    raise AssertionError("_locked_place_protective not found")


SRC = _place_protective_src()


def test_no_leg_is_left_untransmitted():
    """The defect, stated as its own assertion."""
    assert "leg.transmit = True" in SRC
    assert "i == len(legs) - 1" not in SRC, (
        "transmit-on-last requires a parentId to release the held legs; "
        "these legs have no parent, so the non-final one is never transmitted")


def test_these_legs_really_have_no_parent():
    """The premise the fix rests on — if a parentId were ever added here,
    transmit-on-last would become correct again and this test should be the
    thing that makes someone think about it."""
    assert "parentId" not in SRC


def test_the_stop_is_built_before_the_target():
    """Order is load-bearing now that every leg goes live: the legs reach IBKR
    one at a time, so the first is briefly alone. A window with only downside
    protection is strictly safer than one with only an upside target."""
    sl_at = SRC.find("StopOrder(reverse")
    tp_at = SRC.find("LimitOrder(reverse")
    assert sl_at != -1 and tp_at != -1
    assert sl_at < tp_at, "the stop must be placed first"


def test_the_returned_order_id_names_the_stop_not_the_last_leg():
    """It used to be `legs[-1]`, which WAS the stop only because the list was
    built [TP, SL]. Reordering would have silently changed which leg the id
    names, and callers persist it (`trades.sl_order_id`)."""
    assert "legs[-1].orderId" not in SRC
    # quote-agnostic: `ast.unparse` normalises string quoting.
    assert re.search(r"type\(leg\)\.__name__ == ['\"]StopOrder['\"]", SRC), (
        "the returned id must be resolved by leg TYPE, not by list position")


def test_both_legs_are_still_tied_into_one_oca_group():
    """Transmitting both must not break the OCA tie — one filling has to cancel
    the other, or a filled target leaves a live stop on a flat book."""
    assert "leg.ocaGroup = oca_group" in SRC
    assert "leg.ocaType = 1" in SRC
