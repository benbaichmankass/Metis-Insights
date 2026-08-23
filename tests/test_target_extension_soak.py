"""The target-extension annotate soak, and its wiring into the live monitors.

The controls that matter: the soak must change NO verdict (it is observe-only
and sits on a live-money path), and its rows must carry the expectation state
beside the extension state — a soak that goes quiet because 29 legs have no
target must not read as a lever that never fires.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from src.runtime import target_extension_soak as tes


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    import src.utils.paths as paths
    monkeypatch.setattr(paths, "runtime_logs_dir", lambda: tmp_path)
    tes._ANNOTATED.clear()
    yield
    tes._ANNOTATED.clear()


def _rows(tmp_path):
    p = tmp_path / tes.SOAK_LOG_NAME
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line]


class TestRecord:
    def test_a_would_extend_writes_a_row_carrying_BOTH_states(self, tmp_path):
        rec = tes.record_target_extension(
            strategy="s", symbol="X", direction="long", order_package_id="p1",
            expectation={"state": "declared", "target_r": 3.0, "cap_r": 9.9},
            extension={"state": "extend", "new_target": 104.0, "extends_so_far": 1},
        )
        assert rec is not None and rec["observe_only"] is True
        rows = _rows(tmp_path)
        assert len(rows) == 1
        assert rows[0]["expectation_state"] == "declared"
        assert rows[0]["extension_state"] == "extend"
        assert rows[0]["would_move_tp_to"] == 104.0

    def test_a_sentinel_leg_logs_WHY_it_cannot_extend(self, tmp_path):
        """Otherwise its silence reads as 'the lever never fires' when it means
        'there was never a target'."""
        tes.record_target_extension(
            strategy="s", symbol="X", direction="long", order_package_id="p2",
            expectation={"state": "sentinel_no_expectation", "target_r": 50.0},
            extension={"state": "no_expectation_declared"},
        )
        rows = _rows(tmp_path)
        assert rows[0]["expectation_state"] == "sentinel_no_expectation"
        assert rows[0]["extension_state"] == "no_expectation_declared"

    def test_not_approaching_is_NOT_logged(self, tmp_path):
        """The ordinary state of nearly every trade on nearly every tick."""
        assert tes.record_target_extension(
            strategy="s", symbol="X", direction="long", order_package_id="p3",
            expectation={"state": "declared"},
            extension={"state": "not_approaching"},
        ) is None
        assert _rows(tmp_path) == []

    def test_it_dedups_per_package_state_and_extend_count(self, tmp_path):
        for _ in range(4):
            tes.record_target_extension(
                strategy="s", symbol="X", direction="long", order_package_id="p4",
                expectation={"state": "declared"},
                extension={"state": "thesis_broken_hold", "extends_so_far": 0},
            )
        assert len(_rows(tmp_path)) == 1

    def test_a_NEW_state_on_the_same_package_still_logs(self, tmp_path):
        for st in ("thesis_broken_hold", "extend"):
            tes.record_target_extension(
                strategy="s", symbol="X", direction="long", order_package_id="p5",
                expectation={"state": "declared"},
                extension={"state": st, "extends_so_far": 0},
            )
        assert len(_rows(tmp_path)) == 2

    def test_it_never_raises_on_garbage(self):
        assert tes.record_target_extension(
            strategy=None, symbol=None, direction=None,
            expectation=None, extension=None) is None
        assert tes.annotate_from_monitor(
            strategy="s", open_pkg={}, meta={}, price=None,
            thesis_intact=None) is None


class TestMonitorWiring:
    """End-to-end through the live monitors. The verdict must not move."""

    def _candles(self, n=60, base=100.0, rising=True):
        rows = []
        for i in range(n):
            c = base + (i * 0.5 if rising else -i * 0.5)
            rows.append({"timestamp": pd.Timestamp("2026-08-01", tz="UTC")
                         + pd.Timedelta(hours=i),
                         "open": c, "high": c + 0.4, "low": c - 0.4,
                         "close": c, "volume": 100.0})
        return pd.DataFrame(rows)

    def test_donchian_monitor_writes_a_row_and_returns_the_same_verdict(
            self, tmp_path, monkeypatch):
        from src.units.strategies import trend_donchian as td
        df = self._candles()
        price = float(df["close"].iloc[-1])
        pkg = {
            "order_package_id": "pkg-d1", "strategy_name": "trend_donchian_sol",
            "symbol": "SOLUSDT", "direction": "long",
            "entry": price - 3.0, "sl": price - 6.0, "tp": price + 100.0,
            "meta": {"donchian": 20, "atr": 1.0, "atr_stop_mult": 2.5,
                     "trail_mult": 5.0, "tp_r": 50.0, "timeframe": "1h",
                     "risk_per_unit": 3.0},
        }
        with_soak = td.monitor({}, df, pkg)
        rows = _rows(tmp_path)
        assert rows, "the donchian monitor must produce an annotate row"
        assert rows[0]["strategy"] == "trend_donchian_sol"
        # tp_r 50 -> the sentinel, so there is nothing to extend from.
        assert rows[0]["expectation_state"] == "sentinel_no_expectation"
        assert rows[0]["extension_state"] == "no_expectation_declared"
        assert rows[0]["observe_only"] is True

        # Now disable the soak entirely and assert the verdict is identical.
        tes._ANNOTATED.clear()
        monkeypatch.setattr(tes, "annotate_from_monitor",
                            lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
        without_soak = td.monitor({}, df, dict(pkg))
        assert with_soak == without_soak, (
            "the annotate soak must not change the monitor's verdict")

    def test_a_soak_EXPLOSION_cannot_break_the_monitor(self, tmp_path, monkeypatch):
        from src.units.strategies import trend_donchian as td
        monkeypatch.setattr(tes, "record_target_extension",
                            lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
        df = self._candles()
        price = float(df["close"].iloc[-1])
        pkg = {"order_package_id": "pkg-d2", "direction": "long",
               "symbol": "SOLUSDT", "entry": price - 3.0, "sl": price - 6.0,
               "tp": price + 100.0,
               "meta": {"donchian": 20, "atr": 1.0, "trail_mult": 5.0,
                        "tp_r": 50.0, "timeframe": "1h"}}
        td.monitor({}, df, pkg)   # must not raise

    def test_pullback_monitor_writes_a_row(self, tmp_path):
        from src.units.strategies import htf_pullback_trend_2h as hp
        df = self._candles()
        price = float(df["close"].iloc[-1])
        pkg = {
            "order_package_id": "pkg-p1", "strategy_name": "xrp_pullback_2h",
            "symbol": "XRPUSDT", "direction": "long",
            "entry": price - 3.0, "sl": price - 6.0, "tp": price + 100.0,
            "meta": {"atr": 1.0, "atr_stop_mult": 2.5, "trail_mult": 5.0,
                     "tp_r": 50.0, "timeframe": "2h", "risk_per_unit": 3.0,
                     "adx_min": 25.0, "adx_period": 14},
        }
        hp.monitor({}, df, pkg)
        rows = _rows(tmp_path)
        assert rows, "the pullback monitor must produce an annotate row"
        assert rows[0]["strategy"] == "xrp_pullback_2h"
        assert rows[0]["expectation_state"] == "sentinel_no_expectation"


class TestThesisPredicates:
    def _df(self, closes):
        return pd.DataFrame([
            {"timestamp": pd.Timestamp("2026-08-01", tz="UTC") + pd.Timedelta(hours=i),
             "open": c, "high": c + 0.2, "low": c - 0.2, "close": c, "volume": 1.0}
            for i, c in enumerate(closes)
        ])

    def test_donchian_thesis_holds_while_price_still_breaks_the_channel(self):
        from src.units.strategies.trend_donchian import _donchian_thesis_intact
        df = self._df([100 + i for i in range(30)])
        ok, detail = _donchian_thesis_intact({"donchian": 20}, df, 200.0, "long")
        assert ok is True and detail["predicate"] == "donchian_rebreak"

    def test_donchian_thesis_breaks_when_price_falls_back_inside(self):
        from src.units.strategies.trend_donchian import _donchian_thesis_intact
        df = self._df([100 + i for i in range(30)])
        ok, _ = _donchian_thesis_intact({"donchian": 20}, df, 105.0, "long")
        assert ok is False

    def test_donchian_thesis_is_UNKNOWN_when_it_cannot_be_computed(self):
        """Never False — 'we could not look' is not 'the thesis broke'."""
        from src.units.strategies.trend_donchian import _donchian_thesis_intact
        ok, detail = _donchian_thesis_intact({}, None, 105.0, "long")
        assert ok is None
        ok, detail = _donchian_thesis_intact({"donchian": 20},
                                             self._df([100, 101]), 105.0, "long")
        assert ok is None and detail["reason"] == "insufficient_bars"

    def test_pullback_thesis_is_UNKNOWN_when_NEITHER_predicate_is_available(self):
        """UPDATED 2026-08-23 — the contract widened, the guarantee did not.

        This asserted that a leg with no `adx_min` is always UNKNOWN. That is no
        longer the contract: 13 of the 19 enabled legs declare no floor
        (measured 2026-08-23), and they now fall back to
        the TREND-STRUCTURE predicate their entry actually uses
        (`tests/test_pullback_trend_structure_thesis.py`).

        What must NOT change is the guarantee underneath it: when NEITHER
        predicate can be evaluated, the answer is `None` — *we could not look* —
        and never `False`. So the case is kept and tightened rather than deleted.
        """
        from src.units.strategies.htf_pullback_trend_2h import _pullback_thesis_intact
        df = self._df([100 + i for i in range(40)])

        # no floor AND no direction -> the fallback cannot be graded either
        ok, detail = _pullback_thesis_intact({"trend_lookback": 10}, df)
        assert ok is None
        assert detail["predicate"] == "trend_structure"
        assert detail["reason"] == "direction_unreadable"

        # no floor AND no trend window -> likewise unknown, and it still records
        # WHY the ADX branch did not apply
        ok, detail = _pullback_thesis_intact({}, df, direction="long")
        assert ok is None
        assert detail["reason"] == "no_trend_lookback_declared"
        assert detail["adx_fallback_reason"] == "no_adx_min_declared"

    def test_pullback_thesis_falls_back_to_trend_structure(self):
        """The widening itself, asserted here too so this file records it."""
        from src.units.strategies.htf_pullback_trend_2h import _pullback_thesis_intact
        df = self._df([100 + i for i in range(40)])
        ok, detail = _pullback_thesis_intact(
            {"trend_lookback": 10}, df, direction="long")
        assert ok is True
        assert detail["predicate"] == "trend_structure"

    def test_pullback_thesis_reads_the_declared_floor(self):
        from src.units.strategies.htf_pullback_trend_2h import _pullback_thesis_intact
        df = self._df([100 + i * 2 for i in range(60)])   # a strong clean trend
        ok, detail = _pullback_thesis_intact({"adx_min": 25.0, "adx_period": 14}, df)
        assert ok in (True, False)          # computable either way
        assert detail["predicate"] == "adx_floor" and detail["adx_min"] == 25.0
