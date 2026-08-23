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


# ---------------------------------------------------- classify_pnl (two-key)
def _row(**kw):
    return {"notes": json.dumps(kw)}


def test_classify_pnl_defers_to_exit_price_when_pnl_source_says_nothing():
    """The live journal's `pnl_source` is only `(none)` or `local_compute` —
    nearly information-free. Keying coverage on it alone reported 0.0 for every
    window, including the 504 rows whose exit price is genuine broker truth."""
    assert _row(pnl_source="local_compute",
                exit_price_source="bybit_closed_pnl") and \
        P.classify_pnl(_row(pnl_source="local_compute",
                            exit_price_source="bybit_closed_pnl"))[0] == P.MEASURED
    assert P.classify_pnl(_row(exit_price_source="bybit_closed_pnl"))[0] == P.MEASURED


def test_classify_pnl_takes_the_WORST_recognised_evidence():
    """`pnl` is only as trustworthy as the weakest evidence behind it: a
    locally-computed PnL derived from a mark-substituted exit price is
    fabricated no matter how the arithmetic was done."""
    assert P.classify_pnl(_row(pnl_source="local_compute",
                               exit_price_source="local_markprice"))[0] == P.FABRICATED
    assert P.classify_pnl(_row(pnl_source="bybit_closed_pnl",
                               exit_price_source="local_markprice"))[0] == P.FABRICATED
    assert P.classify_pnl(_row(pnl_source="bybit_closed_pnl",
                               exit_price_source="candle_at_close"))[0] == P.ESTIMATED


def test_classify_pnl_unverified_when_neither_key_speaks():
    assert P.classify_pnl(_row())[0] == P.UNVERIFIED
    assert P.classify_pnl({})[0] == P.UNVERIFIED
    assert P.classify_pnl(_row(pnl_source="local_compute"))[0] == P.UNVERIFIED


def test_classify_pnl_never_promotes_to_measured_without_evidence():
    """`local_compute` describes the ARITHMETIC, not the evidence — it must
    never be enough on its own."""
    bucket, why = P.classify_pnl(_row(pnl_source="local_compute"))
    assert bucket == P.UNVERIFIED          # the load-bearing assertion, unchanged
    # This used to assert `"no provenance" in why`, which encoded a FALSE
    # statement: `local_compute` IS recorded — it is simply not EVIDENCE, and the
    # module's own docs describe it as deliberately "unrecognised" so the
    # classifier defers to `exit_price_source`. Claiming no provenance was
    # recorded sent auditors hunting a missing writer (measured on trade 4164).
    # The reason now names what was found; the BUCKET is what protects trust.
    assert "local_compute" in why
    assert "no provenance" not in why


def test_classify_pnl_returns_the_deciding_evidence():
    _, why = P.classify_pnl(_row(exit_price_source="local_markprice"))
    assert "exit_price_source=local_markprice" in why


def test_classify_pnl_reproduces_the_live_2026_07_30_distribution():
    """Regression against REAL measured data (trainer journal, 829-row closed
    population, trainer-diag #8073). If a vocabulary edit silently moves
    coverage, this fails."""
    live = [("bybit_closed_pnl", 324), ("local_markprice", 206),
            ("bybit_closed_pnl_rebuild", 131), (None, 119),
            ("recorded_exit_price", 46), ("bybit_closed_pnl_backfill", 2),
            ("operator_flatten_fill", 1)]
    counts = {"total": 0}
    for xsrc, n in live:
        kw = {"pnl_source": "local_compute"}
        if xsrc:
            kw["exit_price_source"] = xsrc
        b = P.classify_pnl(_row(**kw))[0]
        counts[b] = counts.get(b, 0) + n
        counts["total"] += n
    assert counts["total"] == 829
    assert counts[P.MEASURED] == 504
    assert counts[P.FABRICATED] == 206
    assert counts[P.UNVERIFIED] == 119
    assert P.coverage(counts) == 0.608


class TestPnlIsTrustworthy:
    """`pnl_is_trustworthy` — the label-grade predicate both builders share.

    Hoisted here from `setup_labels` once `trade_outcomes` needed it too: two
    copies of a provenance rule is the drift this module exists to prevent.
    """

    def test_takes_a_raw_json_STRING_not_just_a_dict(self):
        """The whole reason it exists.

        `classify_pnl` handed a raw JSON string reads `row['pnl_source']` off a
        `str`, catches the TypeError, and returns UNVERIFIED for EVERY row —
        silent and total. The decode belongs in one place.
        """
        from src.runtime.provenance import pnl_is_trustworthy

        assert pnl_is_trustworthy('{"exit_price_source": "bybit_closed_pnl"}')
        assert not pnl_is_trustworthy('{"exit_price_source": "local_markprice"}')

    def test_accepts_a_mapping_too(self):
        from src.runtime.provenance import pnl_is_trustworthy

        assert pnl_is_trustworthy({"exit_price_source": "bybit_closed_pnl"})

    def test_estimated_is_admitted_measured_only_would_be_too_strict(self):
        from src.runtime.provenance import pnl_is_trustworthy

        assert pnl_is_trustworthy({"exit_price_source": "candle_at_close"})

    def test_fails_CLOSED_on_junk_none_and_non_json(self):
        """Burden of proof is on the data for a training label."""
        from src.runtime.provenance import pnl_is_trustworthy

        for blob in (None, "", "sweep-run-2026-07-01", "[1,2,3]", 42, {}):
            assert not pnl_is_trustworthy(blob), blob

    def test_local_compute_alone_defers_to_the_exit_price(self):
        """`local_compute` describes the arithmetic, not the evidence."""
        from src.runtime.provenance import pnl_is_trustworthy

        assert not pnl_is_trustworthy(
            {"pnl_source": "local_compute",
             "exit_price_source": "local_markprice"})
        assert pnl_is_trustworthy(
            {"pnl_source": "local_compute",
             "exit_price_source": "bybit_closed_pnl"})


class TestUnverifiedReasonKeepsAccountability:
    """`classify_pnl` returned "(no provenance on either key)" unconditionally,
    discarding the raw source strings — contradicting this module's own contract
    that "no record" and "explicitly declared unmeasurable" are told apart by
    READING THE RAW SOURCE STRING.

    Measured on trade 4164 (bybit_portfolio XRPUSDT, closed `reconciler_filled`):
    order_monitor deliberately stamps
    `exit_price_source='entry_order_avg_price_unreliable'` to record WHY the exit
    could not be priced, and the classifier answered "no provenance". An auditor
    reading that hunts a missing writer that is not missing.
    """

    def test_a_declared_unmeasurable_reason_is_reported_not_erased(self):
        from src.runtime import provenance as p
        bucket, why = p.classify_pnl(
            {"exit_price_source": "entry_order_avg_price_unreliable", "pnl_source": None})
        assert bucket == p.UNVERIFIED, "TRUST must not change — it is still not a measurement"
        assert "entry_order_avg_price_unreliable" in why
        assert "no provenance" not in why

    def test_genuinely_absent_still_says_absent(self):
        """The inverse error, and the one my first fix introduced: rendering an
        ABSENT key as an unrecognised source. `classify_row` returns the sentinel
        "(none)", so a bare truthiness test counts absence as a recorded value."""
        from src.runtime import provenance as p
        bucket, why = p.classify_pnl({"exit_price_source": None, "pnl_source": None})
        assert bucket == p.UNVERIFIED
        assert why == "(no provenance on either key)"
        assert "unrecognised" not in why

    def test_absent_and_unrecognised_are_distinguishable(self):
        """The whole point: two states, never collapsed into one reason."""
        from src.runtime import provenance as p
        _, absent = p.classify_pnl({"exit_price_source": None, "pnl_source": None})
        _, present = p.classify_pnl({"exit_price_source": "something_new", "pnl_source": None})
        assert absent != present

    def test_recognised_buckets_are_untouched(self):
        """The bucket logic must not move — only the UNVERIFIED reason string."""
        from src.runtime import provenance as p
        assert p.classify_pnl({"exit_price_source": "bybit_closed_pnl"})[0] == p.MEASURED
        assert p.classify_pnl({"exit_price_source": "candle_at_close"})[0] == p.ESTIMATED
        assert p.is_measured({"exit_price_source": "entry_order_avg_price_unreliable"}) is False
