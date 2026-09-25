"""R3 Gate-1 cost fidelity: the rule's decision logic, and that the code and
the registered rule text agree (docs/research/r3-gate1-cost-fidelity-rule-2026-09-25.md)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "research"))

import r3_cost_fidelity as r3  # noqa: E402


def _cell(verdict, basis, stage, acct):
    return {"verdict": verdict, "basis": basis, "stage": stage, "account_id": acct}


def test_rule_constants_match_the_registered_text():
    doc = (REPO / r3.RULE_DOC).read_text()
    assert r3.RULE_ID in doc
    assert f"`N_FLOOR = {r3.N_FLOOR}` per side" in doc
    assert "MD-PROMOTE-S1-S2.bar.cost_tolerance_bps" in doc
    assert r3.VENUE_MIN_N == 20  # "Venue-level pooled figures ... keep D3's 20"


def test_tolerance_is_read_from_the_mandate_field_not_invented():
    import yaml

    doc = yaml.safe_load((REPO / "config/mandates.yaml").read_text())
    field = next(m for m in doc["mandates"] if m["id"] == "MD-PROMOTE-S1-S2")["bar"]["cost_tolerance_bps"]
    assert r3.tolerance_bps() == float(field)


def test_tolerance_refuses_when_unstated(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/mandates.yaml").write_text(
        "mandates:\n  - id: MD-PROMOTE-S1-S2\n    bar: {}\n")
    with pytest.raises(ValueError):
        r3.tolerance_bps(tmp_path)


def test_below_the_floor_on_either_side_is_insufficient_whatever_the_size():
    g = r3.grade([50.0] * 30, [50.0] * (r3.N_FLOOR - 1), 3.0, 0.0)
    assert g["verdict"] == r3.INSUFFICIENT
    assert "ci95" not in g
    g = r3.grade([50.0] * (r3.N_FLOOR - 1), [50.0] * 30, 3.0, 0.0)
    assert g["verdict"] == r3.INSUFFICIENT


def test_divergent_when_ci_lower_clears_modelled_plus_tolerance():
    g = r3.grade([4.0, 5.0, 6.0] * 5, [4.0, 5.0, 6.0] * 5, 3.0, 0.0)
    assert g["roundtrip_mean"] == 10.0
    assert g["ci95"][0] > 3.0
    assert g["verdict"] == r3.DIVERGENT
    # the same data under a tolerance wide enough to cover the CI is consistent
    assert r3.grade([4.0, 5.0, 6.0] * 5, [4.0, 5.0, 6.0] * 5, 3.0, 20.0)["verdict"] == r3.CONSISTENT


def test_consistent_when_ci_upper_at_or_below_threshold_and_flags_overcharge():
    g = r3.grade([-1.0, 0.0, 1.0] * 5, [0.0, 0.5, 1.0] * 5, 3.0, 0.0)
    assert g["ci95"][1] <= 3.0
    assert g["verdict"] == r3.CONSISTENT
    assert g["record_overcharges"] is True


def test_inconclusive_when_ci_straddles():
    g = r3.grade([-10.0, 0.0, 10.0] * 4, [-5.0, 3.0, 11.0] * 4, 3.0, 0.0)
    lo, hi = g["ci95"]
    assert lo <= 3.0 < hi
    assert g["verdict"] == r3.INCONCLUSIVE


def test_favourable_divergence_never_demotes():
    g = r3.grade([-20.0, -21.0, -19.0] * 5, [-20.0, -21.0, -19.0] * 5, 3.0, 0.0)
    assert g["verdict"] == r3.CONSISTENT


def test_no_record_is_its_own_verdict():
    assert r3.grade([1.0] * 12, [1.0] * 12, None, 0.0)["verdict"] == r3.NO_RECORD


def test_market_cell_wins_over_simulator():
    cells = [_cell(r3.CONSISTENT, "simulator", "S1", "bybit_1"),
             _cell(r3.DIVERGENT, "market", "S2", "bybit_2")]
    assert r3._leg_verdict(cells) == {"verdict": r3.DIVERGENT, "from_account": "bybit_2",
                                      "basis": "market"}


def test_insufficient_market_cell_falls_back_to_the_soak_cell():
    cells = [_cell(r3.INSUFFICIENT, "market", "S2", "bybit_2"),
             _cell(r3.INCONCLUSIVE, "simulator", "S2-mirror", "bybit_portfolio"),
             _cell(r3.CONSISTENT, "simulator", "S1", "bybit_1")]
    assert r3._leg_verdict(cells)["from_account"] == "bybit_1"


def test_all_cells_insufficient_is_insufficient():
    cells = [_cell(r3.INSUFFICIENT, "market", "S2", "bybit_2"),
             _cell(r3.INSUFFICIENT, "simulator", "S1", "bybit_1")]
    assert r3._leg_verdict(cells)["verdict"] == r3.INSUFFICIENT
