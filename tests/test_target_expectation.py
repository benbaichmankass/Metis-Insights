"""A bracket must carry an expectation at entry — the reader, and the extender.

The controls that matter are the ones keeping four different reasons a target
is not a prediction from collapsing into one, and the one that stops an unread
thesis from being treated as an intact one.
"""
from __future__ import annotations

import pytest

from src.runtime import target_expectation as te


class TestResolveStates:
    def test_the_sentinel_idiom_is_recognised(self):
        r = te.resolve_expectation({"tp_r": 50.0}, entry=100.0, sl=95.0,
                                   direction="long")
        assert r["state"] == te.STATE_SENTINEL
        assert r["source_key"] == "tp_r"

    def test_a_real_target_that_survives_the_cap_is_declared(self):
        # 1.5R on a 1%-of-entry stop = 1.5% — well inside the 9.9% cap.
        r = te.resolve_expectation({"tp_r": 1.5}, entry=100.0, sl=99.0,
                                   direction="long")
        assert r["state"] == te.STATE_DECLARED
        assert r["expectation_price"] == pytest.approx(101.5)
        assert r["placed_price"] == pytest.approx(101.5)

    def test_a_real_target_the_cap_replaces_is_CLAMPED_not_sentinel(self):
        """tp_r 6.0 against a 14.4%-of-entry stop asks for 86% and gets 9.9%.

        This leg HAD an expectation; the venue refused to place it. Merging it
        with the sentinel would lose the distinction the remedies turn on.
        """
        r = te.resolve_expectation({"tp_r": 6.0}, entry=100.0, sl=85.6,
                                   direction="long")
        assert r["state"] == te.STATE_CLAMPED
        assert r["state"] != te.STATE_SENTINEL
        assert r["expectation_price"] > r["placed_price"]
        assert r["placed_price"] == pytest.approx(109.9)

    def test_no_target_key_is_its_own_state_not_the_sentinel(self):
        """20 legs compute their target elsewhere. Calling them sentinel would
        accuse them of a defect they may not have."""
        r = te.resolve_expectation({}, entry=100.0, sl=99.0, direction="long")
        assert r["state"] == te.STATE_NO_TARGET_KEY
        assert r["state"] not in (te.STATE_SENTINEL, te.STATE_UNMEASURABLE)
        assert r["source_key"] is None

    def test_an_unreadable_entry_is_unmeasurable_NOT_no_target_key(self):
        """A missing INPUT and a missing DECLARATION are different absences."""
        r = te.resolve_expectation({"tp_r": 1.5}, entry=None, sl=99.0,
                                   direction="long")
        assert r["state"] == te.STATE_UNMEASURABLE
        assert r["state"] != te.STATE_NO_TARGET_KEY

    def test_a_zero_risk_trade_is_unmeasurable(self):
        r = te.resolve_expectation({"tp_r": 1.5}, entry=100.0, sl=100.0,
                                   direction="long")
        assert r["state"] == te.STATE_UNMEASURABLE

    def test_target_r_wins_over_the_legacy_keys(self):
        r = te.resolve_expectation({"target_r": 2.0, "tp_r": 50.0, "tp_at_r": 1.5},
                                   entry=100.0, sl=99.0, direction="long")
        assert r["source_key"] == "target_r" and r["target_r"] == 2.0
        assert r["state"] == te.STATE_DECLARED

    def test_tp_at_r_is_read_so_ict_scalp_is_not_mis_graded(self):
        r = te.resolve_expectation({"tp_at_r": 1.5}, entry=100.0, sl=99.0,
                                   direction="long")
        assert r["source_key"] == "tp_at_r"
        assert r["state"] == te.STATE_DECLARED


class TestCapR:
    def test_cap_r_is_the_trades_own_reward_to_risk_when_the_cap_binds(self):
        """The live XRP trade: entry 1.4995, stop 1.28355357 -> cap_r 0.687."""
        r = te.resolve_expectation({"tp_r": 50.0}, entry=1.4995, sl=1.28355357,
                                   direction="long")
        assert r["cap_r"] == pytest.approx(0.6874, abs=1e-4)
        assert r["placed_price"] == pytest.approx(1.6479505, abs=1e-6)

    def test_a_short_caps_below_entry(self):
        r = te.resolve_expectation({"tp_r": 50.0}, entry=100.0, sl=110.0,
                                   direction="short")
        assert r["cap_price"] == pytest.approx(90.1)
        assert r["placed_price"] == pytest.approx(90.1)

    def test_the_cap_mirrors_the_value_production_clamps_with(self):
        """Pinned, because this module deliberately mirrors rather than imports."""
        from src.runtime.position_telemetry import _TP_SENTINEL_CAP_PCT
        assert te.TP_VENUE_CAP_PCT == _TP_SENTINEL_CAP_PCT
        import re
        for path in ("src/units/strategies/trend_donchian.py",
                     "src/units/strategies/htf_pullback_trend_2h.py"):
            src = open(path, encoding="utf-8").read()
            m = re.search(r"_TP_SENTINEL_CAP_PCT\s*=\s*([0-9.]+)", src)
            assert m, path
            assert float(m.group(1)) == te.TP_VENUE_CAP_PCT, path


class TestExtension:
    def _decl(self):
        return te.resolve_expectation({"tp_r": 3.0}, entry=100.0, sl=99.0,
                                      direction="long")

    def test_far_from_the_target_it_does_not_consider_extending(self):
        v = te.evaluate_extension(self._decl(), price=101.0, entry=100.0,
                                  direction="long", thesis_intact=True)
        assert v["state"] == te.EXT_NOT_APPROACHING
        assert v["new_target"] is None

    def test_approaching_with_the_thesis_intact_extends(self):
        v = te.evaluate_extension(self._decl(), price=102.9, entry=100.0,
                                  direction="long", thesis_intact=True)
        assert v["state"] == te.EXT_EXTEND
        assert v["new_target"] == pytest.approx(104.0)   # 103 + 1R
        assert v["extends_so_far"] == 1

    def test_approaching_with_a_broken_thesis_HOLDS(self):
        v = te.evaluate_extension(self._decl(), price=102.9, entry=100.0,
                                  direction="long", thesis_intact=False)
        assert v["state"] == te.EXT_THESIS_BROKEN
        assert v["new_target"] is None

    def test_an_UNREAD_thesis_never_extends(self):
        """'We did not check the thesis' must not become 'the thesis holds' —
        a revision rule reading only the trade's own path is the substrate the
        milestone identified as the root cause."""
        v = te.evaluate_extension(self._decl(), price=102.9, entry=100.0,
                                  direction="long", thesis_intact=None)
        assert v["state"] == te.EXT_THESIS_UNKNOWN
        assert v["new_target"] is None

    def test_the_ratchet_is_bounded(self):
        v = te.evaluate_extension(self._decl(), price=102.9, entry=100.0,
                                  direction="long", thesis_intact=True,
                                  extends_so_far=3, max_extends=3)
        assert v["state"] == te.EXT_CAP_REACHED
        assert v["new_target"] is None

    def test_a_SENTINEL_has_nothing_to_extend_FROM(self):
        """Pushing a sentinel out would dress a venue limit up as a prediction."""
        sent = te.resolve_expectation({"tp_r": 50.0}, entry=100.0, sl=99.0,
                                      direction="long")
        v = te.evaluate_extension(sent, price=140.0, entry=100.0,
                                  direction="long", thesis_intact=True)
        assert v["state"] == te.EXT_NO_EXPECTATION
        assert v["new_target"] is None

    def test_a_short_extends_downward(self):
        d = te.resolve_expectation({"tp_r": 3.0}, entry=100.0, sl=101.0,
                                   direction="short")
        v = te.evaluate_extension(d, price=97.1, entry=100.0,
                                  direction="short", thesis_intact=True)
        assert v["state"] == te.EXT_EXTEND
        assert v["new_target"] == pytest.approx(96.0)

    def test_a_missing_expectation_is_reported_not_defaulted(self):
        v = te.evaluate_extension(None, price=102.9, entry=100.0,
                                  direction="long", thesis_intact=True)
        assert v["state"] == te.EXT_NO_EXPECTATION


class TestPurity:
    def test_it_never_raises_on_garbage(self):
        for bad in (None, {}, {"tp_r": "banana"}, {"tp_r": float("nan")},
                    {"tp_r": -1}):
            r = te.resolve_expectation(bad, entry="x", sl=None, direction=None)
            assert r["state"] in (te.STATE_UNMEASURABLE, te.STATE_NO_TARGET_KEY)
            te.evaluate_extension(r, price=None, entry=None, direction=None,
                                  thesis_intact=None)

    def test_the_module_imports_nothing_from_the_repo(self):
        """Pure + dependency-free, so it is import-safe from any layer."""
        import ast
        tree = ast.parse(open("src/runtime/target_expectation.py",
                              encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = getattr(node, "module", None) or ""
                names = [a.name for a in node.names]
                assert not mod.startswith("src"), mod
                assert not any(n.startswith("src") for n in names), names
