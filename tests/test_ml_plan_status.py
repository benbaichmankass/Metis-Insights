"""ml_plan_status: the checker detects each planted defect, and the committed ML plan has no orphans."""

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "ml_plan_status", REPO / "scripts/ops/ml_plan_status.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_self_test_detects_planted_defects():
    assert m.self_test() == 0


def test_committed_plan_has_no_orphan_items():
    # An ORPHAN is a plan item whose row/unit is gone -- the exact way a plan item gets forgotten.
    # (STALE / CLOSED-OPEN move with ordinary work and are reported by the loop, not failed in CI.)
    plan = m.load_plan()
    res = m.analyse(plan, m.rows())
    orphans = [r["id"] + "->" + str(r["ref"]) for r in res if "ORPHAN" in r["flags"]]
    assert not orphans, orphans


def test_every_plan_item_has_a_close_condition_and_unique_id():
    plan = m.load_plan()
    ids = [i["id"] for i in plan["items"]]
    assert len(ids) == len(set(ids))
    assert all(i.get("close") and i.get("ref") for i in plan["items"])


def test_diversity_flags_concentration_and_fail_streak():
    plan = {
        "diversity": {"max_family_share": 0.6, "max_fail_streak": 3},
        "families": {"deep_sequence": {"history": ["FAIL", "FAIL", "FAIL"]}},
        "items": [
            {"id": "a", "state": "open", "family": "gbdt"},
            {"id": "b", "state": "open", "family": "gbdt"},
            {"id": "c", "state": "open", "family": "deep_sequence"},
            {"id": "d", "state": "open"},
        ],
    }
    findings, table = m.diversity(plan, {"x": "gbdt"})
    assert any(f.startswith("CONCENTRATION: gbdt") for f in findings), findings
    assert any(f.startswith("FAIL-STREAK: deep_sequence") for f in findings), findings
    assert any(f.startswith("NOTE 1 open plan items") for f in findings), findings
    assert table["ts_foundation_model"]["open_units"] == 0  # absent families are reported at 0, not omitted


def test_every_committed_trainer_maps_to_a_family():
    findings, _ = m.diversity(m.load_plan(), m.manifest_families())
    assert not [f for f in findings if f.startswith("UNMAPPED-TRAINER")], findings


def test_committed_plan_family_tags_are_known():
    findings, _ = m.diversity(m.load_plan(), m.manifest_families())
    assert not [f for f in findings if f.startswith("UNKNOWN-FAMILY")], findings


def test_family_none_marks_a_process_item_without_counting_or_flagging_it():
    plan = {
        "diversity": {"max_family_share": 0.6, "max_fail_streak": 3},
        "items": [
            {"id": "a", "state": "open", "family": "gbdt"},
            {"id": "b", "state": "open", "family": "none"},
        ],
    }
    findings, table = m.diversity(plan, {})
    assert not [f for f in findings if f.startswith(("UNKNOWN-FAMILY", "NOTE"))], findings
    assert sum(f["open_units"] for f in table.values()) == 1  # `none` is not counted toward any family
