"""Tests for Phase H — the decision round-trip.

The contract under test is one sentence: **transit fails BACK, never forward.**
An answer that does not reach the repo leaves its question UNANSWERED. So most
of what follows is a test that something is NOT reported as decided.

Layers:
  * unit tests over ``src/runtime/work_decisions.py`` — the four answer states
    and the two pairs that must never collapse.
  * route tests via ``TestClient`` — the fail-closed write gate, the validation
    the write route performs, and the end-to-end round-trip:
    submit → in_transit → commit → committed.

⚠️ **Positive controls, deliberately.** Several tests assert the probe finds the
GOOD case before asserting it reports the bad one — a test that only ever sees
the failure cannot distinguish "correctly reported" from "reports everything".
The write-gate test is the sharpest: it asserts a 401 with the token SET, so the
503-when-unset assertion cannot pass for the trivial reason that the route is
broken for every caller.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import yaml
from fastapi.testclient import TestClient

from src.runtime import work_decisions as wd
from src.web.api.main import app
from src.web.api.routers import work as wk

TOKEN = "test-decision-token"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Point the router at a synthetic store AND a synthetic transit log.

    Both are redirected: a test that wrote into the real
    ``runtime_logs/work_decision_transit.jsonl`` would leave a live submission
    behind, and one that wrote into the real store would edit the state of
    record for work.
    """
    root = tmp_path / "work"
    for sub in ("intents", "objects", "steps"):
        (root / sub).mkdir(parents=True)
    monkeypatch.setattr(wk, "_work_dir", lambda: root)
    monkeypatch.setattr(wk, "_intents_dir", lambda: root / "intents")
    monkeypatch.setattr(wk, "_objects_dir", lambda: root / "objects")
    monkeypatch.setattr(wk, "_steps_dir", lambda: root / "steps")
    transit = tmp_path / "transit.jsonl"
    monkeypatch.setattr(wk, "transit_log_path", lambda: transit)
    monkeypatch.setattr(wd, "transit_log_path", lambda: transit)
    wk._CACHE.clear()
    yield root, transit
    wk._CACHE.clear()


def _write_object(root, object_id="WO-TEST", answer=None, options=("a", "b")):
    body = {
        "id": object_id,
        "type": "commitment",
        "title": "A test object",
        "lifecycle": "in_flight",
        "blocked_on": [
            {"kind": "operator_decision", "ref": "DEC-TEST", "since": "2026-09-01"}
        ],
        "decision_requests": [
            {
                "id": "DEC-TEST",
                "question": "Which way?",
                "urgency": "blocking",
                "asked_on": "2026-09-01",
                "options": [{"key": k, "label": k.upper()} for k in options],
                "allows_free_text": True,
            }
        ],
    }
    if answer is not None:
        body["decision_requests"][0]["answer"] = answer
    (root / "objects" / f"{object_id}.yaml").write_text(
        yaml.safe_dump(body, sort_keys=False), encoding="utf-8"
    )
    wk._CACHE.clear()
    return body


# ═══════════════════════════════════════════════════════════════════════════
# The four states, and the two pairs that must never collapse
# ═══════════════════════════════════════════════════════════════════════════


def test_the_four_states_are_distinct_values():
    assert len(set(wd.ANSWER_STATES)) == 4
    assert wd.ANSWER_STATES == (
        wd.NOT_SUBMITTED, wd.IN_TRANSIT, wd.COMMITTED, wd.UNREADABLE
    )


def test_unreadable_transit_is_not_reported_as_not_submitted():
    """The pair that matters most.

    A broken channel must not read as "the operator has not answered" — that
    would put a question back on the operator that they may already have
    answered, and make a broken channel indistinguishable from a quiet one.
    """
    req = {"answer": None}
    # POSITIVE CONTROL: with a readable-but-empty channel the same input grades
    # not_submitted, so the assertion below is about the READ STATE and not
    # about the grader refusing everything.
    assert wd.grade_answer_state(req, None, wd.TRANSIT_ABSENT) == wd.NOT_SUBMITTED
    assert wd.grade_answer_state(req, None, wd.TRANSIT_UNREADABLE) == wd.UNREADABLE


def test_a_submission_that_has_not_committed_is_unanswered_not_answered():
    """Transit fails BACK. `in_transit` is NOT `committed`."""
    req = {"answer": None}
    sub = {"submission_id": "x", "submitted_at": "2026-09-01T00:00:00Z"}
    assert wd.grade_answer_state(req, sub, wd.TRANSIT_READ) == wd.IN_TRANSIT
    assert wd.grade_answer_state(req, sub, wd.TRANSIT_READ) != wd.COMMITTED


def test_committed_is_read_from_the_repo_and_survives_an_unreadable_log():
    """A decision already made cannot be un-made by a read failure."""
    req = {"answer": {"chosen": "a"}}
    assert wd.grade_answer_state(req, None, wd.TRANSIT_UNREADABLE) == wd.COMMITTED


def test_a_half_written_answer_block_does_not_grade_as_committed():
    """`answer:` with neither a choice nor free text is not a decision."""
    assert wd.normalise_answer({"answered_by": "operator"}) is None
    assert wd.normalise_answer({"chosen": "   "}) is None
    assert wd.normalise_answer({"chosen": "a"})["chosen"] == "a"


def test_absent_transit_file_is_absent_not_unreadable(tmp_path):
    rows, state, err = wd.read_transit(tmp_path / "nope.jsonl")
    assert (rows, state, err) == ([], wd.TRANSIT_ABSENT, None)


def test_a_malformed_line_does_not_hide_the_good_submissions(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(
        '{"object_id":"A","request_id":"R","submitted_at":"2026-09-01T00:00:00Z"}\n'
        "not json at all\n"
        '{"object_id":"B","request_id":"R","submitted_at":"2026-09-01T00:00:01Z"}\n',
        encoding="utf-8",
    )
    rows, state, _ = wd.read_transit(p)
    assert state == wd.TRANSIT_READ
    assert len(rows) == 2  # one bad append must not bury every good one


def test_transit_window_age_is_none_not_zero_when_undateable():
    """Zero is a real reading (submitted just now). Undateable is not."""
    w = wd.transit_window({"submission_id": "x", "submitted_at": "garbage"})
    assert w["ageSeconds"] is None
    # ...and the fail-safe reading of an open write window is STALE.
    assert w["stale"] is True


def test_transit_window_flags_a_stale_open_window():
    old = (datetime.now(timezone.utc) - timedelta(seconds=wd.STALE_TRANSIT_SECONDS + 60))
    fresh = datetime.now(timezone.utc)
    assert wd.transit_window(
        {"submission_id": "x", "submitted_at": fresh.isoformat()})["stale"] is False
    assert wd.transit_window(
        {"submission_id": "x", "submitted_at": old.isoformat()})["stale"] is True


def test_latest_submission_wins():
    rows = [
        {"object_id": "A", "request_id": "R", "chosen": "a",
         "submitted_at": "2026-09-01T00:00:00Z"},
        {"object_id": "A", "request_id": "R", "chosen": "b",
         "submitted_at": "2026-09-01T01:00:00Z"},
    ]
    assert wd.latest_submissions(rows)[("A", "R")]["chosen"] == "b"


def test_a_request_without_an_id_is_dropped_and_counted():
    """A question the operator can SEE and cannot ANSWER is worse than none."""
    data = {"decision_requests": [{"question": "no id"}, {"id": "ok", "question": "q"}]}
    assert [r["id"] for r in wd.normalise_requests(data, "WO")] == ["ok"]
    assert wd.malformed_request_count(data) == 1


# ═══════════════════════════════════════════════════════════════════════════
# The write gate — fail-closed, the prop.py polarity
# ═══════════════════════════════════════════════════════════════════════════


def test_write_is_503_when_the_token_is_unset(_isolate, monkeypatch):
    root, _ = _isolate
    _write_object(root)
    monkeypatch.delenv("DASHBOARD_API_TOKEN", raising=False)
    r = TestClient(app).post(
        "/api/bot/work/decision",
        json={"object_id": "WO-TEST", "request_id": "DEC-TEST", "chosen": "a"},
    )
    # FAIL-CLOSED: a dropped .env value closes writes rather than reopening an
    # anonymous write hole. Deliberately NOT the permissive `devices` shape.
    assert r.status_code == 503


def test_write_is_401_without_a_bearer_when_the_token_is_set(_isolate, monkeypatch):
    """POSITIVE CONTROL for the test above — with a token configured the route
    is reachable and refuses on AUTH, so the 503 there is about the gate being
    unconfigured rather than the route being broken for everyone."""
    root, _ = _isolate
    _write_object(root)
    monkeypatch.setenv("DASHBOARD_API_TOKEN", TOKEN)
    c = TestClient(app)
    body = {"object_id": "WO-TEST", "request_id": "DEC-TEST", "chosen": "a"}
    assert c.post("/api/bot/work/decision", json=body).status_code == 401
    assert c.post("/api/bot/work/decision", json=body,
                  headers={"Authorization": "Basic xyz"}).status_code == 401
    assert c.post("/api/bot/work/decision", json=body,
                  headers={"Authorization": f"Bearer {TOKEN}-wrong"}).status_code == 401
    # ...and the right bearer gets through, which is what makes the three
    # refusals above evidence about AUTH rather than about the route.
    assert c.post("/api/bot/work/decision", json=body,
                  headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_write_gate_state_is_published_on_the_read_route(_isolate, monkeypatch):
    """The SPA must be able to say 'answering is closed' instead of rendering a
    submit button that 503s."""
    root, _ = _isolate
    _write_object(root)
    c = TestClient(app)
    monkeypatch.delenv("DASHBOARD_API_TOKEN", raising=False)
    assert c.get("/api/bot/work/decisions").json()["writeGate"]["state"] == "closed_no_token"
    monkeypatch.setenv("DASHBOARD_API_TOKEN", TOKEN)
    gate = c.get("/api/bot/work/decisions").json()["writeGate"]
    assert gate["state"] == "open" and gate["acceptsWrites"] is True
    # The VALUE is never echoed, only whether one is set.
    assert TOKEN not in json.dumps(gate)


# ═══════════════════════════════════════════════════════════════════════════
# Validation — a submission must answer THIS question
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def client(_isolate, monkeypatch):
    monkeypatch.setenv("DASHBOARD_API_TOKEN", TOKEN)
    return TestClient(app)


def _post(client, **body):
    return client.post(
        "/api/bot/work/decision", json=body,
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


def test_an_option_the_author_never_wrote_is_refused(_isolate, client):
    root, _ = _isolate
    _write_object(root, options=("a", "b"))
    assert _post(client, object_id="WO-TEST", request_id="DEC-TEST",
                 chosen="a").status_code == 200
    assert _post(client, object_id="WO-TEST", request_id="DEC-TEST",
                 chosen="c").status_code == 400


def test_an_empty_submission_is_refused(_isolate, client):
    """A vacuous answer that reads as compliance is worse than no answer —
    the same rule board-post.yml applies to a blank comment."""
    root, _ = _isolate
    _write_object(root)
    assert _post(client, object_id="WO-TEST", request_id="DEC-TEST").status_code == 400
    assert _post(client, object_id="WO-TEST", request_id="DEC-TEST",
                 chosen="  ", free_text=" ").status_code == 400


def test_unknown_object_and_unknown_request_are_404(_isolate, client):
    root, _ = _isolate
    _write_object(root)
    assert _post(client, object_id="WO-NOPE", request_id="DEC-TEST",
                 chosen="a").status_code == 404
    assert _post(client, object_id="WO-TEST", request_id="DEC-NOPE",
                 chosen="a").status_code == 404


def test_a_traversing_object_id_is_refused(_isolate, client):
    root, _ = _isolate
    _write_object(root)
    assert _post(client, object_id="../../etc/passwd", request_id="DEC-TEST",
                 chosen="a").status_code == 400


def test_an_already_committed_decision_refuses_a_second_answer(_isolate, client):
    root, _ = _isolate
    _write_object(root, answer={"chosen": "a", "answered_at": "2026-09-01T00:00:00Z"})
    r = _post(client, object_id="WO-TEST", request_id="DEC-TEST", chosen="b")
    assert r.status_code == 409


# ═══════════════════════════════════════════════════════════════════════════
# The round-trip
# ═══════════════════════════════════════════════════════════════════════════


def test_submit_then_commit_is_the_whole_contract(_isolate, client, tmp_path):
    root, transit = _isolate
    _write_object(root)
    c = client

    # 1. Nobody has answered.
    inbox = c.get("/api/bot/work/decisions").json()
    assert inbox["summary"]["byAnswerState"]["not_submitted"] == 1
    assert inbox["summary"]["awaitingOperator"] == 1

    # 2. The operator answers from the UI. The response says SUBMITTED, not
    #    decided — this is the assertion that pins the forward-failure refusal.
    r = _post(c, object_id="WO-TEST", request_id="DEC-TEST", chosen="b",
              free_text="because of X")
    assert r.status_code == 200
    assert r.json()["answerState"] == wd.IN_TRANSIT
    sub_id = r.json()["submissionId"]

    # 3. The question is STILL unanswered, and the open window is enumerable.
    wk._CACHE.clear()
    inbox = c.get("/api/bot/work/decisions").json()
    req = inbox["requests"][0]
    assert req["answerState"] == wd.IN_TRANSIT
    assert req["answer"] is None          # the repo holds no decision yet
    assert inbox["summary"]["decided"] == 0
    assert inbox["summary"]["awaitingCommit"] == 1
    # ...and it is NOT counted as waiting on the operator: it is waiting on a
    # COMMITTER, and pooling them would put work on the operator that is not
    # theirs.
    assert inbox["summary"]["awaitingOperator"] == 0
    assert req["transit"]["submissionId"] == sub_id
    assert req["transit"]["ageSeconds"] is not None

    # 4. The committer writes it into the repo. THAT is what makes it true.
    import scripts.ops.commit_work_decisions as committer
    committer.OBJECTS_DIR = root / "objects"
    rc = committer.main(["--transit", str(transit), "--apply", "--json"])
    assert rc == 0

    stored = yaml.safe_load((root / "objects" / "WO-TEST.yaml").read_text())
    answer = stored["decision_requests"][0]["answer"]
    assert answer["chosen"] == "b" and answer["free_text"] == "because of X"
    assert answer["submission_id"] == sub_id

    # 5. Only NOW is it decided.
    wk._CACHE.clear()
    inbox = c.get("/api/bot/work/decisions").json()
    assert inbox["requests"][0]["answerState"] == wd.COMMITTED
    assert inbox["summary"]["decided"] == 1
    assert inbox["summary"]["awaitingCommit"] == 0


def test_committing_twice_is_idempotent(_isolate, client, tmp_path):
    root, transit = _isolate
    _write_object(root)
    _post(client, object_id="WO-TEST", request_id="DEC-TEST", chosen="a")
    import scripts.ops.commit_work_decisions as committer
    committer.OBJECTS_DIR = root / "objects"
    assert committer.main(["--transit", str(transit), "--apply"]) == 0
    first = (root / "objects" / "WO-TEST.yaml").read_text()
    # Re-running sees the answer block and skips — the transit log is NEVER
    # pruned (it is the audit trail), so idempotence has to come from the repo.
    assert committer.main(["--transit", str(transit), "--apply"]) == 0
    assert (root / "objects" / "WO-TEST.yaml").read_text() == first


def test_committer_dry_run_writes_nothing(_isolate, client, tmp_path):
    root, transit = _isolate
    _write_object(root)
    _post(client, object_id="WO-TEST", request_id="DEC-TEST", chosen="a")
    before = (root / "objects" / "WO-TEST.yaml").read_text()
    import scripts.ops.commit_work_decisions as committer
    committer.OBJECTS_DIR = root / "objects"
    assert committer.main(["--transit", str(transit)]) == 0   # no --apply
    assert (root / "objects" / "WO-TEST.yaml").read_text() == before


def test_committer_refuses_an_orphan_submission(_isolate, tmp_path):
    root, transit = _isolate
    _write_object(root)
    transit.write_text(json.dumps({
        "submission_id": "x", "object_id": "WO-GONE", "request_id": "DEC-TEST",
        "chosen": "a", "submitted_at": "2026-09-01T00:00:00Z",
    }) + "\n", encoding="utf-8")
    import scripts.ops.commit_work_decisions as committer
    committer.OBJECTS_DIR = root / "objects"
    # Non-zero: a workflow must not report a clean run over an answer the
    # operator gave that matches nothing.
    assert committer.main(["--transit", str(transit), "--apply"]) == 1


def test_committer_reports_unreadable_rather_than_nothing_to_do(tmp_path):
    """`--transit` pointing at a directory is a read failure, not an empty log."""
    import scripts.ops.commit_work_decisions as committer
    d = tmp_path / "adir"
    d.mkdir()
    assert committer.main(["--transit", str(d)]) == 2


# ═══════════════════════════════════════════════════════════════════════════
# The gap the inbox exists to surface
# ═══════════════════════════════════════════════════════════════════════════


def test_an_operator_edge_with_no_answerable_request_is_surfaced_separately(
    _isolate, client
):
    """A question the operator is blocking on that they CANNOT answer from the
    UI, because nobody wrote it down as a request. Folding it in with the
    answerable ones would hide exactly that gap."""
    root, _ = _isolate
    (root / "objects" / "WO-BARE.yaml").write_text(yaml.safe_dump({
        "id": "WO-BARE", "title": "bare", "lifecycle": "waiting",
        "blocked_on": [{"kind": "operator_decision", "ref": "something",
                        "since": "2026-09-01"}],
    }, sort_keys=False), encoding="utf-8")
    wk._CACHE.clear()
    inbox = client.get("/api/bot/work/decisions").json()
    assert inbox["summary"]["unanswerableOperatorEdgeCount"] == 1
    assert inbox["summary"]["requestCount"] == 0


def test_an_edge_naming_its_own_request_is_not_double_counted(_isolate, client):
    root, _ = _isolate
    _write_object(root)          # its edge ref IS "DEC-TEST"
    inbox = client.get("/api/bot/work/decisions").json()
    assert inbox["summary"]["requestCount"] == 1
    assert inbox["summary"]["unanswerableOperatorEdgeCount"] == 0


def test_the_read_route_degrades_rather_than_5xxing(_isolate, client, monkeypatch):
    monkeypatch.setattr(wk, "_get_index", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    r = client.get("/api/bot/work/decisions")
    assert r.status_code == 200
    body = r.json()
    assert body["present"] is False and "boom" in body["reason"]
    # ⚠️ `unreadable`, NEVER `absent`: we failed before reaching the channel, so
    # we did not establish that it is empty.
    assert body["transit"]["state"] == wd.TRANSIT_UNREADABLE
    assert set(body["summary"]["byAnswerState"]) == set(wd.ANSWER_STATES)


# ═══════════════════════════════════════════════════════════════════════════
# The real store — the mechanism is exercised by real content
# ═══════════════════════════════════════════════════════════════════════════


def test_the_committed_store_declares_at_least_one_real_decision_request():
    """Phase A's precedent: a mechanism exercised by real content from its first
    commit, or it is deployed and unproven. Reads the REAL store, not a fixture.
    """
    import glob
    import os
    from src.utils.paths import repo_root

    found = []
    for path in glob.glob(os.path.join(str(repo_root()), "docs/claude/work/objects/*.yaml")):
        data = yaml.safe_load(open(path, encoding="utf-8")) or {}
        if not isinstance(data, dict):
            continue
        found += wd.normalise_requests(data, str(data.get("id") or ""))
    assert found, (
        "the decision channel carries no questions — deployed, not working"
    )
    # Every declared option must be selectable, i.e. carry a key. A question
    # whose options cannot be named is not answerable from the UI.
    for req in found:
        assert req["options"], f"{req['id']} declares no answerable option"
