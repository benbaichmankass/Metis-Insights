"""ict_scalp's SL/TP-cross close path — BL-20260818-ICT-SCALP-HAS-NO-TAKE-PROFIT-CLOSE-PATH.

The strategy had no close of its own, so it depended ENTIRELY on a venue
bracket nothing verifies. MGC 4487 sat 122.74 points past its declared target
for 11 days with zero LMT orders resting on the account.

The controls pin (a) that it now closes where the declared geometry said to,
(b) that it fires ONLY past a declared level — it must introduce no new opinion
about where a trade ends — and (c) that it matches the sibling families, which
is the whole basis for adding it.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.units.strategies import ict_scalp


def _df(close, n=30):
    return pd.DataFrame([
        {"timestamp": pd.Timestamp("2026-08-01", tz="UTC") + pd.Timedelta(minutes=5 * i),
         "open": close, "high": close + 0.1, "low": close - 0.1,
         "close": close, "volume": 1.0}
        for i in range(n)
    ])


def _pkg(direction="long", entry=100.0, sl=99.0, tp=101.5):
    return {"order_package_id": "p1", "symbol": "BTCUSDT", "direction": direction,
            "entry": entry, "sl": sl, "tp": tp, "meta": {"timeframe": "5m"}}


class TestClosesPastADeclaredLevel:
    def test_a_long_past_its_target_closes_tp_cross(self):
        v = ict_scalp.monitor({}, _df(102.0), _pkg())
        assert v["action"] == "close" and v["reason"] == "tp_cross"
        assert v["exit_price"] == pytest.approx(102.0)

    def test_a_long_past_its_stop_closes_sl_cross(self):
        v = ict_scalp.monitor({}, _df(98.0), _pkg())
        assert v["action"] == "close" and v["reason"] == "sl_cross"

    def test_a_short_past_its_target_closes(self):
        v = ict_scalp.monitor({}, _df(98.0), _pkg(direction="short", sl=101.0, tp=98.5))
        assert v["action"] == "close" and v["reason"] == "tp_cross"

    def test_a_short_past_its_stop_closes(self):
        v = ict_scalp.monitor({}, _df(102.0), _pkg(direction="short", sl=101.0, tp=98.5))
        assert v["action"] == "close" and v["reason"] == "sl_cross"

    def test_the_MGC_4487_shape_now_closes(self):
        """122.74 points past a declared target is no longer an open trade."""
        v = ict_scalp.monitor({}, _df(3500.0 + 122.74),
                              _pkg(entry=3400.0, sl=3380.0, tp=3500.0))
        assert v["action"] == "close" and v["reason"] == "tp_cross"


class TestItIntroducesNoNewOpinion:
    def test_inside_the_brackets_it_does_NOT_close(self):
        v = ict_scalp.monitor({}, _df(100.5), _pkg())
        assert v is None or v.get("action") != "close"

    def test_a_package_with_NO_tp_is_untouched_by_the_tp_branch(self):
        pkg = _pkg()
        pkg["tp"] = None
        v = ict_scalp.monitor({}, _df(102.0), pkg)
        assert v is None or v.get("reason") != "tp_cross"

    def test_a_package_with_NO_sl_is_untouched_by_the_sl_branch(self):
        pkg = _pkg()
        pkg["sl"] = None
        v = ict_scalp.monitor({}, _df(98.0), pkg)
        assert v is None or v.get("reason") != "sl_cross"

    def test_an_unparseable_direction_closes_nothing(self):
        v = ict_scalp.monitor({}, _df(102.0), _pkg(direction="sideways"))
        assert v is None or v.get("action") != "close"

    def test_empty_candles_return_None(self):
        assert ict_scalp.monitor({}, pd.DataFrame(), _pkg()) is None
        assert ict_scalp.monitor({}, None, _pkg()) is None


class TestMatchesTheSiblings:
    def test_all_three_families_now_emit_the_same_close_reasons(self):
        """The basis for adding this: the siblings have run it all along."""
        import ast

        def reasons(path):
            out = set()
            tree = ast.parse(open(path, encoding="utf-8").read())
            for n in ast.walk(tree):
                if isinstance(n, ast.FunctionDef) and n.name == "monitor":
                    for sub in ast.walk(n):
                        if isinstance(sub, ast.Dict):
                            keys = [k.value for k in sub.keys
                                    if isinstance(k, ast.Constant)]
                            if "action" not in keys:
                                continue
                            for k, v in zip(sub.keys, sub.values):
                                if (isinstance(k, ast.Constant) and k.value == "reason"
                                        and isinstance(v, ast.Constant)):
                                    out.add(v.value)
            return out

        scalp = reasons("src/units/strategies/ict_scalp.py")
        assert {"sl_cross", "tp_cross"} <= scalp
        for sibling in ("trend_donchian", "htf_pullback_trend_2h"):
            assert {"sl_cross", "tp_cross"} <= reasons(
                f"src/units/strategies/{sibling}.py"), sibling


class TestOrdering:
    def test_the_cross_check_runs_BEFORE_the_stale_stop(self, monkeypatch):
        """A bar that has crossed SL/TP is not a bar the stale-stop should be
        reasoning about — the stop-first ordering its own docstring assumes."""
        called = {"stale": False}

        def _spy(*a, **kw):
            called["stale"] = True
            return {"action": "close", "reason": "stale_stop"}

        monkeypatch.setattr(ict_scalp, "_stale_stop_verdict", _spy)
        v = ict_scalp.monitor({}, _df(102.0), _pkg())
        assert v["reason"] == "tp_cross"
        assert called["stale"] is False
