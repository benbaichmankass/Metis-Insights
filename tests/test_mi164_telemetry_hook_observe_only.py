"""MI-164 — the `record_position_telemetry` hook on the four unhooked units.

WHY THIS FILE EXISTS
--------------------
`record_position_telemetry` is called from INSIDE a strategy unit's
`monitor()`, so a leg on a unit that does not carry the hook emits no peak-R
telemetry however much it trades. Measured by MI-163
(`docs/research/banking-half-2026-09-07.md` § 3): only `trend_donchian` and
`htf_pullback_trend_2h` carried it, leaving **9 of the 44 enabled+live legs
structurally invisible** — the 8 `ict_scalp_*` legs plus `squeeze_breakout_4h`.

MI-155 graded 13 legs `n_live = 0` and called all 13 `insufficient_n`. That
verdict stands, but the REASON splits: 9 are *we did not look*, 4 are a real
absence of positions. Collapsing those two is the failure `CLAUDE.md` names
directly, and only the 9 are fixable by code.

⚠️ THE ONLY THING THAT MAKES THIS SAFE IS THAT IT CANNOT CHANGE AN EXIT.
These units run on the live trader on every monitor tick, against real money.
So the controls below are not "does telemetry work" — they are:

  1. the hook FIRES on each of the four units (`TestItFires`);
  2. the verdict is byte-identical whether the hook succeeds, RAISES, or its
     module cannot even be imported (`TestItCannotAlterAnExit`);
  3. it does NOT fire on a tick that closes, so an exiting position is never
     also recorded as still-open (`TestItDoesNotFireOnACloseTick`);
  4. it costs one pass over the ALREADY-FETCHED frame — no broker call, no
     second fetch, no loop (`TestItIsAffordable`);
  5. what each unit can actually measure is NAMED, never faked
     (`TestAbsenceIsNamedNeverFaked`).

⚠️ A PASSING TEST HERE IS NOT AN OBSERVATION OF THE LIVE FLEET. It proves the
call site is correct and inert. Whether a real row ever appears for one of the
9 legs is answered at `/api/diag/position_telemetry`, not here.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import pandas as pd
import pytest

from src.runtime import position_telemetry
from src.units.strategies import ict_scalp, squeeze_breakout_4h, turtle_soup, vwap

# ---------------------------------------------------------------------------
# Fixtures — each frame is built so exactly ONE verdict path is in play and the
# hook sits on it. Shapes are lifted from each unit's existing test file so the
# baseline verdicts below are the ones already pinned elsewhere.
# ---------------------------------------------------------------------------


def _squeeze_frame() -> pd.DataFrame:
    highs, lows, closes = [130, 150, 145], [120, 140, 138], [128, 148, 140.0]
    rng = pd.date_range("2026-02-01", periods=3, freq="4h", tz="UTC")
    return pd.DataFrame({"timestamp": rng, "open": closes, "high": highs,
                         "low": lows, "close": closes, "volume": [1.0] * 3})


def _squeeze_pkg() -> dict:
    return {"order_package_id": "sq-1", "strategy_name": "squeeze_breakout_4h",
            "direction": "long", "entry": 110.0, "sl": 83.75, "tp": 1500.0,
            "meta": {"atr": 10.5, "trail_mult": 3.0, "atr_period": 14,
                     "risk_per_unit": 26.25,
                     "entry_time": "2026-02-01T00:00:00+00:00"}}


def _scalp_frame(close: float, n: int = 30) -> pd.DataFrame:
    return pd.DataFrame([
        {"timestamp": pd.Timestamp("2026-08-01", tz="UTC") + pd.Timedelta(minutes=5 * i),
         "open": close, "high": close + 0.1, "low": close - 0.1,
         "close": close, "volume": 1.0}
        for i in range(n)
    ])


def _scalp_pkg() -> dict:
    return {"order_package_id": "sc-1", "strategy_name": "ict_scalp",
            "symbol": "BTCUSDT", "direction": "long", "entry": 100.0,
            "sl": 99.0, "tp": 101.5,
            "meta": {"timeframe": "5m", "risk_per_unit": 1.0,
                     "entry_time": "2026-08-01T00:00:00+00:00"}}


def _turtle_frame(close: float, n: int = 5) -> pd.DataFrame:
    return pd.DataFrame([
        {"timestamp": pd.Timestamp("2026-08-01", tz="UTC") + pd.Timedelta(minutes=15 * i),
         "open": close, "high": close + 0.05, "low": close - 0.05,
         "close": close, "volume": 1.0}
        for i in range(n)
    ])


def _turtle_pkg() -> dict:
    return {"order_package_id": "ts-1", "strategy_name": "turtle_soup",
            "symbol": "BTCUSDT", "direction": "long", "entry": 100.0,
            "sl": 99.0, "tp": 101.0,
            "meta": {"atr": 0.5, "risk_per_unit": 1.0, "tp2": 102.0,
                     "timeframe": "15m"}}


def _vwap_frame(last_close: float) -> pd.DataFrame:
    closes = [105.0, 105.0, last_close]
    return pd.DataFrame({"open": closes, "high": [c * 1.001 for c in closes],
                         "low": [c * 0.999 for c in closes], "close": closes,
                         "volume": [100.0] * 3})


def _vwap_pkg() -> dict:
    return {"order_package_id": "vw-1", "strategy_name": "vwap",
            "symbol": "BTCUSDT", "direction": "long", "entry": 100.0,
            "sl": 99.0, "tp": 110.0, "status": "open",
            "created_at": datetime.now(timezone.utc).isoformat()}


#: (label, module, cfg, frame, package, the verdict this tick must produce).
#: The verdicts are the ones the units already produced BEFORE this hook — the
#: point of the table is that adding telemetry leaves every one unchanged.
CASES = [
    ("squeeze_breakout_4h", squeeze_breakout_4h, {}, _squeeze_frame(),
     _squeeze_pkg(), {"sl": 118.5}),
    ("ict_scalp", ict_scalp, {}, _scalp_frame(101.2), _scalp_pkg(),
     {"sl": 100.0}),
    ("turtle_soup", turtle_soup, {"be_at_r": 0.5}, _turtle_frame(100.5),
     _turtle_pkg(), {"sl": 100.0}),
    ("vwap", vwap, {"be_at_r": 0.5}, _vwap_frame(100.5), _vwap_pkg(),
     {"sl": 100.0}),
]
IDS = [c[0] for c in CASES]

#: Ticks that must produce a CLOSE — the hook must not run on these.
CLOSE_CASES = [
    ("squeeze_breakout_4h", squeeze_breakout_4h, {},
     pd.DataFrame({"timestamp": pd.date_range("2026-02-01", periods=2, freq="4h",
                                              tz="UTC"),
                   "open": [88, 80.0], "high": [90, 88.0], "low": [82, 80.0],
                   "close": [88, 80.0], "volume": [1.0, 1.0]}),
     _squeeze_pkg()),
    ("ict_scalp", ict_scalp, {}, _scalp_frame(98.0), _scalp_pkg()),
    ("turtle_soup", turtle_soup, {}, _turtle_frame(98.0), _turtle_pkg()),
    ("vwap", vwap, {}, _vwap_frame(98.0), _vwap_pkg()),
]


@pytest.fixture
def spy(monkeypatch):
    """Capture every hook call without writing a row anywhere."""
    calls = []

    def _rec(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(position_telemetry, "record_position_telemetry", _rec)
    return calls


# ---------------------------------------------------------------------------


class TestItFires:
    """The defect MI-163 found was silence. These four were silent."""

    @pytest.mark.parametrize("label,mod,cfg,frame,pkg,_v", CASES, ids=IDS)
    def test_the_hook_runs_once_on_a_managed_tick(self, label, mod, cfg, frame,
                                                  pkg, _v, spy):
        mod.monitor(cfg, frame, pkg)
        assert len(spy) == 1, f"{label}: hook fired {len(spy)}× on one tick"

    @pytest.mark.parametrize("label,mod,cfg,frame,pkg,_v", CASES, ids=IDS)
    def test_it_is_handed_the_package_and_its_own_frame(self, label, mod, cfg,
                                                        frame, pkg, _v, spy):
        mod.monitor(cfg, frame, pkg)
        kw = spy[0]
        assert kw["open_pkg"] is pkg
        assert kw["direction"] == "long"
        assert isinstance(kw["window"], pd.DataFrame)


class TestItCannotAlterAnExit:
    """THE CONTROL THAT MATTERS. Real money rides on these verdicts.

    Three worlds — the hook works, the hook raises, the hook's module cannot be
    imported at all — must produce the SAME verdict, and it must be the verdict
    the unit produced before the hook existed.
    """

    @pytest.mark.parametrize("label,mod,cfg,frame,pkg,want", CASES, ids=IDS)
    def test_the_verdict_is_the_pre_hook_verdict(self, label, mod, cfg, frame,
                                                 pkg, want, spy):
        got = mod.monitor(cfg, frame, pkg)
        assert got == pytest.approx(want), f"{label}: verdict moved"

    @pytest.mark.parametrize("label,mod,cfg,frame,pkg,want", CASES, ids=IDS)
    def test_a_raising_hook_changes_nothing(self, label, mod, cfg, frame, pkg,
                                            want, monkeypatch):
        def _boom(**_kwargs):
            raise RuntimeError("telemetry is down")

        monkeypatch.setattr(position_telemetry, "record_position_telemetry", _boom)
        assert mod.monitor(cfg, frame, pkg) == pytest.approx(want)

    @pytest.mark.parametrize("label,mod,cfg,frame,pkg,want", CASES, ids=IDS)
    def test_an_unimportable_hook_changes_nothing(self, label, mod, cfg, frame,
                                                  pkg, want, monkeypatch):
        """The `import` is inside the guard too, not only the call."""
        monkeypatch.setitem(sys.modules, "src.runtime.position_telemetry", None)
        assert mod.monitor(cfg, frame, pkg) == pytest.approx(want)

    @pytest.mark.parametrize("label,mod,cfg,frame,pkg,_w", CASES, ids=IDS)
    def test_the_hook_is_not_handed_anything_it_could_write_back_through(
            self, label, mod, cfg, frame, pkg, _w, spy):
        """`record_position_telemetry` returns a row and reads nothing back.

        The unit discards the return value, so there is no channel by which a
        telemetry result could reach the `{"sl": ...}` the monitor returns.
        """
        before = dict(pkg)
        mod.monitor(cfg, frame, pkg)
        assert pkg == before, f"{label}: the hook mutated the package"


class TestItDoesNotFireOnACloseTick:
    """A tick that EXITS must not also be recorded as a still-open position.

    Every unit's close paths are checked before the hook, so a closing tick
    returns before reaching it. This pins that ordering.
    """

    @pytest.mark.parametrize("label,mod,cfg,frame,pkg", CLOSE_CASES,
                             ids=[c[0] for c in CLOSE_CASES])
    def test_a_closing_tick_records_nothing(self, label, mod, cfg, frame, pkg,
                                            spy):
        verdict = mod.monitor(cfg, frame, pkg)
        assert verdict["action"] == "close", f"{label}: expected a close"
        assert spy == [], f"{label}: hook ran on a closing tick"


class TestItIsAffordable:
    """These run on the live trader every monitor tick, so cost is a control."""

    @pytest.mark.parametrize("label,mod,cfg,frame,pkg,_v", CASES, ids=IDS)
    def test_it_reuses_the_already_fetched_frame(self, label, mod, cfg, frame,
                                                 pkg, _v, spy):
        """No second fetch: the window is a restriction of the SAME frame."""
        mod.monitor(cfg, frame, pkg)
        window = spy[0]["window"]
        assert len(window) <= len(frame)
        assert list(window.columns) == list(frame.columns)

    @pytest.mark.parametrize("label,mod,cfg,frame,pkg,_v", CASES, ids=IDS)
    def test_no_broker_client_is_constructed(self, label, mod, cfg, frame, pkg,
                                             _v, spy, monkeypatch):
        """A hook that opened a broker session would be a per-tick network call."""
        import src.runtime.position_telemetry as pt

        monkeypatch.setattr(pt, "write_record",
                            lambda *_a, **_k: pytest.fail("wrote during spy run"))
        mod.monitor(cfg, frame, pkg)
        assert len(spy) == 1


class TestAbsenceIsNamedNeverFaked:
    """What each unit can MEASURE differs, and the difference must be visible.

    `build_record` derives `peak_r` from `meta['risk_per_unit']` and anchors the
    window on `meta['entry_time']`. Two of these four units stamp neither, so
    their rows must carry a NAMED absence rather than a peak divided by a
    guessed R or measured off a pre-entry bar.

    ⚠️ Fixing that means editing each unit's `order_package`, which is a
    separate Tier-3 change and deliberately NOT in this PR.
    """

    #: label -> the peak_state its rows can honestly carry today.
    WANT = {
        # Stamps risk_per_unit AND entry_time — these are the 9 invisible legs.
        "squeeze_breakout_4h": "measured",
        "ict_scalp": "measured",
        # Stamps risk_per_unit but NO entry_time -> the window is unanchored.
        "turtle_soup": "unanchored",
        # Stamps neither -> there is no R to divide by.
        "vwap": "no_risk",
    }

    @pytest.mark.parametrize("label,mod,cfg,frame,pkg,_v", CASES, ids=IDS)
    def test_peak_state_is_what_the_unit_can_honestly_claim(self, label, mod,
                                                            cfg, frame, pkg,
                                                            _v, spy):
        mod.monitor(cfg, frame, pkg)
        row = position_telemetry.build_record(**spy[0])
        assert row is not None, f"{label}: no row built"
        assert row["peak_state"] == self.WANT[label]
        if self.WANT[label] != "measured":
            assert row["peak_r"] is None, f"{label}: faked a peak"
        else:
            assert row["peak_r"] is not None

    def test_the_two_units_carrying_the_9_invisible_legs_measure_a_real_peak(
            self, spy):
        """The whole point: `ict_scalp`'s one-shot mechanism becomes visible.

        MI-163 § 3 — all 8 live `ict_scalp` legs plus `squeeze_breakout_4h` are
        the 9. If these two cannot produce a `peak_r`, the hook bought nothing.
        """
        ict_scalp.monitor({}, _scalp_frame(101.2), _scalp_pkg())
        row = position_telemetry.build_record(**spy[0])
        assert row["peak_r"] == pytest.approx(1.3)

        spy.clear()
        squeeze_breakout_4h.monitor({}, _squeeze_frame(), _squeeze_pkg())
        row = position_telemetry.build_record(**spy[0])
        # peak high 150, entry 110, risk 26.25 -> 40 / 26.25. Compared at 1e-4
        # because the stored schema rounds peak_r to 4dp (`position_telemetry._r`),
        # so 4dp is the resolution any consumer of this column actually gets.
        assert row["peak_r"] == pytest.approx(40.0 / 26.25, abs=1e-4)
