"""scripts/ops/exit_cell_runner.py -- the runner for MD-SOAK-EXIT-CELL-PATHB.

Fixtures are built on a tmp root (never the real tree) and reuse the passing
row from tests/test_mandate_exit_cell.py, so the resolver clauses are tested
there and only the runner's own behaviour is tested here: cell selection, the
narrow edit, the matrix flip, the firing record, and NEEDS-DATA filing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ops"))
sys.path.insert(0, str(REPO / "tests"))
import exit_cell_runner as er  # noqa: E402
import mandate_resolver as mr  # noqa: E402
import pipeline  # noqa: E402
import test_mandate_exit_cell as base  # noqa: E402

LEG = base.LEG
STRATEGIES = """strategies:
  other_leg:
    execution: live
    atr_stop_mult: 9.0  # untouched
  spy_pullback_1h:
    execution: live
    # a comment that must survive
    atr_stop_mult: 2.5  # old note
    trail_mult: 5.0
  after_leg:
    atr_stop_mult: 7.0
"""


def _root(tmp_path, rows=None, cur_text=STRATEGIES, **kw):
    root = base._build(tmp_path, rows=rows, granted=True, **kw)
    (root / "config/strategies.yaml").write_text(cur_text)
    (root / er.MATRIX_REL).parent.mkdir(parents=True, exist_ok=True)
    matrix = {"rows": [{"strategy": LEG, "bracket_geometry": {
        "status": "passed_unshipped", "ref": "old ref", "base_is": None, "base_oos": None,
        "timeout_binding": "MEASURED 2026-08-29: leg CLEAN -- the force-close is inert here"}}]}
    (root / er.MATRIX_REL).write_text(json.dumps(matrix, indent=1, ensure_ascii=False) + "\n")
    return root


def test_pick_cells_follows_the_b4_rule_and_ignores_tp_and_timeout_cells(tmp_path):
    rows = [dict(base.ROW, cell="sm2", wf_wins_effective=5, d_net_r=10.0),
            dict(base.ROW, cell="sm1.5", wf_wins_effective=5, d_net_r=28.0),
            dict(base.ROW, cell="sm3", wf_wins_effective=4, d_net_r=99.0),           # fewer folds
            dict(base.ROW, cell="tp6_sm1.5", axis="tp+stop", tp_r=6.0, wf_wins_effective=6, d_net_r=50.0),
            dict(base.ROW, cell="sm1.5_to24", timeout=24, wf_wins_effective=6, d_net_r=60.0)]
    root = _root(tmp_path, rows=rows)
    assert er.pick_cells(root)[LEG]["cell"] == "sm1.5"   # wf ties -> higher d_net_r


def test_a_nan_ranked_cell_never_becomes_the_pick(tmp_path):
    rows = [dict(base.ROW, cell="sm1.5", wf_wins_effective=5, d_net_r=28.0),
            dict(base.ROW, cell="sm2", wf_wins_effective=float("nan"), d_net_r=float("inf"))]
    assert er.pick_cells(_root(tmp_path, rows=rows))[LEG]["cell"] == "sm1.5"


def test_a_fire_edits_exactly_one_key_and_nothing_else(tmp_path):
    root = _root(tmp_path)
    out = er.run(root)
    assert [e["cell"] for e in out["fire"]] == ["sm1.5"] and not out["refuse"]
    assert out["fire"][0]["timeout_binding"]["audit"] == "clean"
    written = er.apply_fire(out["fire"][0], root, "2026-10-01", "run-1")
    text = (root / "config/strategies.yaml").read_text()
    assert "atr_stop_mult: 1.5  # MD-SOAK-EXIT-CELL-PATHB 2026-10-01: cell sm1.5" in text
    assert "was 2.5" in text
    # every other line is byte-identical
    old, new = STRATEGIES.splitlines(), text.splitlines()
    assert len(old) == len(new)
    assert [i for i, (a, b) in enumerate(zip(old, new)) if a != b] == [old.index("    atr_stop_mult: 2.5  # old note")]
    m = json.loads((root / er.MATRIX_REL).read_text())["rows"][0]["bracket_geometry"]
    assert m["status"] == "shipped" and m["base_oos"] == 58 and "run-1" in m["ref"]
    fr = json.loads((root / written[2]).read_text())
    assert fr["mandate"] == mr.EXIT_CELL_MANDATE_ID and fr["config_edit"]["to"] == 1.5
    assert sorted(written) == sorted([mr.STRATEGIES_REL, er.MATRIX_REL, written[2]])


def test_a_contaminated_leg_never_fires_even_when_it_is_the_best_cell(tmp_path):
    root = _root(tmp_path, binding=True)
    out = er.run(root)
    assert not out["fire"] and out["refuse"][0]["clause"] == "R-TIMEOUT-BINDING"


def test_already_declared_is_a_noop_and_writes_nothing(tmp_path):
    root = _root(tmp_path, cur_text=STRATEGIES.replace("2.5", "1.5"))
    out = er.run(root)
    assert not out["fire"] and len(out["noop"]) == 1


def test_a_refused_best_cell_is_not_replaced_by_the_second_best(tmp_path):
    rows = [dict(base.ROW, cell="sm1.5", wf_wins_effective=6, d_max_dd=9.0),      # best, over the dd cap
            dict(base.ROW, cell="sm2", wf_wins_effective=5, d_max_dd=1.0)]        # would pass
    out = er.run(_root(tmp_path, rows=rows))
    assert not out["fire"] and out["refuse"][0]["cell"] == "sm1.5"


def test_edit_refuses_when_it_cannot_prove_what_it_replaces():
    with pytest.raises(ValueError, match="declares no atr_stop_mult"):
        er.edit_strategies_text("strategies:\n  x:\n    execution: live\n", "x", 2.5, 1.5, "n")
    with pytest.raises(ValueError, match="resolver read"):
        er.edit_strategies_text(STRATEGIES, LEG, 3.0, 1.5, "n")
    with pytest.raises(ValueError, match="block not found"):
        er.edit_strategies_text(STRATEGIES, "nope", 2.5, 1.5, "n")


def test_matrix_flip_round_trips_formatting(tmp_path):
    root = _root(tmp_path)
    before = (root / er.MATRIX_REL).read_text()
    assert er.flip_matrix(before, LEG, {}, "x").count("\n") == before.count("\n")
    with pytest.raises(ValueError):
        er.flip_matrix(before, "nope", {}, "x")


def test_needs_data_files_one_row_per_clause_and_dedupes(tmp_path):
    store = tmp_path / "pl"
    e = lambda leg: {"leg": leg, "cell": "sm2", "detail": "d", "result": {"data_task": {"what": "w", "clears_when": "c"}}}  # noqa: E731
    first = er.file_needs_data([e("a"), e("b")], "R-BASE-N", "session_x", store)
    assert first and len(pipeline.load(store).items) == 1
    assert er.file_needs_data([e("c")], "R-BASE-N", "session_x", store) is None   # open row names mandate + clause
    assert er.file_needs_data([e("c")], "R-FOLDS", "session_x", store)            # a different clause is a new cause
    assert len(pipeline.load(store).items) == 2


def test_real_tree_never_fires_on_a_leg_outside_the_soak_rosters():
    out = er.run(REPO)
    accts = yaml.safe_load((REPO / "config/accounts.yaml").read_text())["accounts"]
    for e in out["fire"]:
        on = [a for a, c in accts.items() if e["leg"] in (c.get("strategies") or [])]
        assert on and all(a in ("bybit_1", "alpaca_paper", "ib_paper") for a in on)
