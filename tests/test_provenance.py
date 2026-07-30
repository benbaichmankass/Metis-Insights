"""The canonical provenance vocabulary — ``src/runtime/provenance.py``.

This module is now the single arbiter of whether a journal number is a
MEASUREMENT or a MANUFACTURE, and `/performance`, the exit diagnostics and the
`/positions` wire all depend on it. It shipped without unit tests; these close
that gap and pin the invariants that make it worth having.

The invariants that must never regress:

1. ``UNVERIFIED`` is never folded into ``MEASURED``. Absence of a provenance
   record is not evidence of measurement. (The 247 legacy rows.)
2. ``ESTIMATED`` is not ``MEASURED`` either — a responsible reconstruction is
   still not a fill.
3. ``coverage`` returns ``None``, not ``0.0``, on an empty window, so "no
   trades" stays distinguishable from "no trade was measured".
4. Key-aware classification: the SAME source string can be an estimate on an
   open position and a fabrication on a closed trade.
"""
from __future__ import annotations

import json

import pytest

from src.runtime import provenance as P


# ------------------------------------------------------------------- buckets
def test_bucket_names_are_distinct():
    assert len({P.MEASURED, P.ESTIMATED, P.FABRICATED, P.UNVERIFIED}) == 4


def test_untrusted_buckets_excludes_measured_only():
    assert P.MEASURED not in P.UNTRUSTED_BUCKETS
    for b in (P.ESTIMATED, P.FABRICATED, P.UNVERIFIED):
        assert b in P.UNTRUSTED_BUCKETS


def test_source_sets_are_disjoint():
    """A source string in two buckets would make classification order-dependent
    — a silent, unreviewable behaviour change on any reordering."""
    assert not (P.MEASURED_SOURCES & P.ESTIMATED_SOURCES)
    assert not (P.MEASURED_SOURCES & P.FABRICATED_SOURCES)
    assert not (P.ESTIMATED_SOURCES & P.FABRICATED_SOURCES)


# ------------------------------------------------------------------ classify
@pytest.mark.parametrize("src", sorted(P.MEASURED_SOURCES))
def test_measured_sources_classify_measured(src):
    assert P.classify(src) == P.MEASURED


@pytest.mark.parametrize("src", sorted(P.FABRICATED_SOURCES))
def test_fabricated_sources_classify_fabricated(src):
    assert P.classify(src) == P.FABRICATED


@pytest.mark.parametrize("src", ["", None, "  ", "something_new", "unknown"])
def test_unrecognised_is_unverified_never_measured(src):
    assert P.classify(src) == P.UNVERIFIED


def test_classify_is_total_and_never_raises():
    """A caller must not be able to skip the check via an exception path."""
    for weird in (object(), 12, [], {}, b"x"):
        assert P.classify(weird) in (
            P.MEASURED, P.ESTIMATED, P.FABRICATED, P.UNVERIFIED
        )


def test_whitespace_is_stripped():
    assert P.classify("  bybit_closed_pnl  ") == P.MEASURED


# ------------------------------------------------- key-aware classification
def test_mark_on_an_open_position_is_estimated_not_fabricated():
    """Marking an OPEN position to the live market is the correct valuation —
    no truer number exists until it closes. Filing it as fabrication would cry
    wolf on every open position and devalue the signal where it matters."""
    assert P.classify("markprice_local", "unrealizedPnlSource") == P.ESTIMATED
    assert P.classify("prop_estimate", "unrealizedPnlSource") == P.ESTIMATED


def test_the_same_string_on_a_closed_trade_is_still_fabricated():
    """The whole point of the key-awareness: a mark stamped on a trade that
    CLOSED hours earlier IS a fabrication — a true value exists and this is not
    it."""
    assert P.classify("markprice_local", "exit_price_source") == P.FABRICATED
    assert P.classify("local_markprice", "exit_price_source") == P.FABRICATED
    assert P.classify("local_markprice") == P.FABRICATED  # strict default


def test_key_aware_overrides_never_upgrade_to_measured_wrongly():
    """The override table changes REPORTING, never trust. Nothing it maps may
    become MEASURED unless the venue genuinely reported it."""
    for key, table in P._KEY_BUCKET_OVERRIDES.items():
        for src, bucket in table.items():
            if bucket == P.MEASURED:
                # Only a broker-reported value may be MEASURED.
                assert src in ("broker",), (key, src)


def test_broker_upnl_is_measured_and_unavailable_is_unverified():
    assert P.classify("broker", "unrealizedPnlSource") == P.MEASURED
    assert P.classify("unavailable", "unrealizedPnlSource") == P.UNVERIFIED


def test_exit_reason_provenance():
    """A reason DERIVED from the bracket is an estimate; `unresolved` is an
    honest absence and must not read as a finding."""
    assert P.classify("price_vs_pkg_bracket", "exit_reason_source") == P.ESTIMATED
    assert P.classify("unresolved", "exit_reason_source") == P.UNVERIFIED


def test_unknown_key_falls_back_to_strict_default():
    """An unrecognised key must take the SAFE reading — over-report
    fabrication, never under-report it."""
    assert P.classify("markprice_local", "some_future_key") == P.FABRICATED


# ---------------------------------------------------------------- classify_row
def test_classify_row_reads_notes_json():
    row = {"notes": json.dumps({"exit_price_source": "bybit_closed_pnl"})}
    assert P.classify_row(row, "exit_price_source") == (P.MEASURED,
                                                        "bybit_closed_pnl")


def test_classify_row_prefers_a_top_level_column():
    """So a future typed column works without touching any caller."""
    row = {"exit_price_source": "bybit_closed_pnl",
           "notes": json.dumps({"exit_price_source": "local_markprice"})}
    assert P.classify_row(row, "exit_price_source")[0] == P.MEASURED


def test_classify_row_handles_missing_notes_and_garbage():
    for row in ({}, {"notes": None}, {"notes": ""}, {"notes": "not json"},
                {"notes": "[1,2,3]"}):
        assert P.classify_row(row, "exit_price_source") == (P.UNVERIFIED, "(none)")


def test_classify_row_is_key_aware():
    row = {"notes": json.dumps({"unrealizedPnlSource": "markprice_local"})}
    assert P.classify_row(row, "unrealizedPnlSource")[0] == P.ESTIMATED


def test_classify_row_rejects_an_undeclared_key():
    """Forces a new signal through PROVENANCE_KEYS — which is what makes the CI
    guard able to see it and demand a consumer."""
    with pytest.raises(ValueError, match="not a declared provenance key"):
        P.classify_row({}, "made_up_key")


# ------------------------------------------------------------------ is_measured
def test_is_measured_is_strictly_binary():
    assert P.is_measured({"exit_price_source": "bybit_closed_pnl"}) is True
    assert P.is_measured({"exit_price_source": "local_markprice"}) is False
    assert P.is_measured({"exit_price_source": "candle_at_close"}) is False
    assert P.is_measured({}) is False  # unverified is NOT measured


# ------------------------------------------------------- split_counts/coverage
def test_split_counts_totals():
    rows = [
        {"exit_price_source": "bybit_closed_pnl"},
        {"exit_price_source": "local_markprice"},
        {"exit_price_source": "candle_at_close"},
        {},
    ]
    counts = P.split_counts(rows)
    assert counts == {P.MEASURED: 1, P.ESTIMATED: 1, P.FABRICATED: 1,
                      P.UNVERIFIED: 1, "total": 4}


def test_coverage_is_none_on_an_empty_window():
    """None, not 0.0 — 'no trades' must stay distinguishable from 'nothing was
    measured'. That distinction is what made the bug invisible."""
    assert P.coverage(P.split_counts([])) is None
    assert P.coverage({"total": 0}) is None


def test_coverage_zero_means_nothing_measured():
    counts = P.split_counts([{"exit_price_source": "local_markprice"}] * 5)
    assert P.coverage(counts) == 0.0


def test_coverage_fraction():
    rows = ([{"exit_price_source": "bybit_closed_pnl"}] * 3
            + [{"exit_price_source": "local_markprice"}])
    assert P.coverage(P.split_counts(rows)) == 0.75


# --------------------------------------------------------------- require_measured
def test_require_measured_passes_on_all_measured():
    P.require_measured([{"exit_price_source": "bybit_closed_pnl"}])


def test_require_measured_raises_on_fabricated():
    with pytest.raises(P.ProvenanceError, match="refusing to use"):
        P.require_measured([{"exit_price_source": "local_markprice"}])


def test_require_measured_raises_on_estimated_too():
    """A responsible reconstruction is still not a fill — a promotion gate or
    ML label set must not silently average it in."""
    with pytest.raises(P.ProvenanceError):
        P.require_measured([{"exit_price_source": "candle_at_close"}])


def test_require_measured_rejects_unverified_by_default():
    with pytest.raises(P.ProvenanceError):
        P.require_measured([{}])


def test_allow_unverified_still_rejects_fabricated():
    """The legacy-row escape hatch must not become a fabrication escape hatch."""
    P.require_measured([{}], allow_unverified=True)  # tolerated
    with pytest.raises(P.ProvenanceError):
        P.require_measured([{"exit_price_source": "local_markprice"}],
                           allow_unverified=True)


def test_require_measured_error_names_the_context_and_the_split():
    with pytest.raises(P.ProvenanceError) as exc:
        P.require_measured(
            [{"exit_price_source": "local_markprice"}], context="promotion gate"
        )
    msg = str(exc.value)
    assert "promotion gate" in msg
    assert "fabricated=1" in msg


# ------------------------------------------------------------ guard coupling
def test_every_declared_key_is_a_string_and_unique():
    assert len(set(P.PROVENANCE_KEYS)) == len(P.PROVENANCE_KEYS)
    assert all(isinstance(k, str) and k for k in P.PROVENANCE_KEYS)


def test_override_table_keys_are_declared_provenance_keys():
    """An override for an undeclared key would be dead config the guard can
    never see."""
    for key in P._KEY_BUCKET_OVERRIDES:
        assert key in P.PROVENANCE_KEYS
