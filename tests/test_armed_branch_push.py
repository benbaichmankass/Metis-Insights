"""A commit pushed to an armed branch may never land, and nothing said so.

`claude-pr-automerge` arms native auto-merge, and from that moment the branch is
CLOSED TO NEW CONTENT — but `git push` reports success either way and the arming
state is invisible at push time. Five commits have been lost this way
(`BL-20260908-CLAUDE-PR-AUTOMERGE-OPENS-A-PR-WITH-NO-CI-AND-ARMS-A-RACE-THAT-DROPS-COMMITS-FOUR-SESSIONS-IN-ONE-DAY`).

The two that pin the mechanism, and both are replayed below:

* **#11409** (2026-09-08) — auto-merge took head `5590ef5b` at 15:05:55Z while
  `527a9b4c` was already pushed on the branch. It never reached `main`.
* **#11846** (2026-09-12) — auto-merge squashed `63e55b8ef` at 01:03:28Z;
  `49653310e` was pushed ~30 seconds LATER, to a branch whose PR had already
  merged. The dropped commit carried the `SESSIONS.json` row for a sub-session
  **that was already running**, so a live lane existed on the fleet with no
  record on `main` for nine minutes.

⚠️ **THE TRAP THESE TESTS EXIST TO PIN** is the containment test. This repo
squash-merges, so `merge_commit_sha` shares no ancestry with the branch and any
`merge-base --is-ancestor` containment check answers NO for **every correctly
merged commit** — a false `dropped` on the healthy case, which is the
desensitised alarm this repo has measured the cost of. The policy is keyed on
`pr.head.sha`: *is the head GitHub squashed the head that is there now?*

Run: ``python3 -m pytest tests/test_armed_branch_push.py``
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "ci" / "check_armed_branch_push.py"


def _load():
    spec = importlib.util.spec_from_file_location("_armed_push", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = _load()

# The real shas, padded to 40 chars so they are shaped like git object ids.
MERGED_HEAD = "63e55b8ef" + "0" * 31      # what #11846 actually squashed
DROPPED_PUSH = "49653310e" + "0" * 31     # what was pushed 30s later and lost


def _pr(**kw):
    base = {"number": 11846, "state": "open", "merged": False,
            "head": {"sha": MERGED_HEAD}, "auto_merge": None}
    base.update(kw)
    return base


def test_self_test_passes():
    ok, fails = G._self_test(quiet=True)
    assert ok, f"armed-branch-push self-test failures: {fails}"


# ── the #11846 case, and its control ────────────────────────────────────────

def test_pushing_to_an_already_merged_pr_is_graded_dropped():
    row = G.grade(DROPPED_PUSH, _pr(merged=True, state="closed"))
    assert row["state"] == G.DROPPED
    assert row["error"] is True and row["warn"] is False
    assert G.exit_code(row) == G.EXIT_ERROR, "the run must FAIL so the alert channel sees it"


def test_the_dropped_message_names_the_remedy_and_both_shas():
    row = G.grade(DROPPED_PUSH, _pr(merged=True, state="closed"))
    # A merged PR is finished and must not be reused — the remedy is a fresh branch.
    assert "FRESH branch" in row["why"]
    assert MERGED_HEAD[:8] in row["why"] and DROPPED_PUSH[:8] in row["why"]
    # And the false-statement half: a successful push is not a landed change.
    assert "not about `main`" in row["why"]


def test_the_healthy_squash_is_not_reported_as_a_drop():
    """THE CONTROL, and the one that kills the ancestry approach.

    A squash merge of the current head shares no commit with the branch, so an
    ancestry-based containment test would call this a drop — on every healthy
    merge in the repo.
    """
    row = G.grade(MERGED_HEAD, _pr(merged=True, state="closed"))
    assert row["state"] == G.MERGED_CONTAINED
    assert not row["error"] and not row["warn"]
    assert G.exit_code(row) == G.EXIT_QUIET


# ── armed vs unarmed: a warning, never an error ─────────────────────────────

def test_an_armed_open_pr_warns_but_does_not_fail_the_run():
    row = G.grade(DROPPED_PUSH, _pr(auto_merge={"merge_method": "SQUASH"}))
    assert row["state"] == G.ARMED
    assert row["warn"] is True and row["error"] is False
    assert G.exit_code(row) == G.EXIT_QUIET, (
        "arming is legitimate and the push may well land — failing the run here "
        "would train everyone past the channel")
    assert "closed to new content" in row["why"]


def test_an_unarmed_open_pr_is_quiet():
    row = G.grade(DROPPED_PUSH, _pr())
    assert row["state"] == G.UNARMED
    assert not row["warn"] and not row["error"]


@pytest.mark.parametrize("pr,want", [
    ({"auto_merge": None}, False),                         # off: present-and-null
    ({}, False),                                           # absent
    ({"auto_merge": {"merge_method": "SQUASH"}}, True),    # on
])
def test_auto_merge_is_read_by_value_not_by_key_presence(pr, want):
    """GitHub returns `auto_merge: null` on every unarmed PR, so a `in pr` test
    would grade the entire repo as armed."""
    assert G._auto_merge_enabled(pr) is want


# ── "we did not look" is never a pass ───────────────────────────────────────

def test_a_failed_lookup_is_unknown_and_never_no_pr():
    blind = G.grade(DROPPED_PUSH, None, pr_read_ok=False)
    found_nothing = G.grade(DROPPED_PUSH, None, pr_read_ok=True)
    assert blind["state"] == G.UNKNOWN
    assert found_nothing["state"] == G.NO_PR
    assert blind["state"] != found_nothing["state"]
    assert "COULD NOT LOOK" in blind["why"]


def test_unknown_does_not_fail_the_run():
    """A flaky API must not become a daily alarm — but it must still be SAID."""
    row = G.grade(DROPPED_PUSH, None, pr_read_ok=False)
    assert G.exit_code(row) == G.EXIT_QUIET
    assert row["state"] in G.ALL_STATES and row["state"] == G.UNKNOWN


@pytest.mark.parametrize("pr,why", [
    ({"number": 1, "merged": True, "head": {}}, "merged with no head sha"),
    ({"number": 1, "state": "closed", "merged": False}, "closed but not merged"),
])
def test_ungradeable_shapes_are_unknown_not_a_pass(pr, why):
    assert G.grade(DROPPED_PUSH, pr)["state"] == G.UNKNOWN, why


def test_a_missing_pushed_sha_is_unknown():
    assert G.grade(None, _pr())["state"] == G.UNKNOWN


def test_every_state_is_reachable_so_none_is_decorative():
    reached = {
        G.grade(DROPPED_PUSH, _pr(merged=True, state="closed"))["state"],
        G.grade(MERGED_HEAD, _pr(merged=True, state="closed"))["state"],
        G.grade(DROPPED_PUSH, _pr(auto_merge={"m": 1}))["state"],
        G.grade(DROPPED_PUSH, _pr())["state"],
        G.grade(DROPPED_PUSH, None)["state"],
        G.grade(DROPPED_PUSH, None, pr_read_ok=False)["state"],
    }
    assert reached == set(G.ALL_STATES)


# ── the CLI, since the workflow calls it and CI never runs that workflow ────

def test_cli_exits_non_zero_on_a_drop_and_zero_on_the_control(tmp_path):
    merged = tmp_path / "pr.json"
    merged.write_text(json.dumps([_pr(merged=True, state="closed")]), encoding="utf-8")
    assert G.main(["--pushed-sha", DROPPED_PUSH, "--pr-json", str(merged)]) == 1
    assert G.main(["--pushed-sha", MERGED_HEAD, "--pr-json", str(merged)]) == 0


def test_cli_treats_a_missing_pr_json_as_unknown_not_as_no_pr(capsys):
    """The workflow leaves `--pr-json` unset when the API read FAILED, so this
    wiring is the failure path and it must not be able to produce a quiet pass
    that reads as 'this branch has no PR'."""
    assert G.main(["--pushed-sha", DROPPED_PUSH]) == 0
    assert "UNKNOWN" in capsys.readouterr().out


def test_cli_none_means_the_read_succeeded_and_found_nothing(capsys):
    assert G.main(["--pushed-sha", DROPPED_PUSH, "--pr-json", "none"]) == 0
    assert "NO_PR" in capsys.readouterr().out


# ── mutation checks: break a predicate in isolation, prove the suite notices ─

def test_mutation_head_sha_comparison_is_load_bearing(monkeypatch):
    """Grading a merged PR's differing head as contained is the silent drop."""
    orig = G.grade

    def blind(pushed_sha, pr, pr_read_ok=True):
        row = orig(pushed_sha, pr, pr_read_ok)
        if row["state"] == G.DROPPED:
            row["state"], row["error"] = G.MERGED_CONTAINED, False
        return row

    monkeypatch.setattr(G, "grade", blind)
    assert G.grade(DROPPED_PUSH, _pr(merged=True, state="closed"))["state"] \
        == G.MERGED_CONTAINED, "sanity: the mutation took"
    # The module-level self-test calls the real `grade`, so assert against the
    # mutated view directly rather than claiming the self-test caught it.
    assert not G.grade(DROPPED_PUSH, _pr(merged=True, state="closed"))["error"], (
        "…and the consequence is that the run would no longer fail — which is "
        "exactly the nine silent minutes of #11846")


def test_mutation_auto_merge_key_presence_is_load_bearing(monkeypatch):
    monkeypatch.setattr(G, "_auto_merge_enabled", lambda pr: "auto_merge" in pr)
    row = G.grade(DROPPED_PUSH, _pr())      # auto_merge present-and-null == OFF
    assert row["state"] == G.ARMED, "sanity: the mutation took"
    assert row["warn"], (
        "…and every unarmed PR in the repo would warn on every push, which is "
        "the desensitised alarm rather than a detector")


def test_containment_is_full_sha_equality_not_an_abbreviation():
    """Added after a mutation run: a 4-character prefix comparison passed the
    whole suite, because the two real shas in these fixtures differ in their
    FIRST character. A loosened comparison manufactures `merged_contained` —
    it reports a drop as safe — so the property is pinned on shas that agree
    on everything but the tail."""
    p1, p2 = "abc12345" + "1" * 32, "abc12345" + "2" * 32
    row = G.grade(p2, _pr(merged=True, state="closed", head={"sha": p1}))
    assert row["state"] == G.DROPPED
