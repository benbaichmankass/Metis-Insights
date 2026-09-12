"""A shared-register merge can revert a FIELD while every row survives.

The registers are edited by every lane concurrently, so they conflict
constantly, and the proof everyone reaches for is a **union by id set**: show
the ids on both sides all survive and call the merge clean.

**That proof is blind to a dropped field.** The id is present on both sides, the
sets match, the proof passes — and a key one side added has quietly gone.

MEASURED on the real register while writing this guard (population: the live
`docs/claude/work/SESSIONS.json` at `origin/main`, 231 rows). Deleting ONE key
(`observed_by`) from one real row:

    union-by-id proof : base 231 ids, head 231 ids, identical=True   -> CLEAN
    this guard        : FIELD_LOSS ... 'observed_by' ... exit 1      -> RED

Observed live on 2026-09-11/12: MI-277's branch carried a pre-tick
`SESSIONS.json`, and resolving only the marked conflicts would have reverted two
manager observation write-backs through lines git merges without complaint. The
sibling `BL-20260911-A-SHARED-REGISTER-LOSES-ITS-TOP-LEVEL-KEYS-TO-A-LINE-LEVEL-MERGE-WHILE-ITS-ROWS-SURVIVE`
is the same mechanism on the top-level keys.

⚠️ `scripts/ops/merge_json_register.py` already REFUSES this case — when it
runs. It is a **client-side** merge driver, registered only by
`install_merge_driver.sh` in that clone, and GitHub's servers never run it.
Measured in a fresh sub-session container: `git config --get
merge.jsonregister.driver` returns nothing. So the guard grades the ARTIFACT.

Run: ``python3 -m pytest tests/test_register_field_loss.py``
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "ci" / "check_register_field_loss.py"


def _load():
    spec = importlib.util.spec_from_file_location("_reg_field_loss", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = _load()

# A real row shape: an id, the lane's own fields, and a manager write-back.
ROW = {"session_id": "s1", "title": "engineering lane", "confirmed_at": "c",
       "observed_2026_09_12T0152Z": "the manager's write-back"}


def _doc(rows, **top):
    d = {"schema_version": 1, "updated_at": "t", "sessions": rows}
    d.update(top)
    return d


def _cmp(base, head):
    return G.compare(base, head, "sessions", "session_id", "R.json")


def test_self_test_passes():
    ok, fails = G._self_test(quiet=True)
    assert ok, f"register-field-loss self-test failures: {fails}"


# ── the MI-277 case ─────────────────────────────────────────────────────────

def test_a_row_that_keeps_its_id_and_loses_a_key_is_caught():
    head = _doc([{k: v for k, v in ROW.items() if k != "observed_2026_09_12T0152Z"}])
    v = _cmp(_doc([dict(ROW)]), head)
    assert [f["kind"] for f in v["findings"]] == [G.FIELD_LOSS]
    assert v["findings"][0]["key"] == "observed_2026_09_12T0152Z"


def test_the_union_by_id_proof_is_blind_to_exactly_this():
    """The control that makes the guard worth having, asserted rather than told."""
    base = _doc([dict(ROW)])
    head = _doc([{k: v for k, v in ROW.items() if k != "observed_2026_09_12T0152Z"}])
    base_ids = {r["session_id"] for r in base["sessions"]}
    head_ids = {r["session_id"] for r in head["sessions"]}
    assert base_ids == head_ids, "the union-by-id proof passes…"
    assert _cmp(base, head)["findings"], "…and the loss is real"


# ── the controls: ordinary work must stay quiet ─────────────────────────────

@pytest.mark.parametrize("head,why", [
    (_doc([dict(ROW, observed_2026_09_12T0152Z="a NEWER observation")]),
     "a CHANGED value is ordinary work"),
    (_doc([dict(ROW, lane_note="new")]), "an ADDED key is ordinary work"),
    (_doc([dict(ROW)]), "an identical document"),
    (_doc([dict(ROW), {"session_id": "s2", "title": "a new lane"}]),
     "an APPENDED row is the normal register operation"),
])
def test_ordinary_edits_are_not_findings(head, why):
    assert _cmp(_doc([dict(ROW)]), head)["findings"] == [], why


# ── the three losses are graded SEPARATELY ──────────────────────────────────

def test_a_lost_top_level_key_is_its_own_kind():
    v = _cmp(_doc([dict(ROW)], extra="x"), _doc([dict(ROW)]))
    assert [f["kind"] for f in v["findings"]] == [G.TOPLEVEL_LOSS]
    assert v["findings"][0]["id"] is None, "not attributed to a row"


def test_a_lost_row_is_row_loss_not_a_pile_of_field_losses():
    v = _cmp(_doc([dict(ROW)]), _doc([]))
    assert [f["kind"] for f in v["findings"]] == [G.ROW_LOSS]


# ── three read states, never collapsed ──────────────────────────────────────

@pytest.mark.parametrize("base", [
    {"sessions": "not a list"},
    {"no_array_at_all": 1},
    [],
])
def test_an_unparseable_side_is_unreadable_never_clean(base):
    v = _cmp(base, _doc([]))
    assert v["state"] == G.UNREADABLE
    assert v["findings"] == [], "and it invents no findings — we did not look"


def test_a_row_with_no_id_does_not_crash_the_grader():
    assert _cmp(_doc([{"title": "no id"}]), _doc([]))["findings"] == []


def test_no_base_is_not_a_pass():
    assert "not a pass" in G.check(None)["summary"]


# ── THE OVERRIDE IS VERIFIED, NOT PRESENCE-ONLY ─────────────────────────────

def _one_field_loss():
    head = _doc([{k: v for k, v in ROW.items() if k != "observed_2026_09_12T0152Z"}])
    return _cmp(_doc([dict(ROW)]), head)["findings"]


def test_a_true_declaration_excuses_its_own_loss():
    decl = [{"file": "R.json", "id": "s1",
             "keys": ["observed_2026_09_12T0152Z"], "declared_in": "x.json"}]
    remaining, excused, phantom = G.apply_removals(_one_field_loss(), decl)
    assert not remaining and len(excused) == 1 and not phantom


@pytest.mark.parametrize("decl,why", [
    ([{"file": "R.json", "id": "s1", "keys": ["something_else"],
       "declared_in": "x.json"}], "wrong key"),
    ([{"file": "OTHER.json", "id": "s1", "keys": ["observed_2026_09_12T0152Z"],
       "declared_in": "x.json"}], "wrong file"),
    ([{"file": "R.json", "id": "s2", "keys": ["observed_2026_09_12T0152Z"],
       "declared_in": "x.json"}], "wrong row"),
])
def test_a_declaration_that_does_not_match_excuses_nothing_and_is_a_phantom(decl, why):
    remaining, excused, phantom = G.apply_removals(_one_field_loss(), decl)
    assert len(remaining) == 1 and not excused, why
    assert len(phantom) == 1, (
        "a declaration that names no real removal is a FALSE statement about "
        "the diff — that is what stops the file being a blanket silencer")


def test_a_declaration_with_no_loss_at_all_is_a_phantom():
    decl = [{"file": "R.json", "id": "s1", "keys": ["k"], "declared_in": "x.json"}]
    remaining, _, phantom = G.apply_removals([], decl)
    assert not remaining and len(phantom) == 1


def test_a_whole_row_removal_needs_an_empty_key_list():
    row_loss = _cmp(_doc([dict(ROW)]), _doc([]))["findings"]
    assert not G.apply_removals(
        row_loss, [{"file": "R.json", "id": "s1", "keys": [],
                    "declared_in": "x.json"}])[0]
    assert G.apply_removals(
        row_loss, [{"file": "R.json", "id": "s1", "keys": ["title"],
                    "declared_in": "x.json"}])[0], (
        "a KEYED declaration must not excuse the loss of the whole row")


def test_a_malformed_declaration_file_silences_nothing(tmp_path):
    d = tmp_path / ".github" / "register-removals"
    d.mkdir(parents=True)
    (d / "broken.json").write_text("{not json", encoding="utf-8")
    assert G.load_removals(tmp_path) == [], (
        "a broken override is ignored rather than trusted, so the loss it was "
        "meant to excuse is still reported")


# ── the live entry point, on the REAL register ──────────────────────────────

def test_the_real_register_is_clean_on_this_tree():
    """Positive control for the live path — and it proves the probe can be
    quiet, which is what makes its red mean something."""
    verdict = G.check("origin/main")
    assert verdict["ok"], verdict["summary"]


def test_planting_the_defect_on_the_real_register_turns_it_red(tmp_path):
    """The whole point, run against the real file rather than a fixture.

    A copy of the live `SESSIONS.json` with ONE key removed from ONE real row
    must be caught — while the id sets still match exactly.
    """
    live = REPO / "docs" / "claude" / "work" / "SESSIONS.json"
    doc = json.loads(live.read_text(encoding="utf-8"))
    victim = next((r for r in doc["sessions"]
                   if any(k.startswith(("observed_", "manager_observation_"))
                          for k in r)), None)
    if victim is None:
        pytest.skip("no row on this tree carries a manager write-back to remove")
    key = next(k for k in victim
               if k.startswith(("observed_", "manager_observation_")))

    before = json.loads(live.read_text(encoding="utf-8"))
    after = json.loads(live.read_text(encoding="utf-8"))
    for r in after["sessions"]:
        if r["session_id"] == victim["session_id"]:
            r.pop(key)

    assert ({r["session_id"] for r in before["sessions"]}
            == {r["session_id"] for r in after["sessions"]}), (
        "the union-by-id proof still passes — that is the premise")

    v = G.compare(before, after, "sessions", "session_id",
                  "docs/claude/work/SESSIONS.json")
    assert [f["kind"] for f in v["findings"]] == [G.FIELD_LOSS]
    assert v["findings"][0]["key"] == key


# ── the VERDICT ASSEMBLY, which a mutation run proved was unpinned ───────────
# While `ok` was inlined in `check()`, the only way to reach it was the live
# filesystem path — so deleting `phantom` from the failure condition, which
# turns the verified override back into a blanket silencer, passed the whole
# suite. It is a pure function now, and these are its controls.

def _clean_result():
    return [{"path": "R.json", "state": G.COMPARED, "findings": [], "why": ""}]


def _loss_result():
    return [{"path": "R.json", "state": G.COMPARED,
             "findings": _one_field_loss(), "why": ""}]


def _true_decl():
    return [{"file": "R.json", "id": "s1",
             "keys": ["observed_2026_09_12T0152Z"], "declared_in": "x.json"}]


def test_verdict_is_ok_on_a_clean_comparison():
    assert G.verdict_of(_clean_result(), [])["ok"]


def test_verdict_is_not_ok_on_a_real_loss():
    assert not G.verdict_of(_loss_result(), [])["ok"]


def test_verdict_is_ok_when_the_loss_carries_its_true_declaration():
    assert G.verdict_of(_loss_result(), _true_decl())["ok"]


def test_a_phantom_declaration_alone_fails_the_run_over_a_clean_tree():
    """The term the mutation run found unpinned. Without it, an inaccurate
    override file sits in the tree forever and the next author inherits a
    silencer rather than a record."""
    v = G.verdict_of(_clean_result(), _true_decl())
    assert not v["ok"] and len(v["phantom"]) == 1


def test_an_unreadable_register_fails_rather_than_passing_quietly():
    v = G.verdict_of(
        [{"path": "R.json", "state": G.UNREADABLE, "findings": [], "why": ""}], [])
    assert not v["ok"]
