"""`beats_baseline` is a RELATIVE test and says nothing about the sign.

Measured 2026-08-23 on `eth_pullback_2h.tp_r`: EVERY grid value was net-negative
(best −5.81 R against a −9.61 R baseline) and the recommendation still read
`action: propose_value`, `beats_baseline: true`, with a ready-to-paste Tier-3
YAML line. "Less bad" rendered as a proposal.

That is the unprovenanced-diagnostic sub-class A shape: the label names a
comparison, a reader maps it to "is this good?", and nothing in the output
reveals the substitution. The pre-existing `train_oos_consistent` caveat does
NOT cover it — it grades CONSISTENCY, not profitability.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "strategy_tune_sweep",
    Path(__file__).resolve().parents[1] / "scripts/ml/strategy_tune_sweep.py",
)
sweep = importlib.util.module_from_spec(_SPEC)
sys.modules["strategy_tune_sweep"] = sweep
_SPEC.loader.exec_module(sweep)


class _Recipe:
    target = "config/strategies.yaml::leg.tp_r"


def _row(value, net, train_net=None):
    r = {"value": value, "net_total": net, "net_expectancy": net / 10.0,
         "trades": 40}
    if train_net is not None:
        r["train"] = {"net_total": train_net}
    return r


def _rec(rows, pick, baseline, **kw):
    return sweep._recommendation(_Recipe(), pick, pick, baseline, rows=rows, **kw)


class TestAllNegativeGrid:
    def _all_neg(self):
        rows = [_row(2.0, -6.17), _row(2.5, -5.81), _row(50.0, -9.61)]
        return rows, rows[1], rows[2]

    def test_action_says_there_is_no_profitable_value(self):
        rows, pick, base = self._all_neg()
        r = _rec(rows, pick, base)
        assert r["action"] == "no_profitable_value"

    def test_it_does_NOT_read_as_propose_value(self):
        """The live regression: a ready-to-paste Tier-3 line on a losing leg."""
        rows, pick, base = self._all_neg()
        assert _rec(rows, pick, base)["action"] != "propose_value"

    def test_beats_baseline_is_still_TRUE_and_that_is_the_point(self):
        """It is not wrong — −5.81 IS greater than −9.61. It is INSUFFICIENT,
        which is why the sign must be published beside it rather than instead
        of it."""
        rows, pick, base = self._all_neg()
        r = _rec(rows, pick, base)
        assert r["beats_baseline"] is True
        assert r["chosen_is_profitable"] is False
        assert r["any_grid_value_profitable"] is False

    def test_the_detail_says_LESS_BAD_in_those_words(self):
        rows, pick, base = self._all_neg()
        d = _rec(rows, pick, base)["detail"]
        assert "NO TESTED VALUE IS PROFITABLE" in d
        assert "LESS BAD" in d

    def test_the_chosen_net_total_is_published_not_just_a_boolean(self):
        """A reader must be able to see HOW negative, not only that it is."""
        rows, pick, base = self._all_neg()
        assert _rec(rows, pick, base)["chosen_net_total"] == -5.81


class TestProfitableGridIsUnchanged:
    def _profitable(self):
        rows = [_row(2.5, 4.12, train_net=8.0), _row(3.0, 4.38, train_net=9.0),
                _row(50.0, 2.16, train_net=5.0)]
        return rows, rows[1], rows[2]

    def test_a_winning_grid_still_proposes(self):
        rows, pick, base = self._profitable()
        r = _rec(rows, pick, base)
        assert r["action"] == "propose_value"
        assert r["chosen_is_profitable"] is True
        assert r["any_grid_value_profitable"] is True

    def test_no_scary_caveat_is_added_to_a_clean_result(self):
        """A warning on every packet is how a signal becomes background noise."""
        rows, pick, base = self._profitable()
        d = _rec(rows, pick, base)["detail"]
        assert "NO TESTED VALUE" not in d
        assert "net-NEGATIVE" not in d


class TestMixedGrid:
    def test_a_negative_pick_amid_positive_values_is_flagged_differently(self):
        """Distinct from the all-negative case: profitable values DO exist, so
        the finding is about the PICK, not the leg. Collapsing the two would
        lose which of them a reader is looking at."""
        rows = [_row(2.0, 5.0), _row(2.5, -1.0), _row(50.0, -3.0)]
        r = _rec(rows, rows[1], rows[2])
        assert r["any_grid_value_profitable"] is True
        assert r["chosen_is_profitable"] is False
        assert r["action"] != "no_profitable_value"
        assert "chosen value is itself net-NEGATIVE" in r["detail"]


class TestDefensiveness:
    def test_rows_omitted_does_not_raise(self):
        """The parameter is optional; a caller predating it must not crash.
        It reports no profitable value, which is the conservative reading of
        an empty grid — and `chosen_net_total` still carries the truth."""
        rows = [_row(3.0, 4.0)]
        r = sweep._recommendation(_Recipe(), rows[0], rows[0], None)
        assert r["chosen_net_total"] == 4.0

    def test_a_row_with_a_null_net_total_is_skipped_not_counted(self):
        rows = [{"value": 1.0, "net_total": None, "trades": 5}, _row(2.0, -1.0)]
        r = _rec(rows, rows[1], rows[1])
        assert r["any_grid_value_profitable"] is False
