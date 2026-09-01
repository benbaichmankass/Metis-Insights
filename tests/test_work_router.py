"""Tests for the /api/bot/work router (the read-only work-store view, Phase B).

Two layers, matching ``test_roadmap_api.py``'s shape:

  * unit tests over a SYNTHETIC store in tmp_path — covering the three
    distinctions this route exists to keep apart (a read failure vs an empty
    store; the six lifecycle states vs "we could not grade it"; an empty
    ``blocked_on`` vs an absent one).
  * integration tests via TestClient against the REAL committed
    ``docs/claude/work/`` (structure + the path-traversal guard).

The unit tests carry POSITIVE CONTROLS deliberately: several assert that a probe
finds a row under good input *before* asserting it reports the bad input, because
a test that only ever sees the failure case cannot distinguish "correctly
reported" from "reports everything".
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.web.api.main import app
from src.web.api.routers import work as wk


@pytest.fixture(autouse=True)
def _clear_cache():
    """The router caches on mtime; tests move the store, so reset between them."""
    wk._CACHE.clear()
    yield
    wk._CACHE.clear()


def _point_at(monkeypatch, root):
    """Repoint every store dir at a synthetic tree."""
    monkeypatch.setattr(wk, "_work_dir", lambda: root)
    monkeypatch.setattr(wk, "_intents_dir", lambda: root / "intents")
    monkeypatch.setattr(wk, "_objects_dir", lambda: root / "objects")
    monkeypatch.setattr(wk, "_steps_dir", lambda: root / "steps")


def _store(tmp_path):
    root = tmp_path / "work"
    for sub in ("intents", "objects", "steps"):
        (root / sub).mkdir(parents=True)
    return root


def _obj(root, name, body):
    (root / "objects" / f"{name}.yaml").write_text(body, encoding="utf-8")


# ── lifecycle is never collapsed ─────────────────────────────────────────

def test_all_six_states_plus_unknown_always_present_even_when_zero(tmp_path, monkeypatch):
    root = _store(tmp_path)
    _obj(root, "WO-1", "id: WO-1\nlifecycle: ready\n")
    _point_at(monkeypatch, root)

    lc = wk.get_work()["lifecycle"]
    for state in ("dormant", "ready", "in_flight", "waiting", "done", "accepted", "unknown"):
        assert state in lc, f"{state} key vanished — a consumer would branch on absence"
    assert lc["ready"] == 1
    assert lc["done"] == 0  # an explicit zero, not a missing key


def test_unrecognised_lifecycle_grades_unknown_not_a_default(tmp_path, monkeypatch):
    """A value nobody declared must not be silently defaulted into a real state."""
    root = _store(tmp_path)
    _obj(root, "WO-good", "id: WO-good\nlifecycle: ready\n")      # positive control
    _obj(root, "WO-bad", "id: WO-bad\nlifecycle: banana\n")
    _obj(root, "WO-missing", "id: WO-missing\ntitle: no lifecycle key\n")
    _point_at(monkeypatch, root)

    lc = wk.get_work()["lifecycle"]
    assert lc["ready"] == 1, "positive control: a good row must grade"
    assert lc["unknown"] == 2
    assert lc["dormant"] == 0, "an ungradeable row must not default into a real state"


def test_lifecycle_buckets_sum_to_object_count(tmp_path, monkeypatch):
    root = _store(tmp_path)
    for n, state in enumerate(("dormant", "ready", "in_flight", "waiting", "done", "accepted")):
        _obj(root, f"WO-{n}", f"id: WO-{n}\nlifecycle: {state}\n")
    _point_at(monkeypatch, root)

    d = wk.get_work()
    assert sum(d["lifecycle"].values()) == d["summary"]["objectCount"] == 6


# ── a read failure is REPORTED, never dropped ────────────────────────────

def test_malformed_object_is_reported_and_counted_not_silently_dropped(tmp_path, monkeypatch):
    """The silent-empty defect, consumer side: an unreadable row must not vanish."""
    root = _store(tmp_path)
    _obj(root, "WO-ok", "id: WO-ok\nlifecycle: ready\n")          # positive control
    _obj(root, "WO-broken", "id: WO-broken\n  bad: [indent\n")
    _point_at(monkeypatch, root)

    d = wk.get_work()
    assert d["summary"]["objectCount"] == 1, "positive control: the good row parsed"
    assert d["summary"]["readErrorCount"] == 1, "the broken row must be REPORTED"
    assert d["readErrors"][0]["path"].endswith("WO-broken.yaml")
    assert d["lifecycle"]["unknown"] == 1, "an unreadable row is ungraded, not absent"
    # And the partition still holds: 1 parsed + 1 unreadable == 2 files on disk.
    assert sum(d["lifecycle"].values()) == 2


def test_a_non_mapping_yaml_is_reported_rather_than_serving_a_gap(tmp_path, monkeypatch):
    root = _store(tmp_path)
    _obj(root, "WO-list", "- not\n- a mapping\n")
    _point_at(monkeypatch, root)

    d = wk.get_work()
    assert d["summary"]["readErrorCount"] == 1
    assert "mapping" in d["readErrors"][0]["error"]


# ── an empty blocked_on is a CLAIM, not an absence ───────────────────────

def test_blocked_on_separates_declared_none_from_unstated(tmp_path, monkeypatch):
    root = _store(tmp_path)
    _obj(root, "WO-none", "id: WO-none\nlifecycle: ready\nblocked_on: []\n")
    _obj(root, "WO-unstated", "id: WO-unstated\nlifecycle: ready\n")
    _obj(
        root,
        "WO-blocked",
        "id: WO-blocked\nlifecycle: waiting\n"
        "blocked_on:\n  - kind: object\n    ref: WO-none\n    since: 2026-09-01\n",
    )
    _point_at(monkeypatch, root)

    by_id = {o["id"]: o for o in wk.get_work()["objects"]}
    assert by_id["WO-none"]["blockedOnState"] == "declared_none"
    assert by_id["WO-unstated"]["blockedOnState"] == "unstated"
    assert by_id["WO-blocked"]["blockedOnState"] == "declared"
    # The two must never render alike — that is the whole point of the split.
    assert by_id["WO-none"]["blockedOnState"] != by_id["WO-unstated"]["blockedOnState"]


def test_object_edge_resolution_is_graded_only_for_kind_object(tmp_path, monkeypatch):
    root = _store(tmp_path)
    _obj(root, "WO-target", "id: WO-target\nlifecycle: ready\n")
    _obj(
        root,
        "WO-src",
        "id: WO-src\nlifecycle: waiting\nblocked_on:\n"
        "  - kind: object\n    ref: WO-target\n"
        "  - kind: object\n    ref: WO-absent\n"
        "  - kind: external_event\n    ref: someone observes a thing\n",
    )
    _point_at(monkeypatch, root)

    src = next(o for o in wk.get_work()["objects"] if o["id"] == "WO-src")
    kinds = {(e["kind"], e["refResolvedInStore"]) for e in src["blockedOn"]}
    assert ("object", True) in kinds, "positive control: a real object ref resolves"
    assert ("object", False) in kinds, "a dangling object ref is reported as dangling"
    # A non-object edge names something outside the store and must NOT be graded
    # as a dangling reference.
    assert ("external_event", None) in kinds


# ── the WIP ceiling is DECLARED, not enforced ────────────────────────────

def test_wip_block_reports_a_reading_and_never_claims_enforcement(tmp_path, monkeypatch):
    root = _store(tmp_path)
    for n in range(3):
        _obj(root, f"WO-{n}", f"id: WO-{n}\nlifecycle: in_flight\n")
    _point_at(monkeypatch, root)

    wip = wk.get_work()["wip"]
    assert wip["inFlight"] == 3
    assert wip["ceiling"] == 8
    assert wip["enforced"] is False, "Phase C enforces the ceiling; B only reports it"
    assert wip["state"] == "declared_not_enforced"


def test_only_in_flight_counts_against_the_ceiling(tmp_path, monkeypatch):
    root = _store(tmp_path)
    _obj(root, "WO-a", "id: WO-a\nlifecycle: in_flight\n")
    _obj(root, "WO-b", "id: WO-b\nlifecycle: waiting\n")
    _obj(root, "WO-c", "id: WO-c\nlifecycle: dormant\n")
    _point_at(monkeypatch, root)

    assert wk.get_work()["wip"]["inFlight"] == 1


# ── coverage: the store is knowingly partial ─────────────────────────────

def test_coverage_states_incompleteness_on_every_response(tmp_path, monkeypatch):
    root = _store(tmp_path)
    _obj(root, "WO-1", "id: WO-1\nlifecycle: ready\n")
    _point_at(monkeypatch, root)

    cov = wk.get_work()["coverage"]
    assert cov["complete"] is False
    assert cov["carriedRowsMigrateIn"] == "Phase C"
    assert cov["carriedRowsApprox"] > 0


def test_empty_envelope_keeps_every_key_a_consumer_branches_on(tmp_path, monkeypatch):
    """A missing store degrades; it never 5xxes and never drops a key."""
    _point_at(monkeypatch, tmp_path / "does-not-exist")

    d = wk.get_work()
    assert d["present"] is False
    assert d["reason"]
    for key in ("intents", "objects", "steps", "readErrors", "lifecycle", "wip", "coverage"):
        assert key in d, f"{key} vanished on the degraded envelope"
    assert d["coverage"]["complete"] is False
    assert set(d["lifecycle"]) == {*wk._LIFECYCLE_STATES, "unknown"}


# ── free-form keys are preserved, not dropped ────────────────────────────

def test_unknown_top_level_keys_are_preserved_under_extra(tmp_path, monkeypatch):
    """Objects carry warning keys; dropping them would hide the caveat."""
    root = _store(tmp_path)
    _obj(
        root,
        "WO-x",
        "id: WO-x\nlifecycle: ready\nscope_split: a thing\n"
        "⚠️_do_not_read_this_as_done: a loud caveat\n",
    )
    _point_at(monkeypatch, root)

    obj = wk.get_work()["objects"][0]
    assert obj["extra"]["scope_split"] == "a thing"
    assert "⚠️_do_not_read_this_as_done" in obj["extra"]


# ── single-object route ──────────────────────────────────────────────────

def test_single_object_distinguishes_absent_from_unreadable(tmp_path, monkeypatch):
    root = _store(tmp_path)
    _obj(root, "WO-ok", "id: WO-ok\nlifecycle: ready\n")
    _obj(root, "WO-broken", "id: WO-broken\n  bad: [indent\n")
    _point_at(monkeypatch, root)

    assert wk.get_work_object("WO-ok")["present"] is True  # positive control

    absent = wk.get_work_object("WO-nope")
    assert absent["present"] is False and "error" not in absent

    unreadable = wk.get_work_object("WO-broken")
    assert unreadable["present"] is False
    assert unreadable["error"], "found-but-unreadable must not read as not-found"


@pytest.mark.parametrize("bad", ["../secrets", "a/b", "..", "WO/../../etc"])
def test_object_id_traversal_is_rejected(bad):
    with pytest.raises(Exception):
        wk.get_work_object(bad)


# ── integration against the REAL committed store ─────────────────────────

def test_real_store_serves_and_partition_holds():
    client = TestClient(app)
    r = client.get("/api/bot/work")
    assert r.status_code == 200
    d = r.json()
    assert d["present"] is True, "the committed work store must parse"
    assert d["summary"]["objectCount"] >= 1
    assert sum(d["lifecycle"].values()) == d["summary"]["objectCount"]
    assert d["coverage"]["complete"] is False
    assert d["wip"]["enforced"] is False


def test_real_store_object_route_round_trips():
    client = TestClient(app)
    listed = client.get("/api/bot/work").json()["objects"]
    assert listed, "positive control: the store is non-empty"
    one = client.get(f"/api/bot/work/object/{listed[0]['id']}").json()
    assert one["present"] is True
    assert one["object"]["id"] == listed[0]["id"]


def test_real_store_rejects_traversal_over_http():
    client = TestClient(app)
    assert client.get("/api/bot/work/object/..%2F..%2Fsecrets").status_code in (400, 404)


# ── the "never a 5xx" contract holds end to end ──────────────────────────

def test_whole_envelope_is_json_serialisable(tmp_path, monkeypatch):
    """A non-encodable value would 500 at RESPONSE render — AFTER the module's
    own try/except — so the contract has to hold at build time, not just in
    _build_index's error handling."""
    import json

    root = _store(tmp_path)
    # An unquoted YAML date parses to datetime.date; a nested one lands inside
    # `extra`, which preserves arbitrary free-form keys by design.
    _obj(
        root,
        "WO-dates",
        "id: WO-dates\nlifecycle: ready\nopened_at: 2026-09-01\n"
        "scope_split:\n  when: 2026-09-02\n  nested:\n    - 2026-09-03\n",
    )
    _point_at(monkeypatch, root)

    d = wk.get_work()
    json.dumps(d)  # must not raise
    obj = d["objects"][0]
    assert obj["openedAt"] == "2026-09-01"
    assert obj["extra"]["scope_split"]["when"] == "2026-09-02"
    assert obj["extra"]["scope_split"]["nested"] == ["2026-09-03"]


def test_jsonable_stringifies_an_unknown_type_rather_than_dropping_it():
    """A value we could not type is still a value the reader should see."""
    class Weird:
        def __str__(self):
            return "weird-value"

    assert wk._jsonable(Weird()) == "weird-value"
    # Positive control: ordinary scalars pass through untouched.
    assert wk._jsonable("s") == "s" and wk._jsonable(3) == 3 and wk._jsonable(None) is None
