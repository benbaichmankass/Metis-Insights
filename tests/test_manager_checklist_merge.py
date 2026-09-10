"""The `state`/`status` merge owner, and the checklist route built on it (MI-238).

`MANAGER-CHECKLIST.json` carries TWO competing status fields. MEASURED
2026-09-10 over all 244 items on `origin/claude/mgr-checklist-schema-20260910T0718`
(population: every entry in `items`): 179 carry `state`, 83 carry `status`, 18
carry both and **13 of those DISAGREE**; 65 carry `status` and no `state`.

The design rule these tests defend is that the merge has **exactly one owner**
(`manager_status.effective_state`). A second implementation — in this route, in
a report, in the SPA's TypeScript — becomes a second definition of an item's
status, free to drift from the one `scripts/ops/manager_view.py` and the
manager guards read.

⚠️ Several tests carry POSITIVE CONTROLS deliberately: they assert a probe
finds the healthy case *before* asserting it reports the bad one, because a
test that only ever sees the failure cannot distinguish "correctly reported"
from "reports everything". The freshness tests do this in both directions —
a clean tree must produce ZERO warnings, or "no warnings" would be evidence of
nothing.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.runtime import manager_status as ms
from src.web.api.main import app
from src.web.api.routers import work as wk


# ── the merge owner ──────────────────────────────────────────────────────────

def test_every_declared_basis_is_reachable():
    """A contract naming a basis nothing can produce is a dead claim."""
    produced = {
        ms.effective_state(item).basis
        for item in (
            {"state": "in_flight"},                      # state_only
            {"status": "done"},                          # status_only
            {"state": "done", "status": "done"},         # agree
            {"state": "in_flight", "status": "done"},    # disagree
            {"id": "MI-x"},                              # undeclared
        )
    }
    assert produced == set(ms.STATUS_BASES)


def test_state_wins_a_disagreement_and_the_disagreement_is_reported():
    """Picking a value must not silently discard the fact that they differ."""
    eff = ms.effective_state({"state": "in_flight", "status": "done"})
    assert eff.value == "in_flight", "state wins — it is what every other consumer reads"
    assert eff.basis == ms.STATUS_BASIS_DISAGREE
    assert eff.disagrees is True
    # Both declarations survive: `state` winning is a rule for picking a value,
    # NOT a finding that `status` was wrong, so neither field is discarded.
    assert (eff.state, eff.status) == ("in_flight", "done")


def test_a_status_only_row_is_not_invisible():
    """The regression this whole change exists to fix.

    65 of 244 rows carry only `status`. Reading `item["state"]` directly — what
    `build_sections` used to do — drops every one of them.
    """
    eff = ms.effective_state({"status": "queued"})
    assert eff.value == "queued"
    assert eff.basis == ms.STATUS_BASIS_STATUS_ONLY

    items = [{"id": "A", "state": "queued"}, {"id": "B", "status": "queued"}]
    sections = {s.key: s for s in ms.build_sections(items, None)}
    assert len(sections["next"].lines) == 2, (
        "a `status`-only row must reach the section its status names")


def test_an_undeclared_row_never_renders_as_a_declared_status():
    """`undeclared` is measured at ZERO today; a zero state is not an absent one."""
    eff = ms.effective_state({"id": "MI-x"})
    assert eff.value is None and eff.basis == ms.STATUS_BASIS_UNDECLARED
    assert eff.in_declared_vocabulary is False
    line = ms._labelled_line({"id": "MI-x", "title": "t"}, None)
    assert "no status declared" in line
    assert "None" not in line, "a null status must not render as the string None"


def test_an_undeclared_vocabulary_value_passes_through_verbatim():
    """Guessing that `superseded` means `dropped` would invent a decision."""
    eff = ms.effective_state({"status": "superseded"})
    assert eff.value == "superseded", "never mapped into a declared bucket"
    assert eff.in_declared_vocabulary is False
    # positive control: a declared value grades the other way, so the flag is
    # reporting the vocabulary rather than reporting everything.
    assert ms.effective_state({"state": "done"}).in_declared_vocabulary is True


def test_the_files_own_vocabulary_wins_over_the_constant():
    """Field beats comment: the file declares its states, this module guesses."""
    assert ms._declared_vocabulary({"states": {"weird": "x"}}) == ("weird",)
    assert ms.effective_state(
        {"state": "weird"}, vocabulary=("weird",)).in_declared_vocabulary is True
    assert ms._declared_vocabulary({}) == ms.DECLARED_STATE_VOCABULARY


def test_bases_partition_the_population():
    """An arithmetic cross-check, which catches what proofreading does not."""
    items = [{"state": "done"}, {"status": "done"}, {"state": "a", "status": "a"},
             {"state": "a", "status": "b"}, {}, {"state": "queued"}]
    counted = {b: 0 for b in ms.STATUS_BASES}
    for item in items:
        counted[ms.effective_state(item).basis] += 1
    assert sum(counted.values()) == len(items)


def test_a_non_mapping_item_grades_undeclared_rather_than_raising():
    assert ms.effective_state("not a dict").basis == ms.STATUS_BASIS_UNDECLARED


def test_sectioned_states_are_derived_not_restated():
    """A section gaining a state must not leave the population line stale."""
    assert ms._SECTIONED_STATES == frozenset(
        ms._IN_FLIGHT + ms._BLOCKED + ms._RECENTLY_DONE + ms._NEXT)


def test_the_counts_section_states_what_the_sections_do_not_cover():
    """Four sections that cover part of the file must not read as the whole."""
    items = [{"id": "A", "state": "in_flight"}, {"id": "B", "status": "superseded"}]
    counts = ms.build_sections(items, None)[0].lines
    joined = "\n".join(counts)
    assert "1 of 2 item(s) have a status NO section below covers" in joined
    assert "superseded" in joined
    # positive control: with nothing unsectioned the line must be ABSENT, or
    # its presence would be evidence of nothing.
    clean = "\n".join(ms.build_sections([{"id": "A", "state": "done"}], None)[0].lines)
    assert "NO section below covers" not in clean


def test_a_disagreement_is_flagged_in_the_rendered_line():
    line = ms._labelled_line(
        {"id": "MI-1", "title": "t", "state": "in_flight", "status": "done"}, None)
    assert "in_flight" in line and "⚠️status=done" in line


# ── the per-file as-of stamp ────────────────────────────────────────────────

def _git(mapping):
    def run(args):
        for key, value in mapping.items():
            if key in " ".join(args):
                return value
        return None, "unmatched"
    return run


def test_file_commit_reads_a_known_commit_and_its_age():
    fc = ms.read_file_commit(
        "x.json",
        git=_git({"log": ("abc123def4567\t2026-09-10T06:00:00Z", None),
                  "status": ("", None)}),
        now=__import__("datetime").datetime(2026, 9, 10, 9, 0,
                                            tzinfo=__import__("datetime").timezone.utc),
    )
    assert fc.state == ms.FILE_COMMIT_KNOWN
    assert fc.age_hours == pytest.approx(3.0)
    assert fc.dirty is False


def test_an_empty_git_log_is_uncommitted_and_an_unreadable_one_is_unknown():
    """*git answered and had nothing* and *we could not look* are opposites."""
    empty = ms.read_file_commit("x.json", git=_git({"log": ("", None)}))
    assert empty.state == ms.FILE_COMMIT_UNCOMMITTED

    broken = ms.read_file_commit("x.json", git=lambda a: (None, "boom"))
    assert broken.state == ms.FILE_COMMIT_UNKNOWN


def test_unreadable_cleanliness_is_none_never_false():
    """A fabricated `False` would stamp a trustworthy time onto other bytes."""
    fc = ms.read_file_commit(
        "x.json",
        git=_git({"log": ("abc\t2026-09-10T06:00:00Z", None),
                  "status": (None, "git failed")}),
    )
    assert fc.dirty is None


def test_every_declared_file_commit_state_is_reachable():
    assert set(ms.FILE_COMMIT_STATES) == {
        ms.read_file_commit("x", git=_git({"log": ("a\t2026-09-10T06:00:00Z", None),
                                           "status": ("", None)})).state,
        ms.read_file_commit("x", git=_git({"log": ("", None)})).state,
        ms.read_file_commit("x", git=lambda a: (None, "boom")).state,
    }


# ── the route ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_checklist_cache():
    wk._checklist_cache = None
    yield
    wk._checklist_cache = None


def _checklist(tmp_path, doc):
    root = tmp_path / "repo"
    (root / "docs" / "claude" / "work").mkdir(parents=True)
    (root / ms.CHECKLIST_RELPATH).write_text(json.dumps(doc), encoding="utf-8")
    return root


def test_route_serves_the_checklist_through_the_one_owner(tmp_path, monkeypatch):
    root = _checklist(tmp_path, {
        "as_of": "2026-09-10T07:00:00Z", "cycle": "CY-x",
        "states": {"done": "merged AND observed"},
        "items": [
            {"id": "MI-1", "title": "t", "state": "in_flight", "status": "done",
             "some_free_form_prose_key": "must survive verbatim"},
            {"id": "MI-2", "title": "u", "status": "superseded"},
        ],
    })
    monkeypatch.setattr(wk, "repo_root", lambda: root)
    with TestClient(app) as client:
        body = client.get("/api/bot/work/checklist").json()

    assert body["present"] is True and body["asOf"] == "2026-09-10T07:00:00Z"
    assert body["summary"]["total"] == 2
    assert body["summary"]["disagreeing"] == 1
    assert body["summary"]["statusOnly"] == 1
    assert body["summary"]["unsectioned"] == 1

    rows = {r["id"]: r for r in body["items"]}
    assert rows["MI-1"]["status"] == {
        "value": "in_flight", "basis": "disagree", "declaredState": "in_flight",
        "declaredStatus": "done", "disagrees": True,
        "inDeclaredVocabulary": False,
        "note": wk._STATUS_BASIS_NOTES[ms.STATUS_BASIS_DISAGREE],
    }
    # Free-form keys are the detail the page exists to show; an allowlist here
    # would silently drop exactly them.
    assert rows["MI-1"]["fields"]["some_free_form_prose_key"] == \
        "must survive verbatim"
    # The file's OWN vocabulary is served, not a copy of it.
    assert body["declaredStates"] == {"done": "merged AND observed"}


def test_route_never_re_derives_the_merge():
    """The design rule, asserted rather than trusted.

    If the route grew its own `item["state"]` read it would become a second
    definition of an item's status. The merge is `effective_state`'s alone.
    """
    import inspect
    src = inspect.getsource(wk._checklist_item) + inspect.getsource(wk._checklist_summary)
    for forbidden in ('item.get("state")', 'item.get("status")',
                      'item["state"]', 'item["status"]'):
        assert forbidden not in src, (
            f"{forbidden} re-derives the merge the route must delegate")
    assert "effective_state" in src


def test_bases_in_the_summary_ship_with_explicit_zeros(tmp_path, monkeypatch):
    """A key that vanishes makes a consumer branch on absence."""
    root = _checklist(tmp_path, {"items": [{"id": "A", "state": "done"}]})
    monkeypatch.setattr(wk, "repo_root", lambda: root)
    with TestClient(app) as client:
        summary = client.get("/api/bot/work/checklist").json()["summary"]
    assert set(summary["byBasis"]) == set(ms.STATUS_BASES)
    assert summary["byBasis"]["disagree"] == 0
    assert sum(summary["byBasis"].values()) == summary["total"]


def test_reason_is_present_on_the_healthy_envelope_too(tmp_path, monkeypatch):
    """A key that VANISHES makes a consumer branch on absence.

    Found by the SPA's own api-contract checker against a real captured
    payload — the direction `provenance-consumer-guard` cannot see (a consumer
    reading a key with no writer). `Workflow.svelte` reads `checklist?.reason`
    to render a degraded read; on the healthy shape that resolved to
    `undefined`, which is indistinguishable from a reason nobody wrote.
    """
    root = _checklist(tmp_path, {"items": [{"id": "A", "state": "done"}]})
    monkeypatch.setattr(wk, "repo_root", lambda: root)
    with TestClient(app) as client:
        body = client.get("/api/bot/work/checklist").json()
    assert body["present"] is True
    assert "reason" in body and body["reason"] is None


def test_an_unreadable_checklist_is_not_an_empty_one(tmp_path, monkeypatch):
    """*we could not read it* and *there is no work* are opposite statements."""
    root = tmp_path / "repo"
    (root / "docs" / "claude" / "work").mkdir(parents=True)
    (root / ms.CHECKLIST_RELPATH).write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(wk, "repo_root", lambda: root)
    with TestClient(app) as client:
        body = client.get("/api/bot/work/checklist").json()
    assert body["present"] is False
    assert body["readState"] == "unreadable"
    assert body["reason"]
    assert body["items"] == []


def test_an_absent_checklist_grades_absent_not_unreadable(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "docs" / "claude" / "work").mkdir(parents=True)
    monkeypatch.setattr(wk, "repo_root", lambda: root)
    with TestClient(app) as client:
        body = client.get("/api/bot/work/checklist").json()
    assert body["present"] is False and body["readState"] == "absent"


def test_the_route_never_5xxs(tmp_path, monkeypatch):
    """A Tier-1 read surface that 500s is invisible rather than empty."""
    def boom():
        raise RuntimeError("exploded")
    monkeypatch.setattr(wk, "_checklist_payload", boom)
    with TestClient(app) as client:
        response = client.get("/api/bot/work/checklist")
    assert response.status_code == 200
    body = response.json()
    assert body["present"] is False and "exploded" in body["reason"]
    assert body["freshness"]["commitState"] == ms.FILE_COMMIT_UNKNOWN


# ── freshness: a frozen page must not read like a live one ───────────────────

class _T:
    def __init__(self, **kw):
        self.state = ms.TREE_SYNCED
        self.behind_commits = 0
        # ⚠️ A HEALTHY TREE NOW REQUIRES A RECENT FETCH, and this default is
        # the change (MI-262, 2026-09-10). `synced` compares HEAD against the
        # LOCAL `origin/main` ref, and `deploy_pull_restart.sh` hard-resets
        # HEAD to it right after fetching — so on the live VM the equality
        # holds BY CONSTRUCTION and `behind_commits: 0` is guaranteed rather
        # than measured. A stub with no fetch age is therefore not a healthy
        # tree; it is one whose currency cannot be established.
        self.main_ref_age_hours = 1.0 / 60.0
        self.note = ""
        self.__dict__.update(kw)


class _C:
    def __init__(self, **kw):
        self.state = ms.FILE_COMMIT_KNOWN
        self.age_hours = 0.1
        self.dirty = False
        self.note = ""
        self.__dict__.update(kw)


def test_a_healthy_page_produces_no_warnings():
    """THE POSITIVE CONTROL. Without it, "no warnings" is evidence of nothing."""
    assert wk._freshness_warnings(_T(), _C()) == []


@pytest.mark.parametrize("tree, commit, expect", [
    (_T(state=ms.TREE_BEHIND, behind_commits=4), _C(), "BEHIND origin/main"),
    (_T(state=ms.TREE_UNKNOWN, note="git unreadable"), _C(), "could not look"),
    (_T(), _C(state=ms.FILE_COMMIT_UNCOMMITTED), "never been pushed"),
    (_T(), _C(state=ms.FILE_COMMIT_UNKNOWN), "could not be read"),
    (_T(), _C(age_hours=3.4), "last COMMITTED 3.4h ago"),
    (_T(), _C(dirty=True), "differs from its last commit"),
    (_T(), _C(dirty=None), "could not be established"),
    # MI-262: `synced` has to justify itself. Both of these describe a tree
    # that reports `synced` / `behind 0` and is still not to be trusted.
    (_T(main_ref_age_hours=45.0 / 60.0), _C(), "last FETCHED 45m ago"),
    (_T(main_ref_age_hours=None), _C(), "last FETCHED could not be established"),
])
def test_each_staleness_condition_announces_itself(tree, commit, expect):
    warnings = wk._freshness_warnings(tree, commit)
    assert any(expect in w for w in warnings), warnings


def test_a_behind_tree_and_an_unknown_tree_read_differently():
    """Collapsing them would report *we could not look* as a known lag."""
    behind = wk._freshness_warnings(_T(state=ms.TREE_BEHIND, behind_commits=2), _C())
    unknown = wk._freshness_warnings(_T(state=ms.TREE_UNKNOWN, note="x"), _C())
    assert behind != unknown and behind and unknown


# ── the real committed file ─────────────────────────────────────────────────

def test_the_real_checklist_partitions_and_is_served():
    """Against the committed file, not a fixture — the shape must hold there."""
    with TestClient(app) as client:
        body = client.get("/api/bot/work/checklist").json()
    if not body["present"]:
        pytest.skip(f"checklist not readable here: {body.get('reason')}")
    summary = body["summary"]
    assert summary["total"] == len(body["items"])
    assert sum(summary["byBasis"].values()) == summary["total"]
    assert sum(summary["byStatus"].values()) == summary["total"]
    # Every row carries a basis from the closed vocabulary and a note for it.
    for row in body["items"]:
        assert row["status"]["basis"] in ms.STATUS_BASES
        assert row["status"]["note"]
    assert set(body["freshness"]) >= {
        "commitState", "commitSha", "committedAt", "commitAgeHours",
        "workingTreeDirty", "treeState", "warnings"}


# ── the live-sessions panel: a decayed state must not read as a live one ─────

NOW = __import__("datetime").datetime(
    2026, 9, 10, 12, 0, tzinfo=__import__("datetime").timezone.utc)


def test_every_declared_observation_state_is_reachable():
    produced = {
        ms.observe_session(row, now=NOW, stale_minutes=90.0).state
        for row in (
            {"state_observed_at": "2026-09-10T11:30:00Z"},   # recent
            {"state_observed_at": "2026-09-10T06:00:00Z"},   # stale
            {"state": "working"},                            # unknown
        )
    }
    assert produced == set(ms.OBSERVATION_STATES)


def test_a_row_with_no_timestamp_is_unknown_not_recent():
    """*nobody recorded when this was looked at* is not a fresh lane."""
    obs = ms.observe_session({"state": "working"}, now=NOW)
    assert obs.state == ms.OBS_UNKNOWN
    assert obs.basis == ms.OBS_NONE
    assert obs.age_minutes is None


def test_spawned_at_is_never_treated_as_an_observation():
    """THE REASSURING-DIRECTION ERROR THIS GUARDS.

    Falling back to `spawned_at` would give every row a timestamp and make the
    whole register look freshly checked. Spawn time is not an observation of a
    state.
    """
    obs = ms.observe_session(
        {"state": "working", "spawned_at": "2026-09-10T11:59:00Z"}, now=NOW)
    assert obs.state == ms.OBS_UNKNOWN, "spawned_at must not confer freshness"


def test_confirmed_at_grades_spawn_only_not_a_state_observation():
    """Measured: `confirmed_at` equals `spawned_at` to within seconds on a
    freshly spawned lane, so nobody has checked the STATE since."""
    obs = ms.observe_session(
        {"state": "working", "confirmed_at": "2026-09-10T11:59:00Z"}, now=NOW)
    assert obs.basis == ms.OBS_SPAWN_ONLY
    assert obs.state == ms.OBS_RECENT, "recent, but only as a spawn confirmation"
    # positive control: a real observation field grades the other way, so the
    # basis is reporting the FIELD rather than reporting everything.
    assert ms.observe_session(
        {"last_observed_at": "2026-09-10T11:59:00Z"}, now=NOW
    ).basis == ms.OBS_STATE_GRADED


def test_the_nested_last_observed_object_is_read():
    """5 of the 21 live rows carry `last_observed` as an OBJECT whose time is
    `.at`. A reader treating it as a string grades those `unknown`."""
    obs = ms.observe_session(
        {"last_observed": {"at": "2026-09-10T11:30:00Z", "by": "x"}}, now=NOW)
    assert obs.state == ms.OBS_RECENT
    assert obs.from_field == "last_observed"


def test_the_most_specific_observation_field_wins():
    obs = ms.observe_session(
        {"state_observed_at": "2026-09-10T11:00:00Z",
         "confirmed_at": "2026-09-10T11:59:00Z"}, now=NOW)
    assert obs.from_field == "state_observed_at"
    assert obs.basis == ms.OBS_STATE_GRADED


def test_an_unparseable_timestamp_is_unknown_not_recent():
    obs = ms.observe_session({"state_observed_at": "last tuesday"}, now=NOW)
    assert obs.state == ms.OBS_UNKNOWN
    assert obs.at == "last tuesday", "the value we could not read is still shown"


def test_the_stale_threshold_comes_from_the_lease_not_a_constant():
    assert ms._observation_stale_minutes({"ttl_minutes": 45}) == 45.0
    for bad in ({}, {"ttl_minutes": 0}, {"ttl_minutes": "nope"}, None):
        assert ms._observation_stale_minutes(bad) == \
            ms.DEFAULT_OBSERVATION_STALE_MINUTES


def test_the_panel_pairs_every_state_with_its_observation_age(tmp_path, monkeypatch):
    """The whole point of the panel: a state is never published alone."""
    root = _checklist(tmp_path, {"items": []})
    (root / ms.SESSIONS_RELPATH).write_text(json.dumps({"sessions": [
        {"session_id": "session_aaaaaa", "state": "working",
         "checklist_item": "MI-1", "state_observed_at": "2020-01-01T00:00:00Z"},
        {"session_id": "session_bbbbbb", "state": "idle",
         "checklist_item": "MI-2"},
    ]}), encoding="utf-8")
    monkeypatch.setattr(wk, "repo_root", lambda: root)
    with TestClient(app) as client:
        panel = client.get("/api/bot/work/checklist").json()["sessions"]

    assert panel["present"] is True
    # `idle` is not a live lane; only working/running/blocked/... are.
    assert panel["summary"]["live"] == 1
    assert panel["summary"]["registryRows"] == 2
    lane = panel["lanes"][0]
    assert lane["state"] == "working"
    assert lane["observation"]["state"] == ms.OBS_STALE
    assert lane["observation"]["ageMinutes"] > 0
    assert lane["observation"]["note"], "a stale lane must say why it is suspect"
    assert set(panel["summary"]["byObservationState"]) == set(ms.OBSERVATION_STATES)
    # The page must never imply a live feed.
    assert "not a live feed" in panel["note"].lower()
    assert "list_sessions" in panel["note"]


def test_an_unreadable_registry_is_not_an_absence_of_lanes(tmp_path, monkeypatch):
    root = _checklist(tmp_path, {"items": []})
    (root / ms.SESSIONS_RELPATH).write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(wk, "repo_root", lambda: root)
    with TestClient(app) as client:
        panel = client.get("/api/bot/work/checklist").json()["sessions"]
    assert panel["present"] is False
    assert panel["readState"] == "unreadable"
    assert panel["lanes"] == []


def test_the_real_registry_grades_every_live_lane():
    with TestClient(app) as client:
        panel = client.get("/api/bot/work/checklist").json()["sessions"]
    if not panel["present"]:
        pytest.skip(f"registry not readable here: {panel.get('reason')}")
    by = panel["summary"]["byObservationState"]
    assert sum(by.values()) == panel["summary"]["live"], "the partition must hold"
    for lane in panel["lanes"]:
        assert lane["observation"]["state"] in ms.OBSERVATION_STATES
        assert lane["observation"]["basis"] in ms.OBSERVATION_BASES
