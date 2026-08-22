"""The bracket audit's ROLL-UP must not assert more than its verdicts measured.

2026-07-30, diagnostic-provenance sub-class A2 (narrowed/widened claim). The
roll-up bucketed only under-coverage and then printed

    "every audited symbol is fully SL-covered at the broker."

while a **444.7% over-coverage** sat in the body above it. That sentence is
*literally true* at 444.7% (``covered >= size``), which is exactly what makes it
dangerous: it is not a wrong number, it is a summary asserting a clean bill of
health far stronger than the verdict measured. A reader who stops at the
roll-up — which is the point of a roll-up — walks past live leg
over-accumulation, the very condition the runtime's own
``_check_broker_naked_bybit_positions`` flags as ``over_covered``
(BL-20260730-BYBIT1-XRP-LEG-OVERACCUM-WORSENING). The audit and the runtime
disagreed about the same position, and only the audit was being read.

These tests pin the classification without a broker, so the logic is verified
in CI and the live run only has to confirm integration.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "bybit_bracket_audit", _ROOT / "scripts" / "ops" / "bybit_bracket_audit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


audit = _load()


def _summary(*symbols):
    return {"accounts": [{"account_id": "bybit_1", "symbols": list(symbols)}]}


def _sym(symbol, verdict, pct, uncovered=0.0, covered=0.0):
    return {"symbol": symbol, "verdict": verdict, "coverage_pct": pct,
            "uncovered_qty": uncovered, "sl_covered_qty": covered}


def test_the_444_percent_case_is_no_longer_an_all_clear():
    """The exact live shape that produced the false all-clear."""
    bad, over, audited = audit.classify_rollup(
        _summary(_sym("XRPUSDT", "PROTECTED", 444.7, covered=4447.0)))
    assert bad == []
    assert len(over) == 1, "over-coverage must get its own bucket, not the else-branch"
    assert over[0][1] == "XRPUSDT"
    assert audited == 1


def test_genuinely_clean_coverage_is_still_clean():
    """The guard must not turn every PROTECTED symbol into a finding."""
    bad, over, audited = audit.classify_rollup(
        _summary(_sym("BTCUSDT", "PROTECTED", 100.0),
                 _sym("ETHUSDT", "PROTECTED", 100.3)))
    assert (bad, over, audited) == ([], [], 2)


def test_under_coverage_still_reported():
    bad, over, audited = audit.classify_rollup(
        _summary(_sym("SOLUSDT", "PARTIALLY_NAKED", 62.0, uncovered=38.0)))
    assert len(bad) == 1 and over == [] and audited == 1


def test_unreliable_leg_qty_counts_as_under_covered_not_clean():
    """An ungradeable coverage must never fall through to the all-clear."""
    bad, over, _ = audit.classify_rollup(
        _summary(_sym("XRPUSDT", "PROTECTED_UNRELIABLE_LEG_QTY", None)))
    assert len(bad) == 1 and over == []


def test_flat_symbols_are_excluded_from_the_denominator():
    """A symbol with no position is not evidence of coverage either way.

    Counting it would inflate the reassurance the summary line gives — the
    denominator has to mean what the claim ranges over.
    """
    _, _, audited = audit.classify_rollup(
        _summary(_sym("BTCUSDT", "PROTECTED", 100.0),
                 _sym("DOGEUSDT", "FLAT", None),
                 _sym("ADAUSDT", "FLAT", None)))
    assert audited == 1


@pytest.mark.parametrize("pct,expect_over", [
    (100.0, False),
    (100.4, False),   # inside the 0.5% float-noise epsilon
    (100.6, True),    # outside it — a real extra leg
    (444.7, True),
])
def test_over_coverage_threshold_respects_the_float_noise_epsilon(pct, expect_over):
    """Bybit echoes leg qty as a string at the instrument's qty step; a hair of
    float noise must not read as over-accumulation (that would be the
    alarm-fatigue failure mode in the other direction)."""
    _, over, _ = audit.classify_rollup(_summary(_sym("BTCUSDT", "PROTECTED", pct)))
    assert bool(over) is expect_over


def test_the_all_clear_branch_requires_both_buckets_empty():
    """Mixed state must not print the all-clear."""
    bad, over, audited = audit.classify_rollup(
        _summary(_sym("BTCUSDT", "PROTECTED", 100.0),
                 _sym("XRPUSDT", "PROTECTED", 444.7)))
    assert not (not bad and not over), "all-clear branch would fire on a real anomaly"
    assert audited == 2


# ---------------------------------------------------------------------------
# 2026-08-16 — the SAME class, twice more, in this same function.
#
# The 444.7% fix above added ONE bucket. That is why there was a second and a
# third: a stop-only book and a dead tracked leg with unbacked journal qty both
# sat in the record while the roll-up printed "0 naked, 0 over-covered". The
# structural answer is `_ROLLUP_DIMENSIONS` — the all-clear is computed FROM a
# declared list and names the dimensions it cleared, and the last test here
# fails if a new concern field is added to the record without being graded.
# ---------------------------------------------------------------------------

def _rich(symbol, verdict="PROTECTED", pct=100.0, *, target_state="present",
          dead_legs=0, divergent=False, excess=0.0):
    """A per-symbol record carrying every dimension the roll-up grades."""
    return {"symbol": symbol, "verdict": verdict, "coverage_pct": pct,
            "uncovered_qty": 0.0, "sl_covered_qty": 0.0,
            "target_state": target_state,
            "trades_with_tracked_leg_dead": dead_legs,
            "journal_qty_divergent": divergent, "journal_qty_excess": excess}


def test_a_stop_only_book_is_NOT_an_all_clear():
    """The finding: fully stop-covered, zero take-profit, and it read clean."""
    found, audited = audit.grade_rollup(_summary(_rich("MGCUSDT", target_state="absent")))
    assert audited == 1
    assert found["sl_coverage"] == [] and found["over_coverage"] == []   # stop side IS fine
    assert len(found["target_present"]) == 1, (
        "a position with a full stop and no target must be reported — it can "
        "only stop out or run"
    )
    assert not _is_all_clear(found)


def test_a_dead_tracked_leg_is_NOT_an_all_clear():
    found, _ = audit.grade_rollup(_summary(_rich("ETHUSDT", dead_legs=1)))
    assert len(found["tracked_legs_alive"]) == 1
    assert not _is_all_clear(found)


def test_unbacked_journal_qty_is_NOT_an_all_clear():
    """bybit_portfolio ETHUSDT: exchange 21.05, journal 35.01, 13.96 unbacked.

    Coverage was 100% — correct against the EXCHANGE size, which is simply a
    different question from whether every journal row is backed.
    """
    found, _ = audit.grade_rollup(
        _summary(_rich("ETHUSDT", divergent=True, excess=13.96)))
    assert len(found["journal_qty_backed"]) == 1
    assert found["journal_qty_backed"][0][3] == 13.96
    assert not _is_all_clear(found)


def test_a_genuinely_clean_symbol_still_clears_every_dimension():
    """The guard must not cry wolf, or it gets ignored — which is the P1."""
    found, audited = audit.grade_rollup(_summary(_rich("XRPUSDT")))
    assert audited == 1
    assert _is_all_clear(found)


def test_flat_symbols_are_still_excluded_from_the_new_dimensions():
    found, audited = audit.grade_rollup(
        _summary({"symbol": "BTCUSDT", "verdict": "FLAT"}))
    assert audited == 0 and _is_all_clear(found)


def _is_all_clear(found):
    return not any(found.values())


def test_every_concern_field_is_graded_by_some_dimension():
    """The guard against a FOURTH recurrence.

    Every prior instance of this bug was a field the record already carried
    that no bucket read. So the invariant is not "grade these three more
    things" — it is that a concern field cannot exist ungraded. Adding one to
    `_audit_symbol` without wiring a dimension fails here, at the moment it is
    introduced, instead of the next time someone reads a false all-clear.
    """
    src = (_ROOT / "scripts" / "ops" / "bybit_bracket_audit.py").read_text()
    concern_fields = {
        "target_state": "target_present",
        "trades_with_tracked_leg_dead": "tracked_legs_alive",
        "journal_qty_divergent": "journal_qty_backed",
        "uncovered_qty": "sl_coverage",
        "coverage_pct": "over_coverage",
        "price_diverged_count": "price_agreement",
    }
    graded = {name for name, _ in audit._ROLLUP_DIMENSIONS}
    for field, dimension in concern_fields.items():
        assert field in src, f"{field} vanished from the record — update this map"
        assert dimension in graded, (
            f"record field {field!r} signals a concern but no roll-up dimension "
            f"reads it, so a symbol carrying it would still print an all-clear"
        )
    # ...and the all-clear must be derived from the declared list, never a
    # hand-written sentence that can drift from what was actually graded.
    assert "_ROLLUP_DIMENSIONS" in src.split("ROLL-UP")[-1], (
        "the roll-up must compute its all-clear from _ROLLUP_DIMENSIONS"
    )


# ---------------------------------------------------------------------------
# BL-20260821-BYBIT-BRACKET-AUDIT-HAS-NO-PRICE-AXIS
#
# Every dimension above grades a QUANTITY or an EXISTENCE. None asked whether
# the resting leg sits WHERE THE JOURNAL DECLARED, so a clean bill of health
# was compatible with every stop resting at the wrong price -- and both sides
# of the comparison were already in the record, never compared.
#
# The row's own resolution criteria require a PLANTED DIVERGENCE shown to FAIL
# before a clean result is trusted. That is `test_a_planted_divergence_FAILS`
# below; the clean case beside it is what stops the check from passing by
# always alarming.
# ---------------------------------------------------------------------------

class _FakeClient:
    """Minimal stand-in for the pybit client: two read calls, no network."""

    def __init__(self, position, legs):
        self._position, self._legs = position, legs

    def get_positions(self, **_kw):
        return {"result": {"list": [self._position]}}

    def get_open_orders(self, **_kw):
        return {"result": {"list": self._legs}}


def _row(trade_id, sl, tracked_id, qty=1.0, symbol="XRPUSDT"):
    return {"id": trade_id, "symbol": symbol, "strategy_name": "s",
            "direction": "buy", "position_size": qty, "stop_loss": sl,
            "take_profit_1": None, "sl_order_id": tracked_id,
            "tp_order_id": None, "created_at": "2026-08-22T00:00:00Z"}


def _partial(position_sl="", legs=(), rows=()):
    pos = {"size": "10", "side": "Buy", "stopLoss": position_sl,
           "takeProfit": "3.0", "tpslMode": "Partial"}
    return audit._audit_symbol(_FakeClient(pos, list(legs)), "linear",
                               "XRPUSDT", list(rows))


def test_a_planted_divergence_FAILS():
    """THE REQUIRED CONTROL. Journal says 2.00, the live leg rests at 1.50.

    Before this dimension existed the audit reported PROTECTED here: the leg
    exists, its qty covers the position, a target rests. Every quantity was
    right and the stop was in the wrong place.
    """
    res = _partial(
        legs=[{"orderId": "L1", "stopOrderType": "PartialStopLoss", "qty": "10",
               "triggerPrice": "1.50", "orderStatus": "Untriggered"},
              {"orderId": "T1", "stopOrderType": "PartialTakeProfit", "qty": "10",
               "triggerPrice": "3.00", "orderStatus": "Untriggered"}],
        rows=[_row(1, 2.00, "L1", qty=10.0)])
    assert res["price_state"] == "diverged", res
    assert res["price_diverged_count"] == 1
    t = res["journal_open_trades"][0]
    assert t["broker_sl"] == 1.50 and t["journal_sl"] == 2.00
    # ...and it must reach the roll-up, or the body carries a finding the
    # summary still calls clean -- the exact bug this file exists for.
    found, _ = audit.grade_rollup(_summary(res))
    assert len(found["price_agreement"]) == 1
    assert not _is_all_clear(found)


def test_matching_prices_are_clean():
    """The guard must not cry wolf, or it gets ignored -- which is the P1."""
    res = _partial(
        legs=[{"orderId": "L1", "stopOrderType": "PartialStopLoss", "qty": "10",
               "triggerPrice": "2.00", "orderStatus": "Untriggered"}],
        rows=[_row(1, 2.00, "L1", qty=10.0)])
    assert res["price_state"] == "agree"
    found, _ = audit.grade_rollup(_summary(res))
    assert found["price_agreement"] == []


def test_tick_rounding_is_not_a_divergence():
    """2.000 vs 2.005 is 0.25% -- inside the stated tolerance, not an alarm."""
    res = _partial(
        legs=[{"orderId": "L1", "stopOrderType": "PartialStopLoss", "qty": "10",
               "triggerPrice": "2.005", "orderStatus": "Untriggered"}],
        rows=[_row(1, 2.00, "L1", qty=10.0)])
    assert res["price_state"] == "agree"


def test_full_mode_netting_surfaces_the_rows_riding_someone_elses_stop():
    """The documented Jun 21-23 mechanism, which the audit could not see.

    Full mode holds ONE position-level stopLoss and each new open REPLACES it,
    so N rows with N declared stops all ride the newest geometry. Here two of
    three rows are wrong, and every quantity dimension is still perfectly clean.
    """
    res = _partial(position_sl="1.50",
                   rows=[_row(1, 2.00, None), _row(2, 1.80, None), _row(3, 1.50, None)])
    assert res["price_diverged_count"] == 2, res["journal_open_trades"]
    assert res["price_agree_count"] == 1
    assert res["price_state"] == "diverged"
    assert all(t["price_basis"] == "full_mode_position_stopLoss"
               for t in res["journal_open_trades"])


def test_no_tracked_leg_is_ungradeable_never_agree():
    """"We could not look" must not collapse into "the price is right"."""
    res = _partial(
        legs=[{"orderId": "L9", "stopOrderType": "PartialStopLoss", "qty": "10",
               "triggerPrice": "1.50", "orderStatus": "Untriggered"}],
        rows=[_row(1, 2.00, None, qty=10.0)])
    assert res["price_state"] == "ungradeable"
    assert res["price_ungradeable_count"] == 1
    assert res["price_diverged_count"] == 0
    # An entirely ungradeable symbol must not present as clean-by-silence: the
    # envelope says `ungradeable`, so the denominator travels with the verdict.
    assert res["price_agree_count"] == 0


def test_a_dead_tracked_leg_is_ungradeable_not_agree():
    """A row whose tracked leg no longer rests has no price to compare to."""
    res = _partial(
        legs=[{"orderId": "OTHER", "stopOrderType": "PartialStopLoss", "qty": "10",
               "triggerPrice": "2.00", "orderStatus": "Untriggered"}],
        rows=[_row(1, 2.00, "GONE", qty=10.0)])
    assert res["price_state"] == "ungradeable"
    assert res["journal_open_trades"][0]["price_basis"] == "no_tracked_live_leg"
