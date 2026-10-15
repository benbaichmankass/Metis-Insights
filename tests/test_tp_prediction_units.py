"""The six BRACKETS-AS-PREDICTIONS queue units (RQ-20261015-cab7-01..06) stay pinned to the script they run."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "research"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO))


def test_units_point_at_the_script_and_carry_l_atm() -> None:
    units = sorted((REPO / "research" / "queue").glob("RQ-20261015-cab7-0*.yaml"))
    assert len(units) == 6
    for p in units:
        u = yaml.safe_load(p.read_text())
        assert u["run"]["command"][1] == "scripts/research/tp_prediction_derive.py"
        assert "L-ATM" in u["roadmap"]
        assert u["decision_rule"]["registered_before_run"] is True


def test_registered_thresholds_match_the_script_constants() -> None:
    import tp_prediction_derive as t
    assert (t.REACH_FLOOR, t.IS_FRAC, t.MIN_OOS, t.NONINF_MARGIN, t.CAL_TOL) == (0.10, 0.60, 39, 0.05, 0.10)
    rule = yaml.safe_load((REPO / "research/queue/RQ-20261015-cab7-01.yaml").read_text())["decision_rule"]
    for token in ("0.10", "-0.05", "n_oos < 39"):
        assert token in rule["rule"]
    assert "60% IS" in rule["statistic"]
