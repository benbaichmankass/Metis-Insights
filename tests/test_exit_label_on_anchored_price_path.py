"""The exit LABEL must also be re-derived on the ANCHORED-price sweep.

``_sweep_pending_pnl_from_bybit`` learned this in #10151 (item 1.8). Its sibling
``_sweep_local_pnl_for_unpriced`` — the sweep that prices every row Bybit broker
truth never covers — did not, so it wrote the price and left the label frozen at
``reconciler_filled``. Fixing one sweep and not the other is the "swept the
instance, not the class" shape this repo has named and re-paid for repeatedly.

MEASURED 2026-08-23 over the whole live journal: 1,309 closed non-backtest rows,
578 still carrying the generic reason, **497** eligible once reduce legs are
excluded. Of those 497, **191** resolve to a real bracket level — **156 off a
MEASURED price**, 35 off an ESTIMATED one — while **105 sit on a FABRICATED
price and must NOT be labelled at all**.

That last number is the point of this file. ``_classify_broker_exit`` is
provenance-BLIND: it compares a price to the package bracket and cannot know
whether that price is a broker fill or ``local_markprice`` — the market read at
SWEEP time, hours after the exit. Classifying the latter manufactures an sl/tp
verdict out of unrelated later price action, which is the fabrication class
``exit_anchor`` exists to stop, one level up in the label instead of the number.

Row: ``BL-20260823-EXIT-LABEL-FROZEN-ON-THE-ANCHORED-PRICE-PATH``.
"""
from __future__ import annotations

import inspect

import pytest

from src.runtime import order_monitor as om
from src.runtime import provenance as prov


def _sweep_src() -> str:
    return inspect.getsource(om._sweep_local_pnl_for_unpriced)


# ── wiring ────────────────────────────────────────────────────────────────────

def test_the_anchored_sweep_calls_the_classifier():
    assert "_classify_broker_exit" in _sweep_src(), (
        "this sweep writes the exit price; it must also re-derive the label that "
        "depends on it, or the label stays frozen at the one instant the answer "
        "could not be known"
    )


def test_the_sweep_reads_exit_reason_so_a_real_reason_is_not_clobbered():
    """The relabel must be gated on the row still carrying the GENERIC reason.

    This sweep selects on ``pnl IS NULL``, which includes rows a different path
    closed with a real reason (``pairs_*``, ``sl_cross``, ``stale_stop``).
    Overwriting one destroys a better record than the one being written — and
    the gate is unreachable if the SELECT never reads the column.
    """
    src = _sweep_src()
    assert "exit_reason " in src or "exit_reason\"" in src, (
        "the SELECT must read exit_reason — otherwise the still-generic guard "
        "cannot be evaluated at all"
    )
    assert "reconciler_filled" in src, "the relabel must be gated on the generic reason"


def test_reduce_legs_stay_excluded_by_the_query_not_a_second_predicate():
    """Guard 1 lives in the WHERE clause; a duplicate derivation could drift.

    Unlike its Bybit sibling, this query already excludes reduce legs in SQL, so
    re-deriving ``is_reduce_leg`` in Python would be a SECOND predicate for one
    rule — the drift shape this repo single-homes everywhere else.
    """
    src = _sweep_src()
    assert "intent_reduce" in src, "the query must still exclude reduce legs"
    assert "is_reduce_leg=False" in src, (
        "the classifier call should state plainly that the query already did the "
        "exclusion, rather than silently defaulting"
    )


def test_the_price_write_cannot_be_lost_to_a_label_failure():
    """Guard 3: the pnl/exit_price update is the load-bearing half."""
    src = _sweep_src()
    i_try = src.find("exit re-classification")
    assert i_try != -1, "the re-classification must carry its own guarded block"
    assert "noqa: BLE001" in src[max(0, i_try - 2000):i_try + 2000], (
        "the label derivation must be wrapped so it can never propagate into the "
        "price write"
    )


# ── the basis gate — the behavioural half ─────────────────────────────────────

@pytest.mark.parametrize("source", sorted(prov.MEASURED_SOURCES))
def test_measured_prices_map_to_the_strong_label_source(source):
    basis = prov.classify(source, "exit_price_source")
    assert basis == prov.MEASURED
    assert om._EXIT_LABEL_SOURCE_BY_BASIS[basis] == "price_vs_pkg_bracket"


def test_an_estimated_price_gets_its_own_weaker_label_source():
    """An inference on an inference must not read as the stronger verdict."""
    basis = prov.classify("candle_at_close", "exit_price_source")
    assert basis == prov.ESTIMATED
    stamped = om._EXIT_LABEL_SOURCE_BY_BASIS[basis]
    assert stamped == "price_vs_pkg_bracket_est_price"
    assert stamped != om._EXIT_LABEL_SOURCE_BY_BASIS[prov.MEASURED], (
        "a label derived from an anchored estimate must be distinguishable from "
        "one derived from a broker fill"
    )


@pytest.mark.parametrize("source", ["local_markprice", "markprice_local",
                                    "netted_duplicate_unattributed",
                                    "netted_prorated", "prop_estimate"])
def test_fabricated_prices_have_no_label_source_at_all(source):
    """THE control this file exists for.

    A fabricated price must be unable to produce a label by construction — not
    by a downstream check that a future edit could forget.
    """
    basis = prov.classify(source, "exit_price_source")
    assert basis == prov.FABRICATED
    assert basis not in om._EXIT_LABEL_SOURCE_BY_BASIS, (
        f"{source!r} is FABRICATED — deriving sl/tp from it manufactures a "
        "verdict out of price action unrelated to the exit"
    )


def test_the_sweep_refuses_rather_than_skipping_silently():
    """A silent skip is indistinguishable from never having looked.

    The ABSENCE of ``exit_reason_source`` is load-bearing — it is the 100%
    signature that made this whole defect class readable. So a row we looked at
    and DECLINED must say so.
    """
    src = _sweep_src()
    assert "EXIT_LABEL_REFUSED_UNMEASURED" in src, (
        "a fabricated-price row must be stamped as refused, not skipped in silence"
    )


def test_the_three_outcomes_are_mutually_distinguishable():
    """resolved / unresolved / refused / never-ran are four states, not two."""
    values = {
        om._EXIT_LABEL_SOURCE_BY_BASIS[prov.MEASURED],
        om._EXIT_LABEL_SOURCE_BY_BASIS[prov.ESTIMATED],
        "unresolved",
        prov.EXIT_LABEL_REFUSED_UNMEASURED,
    }
    assert len(values) == 4, "collapsing any two of these loses a real distinction"


def test_refusal_is_not_smuggled_into_a_provenance_bucket():
    """A refusal is not a grade of a value — no value was produced."""
    v = prov.EXIT_LABEL_REFUSED_UNMEASURED
    assert v not in prov.MEASURED_SOURCES
    assert v not in prov.ESTIMATED_SOURCES
    assert v not in prov.FABRICATED_SOURCES
    assert prov.classify(v) == prov.UNVERIFIED
