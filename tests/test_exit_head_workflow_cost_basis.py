"""The exit-head pipeline must grade its policy sim NET of venue cost.

FIX-CA-28 / `CA-B03-exit-head-workflow-never-applies-net-of-fee-cost` (code
audit 2026-09-27). `research-exit-head-build.yml` never passed a cost to
`analyze_exit_head.py`, whose cost flags default to 0.0 — so the pipeline's
"net-of-fee exit-policy sim" and its pre-registered bar were GROSS.

The fix resolves a venue-aware cost in bps from `src.runtime.execution_costs`
for the run's symbol and has the analyzer convert it to each trade's own R via
the panel's per-row `r_per_bp` (entry / |entry - stop| / 1e4).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import yaml

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.research import analyze_exit_head as aeh  # noqa: E402

WF = REPO / ".github" / "workflows" / "research-exit-head-build.yml"


def _steps() -> list[dict]:
    wf = yaml.safe_load(WF.read_text())
    return wf["jobs"]["build"]["steps"]


def test_workflow_passes_venue_cost_to_the_analyzer():
    runs = [s.get("run", "") for s in _steps()]
    analyze = [r for r in runs if "scripts/research/analyze_exit_head.py" in r]
    assert analyze, "analyzer step not found — the guard must be re-derived"
    for r in analyze:
        assert re.search(r"--cost-bps\s+\"\$\{eh_cost_bps\}\"", r), r
        assert re.search(r"--exit-fee-bps\s+\"\$\{eh_exit_fee_bps\}\"", r), r


def test_workflow_resolves_the_cost_from_execution_costs():
    runs = [s.get("run", "") for s in _steps()]
    resolve = [r for r in runs if "eh_cost_bps=" in r]
    assert resolve, "no step exports eh_cost_bps"
    body = resolve[0]
    assert "execution_costs" in body and "slippage_bps_roundtrip_for" in body
    assert "DEFAULT_FEE_BPS_ROUNDTRIP" in body


def test_resolved_cost_is_nonzero_for_the_default_symbol():
    from src.runtime import execution_costs as ec
    slip = ec.slippage_bps_roundtrip_for("BTCUSDT")
    assert ec.DEFAULT_FEE_BPS_ROUNDTRIP + slip > 0 and slip / 2.0 > 0


def _rows():
    # One trade, entry 100 / stop 99 -> 1% risk -> r_per_bp = 0.01.
    # Bar 1: head sees a feature that makes it EXIT at upnl 0.5R.
    common = {"trade_id": 1, "trade_realized_r": 1.0, "r_per_bp": 0.01}
    return [
        {**common, "decision_time": "2026-01-01T00:00", "feat_x": 1.0,
         "feat_upnl_r": 0.5},
    ]


def _sim(**kw):
    rows = _rows()
    head, base, delta = [], [], []
    # beta = [intercept, coef]; large negative => p_hold ~ 0 => exit now.
    beta = np.array([-10.0, 0.0])
    aeh._policy_sim(rows, [0], beta, np.array([0.0]), np.array([1.0]),
                    ["feat_x"], 0.5, 0.0, head, base, delta, **kw)
    return head[0], base[0], delta[0]


def test_positive_control_zero_cost_is_gross():
    head, base, delta = _sim()
    assert (head, base, delta) == (0.5, 1.0, -0.5)


def test_venue_cost_is_charged_in_each_trades_own_r():
    # cost 10.5 bps round-trip * 0.01 R/bp = 0.105R on BOTH arms;
    # exit fee 1.5 bps * 0.01 = 0.015R on the early exit only.
    head, base, delta = _sim(cost_bps=10.5, exit_fee_bps=1.5)
    assert base == 1.0 - 0.105
    assert abs(head - (0.5 - 0.105 - 0.015)) < 1e-12
    assert abs(delta - (-0.5 - 0.015)) < 1e-12


def test_cost_basis_is_reported_honestly():
    rows = _rows()
    assert aeh._cost_basis(rows, 0.0, 0.0, 0.0) == "gross"
    assert aeh._cost_basis(rows, 10.5, 1.5, 0.0) == "net_venue_bps"
    legacy = [{k: v for k, v in r.items() if k != "r_per_bp"} for r in rows]
    assert aeh._cost_basis(legacy, 10.5, 1.5, 0.0) == "gross_panel_lacks_r_per_bp"


def test_panel_builder_emits_r_per_bp():
    src = (REPO / "scripts" / "research" / "build_intrabar_exit_panel.py").read_text()
    assert '"r_per_bp": round(entry / abs(entry - stop) / 1.0e4, 8)' in src
