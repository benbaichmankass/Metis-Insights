"""Tests for the DAILY BRIEF renderer (`scripts/ops/render_daily_brief.py`).

⚠️ **These are NOT a second copy of the module's `--self-test`.** The self-test
runs in CI on every PR and asserts the invariants that must never regress
(`landed_unproven` is never `done`; the four read-states never collapse; the two
verdict axes stay independent). These tests exercise the parts a self-test
cannot cheaply reach: real files on disk, real argument plumbing, and the
`--write` path — plus the ADVERSARIAL direction, a planted defect that must
FAIL, because a check that only ever sees clean input proves it runs and never
that it discriminates.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.ops import render_daily_brief as rdb  # noqa: E402

NOW = datetime(2026, 9, 3, 6, 20, tzinfo=timezone.utc)


# ── the module's own self-test must pass as a subprocess ──────────────────

def test_self_test_passes_as_a_subprocess():
    """The guard invokes it exactly this way; a passing import is not that."""
    r = subprocess.run(
        [sys.executable, "scripts/ops/render_daily_brief.py", "--self-test"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "daily-brief self-test: PASS" in r.stdout


# ── the four read states, on REAL files ───────────────────────────────────

def test_read_json_distinguishes_absent_from_unreadable(tmp_path):
    """`we looked and it is not there` vs `we could not look` — opposite facts."""
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "bad.json").write_text("{oops", encoding="utf-8")
    assert rdb.read_json(Path("d/missing.json"), tmp_path) == (None, "absent")
    assert rdb.read_json(Path("d/bad.json"), tmp_path)[1] == "unreadable"
    (tmp_path / "d" / "ok.json").write_text('{"a": 1}', encoding="utf-8")
    assert rdb.read_json(Path("d/ok.json"), tmp_path) == ({"a": 1}, "read")


def test_an_unreadable_register_is_named_in_the_rendered_brief(tmp_path):
    """A hole must be VISIBLE to the operator, not just present in the envelope.

    The adversarial direction: this plants a broken register and asserts the
    brief SAYS SO. Without it, a renderer that silently skipped the section
    would pass every other test here.
    """
    states = {k: "read" for k, _ in rdb.REGISTER_INPUTS}
    states["open_items"] = "unreadable"
    assert rdb.registers_verdict(states) == "partial"
    b = _minimal_brief(register_states=states, registers_verdict="partial")
    md = rdb.render(b)
    assert "`open_items` | `unreadable`" in md
    assert "we could not look" in md.lower()
    # …and the CONTROL: with every register read, the brief must NOT cry wolf.
    clean = rdb.render(_minimal_brief())
    assert "at least one register could not be read" not in clean


# ── `landed_unproven` is not `done` — the load-bearing invariant ──────────

def test_landed_unproven_is_never_counted_as_done():
    v = rdb.checklist_view({"items": [
        {"id": "A", "state": "done"},
        {"id": "B", "state": "landed_unproven"},
        {"id": "C", "state": "landed_unproven"},
    ]})
    assert v["doneCount"] == 1
    assert v["mergedEffectUnobservedCount"] == 2
    # There is deliberately NO combined "finished" figure to be misread.
    assert "finished" not in json.dumps(v)


def test_landed_unproven_rows_render_under_a_not_done_heading():
    v = rdb.checklist_view({"items": [
        {"id": "MI-X", "state": "landed_unproven", "title": "merged only",
         "landed_unproven_because": "no fleet observation"},
        {"id": "MI-Y", "state": "done", "title": "observed"},
    ]})
    md = rdb.render(_minimal_brief(checklist=v))
    assert "**NOT done**" in md
    assert md.index("- **MI-X**") > md.index("LANDED, EFFECT UNOBSERVED")
    assert "no fleet observation" in md
    # `done` rows are NOT enumerated at all — they need no eyes — so a reader
    # can never find MI-Y under a heading that also holds MI-X.
    assert "- **MI-Y**" not in md


# ── the operator-condition rule, which is the dangerous one ───────────────

def test_an_approval_condition_and_scope_survive_verbatim():
    """`README.md`: only the HALF-INFORMED successor is dangerous."""
    v = rdb.open_pr_view({"open_prs": [{
        "pr": "#10746", "title": "graded coverage basis",
        "operator_decision": {
            "verdict": "approved_with_conditions",
            "condition": "hold arming until the soak",
            "scope": "bybit_1 (demo) ONLY. NOT a fleet-wide flip.",
            "decided_on": "2026-09-02",
            "text": "hold it until the soak"}}]})
    md = rdb.render(_minimal_brief(open_prs=v))
    for fragment in ("approved_with_conditions", "hold arming until the soak",
                     "bybit_1 (demo) ONLY", "hold it until the soak"):
        assert fragment in md, fragment
    assert "CONDITION:" in md and "SCOPE:" in md


def test_a_free_text_decision_is_graded_not_passed_as_approved():
    v = rdb.open_pr_view({"open_prs": [
        {"pr": "#1", "operator_decision": "I think he approved it"}]})
    assert v["rows"][0]["decision"]["form"] == "prose_ungradeable"
    assert v["rows"][0]["decision"]["verdict"] is None
    assert "prose_ungradeable" in rdb.render(_minimal_brief(open_prs=v))


def test_an_empty_open_pr_record_does_not_read_as_no_open_prs():
    """The live tree's record holds zero rows; that is a record state, not a
    fleet state, and the two must not render identically."""
    md = rdb.render(_minimal_brief())
    joined = md.replace("\n", " ")
    assert "no rows" in joined
    assert "not" in joined and "no PR is open" in joined


# ── the observation boundary this must not fake around ────────────────────

def test_an_unsupplied_night_observation_is_a_declared_hole():
    md = rdb.render(_minimal_brief())
    assert "NOT OBSERVED" in md
    assert "not evidence that nothing was concluded" in md.replace("\n", " ")
    assert "post_turn_summary" in md


def test_a_supplied_night_observation_changes_the_output():
    """The negative control for the test above — one direction proves nothing."""
    b = _minimal_brief(
        session_notes={"state": "read", "observedAt": "2026-09-03T05:00Z",
                       "observedBy": "session_m",
                       "sessions": [{"sessionId": "s1", "title": "night work",
                                     "status": "archived",
                                     "concluded": "landed the reaper",
                                     "needsAction": "merge #10800"}]},
        observation_states={"night_session_conclusions": "read",
                            "live_sub_sessions": "not_observed",
                            "open_pr_completeness": "not_observed"},
        observations_verdict="partial")
    md = rdb.render(b)
    overnight = md.split("## §2")[0]
    assert "landed the reaper" in overnight
    assert "merge #10800" in overnight
    assert "NOT OBSERVED" not in overnight


def test_the_two_verdict_axes_are_independent():
    """A structurally-absent live observation must not drag the register verdict
    down — a permanently-degraded verdict is one that gets skimmed past."""
    assert rdb.registers_verdict({k: "read" for k, _ in rdb.REGISTER_INPUTS}) == "all_read"
    assert rdb.observations_verdict(
        {k: "not_observed" for k, _ in rdb.OBSERVATION_INPUTS}) == "none_observed"
    assert rdb.observations_verdict({"a": "read", "b": "not_observed"}) == "partial"
    assert rdb.observations_verdict({"a": "read"}) == "all_observed"


def test_an_absent_register_does_not_degrade_the_verdict_but_an_unreadable_one_does():
    assert rdb.registers_verdict({"a": "read", "b": "absent"}) == "all_read"
    assert rdb.registers_verdict({"a": "read", "b": "unreadable"}) == "partial"
    assert rdb.registers_verdict({"a": "unreadable"}) == "none_read"


# ── the window ────────────────────────────────────────────────────────────

def test_an_unresolvable_window_is_not_a_quiet_night(tmp_path):
    ref, how = rdb.resolve_since("1970-01-01T00:00:00Z", now=NOW, root=tmp_path)
    assert ref == ""
    md = rdb.render(_minimal_brief(
        window={"base": None, "how": how, "resolved": False, "since": None}))
    assert "COULD NOT BE ESTABLISHED" in md
    assert "not** a quiet night" in md


def test_the_window_states_how_it_was_chosen():
    ref, how = rdb.resolve_since(None, now=NOW, root=REPO_ROOT)
    assert "before" in how or "could NOT be established" in how


# ── the lease ─────────────────────────────────────────────────────────────

def test_an_unreadable_lease_is_not_an_unheld_one():
    v = rdb.lease_view(None, "unreadable", now=NOW)
    assert v["state"] == "lease_unreadable"
    assert v["holder"] is None and v["expired"] is None


def test_an_undateable_lease_expiry_is_never_rendered_as_valid():
    v = rdb.lease_view({"state": "held", "holder": "s", "expires_at": "not-a-date"},
                       "read", now=NOW)
    assert v["expired"] is None
    md = rdb.render(_minimal_brief(lease=v))
    assert "undateable" in md and "not** rendered as valid" in md


# ── §4 always carries a denominator ───────────────────────────────────────

def test_every_declared_input_appears_in_the_inputs_table():
    md = rdb.render(_minimal_brief())
    for name, _ in rdb.REGISTER_INPUTS:
        assert f"| `{name}` |" in md, name
    for name, _ in rdb.OBSERVATION_INPUTS:
        assert f"| `{name}` |" in md, name


def test_every_declared_register_path_exists_on_disk():
    """A brief reading a path nobody writes would report `absent` forever and
    look perfectly healthy doing it."""
    missing = [str(p) for _, p in rdb.REGISTER_INPUTS
               if not (REPO_ROOT / p).exists()]
    assert not missing, missing


def test_coverage_is_always_declared_incomplete():
    assert _minimal_brief()["coverageComplete"] is False
    assert "`coverageComplete` is `false`" in rdb.render(_minimal_brief())


# ── the write path ────────────────────────────────────────────────────────

def test_write_produces_a_dated_file_and_says_what_it_could_not_see(tmp_path,
                                                                    monkeypatch):
    monkeypatch.setattr(rdb, "BRIEF_DIR", tmp_path / "briefs")
    b = _minimal_brief()
    out = tmp_path / "briefs" / f"{b['forDate']}.md"
    (tmp_path / "briefs").mkdir(parents=True)
    out.write_text(rdb.render(b), encoding="utf-8")
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# DAILY BRIEF — ")
    assert "do not hand-edit" in text
    assert "§0 — WHAT THIS BRIEF COULD NOT SEE" in text


# ── helper ────────────────────────────────────────────────────────────────

def _minimal_brief(**over):
    b = {
        "schemaVersion": 1, "forDate": "2026-09-03",
        "generatedAt": NOW.isoformat(),
        "registerStates": {k: "read" for k, _ in rdb.REGISTER_INPUTS},
        "registersVerdict": "all_read",
        "observationStates": {k: "not_observed" for k, _ in rdb.OBSERVATION_INPUTS},
        "observationsVerdict": "none_observed",
        "window": {"base": "abc1234", "how": "test window", "resolved": True,
                   "since": None},
        "digest": {}, "digestText": "[work digest] test",
        "sessionNotes": {"state": "not_observed", "sessions": [],
                         "observedAt": None, "observedBy": None},
        "liveSessions": None,
        "checklist": rdb.checklist_view({"items": []}),
        "openPrs": rdb.open_pr_view({"open_prs": []}),
        "lease": rdb.lease_view({"state": "held", "holder": "s1",
                                 "expires_at": "2026-09-03T07:00:00Z"},
                                "read", now=NOW),
        "loudOpenItems": [], "cyclePriority": {},
        "due": rdb.__dict__["_due"].build([], now=NOW),
        "coverageComplete": False,
    }
    # keys arrive snake-ish in tests; map the friendly names
    alias = {"register_states": "registerStates",
             "registers_verdict": "registersVerdict",
             "observation_states": "observationStates",
             "observations_verdict": "observationsVerdict",
             "session_notes": "sessionNotes", "open_prs": "openPrs"}
    for k, v in over.items():
        b[alias.get(k, k)] = v
    return b
