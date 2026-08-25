"""Tests for the ladder-headroom probe.

The probe's whole job is to stop a producer being built on a fleet with no room
for one. So the things worth pinning are the ones that would let it report a
confident wrong answer: a share computed over a thin denominator, an MFE that is
MISSING being counted as "did not go favourable", a per-leg cap-in-R fiction, and
the live cap constant drifting away from the strategy source.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "research"))
sys.path.insert(0, str(REPO / "scripts"))

from m20_ladder_headroom import (  # noqa: E402
    CANDIDATE_RUNGS_R, LIVE_TP_CAP_PCT, analyse_trades, verdict_for)


def _trade(entry=100.0, sl=98.0, mfe_r=None, net_r=1.0, nest_mfe=False):
    """entry 100 / sl 98 => risk 2.0 => cap_r = 0.099*100/2 = 4.95R."""
    row = {"entry": entry, "sl": sl, "net_r": net_r}
    if mfe_r is not None:
        if nest_mfe:
            row["meta"] = {"mfe_r": mfe_r}
        else:
            row["mfe_r"] = mfe_r
    return row


class TestCapArithmetic:
    def test_cap_r_is_per_trade_not_per_leg(self):
        """Same 9.9% cap, different risk => different R. That is the point."""
        rows = [_trade(entry=100.0, sl=98.0, mfe_r=1.0),    # risk 2 -> cap 4.95R
                _trade(entry=100.0, sl=90.0, mfe_r=1.0)]    # risk 10 -> cap 0.99R
        s = analyse_trades(rows, LIVE_TP_CAP_PCT)
        # p10 and p90 must straddle: a single leg-level "cap in R" would be a lie.
        assert s["cap_r_p10"] < s["cap_r_p90"]
        assert abs(s["cap_r_p10"] - 0.99) < 0.01
        assert abs(s["cap_r_p90"] - 4.95) < 0.01

    def test_trade_reaching_the_cap_counts_as_utilisation(self):
        rows = [_trade(mfe_r=5.0), _trade(mfe_r=1.0)]  # cap_r 4.95
        s = analyse_trades(rows, LIVE_TP_CAP_PCT)
        assert s["reached_cap"] == 1
        assert s["cap_utilisation_pct"] == 50.0


class TestMissingMfeIsNotZero:
    def test_missing_mfe_is_counted_separately_never_as_flat(self):
        rows = [_trade(mfe_r=None), _trade(mfe_r=None), _trade(mfe_r=3.0)]
        s = analyse_trades(rows, LIVE_TP_CAP_PCT)
        assert s["mfe_missing"] == 2
        assert s["trades_measured"] == 1
        # the share is over MEASURED trades, not emitted ones
        assert s["rung_reach_pct"]["2R"] == 100.0
        assert s["mfe_coverage_pct"] == 33.3

    def test_nested_mfe_is_found(self):
        """backtest_ict_scalp nests mfe_r under meta; the canonical accessor
        handles it, and a leg of scalp trades must NOT read as 0-of-N."""
        rows = [_trade(mfe_r=3.0, nest_mfe=True) for _ in range(5)]
        s = analyse_trades(rows, LIVE_TP_CAP_PCT)
        assert s["mfe_missing"] == 0
        assert s["trades_measured"] == 5

    def test_a_leg_with_no_mfe_at_all_reports_no_shares(self):
        rows = [_trade(mfe_r=None) for _ in range(50)]
        s = analyse_trades(rows, LIVE_TP_CAP_PCT)
        assert s["trades_measured"] == 0
        assert s["cap_utilisation_pct"] is None
        assert s["mfe_coverage_pct"] == 0.0
        v = verdict_for(s, min_trades=30, cap_binds_pct=25.0, rung_floor_pct=15.0)
        assert v["verdict"] == "insufficient_trades"


class TestVerdictsAreNeverCollapsed:
    def _stats(self, rows):
        return analyse_trades(rows, LIVE_TP_CAP_PCT)

    def test_thin_denominator_refuses_a_verdict(self):
        s = self._stats([_trade(mfe_r=3.0) for _ in range(5)])
        v = verdict_for(s, min_trades=30, cap_binds_pct=25.0, rung_floor_pct=15.0)
        assert v["verdict"] == "insufficient_trades"
        assert "5" in v["why"]

    def test_cap_binds_when_most_trades_reach_the_ceiling(self):
        s = self._stats([_trade(mfe_r=6.0) for _ in range(40)])  # cap 4.95
        v = verdict_for(s, min_trades=30, cap_binds_pct=25.0, rung_floor_pct=15.0)
        assert v["verdict"] == "cap_binds"

    def test_no_room_when_even_the_cheapest_rung_is_rarely_reached(self):
        # 40 trades, all MFE 0.2R -> no candidate rung (1.0R min) reached
        s = self._stats([_trade(mfe_r=0.2) for _ in range(40)])
        v = verdict_for(s, min_trades=30, cap_binds_pct=25.0, rung_floor_pct=15.0)
        assert v["verdict"] == "no_room"

    def test_opportunity_when_rungs_reachable_but_cap_is_not(self):
        # MFE 2.5R: clears the 1.0/1.5/2.0R rungs, never the 4.95R cap
        s = self._stats([_trade(mfe_r=2.5) for _ in range(40)])
        v = verdict_for(s, min_trades=30, cap_binds_pct=25.0, rung_floor_pct=15.0)
        assert v["verdict"] == "ladder_opportunity"
        assert s["cap_utilisation_pct"] == 0.0
        assert s["rung_reach_pct"]["2R"] == 100.0

    def test_the_three_outcomes_are_distinguishable(self):
        """Guard against a future edit collapsing two of them into one."""
        got = set()
        for rows in ([_trade(mfe_r=6.0)] * 40,
                     [_trade(mfe_r=0.2)] * 40,
                     [_trade(mfe_r=2.5)] * 40):
            s = self._stats(rows)
            got.add(verdict_for(s, 30, 25.0, 15.0)["verdict"])
        assert got == {"cap_binds", "no_room", "ladder_opportunity"}


class TestConstantsDoNotDrift:
    def test_probe_cap_IS_the_owner_not_a_copy(self):
        """The probe no longer hardcodes 0.099 -- it imports the one owner.

        The old docstring justified a hardcode with "so it runs on a bare
        checkout"; the owner imports only `typing`, so a bare checkout is no
        longer a reason to keep a private literal.
        """
        from src.runtime.tp_venue_cap import TP_VENUE_CAP_PCT
        assert LIVE_TP_CAP_PCT is TP_VENUE_CAP_PCT

    def test_candidate_rungs_are_coarse_and_below_a_typical_cap(self):
        """These decide IF a ladder is worth pursuing, not WHICH one wins — a
        fine grid here would invite reading this probe as a parameter search."""
        assert len(CANDIDATE_RUNGS_R) <= 4
        assert max(CANDIDATE_RUNGS_R) < 4.95
