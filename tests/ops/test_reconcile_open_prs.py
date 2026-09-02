"""The post-merge reconciler: three states, never collapsed, and a MOVE that
destroys nothing.

The load-bearing property is that `could_not_look` moves NOTHING and does not
report success — *we could not look* is not *nothing had merged*. Every test
asserting a refusal is paired with one showing the same path can also succeed,
so none of them is a constant.
"""
import sys
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[2] / "scripts" / "ops"
sys.path.insert(0, str(OPS))
import reconcile_open_prs as rec  # noqa: E402


def _doc(*prs, settled=None):
    return {"schema_version": 3,
            "open_prs": [{"pr": n, "owner_session": "s", "blocker": "b",
                          "operator_decision": {"verdict": "approved",
                                                "text": "verbatim"}} for n in prs],
            "settled_prs": list(settled or [])}


def _open(n):
    return {"number": n, "state": "open", "merged_at": None}


def _merged(n, sha="abc123"):
    return {"number": n, "state": "closed", "merged_at": "2026-09-02T09:40:15Z",
            "merge_commit_sha": sha}


def _closed(n):
    return {"number": n, "state": "closed", "merged_at": None,
            "merge_commit_sha": None}


# --------------------------------------------------------------------------- #
# The three states
# --------------------------------------------------------------------------- #
def test_a_merged_row_is_moved():
    v = rec.reconcile(_doc(1, 2), {1: _open(1), 2: _merged(2, "deadbee")},
                      head_sha="cafe")
    assert v["state"] == rec.RECONCILED
    assert [r["pr"] for r in v["doc"]["open_prs"]] == [1]
    assert [r["pr"] for r in v["doc"]["settled_prs"]] == [2]
    settled = v["doc"]["settled_prs"][0]
    assert settled["terminal"] == "merged"
    assert settled["merge_sha"] == "deadbee"
    assert settled["settled_by"] == "reconciler"


def test_everything_still_open_is_no_change_and_writes_nothing():
    doc = _doc(1, 2)
    v = rec.reconcile(doc, {1: _open(1), 2: _open(2)}, head_sha="cafe")
    assert v["state"] == rec.NO_CHANGE
    assert v["moved"] == []
    # ⚠️ Not even the liveness stamp: a stamp would be a commit to main, which
    # retriggers this workflow, which stamps again.
    assert v["doc"] is doc
    assert "last_reconciled_sha" not in v["doc"]


def test_could_not_look_moves_nothing_and_does_not_report_success():
    """THE ONE THAT MATTERS. A failed observation must not read as a clean one."""
    doc = _doc(1, 2)
    v = rec.reconcile(doc, None, reason="HTTP 503", head_sha="cafe")
    assert v["state"] == rec.COULD_NOT_LOOK
    assert v["state"] != rec.NO_CHANGE
    assert v["moved"] == []
    assert v["doc"] is doc
    assert v["doc"]["open_prs"] == doc["open_prs"]
    assert "last_reconciled_sha" not in v["doc"]


def test_could_not_look_is_all_or_nothing():
    """One unreadable row fails the WHOLE pass rather than settling the rest.

    A partial pass would stamp `last_reconciled_sha` — asserting the reconciler
    ran against this sha — while an unknown row sat unreconciled, and that stamp
    is exactly what `grade_completeness` reads to tell a dead reconciler apart
    from a session that forgot to prune.
    """
    def fetch(pr):
        if pr == 2:
            raise rec.LookupFailure("#2: HTTP 500")
        return _merged(pr)

    obs, reason = rec.observe([1, 2], fetch)
    assert obs is None and "#2" in reason
    v = rec.reconcile(_doc(1, 2), obs, reason)
    assert v["state"] == rec.COULD_NOT_LOOK
    assert v["doc"]["open_prs"] and not v["doc"]["settled_prs"]


def test_a_successful_observation_reads_every_row():
    """The pairing for the test above — `observe` is not a constant `None`."""
    obs, reason = rec.observe([1, 2], lambda pr: _merged(pr))
    assert reason is None
    assert sorted(obs) == [1, 2]


# --------------------------------------------------------------------------- #
# What it must never do
# --------------------------------------------------------------------------- #
def test_it_never_adds_a_row_for_an_unrecorded_open_pr():
    """Structural, not policy: it iterates the RECORD and asks about those
    numbers. It never enumerates what is open, so it cannot invent a row whose
    owner, intent and operator decision nobody established."""
    v = rec.reconcile(_doc(1), {1: _open(1), 999: _open(999)}, head_sha="cafe")
    assert [r["pr"] for r in v["doc"]["open_prs"]] == [1]
    assert v["doc"]["settled_prs"] == []
    assert v["state"] == rec.NO_CHANGE


def test_the_move_preserves_the_operator_decision_verbatim():
    """The reason the split exists at all. #10746's conditional Tier-2 approval
    is MORE load-bearing after the merge than before it."""
    doc = _doc(1)
    doc["open_prs"][0]["operator_decision"] = {
        "verdict": "approved_with_conditions",
        "condition": "bybit_1 (demo) ONLY, NOT a fleet-wide flip",
        "text": "APPROVED WITH STAGING -- bybit_1 (demo) ONLY."}
    v = rec.reconcile(doc, {1: _merged(1)}, head_sha="cafe")
    moved = v["doc"]["settled_prs"][0]
    assert moved["operator_decision"] == doc["open_prs"][0]["operator_decision"]
    assert moved["owner_session"] == "s" and moved["blocker"] == "b"


def test_nothing_is_ever_deleted():
    doc = _doc(1, 2, 3)
    v = rec.reconcile(doc, {1: _open(1), 2: _merged(2), 3: _closed(3)},
                      head_sha="cafe")
    known = {r["pr"] for r in v["doc"]["open_prs"]} | {
        r["pr"] for r in v["doc"]["settled_prs"]}
    assert known == {1, 2, 3}


def test_a_closed_unmerged_pr_is_not_filed_as_merged():
    v = rec.reconcile(_doc(1), {1: _closed(1)}, head_sha="cafe")
    row = v["doc"]["settled_prs"][0]
    assert row["terminal"] == "closed_unmerged"
    assert row["merge_sha"] is None


# --------------------------------------------------------------------------- #
# terminal_of — the payload trap, measured on this repo's own API responses
# --------------------------------------------------------------------------- #
def test_terminal_reads_merged_at_not_the_merged_boolean():
    """⚠️ NOT hypothetical. `list_pull_requests` returned `merged: false` on all
    eight of this repo's genuinely-merged PRs while `merged_at` was set. Reading
    the boolean would file every one of them as `closed_unmerged`, and the new
    disposition check would then demand a reason for an abandonment that never
    happened."""
    payload = {"number": 1, "state": "closed", "merged": False,
               "merged_at": "2026-09-02T07:54:23Z", "merge_commit_sha": "b9fbf95a"}
    assert rec.terminal_of(payload) == "merged"


def test_an_open_pr_has_no_terminal():
    assert rec.terminal_of(_open(1)) is None


@pytest.mark.parametrize("state", list(rec.RECONCILE_STATES))
def test_every_declared_state_is_reachable(state):
    """A declared state nothing can produce is a dead claim."""
    reachable = {
        rec.RECONCILED: lambda: rec.reconcile(_doc(1), {1: _merged(1)}),
        rec.NO_CHANGE: lambda: rec.reconcile(_doc(1), {1: _open(1)}),
        rec.COULD_NOT_LOOK: lambda: rec.reconcile(_doc(1), None, "x"),
    }
    assert reachable[state]()["state"] == state


def test_an_unparseable_record_is_could_not_look_never_no_change():
    v = rec.reconcile("not a dict", {}, head_sha="cafe")
    assert v["state"] == rec.COULD_NOT_LOOK


def test_a_404_is_a_lookup_failure_not_a_settled_pr(monkeypatch):
    """A PR number that 404s is not a merged PR — it is a number this token
    cannot see, and moving a row out of the graded population on that evidence
    would be a silent loss of coverage."""
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 404, "Not Found", None, None)

    monkeypatch.setattr(rec.urllib.request, "urlopen", boom)
    fetch = rec.github_fetch("o/r", "tok")
    with pytest.raises(rec.LookupFailure):
        fetch(10746)
