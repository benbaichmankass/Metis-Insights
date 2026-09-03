"""Tests for the PR action gate — may a manager act on THIS pull request?

The load-bearing property is that `permitted` is UNOBTAINABLE for a PR whose
author is observed LIVE, and equally that the gate is NOT a wall: every test
asserting a refusal is paired with one asserting the same path can permit, so
none of them is a constant.

⚠️ The module's own `--self-test` is the primary control and runs on every
invocation of the tool (and in CI via `run_guards.py :: manager-tooling-
selftests`). These tests exist for what that suite structurally cannot assert
about itself: that the CLI entry point wires to the same grader, and that the
liveness reading comes from the live observation rather than the register.
"""
import json
import sys
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1] / "scripts" / "ops"
sys.path.insert(0, str(OPS))
import pr_action_gate as g  # noqa: E402
import spawn_gate as sg  # noqa: E402

SID = "session_01AAAAAAAAAAAAAAAAAAAA"
OTHER = "session_01BBBBBBBBBBBBBBBBBBBB"
BODY = f"some work\n\nhttps://claude.ai/code/{SID}\n"
PR = {"number": 10857, "body": BODY, "head": {"ref": "claude/mi83-x"}}
AUTO = {"number": 10902, "body": "auto",
        "head": {"ref": "automation/work-digest-1"}}

LIVE = [{"session_id": SID, "status": "session_status_running"}]
IDLE = [{"session_id": SID, "status": "session_status_idle"}]
GONE = [{"session_id": SID, "status": "session_status_archived"}]
HANDED = [{"session_id": SID, "status": "session_status_running",
           "post_turn_summary": {"status_bucket": "review_ready"}}]

EMPTY_REG = ({"sessions": []}, True)
OK_EXC = {"decision": "approved", "covers": ["10857"],
          "approved_by": "operator (ben)", "approved_at": "2026-09-03"}


def _grade(pr=PR, obs=LIVE, reg=None, exc=None, action="undraft"):
    doc, ok = reg or EMPTY_REG
    return g.grade(10857 if pr is None else pr["number"], pr, obs, doc, ok, exc,
                   action)


def test_self_test_passes():
    assert g.main(["--self-test"]) == 0


# --------------------------------------------------------------------------- #
# the one refusal, and its negative control
# --------------------------------------------------------------------------- #
def test_a_live_author_refuses():
    assert _grade(obs=LIVE)["state"] == g.REFUSED


def test_an_idle_author_permits_so_the_gate_is_not_a_wall():
    assert _grade(obs=IDLE)["state"] == g.PERMITTED


def test_the_refusal_names_the_author_and_the_ways_forward():
    reason = _grade(obs=LIVE)["reason"]
    assert SID in reason
    # A refusal that only says "no" teaches nothing and gets routed around.
    assert "ASK IT" in reason
    assert "pr-action-exception" in reason


# --------------------------------------------------------------------------- #
# liveness NEVER comes from the register — the whole point
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("stored", ["working", "running", "idle", None])
def test_the_stored_state_cannot_change_the_verdict(stored):
    """17 of 17 inherited `working` rows were measured wrong (MI-84), so the
    register's opinion about liveness must not reach the verdict at all."""
    row = {"session_id": SID, "branches": ["claude/mi83-x"]}
    if stored:
        row["state"] = stored
    reg = ({"sessions": [row]}, True)
    assert _grade(obs=LIVE, reg=reg)["state"] == g.REFUSED
    assert _grade(obs=IDLE, reg=reg)["state"] == g.PERMITTED


# --------------------------------------------------------------------------- #
# work that is genuinely finished must not stall
# --------------------------------------------------------------------------- #
def test_a_handed_back_author_permits_even_while_running():
    v = _grade(obs=HANDED)
    assert v["state"] == g.PERMITTED
    # ...and it is recorded as a hand-back, never flattened into idleness.
    assert v["liveness"] == g.HANDED_BACK


def test_a_terminal_author_permits():
    assert _grade(obs=GONE)["state"] == g.PERMITTED


def test_an_automation_pr_permits_because_it_has_no_author():
    v = g.grade(10902, AUTO, LIVE, *EMPTY_REG, None)
    assert v["state"] == g.PERMITTED
    assert v["author"] == g.NO_AUTHOR


# --------------------------------------------------------------------------- #
# `unknown` — its own state, never a soft pass
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("obs,why", [
    (None, "no live observation was supplied"),
    ([{"session_id": OTHER, "status": "idle"}], "author absent from the page"),
    ([{"session_id": SID}], "observed row carries no status"),
])
def test_unlookable_liveness_is_unknown_never_permitted(obs, why):
    assert _grade(obs=obs)["state"] == g.UNKNOWN, why


def test_absence_from_the_page_is_not_terminal():
    """`list_sessions` is paginated — a page is not a population."""
    v = _grade(obs=[{"session_id": OTHER, "status": "idle"}])
    assert v["liveness"] == g.ABSENT
    assert v["liveness"] != g.TERMINAL


def test_an_unidentifiable_author_is_unknown_not_a_quiet_pass():
    orphan = {"number": 999, "body": "no footer", "head": {"ref": "claude/x"}}
    v = g.grade(999, orphan, LIVE, *EMPTY_REG, None)
    assert v["state"] == g.UNKNOWN
    assert v["author"] == g.UNIDENTIFIED


def test_no_author_and_unidentified_are_distinct_values():
    """"this PR HAS no author" and "we could not find its author" are opposite
    facts; sharing one value is the collapsed-state defect."""
    assert g.NO_AUTHOR != g.UNIDENTIFIED


def test_every_non_permitted_state_exits_non_zero():
    assert g._EXIT[g.PERMITTED] == 0
    assert g._EXIT[g.REFUSED] != 0
    assert g._EXIT[g.UNKNOWN] != 0


# --------------------------------------------------------------------------- #
# author resolution
# --------------------------------------------------------------------------- #
def test_the_body_footer_outranks_the_register_because_it_cannot_go_stale():
    reg = ({"sessions": [{"session_id": OTHER, "branches": ["claude/mi83-x"]}]},
           True)
    assert _grade(obs=IDLE, reg=reg)["author"] == SID


def test_two_different_sessions_in_one_body_is_ambiguous_not_first_wins():
    pr = dict(PR, body=f"dispatched by https://claude.ai/code/{OTHER}\n{BODY}")
    v = g.grade(10857, pr, LIVE, *EMPTY_REG, None)
    assert v["state"] == g.UNKNOWN
    assert v["author"] == g.AMBIGUOUS


def test_the_same_session_twice_is_not_ambiguous():
    """#10895 carries its footer twice, identically."""
    assert g.author_from_body(f"{BODY}\n{BODY}")[0] == SID


def test_the_footer_pattern_cannot_match_a_json_key_name():
    """`session_registry` measured 3 of 32 findings nonexistent when a loose
    pattern matched keys like `session_context`."""
    assert g.author_from_body('{"session_status": "running"}')[0] is None


def test_a_repo_qualified_branch_ref_resolves_too():
    doc = {"sessions": [{"session_id": SID,
                         "branches": ["Metis-Insights:claude/nobody"]}]}
    assert g.author_from_registry("claude/nobody", 999, doc, True)[0] == SID


# --------------------------------------------------------------------------- #
# the escape hatch — spawn_gate's rules, not a second set
# --------------------------------------------------------------------------- #
def test_an_approved_exception_naming_this_pr_permits_over_a_live_author():
    assert _grade(obs=LIVE, exc=OK_EXC)["state"] == g.PERMITTED


@pytest.mark.parametrize("mutate,why", [
    ({"decision": "pending"}, "filing is not granting"),
    ({"decision": "refused"}, "an operator said no"),
    ({"covers": ["10999"]}, "names a different PR"),
    ({"covers": []}, "a blanket bypass names nothing"),
])
def test_a_defective_exception_still_refuses(mutate, why):
    assert _grade(obs=LIVE, exc=dict(OK_EXC, **mutate))["state"] == g.REFUSED, why


def test_an_approval_with_nobodys_name_on_it_refuses():
    exc = {k: v for k, v in OK_EXC.items() if k != "approved_by"}
    assert _grade(obs=LIVE, exc=exc)["state"] == g.REFUSED


def test_the_exception_is_graded_by_spawn_gates_own_function():
    """Imported, not re-implemented, so the two files cannot drift on what an
    approval means."""
    assert sg.exception_covers(OK_EXC, "10857")[0] is True


def test_there_is_no_force_flag():
    """A bypass flag is cheaper to lie to than to satisfy."""
    with pytest.raises(SystemExit):
        g.main(["--pr", "10857", "--force"])


def test_the_shipped_exception_file_is_pending_so_it_grants_nothing():
    exc, readable = g.read_exception()
    assert readable
    assert exc is not None, "the template must exist to be the known escape hatch"
    assert exc.get("decision") == "pending"
    assert sg.exception_covers(exc, "10857")[0] is False


# --------------------------------------------------------------------------- #
# the CLI wires to the same grader — the join the self-test cannot see
# --------------------------------------------------------------------------- #
def test_the_cli_refuses_a_live_author_end_to_end(tmp_path, capsys):
    """⚠️ This is the case that would have caught the real defect found on
    2026-09-03: all 40 self-test cases passed while the CLI graded every live PR
    `unidentified`, because it fed the payload through a normaliser that drops
    `body`. Hand-built inputs cannot catch a broken input path."""
    prs = tmp_path / "prs.json"
    prs.write_text(json.dumps([PR]))
    live = tmp_path / "live.json"
    live.write_text(json.dumps(LIVE))
    rc = g.main(["--pr", "10857", "--action", "undraft",
                 "--open-prs", str(prs), "--live-sessions", str(live)])
    assert rc == g._EXIT[g.REFUSED]
    assert SID in capsys.readouterr().out


def test_the_cli_permits_an_idle_author_end_to_end(tmp_path):
    prs = tmp_path / "prs.json"
    prs.write_text(json.dumps([PR]))
    live = tmp_path / "live.json"
    live.write_text(json.dumps(IDLE))
    rc = g.main(["--pr", "10857", "--open-prs", str(prs),
                 "--live-sessions", str(live)])
    assert rc == g._EXIT[g.PERMITTED]


def test_the_cli_without_a_live_read_is_unknown_never_permitted(tmp_path):
    prs = tmp_path / "prs.json"
    prs.write_text(json.dumps([PR]))
    assert g.main(["--pr", "10857", "--open-prs", str(prs)]) == g._EXIT[g.UNKNOWN]


def test_the_cli_with_no_pr_is_unknown():
    assert g.main([]) == g._EXIT[g.UNKNOWN]


def test_the_extractor_keeps_the_fields_the_join_needs():
    ent = g.open_pr_entries([PR])
    assert ent is not None
    assert ent[0]["body"] == BODY
    assert ent[0]["head"]["ref"] == "claude/mi83-x"


def test_an_unreadable_pr_payload_is_none_not_empty():
    """"we could not look" and "nothing is open" are opposite facts."""
    assert g.open_pr_entries("not json") is None
    assert g.open_pr_entries([]) is None
