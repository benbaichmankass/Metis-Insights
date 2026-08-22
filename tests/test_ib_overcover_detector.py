"""The IB naked sweep must have an UPPER bound, and grade it by OCA group.

``BL-20260816-IB-STOPS-OVER-COVER-IN-DISJOINT-OCA-GROUPS``.

`_check_broker_naked_ib_positions` only ever asked whether coverage was SUFFICIENT, so
ib_paper MES holding stops 338 (15 @ 7516.50, `oca-protect-336`) and 375 (15 @ 7533.75,
`oca-protect-373`) — **30 contracts against a 15 long** — passed with room to spare.
Bybit's `_bybit_position_protection` has emitted a detect-only `over_covered` at >1.5x
since BL-20260730; the class was implemented for one venue and never ported.

⚠️ The danger is the GROUP COUNT, not the ratio. `ocaType=1` cancels the rest of the SAME
group when a leg fills and says nothing about a different group, so excess inside one
group is self-limiting while excess across two is: stop A fires → flat → stop B still
resting → fires → naked SHORT. These tests pin that distinction, and pin that the
detector never cancels anything.
"""
from __future__ import annotations

import inspect

from src.runtime import order_monitor as om


def _src() -> str:
    return inspect.getsource(om._check_broker_naked_ib_positions)


def test_an_upper_bound_exists_at_all():
    assert "_IB_OVERCOVER_FACTOR" in _src(), (
        "the IB sweep had no upper bound: 30 contracts of stop against a 15 long passed"
    )


def test_it_grades_the_STOP_quantity_not_the_combined_coverage():
    """`covered_qty` counts a stop and a target as interchangeable.

    The naked-short sequence is driven by resting STOPS specifically — a resting target
    that fills does not leave the account short.
    """
    src = _src()
    i = src.index("_IB_OVERCOVER_FACTOR")
    window = src[max(0, i - 600):i + 200]
    assert "stop_qty" in window, "the upper bound must read stop_qty, not covered_qty"


def test_the_two_venues_are_graded_on_the_same_threshold():
    assert om._IB_OVERCOVER_FACTOR == om._BYBIT_OVERCOVER_FACTOR, (
        "a silent threshold divergence between venues is its own trap"
    )


def test_disjoint_groups_are_graded_HARDER_than_one_group():
    """Two levels, not one alarm — an alarm that shouts equally at both is the P1."""
    src = _src()
    i = src.index("_IB_OVERCOVER_FACTOR")
    after = src[i:]
    assert "oca_groups" in after, "the verdict must read the OCA group set"
    assert "len(_groups) > 1" in after, "the disjoint case must be distinguished"
    assert "logger.error" in after and "logger.warning" in after, (
        "the dangerous case escalates; the self-limiting one does not"
    )


def _overcover_block() -> str:
    """Just the over-cover detector, bounded by its own delimiters.

    NOT a fixed-size slice: an earlier draft of this test took 3000 chars after the
    factor and swept in the sweep's LEGITIMATE re-arm code further down, failing on
    `place_protective` — which is that code's actual job and must stay. Asserting over
    the wrong region is how a test manufactures a finding.
    """
    src = _src()
    start = src.index("_stop_q = float(")
    end = src.index("# TARGET-side coverage", start)
    return src[start:end]


def _code_only(block: str) -> str:
    """The block with string literals and comments removed, via tokenize.

    The question is whether the detector CALLS an ordering function, not whether the
    name appears in prose. A first draft asserted over the raw text and tripped on the
    detector's own log message -- "not the shape place_protective creates" -- which is
    exactly the false finding a name-match produces when the question is behavioural.
    tokenize rather than regex: quoting rules are the tokenizer's job.
    """
    import io
    import tokenize
    body = "if True:\n" + "\n".join("    " + ln for ln in block.splitlines())
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(body).readline):
            if tok.type in (tokenize.STRING, tokenize.COMMENT):
                continue
            out.append(tok.string)
    except Exception as exc:  # noqa: BLE001
        # Never let the stripper's own fragility become a silent pass.
        raise AssertionError("could not tokenize the detector block: " + str(exc))
    return " ".join(out)


def test_the_detector_never_cancels_or_rearms_anything():
    """Choosing WHICH leg to cancel is exactly what went wrong on 2026-08-20."""
    code = _code_only(_overcover_block())
    for forbidden in ("cancel_order", "_attempt_naked_autoprotect",
                      "place_protective", "modify_open_order"):
        assert forbidden not in code, (
            "the over-cover detector must be DETECT-ONLY; found a call to "
            + repr(forbidden)
        )


def test_the_literal_stripper_actually_strips():
    """Guard the guard -- a stripper that no-ops makes the test above vacuous."""
    sample = 'y = "place_protective"  # place_protective\nz = cancel_order(1)\n'
    out = _code_only(sample)
    assert "place_protective" not in out, "a string literal must not survive"
    assert "cancel_order" in out, "a REAL call must survive stripping"



def test_the_detector_block_is_the_one_we_think_it_is():
    """Guard the guard: if the delimiters drift, the test above tests nothing."""
    block = _overcover_block()
    assert "_IB_OVERCOVER_FACTOR" in block
    assert "over_covered" in block
    assert 200 < len(block) < 4000, f"unexpected block size {len(block)}"


def test_the_counter_is_declared_up_front_not_created_on_first_use():
    """A sweep that found none must report 0, not omit the key."""
    src = _src()
    assert '"over_covered": 0' in src, (
        "'we looked and found none' must not read as 'we did not look' — which is "
        "precisely what this detector exists to stop being true"
    )


def test_the_summary_actually_carries_the_key_after_a_run():
    """Declared is not the same as reachable — prove it survives an early return."""
    summary = om._check_broker_naked_ib_positions(None)
    assert isinstance(summary, dict)
    assert "over_covered" in summary, (
        "the key must be present even on the paths that return early"
    )
    assert summary["over_covered"] == 0
