"""The merge slot cannot say "nobody holds this", so every claimant guesses.

`check_pr_landing.py` R13 **refuses** to let a branch arm the landing route
without a merge-slot claim, so the CLAIM half is a mechanism and happens every
time. The RELEASE half is prose in three places and is carried by nothing.

MEASURED over the last 25 commits touching `session-board.json`: **zero** ever
wrote a cleared slot. And `.github/actions/commit-to-main` rewrites the claim on
every automated commit — 27 workflows call it — so the field goes stale
**mechanically, at the rate `main` moves** (median 7.1 min between merges).

The cost is a judgement call every claimant makes alone: wait forever on a
merged PR, or displace blind. Both happened live on 2026-09-09.

⚠️ **THE LOAD-BEARING REFUSAL these tests pin: branch PRESENCE is not evidence
of liveness.** Measured 2026-09-12 — the slot on `main` named
`automation/reconcile-open-prs-34677561396-1`, whose branch `git ls-remote`
could not find (decidably a ghost), while **4 of 4** sampled `claude/mi188*` /
`mi191*` / `mi193*` lane branches merged FOUR DAYS earlier were still on
`origin`. So absence is decisive and presence proves nothing. Grading presence
as `live` would recreate the wait-on-a-ghost with the machine's authority
behind it.

Run: ``python3 -m pytest tests/test_merge_slot_state.py``
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "ops" / "merge_slot_state.py"


def _load():
    spec = importlib.util.spec_from_file_location("_merge_slot_state", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = _load()

CLAIM = {"held_by": "session_X", "branch": "claude/thing",
         "claimed_at": "2026-09-12T06:13:28Z", "purpose": "p"}


def test_self_test_passes():
    ok, fails = G._self_test(quiet=True)
    assert ok, f"merge-slot-state self-test failures: {fails}"


# ── the ghost, decidable from git alone: the common automation case ──────────

def test_a_claim_whose_branch_is_gone_is_spent_and_safe_to_displace():
    row = G.grade(CLAIM, branch_present=False)
    assert row["state"] == G.SPENT
    assert row["safe_to_displace"] is True


# ── THE REFUSAL: presence is not liveness ───────────────────────────────────

def test_a_claim_whose_branch_still_exists_is_undecidable_never_live():
    row = G.grade(CLAIM, branch_present=True)
    assert row["state"] == G.UNDECIDABLE
    assert row["state"] != G.LIVE
    assert row["safe_to_displace"] is False


def test_the_undecidable_message_carries_the_measurement_that_justifies_it():
    """Otherwise the refusal reads as caution rather than as a finding."""
    assert "4 of 4" in G.grade(CLAIM, branch_present=True)["why"]


# ── a PR payload is authoritative, in BOTH directions ───────────────────────

@pytest.mark.parametrize("pr,want", [
    ({"number": 1, "merged": True}, G.SPENT),
    ({"number": 1, "merged_at": "2026-09-12T00:00:00Z"}, G.SPENT),
    ({"number": 1, "state": "closed"}, G.SPENT),
    ({"number": 1, "state": "open"}, G.LIVE),
])
def test_a_pr_payload_settles_it(pr, want):
    assert G.grade(CLAIM, branch_present=True, pr=pr)["state"] == want


def test_the_direct_answer_beats_the_proxy():
    """An OPEN PR on a branch the ls-remote said was gone is LIVE, not spent —
    the proxy exists only because the direct answer usually is not to hand."""
    assert G.grade(CLAIM, branch_present=False,
                   pr={"number": 1, "state": "open"})["state"] == G.LIVE


def test_live_still_says_displacement_is_permitted():
    """R13 does not serialize, so this must not read as a lock it is not."""
    row = G.grade(CLAIM, True, {"number": 1, "state": "open"})
    assert "does not serialize" in row["why"]
    assert row["safe_to_displace"] is False, "…but it is not a ghost either"


# ── "we could not look" is its own state ────────────────────────────────────

def test_an_unreachable_origin_is_undecidable_not_spent():
    row = G.grade(CLAIM, branch_present=None)
    assert row["state"] == G.UNDECIDABLE
    assert "not 'the slot is free'" in row["why"]


def test_branch_on_origin_returns_none_for_an_empty_branch():
    assert G.branch_on_origin("") is None, "None is 'we did not look', not False"


def test_an_unreadable_board_is_not_an_empty_slot(tmp_path):
    p = tmp_path / "board.json"
    p.write_text("{not json", encoding="utf-8")
    claim, readable = G.read_claim(p)
    assert readable is False and claim is None


# ── an empty or malformed slot ──────────────────────────────────────────────

@pytest.mark.parametrize("slot", [None, {}, {"branch": ""}, {"branch": "  "},
                                  "nonsense", 7, []])
def test_an_empty_or_malformed_slot_is_unclaimed(slot):
    row = G.grade(slot, branch_present=False)
    assert row["state"] == G.UNCLAIMED
    assert row["safe_to_displace"] is True


def test_an_unattributable_claim_is_still_graded_rather_than_dropped():
    assert G.grade({"branch": "b"}, branch_present=False)["state"] == G.SPENT


def test_all_four_states_are_reachable_so_none_is_decorative():
    reached = {
        G.grade(None, False)["state"],
        G.grade(CLAIM, False)["state"],
        G.grade(CLAIM, True)["state"],
        G.grade(CLAIM, True, {"number": 1, "state": "open"})["state"],
    }
    assert reached == set(G.ALL_STATES)


# ── the claimant's report must never take down the claim ────────────────────

def test_claiming_still_writes_when_the_incumbent_cannot_be_graded(tmp_path,
                                                                   monkeypatch):
    """R13 fails an armed branch that does not hold the slot, so a crash in a
    courtesy read would red a PR over a report."""
    spec = importlib.util.spec_from_file_location(
        "_claim_merge_slot", REPO / "scripts" / "ops" / "claim_merge_slot.py")
    cms = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cms)

    board = tmp_path / "session-board.json"
    board.write_text(
        '{\n "merge_slot": {\n  "held_by": "old",\n  "branch": "claude/old",\n'
        '  "claimed_at": "t",\n  "purpose": ""\n },\n "other": 1\n}\n',
        encoding="utf-8")

    def boom(*a, **k):
        raise RuntimeError("planted: the incumbent read blew up")

    monkeypatch.setattr(cms.mss, "branch_on_origin", boom)
    rc = cms.main(["--board", str(board), "--branch", "claude/new",
                   "--held-by", "session_Y"])
    assert rc == 0, "the claim must still be written"
    assert '"branch": "claude/new"' in board.read_text(encoding="utf-8")


def test_the_incumbent_report_does_not_alter_what_is_written(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "_claim_merge_slot2", REPO / "scripts" / "ops" / "claim_merge_slot.py")
    cms = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cms)

    src = ('{\n "merge_slot": {\n  "held_by": "old",\n  "branch": "claude/old",\n'
           '  "claimed_at": "t",\n  "purpose": ""\n },\n "other": 1\n}\n')
    with_check, without = tmp_path / "a.json", tmp_path / "b.json"
    with_check.write_text(src, encoding="utf-8")
    without.write_text(src, encoding="utf-8")

    args = ["--branch", "claude/new", "--held-by", "s", "--claimed-at", "T"]
    assert cms.main(["--board", str(with_check)] + args) == 0
    assert cms.main(["--board", str(without), "--no-incumbent-check"] + args) == 0
    assert with_check.read_text(encoding="utf-8") == without.read_text(encoding="utf-8")
