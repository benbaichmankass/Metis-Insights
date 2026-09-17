"""Tests for src/research/stop_attribution.py.

THE LOAD-BEARING TEST IS `TestMI275Exhibits`. It is not a synthetic check: the
numbers are the real journal rows for the two packages MI-275 named in
`docs/research/stop-width-counterfactual-2026-09-11.md` section 3.5, read from
`/api/diag/journal` on 2026-09-12. If this module ever stops reproducing
MI-275's own `1.320` and `0.276` ATR figures, one of the two is wrong and the
disagreement must be resolved rather than re-baselined.
"""

import pytest

from src.research.stop_attribution import (
    DEFAULT_TOLERANCE_FRAC,
    GRADED_STATES,
    STATES,
    UNGRADEABLE_STATES,
    classify,
    declared_stop,
    format_split,
    split,
)

# --- the two real packages, verbatim from the live journal ------------------
# pkg-65f02cffa856451f · trend_donchian_avax_4h · AVAXUSDT · short
AVAX_PKG = {
    "order_package_id": "pkg-65f02cffa856451f",
    "strategy_name": "trend_donchian_avax_4h",
    "symbol": "AVAXUSDT",
    "direction": "short",
    "entry": 7.128,
    "sl": 7.25560714,                      # ALREADY TRAILED — not the declared level
    "exit_plan": {"stop": {"price": 7.27296429}},
    "meta": {"atr": 0.096642857142857},    # entry-frozen, verbatim from the journal row
}
AVAX_TRADE = {
    "id": 5247, "direction": "short", "entry_price": 7.099,
    "stop_loss": 7.25560714, "exit_price": 7.254, "exit_reason": "reconciler_filled",
}

# pkg-27b4c7d12e794bcc · trend_donchian · BTCUSDT · long · trades 5674/5675/5676
BTC_PKG = {
    "order_package_id": "pkg-27b4c7d12e794bcc",
    "strategy_name": "trend_donchian",
    "symbol": "BTCUSDT",
    "direction": "long",
    "entry": 77893.8,
    "sl": 77776.37857143,                  # ALREADY TRAILED
    "exit_plan": {"stop": {"price": 77044.07142857}},
    "meta": {"atr": 424.86428571428405},   # entry-frozen, verbatim from the journal row
}
BTC_TRADE = {
    "id": 5676, "direction": "long", "entry_price": 77904.1,
    "stop_loss": 77776.37857143, "exit_price": 77799.6, "exit_reason": "reconciler_filled",
}


class TestMI275Exhibits:
    """Reproduce MI-275's published figures, or say so loudly."""

    @pytest.mark.parametrize("pkg,trade,want_atr", [
        (AVAX_PKG, AVAX_TRADE, 1.320),
        (BTC_PKG, BTC_TRADE, 0.276),
    ])
    def test_final_stop_in_atr_matches_the_published_figure(self, pkg, trade, want_atr):
        r = classify(pkg, trade)
        assert r["state"] == "amended_tighter"
        assert round(r["final_stop_atr_from_anchor"], 3) == want_atr

    @pytest.mark.parametrize("pkg,want_declared_atr", [(AVAX_PKG, 1.5), (BTC_PKG, 2.0)])
    def test_the_declared_width_lands_on_the_legs_own_multiplier(self, pkg, want_declared_atr):
        # A CHECK, not the basis: the module reads the frozen level and never
        # recomputes atr*mult. If this drifts, the fixture ATR is wrong.
        r = classify(pkg, AVAX_TRADE if pkg is AVAX_PKG else BTC_TRADE)
        assert round(r["declared_distance"] / r["atr"], 3) == want_declared_atr

    def test_the_anchor_is_the_package_entry_not_the_fill(self):
        # AVAX declares entry 7.128 while its trade filled at 7.099. MI-275 lost
        # 15 hours to this; anchoring on the fill must not silently happen.
        r = classify(AVAX_PKG, AVAX_TRADE)
        assert r["anchor"] == 7.128
        assert r["anchor_source"] == "package_entry"


class TestTheFallbackIsRefusedNotGraded:
    """`order_packages.sl` is trailed too — the obvious repair inverts the finding."""

    @pytest.mark.parametrize("pkg,trade", [(AVAX_PKG, AVAX_TRADE), (BTC_PKG, BTC_TRADE)])
    def test_without_an_entry_frozen_level_the_verdict_is_refused(self, pkg, trade):
        stripped = dict(pkg)
        stripped["exit_plan"] = None
        r = classify(stripped, trade)
        assert r["declared_stop_source"] == "package_sl_may_be_trailed"
        assert r["state"] == "ungradeable_declared_stop_not_entry_frozen"
        assert r["state"] not in GRADED_STATES

    @pytest.mark.parametrize("pkg,trade", [(AVAX_PKG, AVAX_TRADE), (BTC_PKG, BTC_TRADE)])
    def test_grading_on_the_fallback_WOULD_have_said_clean(self, pkg, trade):
        # This is the harm, asserted rather than described: on both exhibits
        # pkg.sl EQUALS the final stop, so a naive comparison reads a zero move.
        assert pkg["sl"] == trade["stop_loss"]

    def test_the_frozen_level_wins_when_both_are_present(self):
        price, source = declared_stop(BTC_PKG)
        assert source == "exit_plan_stop_entry_frozen"
        assert price == 77044.07142857
        assert price != BTC_PKG["sl"]


class TestDirectionDecidesWhichWayIsTighter:
    BASE = {"entry": 100.0, "exit_plan": {"stop": {"price": 90.0}}, "meta": {"atr": 5.0}}

    def test_long_moving_up_is_tighter(self):
        r = classify({**self.BASE, "direction": "long"},
                     {"direction": "long", "stop_loss": 95.0})
        assert r["state"] == "amended_tighter"

    def test_short_moving_up_is_wider(self):
        # Same numbers, opposite position: 90 -> 95 on a SHORT whose entry is
        # 100 moves the stop AWAY. A module keyed on the number alone gets this
        # backwards and every naive test still passes.
        r = classify({**self.BASE, "direction": "short",
                      "exit_plan": {"stop": {"price": 110.0}}},
                     {"direction": "short", "stop_loss": 115.0})
        assert r["state"] == "amended_wider"

    def test_short_moving_down_is_tighter(self):
        r = classify({**self.BASE, "direction": "short",
                      "exit_plan": {"stop": {"price": 110.0}}},
                     {"direction": "short", "stop_loss": 105.0})
        assert r["state"] == "amended_tighter"

    def test_an_unknown_direction_is_refused_not_guessed(self):
        r = classify({**self.BASE, "direction": ""},
                     {"direction": None, "stop_loss": 95.0})
        assert r["state"] == "ungradeable_no_direction"


class TestTolerance:
    BASE = {"entry": 100.0, "direction": "long", "exit_plan": {"stop": {"price": 90.0}}}

    def test_a_move_inside_the_tolerance_is_the_declared_stop(self):
        # declared distance 10.0; 1% of it is 0.1
        r = classify(self.BASE, {"direction": "long", "stop_loss": 90.09})
        assert r["state"] == "entry_declared"

    def test_a_move_just_outside_the_tolerance_is_amended(self):
        r = classify(self.BASE, {"direction": "long", "stop_loss": 90.2})
        assert r["state"] == "amended_tighter"

    def test_the_tolerance_is_a_parameter_not_a_constant_in_the_body(self):
        r = classify(self.BASE, {"direction": "long", "stop_loss": 90.2},
                     tolerance_frac=0.5)
        assert r["state"] == "entry_declared"
        assert r["tolerance_frac"] == 0.5

    def test_default_tolerance_equals_mi275s_0_02_atr_at_a_2_ATR_width(self):
        # 0.02 ATR / 2.0 ATR of declared width = 0.01 of the width.
        assert DEFAULT_TOLERANCE_FRAC == pytest.approx(0.02 / 2.0)


class TestUngradeableIsNeverACleanAnswer:
    def test_no_declared_stop_anywhere(self):
        r = classify({"entry": 100.0, "direction": "long"},
                     {"direction": "long", "stop_loss": 95.0})
        assert r["state"] == "ungradeable_no_declared_stop"

    def test_no_final_stop_on_the_trade(self):
        r = classify({"entry": 100.0, "direction": "long",
                      "exit_plan": {"stop": {"price": 90.0}}},
                     {"direction": "long", "stop_loss": None})
        assert r["state"] == "ungradeable_no_final_stop"

    def test_no_anchor_at_all(self):
        r = classify({"direction": "long", "exit_plan": {"stop": {"price": 90.0}}},
                     {"direction": "long", "stop_loss": 95.0, "entry_price": None})
        assert r["state"] == "ungradeable_no_anchor"

    def test_a_declared_stop_sitting_on_the_entry_has_no_width_to_scale(self):
        r = classify({"entry": 90.0, "direction": "long",
                      "exit_plan": {"stop": {"price": 90.0}}},
                     {"direction": "long", "stop_loss": 95.0})
        assert r["state"] == "ungradeable_zero_declared_distance"

    def test_the_fill_is_only_a_NAMED_fallback_anchor(self):
        r = classify({"direction": "long", "exit_plan": {"stop": {"price": 90.0}}},
                     {"direction": "long", "stop_loss": 90.0, "entry_price": 100.0})
        assert r["anchor"] == 100.0
        assert r["anchor_source"] == "trade_fill_fallback"

    def test_every_ungradeable_state_is_outside_the_graded_set(self):
        assert not set(UNGRADEABLE_STATES) & set(GRADED_STATES)
        assert set(STATES) == set(GRADED_STATES) | set(UNGRADEABLE_STATES)


class TestMagnitudesAreNoneNotZeroWhenUnknown:
    def test_moved_frac_is_none_when_ungradeable(self):
        r = classify({"entry": 100.0, "direction": "long"},
                     {"direction": "long", "stop_loss": 95.0})
        assert r["moved_frac"] is None
        assert r["declared_distance"] is None

    def test_moved_atr_is_none_when_the_package_carries_no_atr(self):
        # 46.2% of the measured population has no meta.atr. A 0.0 there would
        # read as "the stop did not move".
        r = classify({"entry": 100.0, "direction": "long",
                      "exit_plan": {"stop": {"price": 90.0}}},
                     {"direction": "long", "stop_loss": 95.0})
        assert r["moved_atr"] is None
        assert r["final_stop_atr_from_anchor"] is None
        assert r["state"] == "amended_tighter"      # still gradeable without ATR

    def test_a_real_zero_move_is_zero_not_none(self):
        r = classify({"entry": 100.0, "direction": "long",
                      "exit_plan": {"stop": {"price": 90.0}}, "meta": {"atr": 5.0}},
                     {"direction": "long", "stop_loss": 90.0})
        assert r["moved_frac"] == 0.0
        assert r["moved_atr"] == 0.0

    def test_a_non_numeric_field_does_not_become_a_number(self):
        r = classify({"entry": "not-a-number", "direction": "long",
                      "exit_plan": {"stop": {"price": 90.0}}},
                     {"direction": "long", "stop_loss": 95.0, "entry_price": None})
        assert r["state"] == "ungradeable_no_anchor"


class TestJsonStringFieldsAreAccepted:
    def test_exit_plan_and_meta_may_arrive_as_json_strings(self):
        # /api/diag/journal returns them parsed; sqlite3 returns them as TEXT.
        r = classify({"entry": 100.0, "direction": "long",
                      "exit_plan": '{"stop": {"price": 90.0}}',
                      "meta": '{"atr": 5.0}'},
                     {"direction": "long", "stop_loss": 95.0})
        assert r["state"] == "amended_tighter"
        assert r["moved_atr"] == 1.0

    def test_unparseable_json_does_not_raise_and_does_not_grade(self):
        r = classify({"entry": 100.0, "direction": "long", "exit_plan": "{not json"},
                     {"direction": "long", "stop_loss": 95.0})
        assert r["state"] == "ungradeable_no_declared_stop"


class TestSplitCannotHideTheDenominator:
    def _pop(self):
        return [
            classify(AVAX_PKG, AVAX_TRADE),                       # amended_tighter
            classify(BTC_PKG, BTC_TRADE),                         # amended_tighter
            classify({"entry": 100.0, "direction": "long",
                      "exit_plan": {"stop": {"price": 90.0}}},
                     {"direction": "long", "stop_loss": 90.0}),   # entry_declared
            classify({"entry": 100.0, "direction": "long"},
                     {"direction": "long", "stop_loss": 95.0}),   # ungradeable
        ]

    def test_counts(self):
        s = split(self._pop())
        assert s["amended_tighter"] == 2
        assert s["entry_declared"] == 1
        assert s["ungradeable"] == 1
        assert s["graded"] == 3
        assert s["total"] == 4
        assert s["amended_frac"] == pytest.approx(2 / 3)

    def test_every_state_appears_as_a_key_even_at_zero(self):
        s = split(self._pop())
        for state in STATES:
            assert state in s, f"{state} vanished from the summary"

    def test_amended_frac_is_none_not_zero_when_nothing_is_gradeable(self):
        s = split([classify({"entry": 100.0, "direction": "long"},
                            {"direction": "long", "stop_loss": 95.0})])
        assert s["amended_frac"] is None

    def test_format_split_always_renders_the_ungradeable_count(self):
        line = format_split(split(self._pop()))
        assert "UNGRADEABLE" in line
        assert "1 UNGRADEABLE" in line

    def test_format_split_renders_the_ungradeable_count_even_when_it_is_zero(self):
        s = split([classify(BTC_PKG, BTC_TRADE)])
        assert "0 UNGRADEABLE" in format_split(s)

    def test_an_unrecognised_state_is_counted_not_dropped(self):
        s = split([{"state": "something_new"}])
        assert s["unrecognised_state"] == 1
        assert s["total"] == 1


class TestAgreesWithMI275sOwnTolerance:
    """The rewire of MI-275's script must not move its published verdicts.

    The owner grades `|final - declared|` against 1% of the DECLARED WIDTH;
    MI-275 graded it against 0.02 ATR. They coincide exactly at a 2.0-ATR
    declared width and differ elsewhere, so the claim that the rewire is a
    no-op is a MEASUREMENT, not an inference.

    MEASURED 2026-09-12 over all 322 closed package-linked trades in the
    `/api/diag/journal` 1000-row tail that carry an entry-frozen `meta.atr`:
    **0 disagreements**. The cases below are that comparison in miniature plus
    the exact boundary where the two rules diverge, so a future change to
    either tolerance fails here rather than silently re-grading a memo.
    """

    @staticmethod
    def _mi275_verdict(pkg, trade):
        declared = pkg["exit_plan"]["stop"]["price"]
        atr = pkg["meta"]["atr"]
        final = trade["stop_loss"]
        return abs((final - declared) / atr) <= 0.02

    @pytest.mark.parametrize("pkg,trade", [(AVAX_PKG, AVAX_TRADE), (BTC_PKG, BTC_TRADE)])
    def test_the_two_exhibits_agree(self, pkg, trade):
        mine = classify(pkg, trade)["state"] == "entry_declared"
        assert mine is self._mi275_verdict(pkg, trade) is False

    def test_they_coincide_exactly_at_a_2_ATR_declared_width(self):
        # declared width 10.0 = 2.0 ATR of 5.0; both rules trip at 0.1
        pkg = {"entry": 100.0, "direction": "long",
               "exit_plan": {"stop": {"price": 90.0}}, "meta": {"atr": 5.0}}
        for final, want_declared in ((90.10, True), (90.11, False)):
            trade = {"direction": "long", "stop_loss": final}
            assert (classify(pkg, trade)["state"] == "entry_declared") is want_declared
            assert self._mi275_verdict(pkg, trade) is want_declared

    def test_the_divergence_below_2_ATR_is_real_and_is_in_the_STRICTER_direction(self):
        # declared width 5.0 = 1.0 ATR of 5.0. 0.02 ATR = 0.10; 1% of width = 0.05.
        # A 0.07 move is `amended` here and `entry_declared` under MI-275's rule.
        pkg = {"entry": 100.0, "direction": "long",
               "exit_plan": {"stop": {"price": 95.0}}, "meta": {"atr": 5.0}}
        trade = {"direction": "long", "stop_loss": 95.07}
        assert classify(pkg, trade)["state"] == "amended_tighter"
        assert self._mi275_verdict(pkg, trade) is True
        # Stricter = it reports MORE amendments, never fewer. An attribution
        # error in that direction under-claims the counterfactual population,
        # which is the safe side of the row this module answers.


class TestTheMI275VocabularyMapIsTotal:
    def test_every_owner_state_maps_to_a_published_MI275_name(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_m275", "scripts/research/stop_width_counterfactual_2026_09_11.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        missing = [s for s in STATES if s not in m._MI275_STATE]
        assert not missing, (
            f"stop_attribution added state(s) {missing} that MI-275's script cannot "
            "name — it would KeyError mid-run on a real package")

    def test_no_ungradeable_state_maps_onto_a_graded_MI275_name(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_m275", "scripts/research/stop_width_counterfactual_2026_09_11.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        graded_names = {"stop_is_entry_declared", "stop_amended_tighter",
                        "stop_amended_wider"}
        for s in UNGRADEABLE_STATES:
            assert m._MI275_STATE[s] not in graded_names, (
                f"{s} would be published as a graded verdict")
