"""MI-155 · per-leg backtest<->live exit-location fidelity
(`scripts/research/exit_location_fidelity.py`).

These pin the DISTINCTIONS, not today's numbers. The fleet's n moves every day
and the verdicts move with it; what must not move is that `insufficient_n` stays
a third state, that the timeframe stays part of the join, and that both
denominators are reported on every row.

A test asserting "44 of 44 legs abstain" would fail the moment a soak deepens,
which is the outcome this whole thread is trying to produce. So no test here
asserts a fleet count.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.research.exit_location_fidelity import (  # noqa: E402
    ABSTAIN_FLOOR_N,
    FIDELITY_RATIO_TOLERANCE,
    enabled_live_legs,
    family_of,
    grade_leg,
    live_rows_for_grading,
    load_backtest_reference,
    pct_of_entry,
    selftest,
)
from src.runtime.tp_venue_cap import TP_VENUE_CAP_PCT  # noqa: E402

BT = {
    ("BTCUSDT", "trend_donchian", "15m"): {
        "leg": "trend_donchian_BTCUSDT_15m", "symbol": "BTCUSDT",
        "family": "trend_donchian", "timeframe": "15m", "n": 3194, "p90": 0.0387,
    }
}


def _rows(n, *, pct, lifecycle="closed"):
    """n live telemetry rows whose pct_of_entry is `pct`."""
    cap = 2.0
    peak = pct / TP_VENUE_CAP_PCT * cap
    return [{"peak_gradeable": True, "peak_r": peak, "cap_r": cap,
             "lifecycle": lifecycle, "strategy": "trend_donchian_x",
             "symbol": "BTCUSDT", "peak_r_is_lower_bound": True} for _ in range(n)]


class TestThreeStatesNeverCollapse:
    """`insufficient_n` means WE COULD NOT LOOK. It is not a soft anything."""

    def test_thin_live_against_huge_backtest_abstains(self):
        """The failure this exists to prevent: inheriting the backtest's
        confidence because ITS n is large. n_backtest=3194 licenses nothing
        about a leg with n_live=5."""
        v = grade_leg("trend_donchian_x", [0.0387] * 5, timeframe="15m",
                      symbol="BTCUSDT", backtest=BT)
        assert v["verdict"] == "insufficient_n"
        assert v["verdict"] != "fidelity_ok"
        assert v["verdict"] != "fidelity_failed"

    def test_abstaining_leg_still_reports_both_denominators(self):
        """MI-151's 3,194-vs-63 was hidden by a ratio quoted without both n's.
        An abstaining row is exactly where that hiding is most tempting."""
        v = grade_leg("trend_donchian_x", [0.0387] * 5, timeframe="15m",
                      symbol="BTCUSDT", backtest=BT)
        assert v["n_live"] == 5
        assert v["n_backtest"] == 3194

    def test_reason_distinguishes_which_side_was_missing(self):
        """'no backtest corpus' and 'live soak too shallow' are different facts
        with different remedies; one reason string for both would erase that."""
        no_bt = grade_leg("trend_donchian_x", [0.05] * (ABSTAIN_FLOOR_N + 5),
                          timeframe="4h", symbol="BTCUSDT", backtest=BT)
        assert no_bt["reason"].startswith("no_backtest_corpus_for_leg")
        assert "abstain_floor" not in no_bt["reason"]

        thin = grade_leg("trend_donchian_x", [0.05] * 3, timeframe="15m",
                         symbol="BTCUSDT", backtest=BT)
        assert "abstain_floor" in thin["reason"]
        assert not thin["reason"].startswith("no_backtest_corpus_for_leg")

    def test_empty_live_yields_no_p90_not_zero(self):
        """0.0 is a real quantile of a real distribution. 'Nothing to take a
        quantile of' is not, and must not arrive wearing its clothes."""
        v = grade_leg("trend_donchian_x", [], timeframe="15m", symbol="BTCUSDT",
                      backtest=BT)
        assert v["live_p90_pct"] is None
        assert v["verdict"] == "insufficient_n"


class TestTimeframeIsPartOfTheJoin:
    """The refusal that keeps a horizon difference from being reported as a
    fidelity failure — the defect family M31 exists to close."""

    def test_same_symbol_and_family_at_another_timeframe_finds_no_corpus(self):
        v = grade_leg("trend_donchian_x", [0.09] * (ABSTAIN_FLOOR_N + 20),
                      timeframe="4h", symbol="BTCUSDT", backtest=BT)
        assert v["verdict"] == "insufficient_n"
        assert v["n_backtest"] == 0

    def test_matched_timeframe_does_join(self):
        """The positive control for the test above: the refusal must be about
        the timeframe, not about the join being broken outright."""
        v = grade_leg("trend_donchian_x", [0.0387] * (ABSTAIN_FLOOR_N + 20),
                      timeframe="15m", symbol="BTCUSDT", backtest=BT)
        assert v["n_backtest"] == 3194
        assert v["verdict"] != "insufficient_n"

    def test_committed_reference_is_15m_and_live_donchian_is_not(self):
        """Reads the shipped artifact and the shipped config, so the claim in
        the memo cannot drift from the files. If a 15m donchian leg is ever
        added live, this test fails and the memo needs revisiting."""
        ref = load_backtest_reference(str(ROOT / "docs/research/data"
                                          / "backtest-mfe-reference-2026-09-07.json"))
        assert ref, "the backtest reference artifact must be readable"
        assert {k[2] for k in ref} == {"15m"}

        yaml = pytest.importorskip("yaml")
        cfg = yaml.safe_load((ROOT / "config/strategies.yaml").read_text())
        strat = cfg.get("strategies") or cfg
        live_donchian_tfs = {
            str(b.get("timeframe") or b.get("interval"))
            for n, b in strat.items()
            if isinstance(b, dict) and n.startswith("trend_donchian")
            and b.get("enabled") and str(b.get("execution", "live")) == "live"
        }
        assert "15m" not in live_donchian_tfs, (
            "a live 15m donchian leg now exists — the MI-155 memo's "
            "'no timeframe-matched counterpart' finding must be re-derived"
        )


class TestLifecycleGate:
    """Inherited from m31_mfe_parity refusal #1."""

    def test_open_rows_excluded_when_final_required(self):
        rows = _rows(3, pct=0.02, lifecycle="open") + _rows(2, pct=0.02)
        assert len(live_rows_for_grading(rows, require_final=True)) == 2
        assert len(live_rows_for_grading(rows, require_final=False)) == 5

    def test_unknown_lifecycle_is_not_treated_as_closed(self):
        rows = _rows(4, pct=0.02, lifecycle="unknown_no_trade_id")
        assert live_rows_for_grading(rows, require_final=True) == []

    def test_ungradeable_peak_never_enters_either_gate(self):
        rows = [{"peak_gradeable": False, "peak_r": 1.0, "cap_r": 2.0,
                 "lifecycle": "closed"}]
        assert live_rows_for_grading(rows, require_final=False) == []


class TestPercentOfEntryBasis:
    """MI-148's load-bearing choice: never R, because the R denominator is
    measurably contaminated and the venue clamp is itself a percent of entry."""

    def test_pct_of_entry_uses_only_peak_r_and_cap_r(self):
        assert pct_of_entry({"peak_r": 1.0, "cap_r": 2.0}) == pytest.approx(
            TP_VENUE_CAP_PCT / 2)

    def test_a_trade_at_its_cap_reads_as_the_cap_percent(self):
        assert pct_of_entry({"peak_r": 3.0, "cap_r": 3.0}) == pytest.approx(
            TP_VENUE_CAP_PCT)

    @pytest.mark.parametrize("row", [
        {"peak_r": 1.0, "cap_r": 0},        # never ZeroDivisionError
        {"peak_r": None, "cap_r": 2.0},
        {"peak_r": "x", "cap_r": 2.0},
        {},
    ])
    def test_unreadable_rows_return_none_and_never_raise(self, row):
        assert pct_of_entry(row) is None


class TestVerdictBoundary:
    def test_agreement_grades_ok_and_the_motivating_gap_grades_failed(self):
        n = ABSTAIN_FLOOR_N + 20
        ok = grade_leg("trend_donchian_x", [0.0387] * n, timeframe="15m",
                       symbol="BTCUSDT", backtest=BT)
        assert ok["verdict"] == "fidelity_ok"
        # MI-151's live 9.70% against its backtest 3.87% — the ~2.5x.
        bad = grade_leg("trend_donchian_x", [0.0970] * n, timeframe="15m",
                        symbol="BTCUSDT", backtest=BT)
        assert bad["verdict"] == "fidelity_failed"
        assert bad["ratio"] == pytest.approx(0.0970 / 0.0387, rel=1e-6)

    def test_tolerance_is_tighter_than_the_gap_that_motivated_it(self):
        """A tolerance looser than ~2.5x could not have failed on the very
        discrepancy this module was built to adjudicate."""
        assert FIDELITY_RATIO_TOLERANCE < 0.0970 / 0.0387


class TestHelpers:
    def test_family_of(self):
        assert family_of("trend_donchian_eth_4h") == "trend_donchian"
        assert family_of("eth_pullback_2h") == "pullback"
        assert family_of("ict_scalp_5m") == "ict_scalp"
        assert family_of("squeeze_breakout_4h") == "other"

    def test_enabled_live_legs_excludes_shadow_and_disabled(self):
        meta = {
            "a": {"enabled": True, "execution": "live"},
            "b": {"enabled": True, "execution": "shadow"},
            "c": {"enabled": False, "execution": "live"},
        }
        assert enabled_live_legs(meta) == ["a"]

    def test_missing_reference_file_yields_empty_not_a_crash(self):
        assert load_backtest_reference("/nonexistent/path.json") == {}

    def test_reference_artifact_parses_and_carries_provenance(self):
        path = (ROOT / "docs/research/data"
                / "backtest-mfe-reference-2026-09-07.json")
        payload = json.loads(path.read_text())
        assert payload["basis"] == "percent_of_entry"
        src = payload["source"]
        # A MEASURED must say where the measurement lives.
        assert src["mark"] == "MEASURED"
        assert src["issue"] == 11154
        assert src["trainer_run_id"]
        assert src["corpus_committed"] is False
        for leg in payload["legs"]:
            assert leg["n"] > 0
            assert 0 < leg["p50"] <= leg["p90"] <= leg["p95"]


def test_module_selftest_passes():
    assert selftest() == 0
