"""The system-review checklist: derived item list, uncollapsed statuses, and a
`done` that cannot be claimed without evidence.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "src_checklist", ROOT / "scripts/ops/system_review_checklist.py"
)
cl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cl)


def test_item_list_is_derived_from_the_tuple_ci_enforces():
    """A hand-typed list drifts. SKILL.md's prose already says TEN while the
    enforced tuple holds thirteen -- so the checklist reads the field."""
    from_renderer = cl.coverage_keys()
    ids = {i["id"] for i in cl.canonical_items() if i["kind"] == "coverage"}
    assert ids == set(from_renderer)
    assert len(from_renderer) >= 13, "the enforced tuple shrank -- check before accepting"


def test_every_enforced_coverage_key_is_an_item():
    ids = {i["id"] for i in cl.canonical_items()}
    for k in cl.coverage_keys():
        assert k in ids, f"{k} is enforced by CI but absent from the checklist"


def test_subreviews_are_broken_into_DERIVED_sub_items():
    """Operator 2026-08-31: a single opaque `performance_review` row cannot show
    which half of it was skipped. Sub-items come from each sub-review's own
    response template, not from a typed list."""
    ids = {i["id"] for i in cl.canonical_items()}
    for k in ("consolidated_report", "operator_ping"):
        assert k in ids
    for review in ("health_review", "performance_review", "ml_review"):
        subs = [i for i in ids if i.startswith(review + ".")]
        assert len(subs) >= 5, f"{review} expanded into only {len(subs)} sub-items"
        assert review not in ids, "the opaque parent row must be gone"
    # spot-check that they really came from the schemas
    assert "performance_review.trade_decision_grades" in ids
    assert "ml_review.promotion_recommendations" in ids


def test_burndown_counts_closed_rows_and_never_retriages_them():
    """The metric is CLOSING, not looking. Resolved rows are history, not work."""
    b = cl.backlog_burndown()
    assert b["open_now"] > 0
    assert b["by_month"], "no months parsed -- the probe is broken, not the data"
    for m in b["by_month"]:
        assert m["net"] == m["opened"] - m["closed"]
        assert m["closed"] >= 0
    total_closed = sum(m["closed"] for m in b["by_month"])
    assert total_closed > 0, "a backlog with zero closures would mean the probe missed them"


def test_review_is_not_complete_while_anything_is_outstanding():
    state = {"items": {i["id"]: {"status": "done", "evidence": "x"}
                       for i in cl.canonical_items()}}
    done, out = cl.verdict(state)
    assert done and not out

    one = dict(state["items"])
    one["soak_status"] = {"status": "in_progress", "evidence": "partial"}
    done2, out2 = cl.verdict({"items": one})
    assert not done2 and "soak_status" in out2


def test_done_without_evidence_is_refused():
    """An unevidenced tick is exactly what this checklist exists to stop."""
    state = {"items": {i["id"]: {"status": "done", "evidence": "x"}
                       for i in cl.canonical_items()}}
    state["items"]["execution_capture"] = {"status": "done", "evidence": "   "}
    done, out = cl.verdict(state)
    assert not done
    assert any("done without evidence" in o for o in out)


def test_n_a_without_a_reason_is_refused():
    state = {"items": {i["id"]: {"status": "done", "evidence": "x"}
                       for i in cl.canonical_items()}}
    state["items"]["operator_ping"] = {"status": "n_a", "evidence": ""}
    done, out = cl.verdict(state)
    assert not done and any("n_a without a reason" in o for o in out)


def test_missing_item_defaults_to_not_started_not_to_done():
    """An absent record must never read as complete."""
    done, out = cl.verdict({"items": {}})
    assert not done
    assert len(out) == len(cl.canonical_items())


def test_statuses_are_not_collapsed():
    """not_started / in_progress / blocked are three different facts."""
    assert set(cl.STATUSES) == {"not_started", "in_progress", "blocked", "done", "n_a"}
    assert len(set(cl._GLYPH.values())) == len(cl.STATUSES), "each status renders distinctly"


def test_the_committed_state_file_parses_and_matches_the_item_set():
    """The live checklist must not silently carry a stale or partial item set."""
    p = ROOT / "docs/claude/system-review-checklist.json"
    state = json.loads(p.read_text(encoding="utf-8"))
    ids = {i["id"] for i in cl.canonical_items()}
    unknown = set(state.get("items", {})) - ids
    assert not unknown, f"checklist carries items that are not in the mandate: {unknown}"


def test_render_shows_every_item_and_a_verdict():
    p = ROOT / "docs/claude/system-review-checklist.json"
    out = cl.render(json.loads(p.read_text(encoding="utf-8")))
    for i in cl.canonical_items():
        assert i["label"] in out, f"{i['label']} missing from the rendered chart"
    assert "REVIEW IS" in out
    assert "Notes" in out, "the operator asked for a notes row; it must always render"
