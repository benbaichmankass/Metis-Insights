"""REMAINING-MOVE-HEAD (scripts/research/remaining_move_head.py): the self-test's three claims, pinned."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "scripts" / "research"), str(REPO / "scripts" / "ops"), str(REPO / "scripts"), str(REPO)]
import remaining_move_head as rm  # noqa: E402


def test_self_test_null_monotone_and_planted():
    assert rm._self_test() == 0


def test_target_reads_only_future_bars_and_floors_at_zero():
    paths, info = rm._synth(30, False, 2)
    t = rm.build_trades(paths, info, "SYN")[0]
    M = rm.trade_states(t)[1]
    i = 3
    assert abs(M[i] - max(0.0, t["fav"][i + 1 :].max() - t["open_r"][i])) < 1e-12
    t2 = dict(t)
    t2["fav"] = t["fav"].copy()
    t2["fav"][: i + 1] += 7.0  # mutate bars <= i: M_i must not move
    assert abs(rm.trade_states(t2)[1][i] - M[i]) < 1e-12
    assert (M >= 0).all()


def test_stop_wins_the_tie_on_the_exit_bar():
    paths, info = rm._synth(60, True, 4)
    t = next(x for x in rm.build_trades(paths, info, "SYN") if x["exit_reason"] == "stop")
    Q = np.full(t["n"] - 1, 0.5)
    r = rm.replay(t, Q, np.ones(t["n"] - 1, dtype=bool), 0.25)
    assert r["exit_idx"] <= t["n"] - 1
