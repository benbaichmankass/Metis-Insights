"""THE SWEEPER MUST NOT LAND A PR THAT WOULD REWIND `main`, AND MUST NOT
SILENTLY DROP A QUEUED OPERATOR PING.

`scripts/ops/sweep_stale_automation_prs.py` carries its own `--self-test` for
the classifier's states. These are the properties that must hold even if
somebody edits it later believing they are simplifying it — every one of them
is a thing a plausible "cleanup" would break.

⚠️ THE MOTIVATING MISTAKE IS PLANTED HERE ON PURPOSE. A first pass at this
triage classified by the newest dated register alone and called 22 open PRs
cleanly superseded; separating append-only payloads out put the true figure at
15, with SEVEN work-digest PRs each carrying one `docs/claude/pending-pings.jsonl`
row absent from `main`. Auto-closing on the first classification would have
dropped seven operator notifications. `test_stale_register_plus_unread_ping_is_not_superseded`
is that case, and it must never go green by way of the register alone.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "scripts/ops/sweep_stale_automation_prs.py"


def _load():
    spec = importlib.util.spec_from_file_location("sweep_stale", MODULE)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


sweep = _load()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A scratch repo whose `main` carries a noon register and one queued ping."""
    def g(*a: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True,
                       capture_output=True, text=True)

    g("init", "-q", "-b", "main")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    (tmp_path / "reg.json").write_text(
        json.dumps({"generated_at": "2026-09-09T12:00:00Z", "v": "main"}) + "\n")
    pings = tmp_path / "docs/claude"
    pings.mkdir(parents=True)
    (pings / "pending-pings.jsonl").write_text('{"t":"a"}\n')
    g("add", "-A")
    g("commit", "-q", "-m", "main")
    return tmp_path


def _branch(repo: Path, name: str, files: dict) -> None:
    def g(*a: str) -> None:
        subprocess.run(["git", "-C", str(repo), *a], check=True,
                       capture_output=True, text=True)
    g("checkout", "-q", "main")
    g("checkout", "-q", "-b", name)
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    g("add", "-A")
    g("commit", "-q", "-m", name)
    g("checkout", "-q", "main")


def _classify(repo: Path, number: int, ref: str, holders=None) -> dict:
    return sweep.classify({"number": number, "ref": ref}, "main",
                          holders or {}, cwd=repo)


def test_an_older_register_is_never_actionable(repo: Path) -> None:
    """Refreshing it would REWIND `main` — auto-merge is already armed."""
    _branch(repo, "automation/old",
            {"reg.json": json.dumps(
                {"generated_at": "2026-09-09T11:00:00Z", "v": "old"}) + "\n"})
    entry = _classify(repo, 1, "automation/old")
    assert entry["state"] == sweep.SUPERSEDED_OLDER
    assert entry["state"] not in sweep.ACTIONABLE
    assert "REWIND" in entry["why"]


def test_a_newer_register_that_nothing_beats_is_refreshed(repo: Path) -> None:
    _branch(repo, "automation/new",
            {"reg.json": json.dumps(
                {"generated_at": "2026-09-09T13:00:00Z", "v": "new"}) + "\n"})
    holders = sweep.newest_by_path([{"number": 1, "ref": "automation/new"}],
                                   "main", cwd=repo)
    entry = _classify(repo, 1, "automation/new", holders)
    assert entry["state"] == sweep.REFRESH
    assert entry["state"] in sweep.ACTIONABLE


def test_only_the_newest_of_two_candidates_is_refreshed(repo: Path) -> None:
    """Two PRs regenerating one register must not BOTH land, in either order."""
    _branch(repo, "automation/a",
            {"reg.json": json.dumps(
                {"generated_at": "2026-09-09T13:00:00Z", "v": "a"}) + "\n"})
    _branch(repo, "automation/b",
            {"reg.json": json.dumps(
                {"generated_at": "2026-09-09T14:00:00Z", "v": "b"}) + "\n"})
    prs = [{"number": 1, "ref": "automation/a"},
           {"number": 2, "ref": "automation/b"}]
    holders = sweep.newest_by_path(prs, "main", cwd=repo)
    states = {p["number"]: _classify(repo, p["number"], p["ref"], holders)["state"]
              for p in prs}
    assert states == {1: sweep.SUPERSEDED_BY_OPEN_PR, 2: sweep.REFRESH}
    assert sum(s in sweep.ACTIONABLE for s in states.values()) == 1


def test_stale_register_plus_unread_ping_is_not_superseded(repo: Path) -> None:
    """THE MOTIVATING MISTAKE. Classifying by the register alone drops the ping."""
    _branch(repo, "automation/mixed", {
        "reg.json": json.dumps(
            {"generated_at": "2026-09-09T11:00:00Z", "v": "old"}) + "\n",
        "docs/claude/pending-pings.jsonl": '{"t":"a"}\n{"t":"z"}\n',
    })
    entry = _classify(repo, 1, "automation/mixed")
    assert entry["state"] == sweep.CARRIES_APPEND_ONLY
    assert entry["state"] != sweep.SUPERSEDED_OLDER
    assert "+1 row(s) not on main" in entry["why"]


def test_an_undated_payload_is_reported_not_guessed(repo: Path) -> None:
    """`we could not tell` must never render as `safe`."""
    _branch(repo, "automation/undated",
            {"reg.json": json.dumps({"v": "no date here"}) + "\n"})
    entry = _classify(repo, 1, "automation/undated")
    assert entry["state"] == sweep.UNDATED_PAYLOAD
    assert entry["state"] not in sweep.ACTIONABLE
    assert "COULD NOT BE DETERMINED" in entry["why"]


def test_arming_files_and_the_slot_claim_are_not_payload(repo: Path) -> None:
    """Every arming branch writes them, so they cannot discriminate between PRs."""
    _branch(repo, "automation/arming", {
        ".github/pr-landing/automation-arming.json": "{}\n",
        ".github/pr-automerge-requests/automation-arming.txt": "armed\n",
        sweep.SLOT_FILE: json.dumps({"merge_slot": {"branch": "x"}}) + "\n",
    })
    entry = _classify(repo, 1, "automation/arming")
    assert entry["state"] == sweep.NO_PAYLOAD
    assert entry["files"] == []


def test_refresh_is_the_only_actionable_state() -> None:
    """A later edit that adds a state to ACTIONABLE must justify itself here."""
    assert sweep.ACTIONABLE == (sweep.REFRESH,)
    for state in (sweep.SUPERSEDED_OLDER, sweep.SUPERSEDED_IDENTICAL,
                  sweep.SUPERSEDED_BY_OPEN_PR, sweep.CARRIES_APPEND_ONLY,
                  sweep.ABSENT_ON_MAIN, sweep.UNDATED_PAYLOAD, sweep.NO_PAYLOAD):
        assert state not in sweep.ACTIONABLE


def test_pending_pings_is_treated_as_append_only() -> None:
    """The operator's notification queue. Dropping a row here is silent."""
    assert "docs/claude/pending-pings.jsonl" in sweep.APPEND_ONLY


def test_an_unreadable_open_set_raises_rather_than_returning_empty(
        tmp_path: Path) -> None:
    """`we could not look` is not `nothing is stranded`."""
    missing = tmp_path / "nope.json"
    with pytest.raises(sweep.CouldNotLook):
        sweep.open_automation_prs(missing)


def test_the_sweeper_self_test_passes() -> None:
    """The module's own planted-defect suite, run in CI rather than by hand."""
    done = subprocess.run(["python3", str(MODULE), "--self-test"],
                          capture_output=True, text=True, cwd=str(REPO))
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_workflow_pushes_with_the_pat_not_github_token() -> None:
    """A GITHUB_TOKEN push creates no workflow runs, so the refresh would mint a
    head sha NOTHING GRADES — worse than the strand, and it reads as progress."""
    wf = (REPO / ".github/workflows/stale-automation-sweep.yml").read_text()
    assert "secrets.BRANCH_PROTECTION_TOKEN" in wf
    assert "fetch-depth: 0" in wf, (
        "a shallow clone resolves the merge-base wrong and grades every branch "
        "against the wrong tree — a CONFIDENT wrong answer, not an error")
    assert "on:\n  schedule:" in wf and "\n  push:" not in wf, (
        "a push trigger would retrigger this workflow on its own refresh pushes")


# ---------------------------------------------------------------------------
# THE SECOND APPEND-ONLY CLASS, planted. Found on 2026-09-09 by HAND-TRIAGING
# #11475 -- a PR the `APPEND_ONLY` path list does not cover. Its
# `comms/macro/econ_calendar_snapshots.jsonl` is `main` plus 427 rows main does
# not have, and it also carries a point-in-time capture absent from main
# entirely. It graded `undated_payload` and so was never actionable -- but only
# BY ACCIDENT, because JSONL does not parse as a JSON object. Stamping
# `generated_at` on that generator is EXACTLY the remedy this session filed for
# the 13 undated PRs, and it would have ARMED the bug.
# ---------------------------------------------------------------------------
def test_an_appended_payload_outside_the_list_is_still_append_only(
        repo: Path) -> None:
    """The list is a memory aid; the PREFIX RELATION is the actual property."""
    _branch(repo, "automation/appended", {
        "feed.jsonl": '{"t":"a"}\n',
    })
    # main gains the file first, then the branch appends to it
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)
    (repo / "feed.jsonl").write_text('{"t":"a"}\n')
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                   capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "feed"],
                   check=True, capture_output=True)
    _branch(repo, "automation/appended2", {"feed.jsonl": '{"t":"a"}\n{"t":"b"}\n'})
    entry = _classify(repo, 1, "automation/appended2")
    assert entry["state"] == sweep.CARRIES_APPEND_ONLY
    assert entry["state"] not in sweep.ACTIONABLE
    assert "feed.jsonl" not in sweep.APPEND_ONLY, (
        "this test is worthless if the path was simply added to the list — the "
        "point is that the PROPERTY is detected, not the name"
    )


def test_the_append_test_is_asked_BEFORE_the_register_comparison() -> None:
    """⚠️ THE DANGEROUS DIRECTION, asserted on the SOURCE and not on behaviour,
    because I could not construct the behavioural case and say so rather than
    fake it.

    A file that is BOTH dated and a strict byte-prefix append is close to
    unconstructible for a JSON object: two valid JSON dicts are not prefixes of
    one another, `_blob` strips, and a JSONL append does not parse as a dict so
    `dated_at` already returns None. So there is no fixture that proves the
    precedence at runtime today.

    What CAN be asserted is the precedence itself: in `classify`, the append
    test is reached before `dated_at` is consulted. If a future generator ever
    produces a dated append — and stamping `generated_at` on these generators is
    EXACTLY the remedy this session filed for the 13 undated PRs — the append
    must win, or the stamp arms the row-dropping bug.
    """
    src = MODULE.read_text(encoding="utf-8")
    body = src.split("def classify(")[1].split("\nclass ")[0]
    assert body.index("looks_appended(") < body.index("dated_at(head"), (
        "the append test must be asked BEFORE the register comparison, or a "
        "dated append is closable on its timestamp"
    )


def test_a_payload_absent_from_main_is_its_own_state(repo: Path) -> None:
    """`main has never had this file` and `neither side carries a date` both
    make `dated_at` return None, and they are DIFFERENT FACTS."""
    _branch(repo, "automation/pit",
            {"captures/US-20260909T002929Z.json": '{"generated_at": "2026-09-09T00:29:29Z"}'})
    entry = _classify(repo, 1, "automation/pit")
    assert entry["state"] == sweep.ABSENT_ON_MAIN
    assert entry["state"] != sweep.UNDATED_PAYLOAD, (
        "it carries a perfectly good timestamp — reporting 'no comparable "
        "timestamp' would name a cause no code path tested"
    )
    assert entry["state"] not in sweep.ACTIONABLE
    assert "do NOT EXIST on main" in entry["why"]


def test_classify_consults_the_structural_append_detector() -> None:
    """A later edit that drops back to the bare path list must fail here."""
    src = MODULE.read_text(encoding="utf-8")
    body = src.split("def classify(")[1].split("\nclass ")[0]
    assert "looks_appended(" in body, (
        "classify() must test the PREFIX RELATION, not only `path in "
        "APPEND_ONLY` — the list covered one of the two append-only payload "
        "classes actually present in the live queue"
    )
    assert "main_blob is None" in body, (
        "classify() must ask whether main HAS the file before asking how it is "
        "dated"
    )


# ---------------------------------------------------------------------------
# THE FALSE SUCCESS, planted. Found on the sweeper's FIRST LIVE RUN (2026-09-09):
# `refresh()` used `git commit --no-edit`, which has no message to reuse on the
# no-conflict path because `git merge` has already committed. The commit failed,
# its exit code was discarded, the push carried the merge commit alone, and the
# function returned "refreshed and pushed" for a claim it never wrote — PR #11490
# came back still failing R13 while the sweeper reported it fixed.
# ---------------------------------------------------------------------------
def test_refresh_never_uses_no_edit_to_commit_the_claim() -> None:
    """`--no-edit` silently no-ops on the path that has already committed."""
    src = MODULE.read_text(encoding="utf-8")
    body = src.split("def refresh(")[1].split("\ndef ")[0]
    assert '"--no-edit"' not in body, (
        "refresh() must commit the re-asserted claim with an explicit -m; "
        "--no-edit has no message to reuse after `git merge` auto-commits, so "
        "the commit fails and the claim is never written"
    )
    assert '"-m"' in body


def test_refresh_checks_the_commit_exit_code_and_verifies_the_effect() -> None:
    """Every step can succeed while leaving the claim absent — so check the claim."""
    src = MODULE.read_text(encoding="utf-8")
    body = src.split("def refresh(")[1].split("\ndef ")[0]
    push_at = body.index('"push"')
    before_push = body[:push_at]
    assert "could not commit the re-asserted merge-slot claim" in before_push, (
        "a failed commit must abort the refresh BEFORE the push, not fall through"
    )
    assert "merge-base" in before_push and "SLOT_FILE" in before_push, (
        "refresh() must verify the slot file is in this branch's own diff "
        "against the merge-base before pushing — verify the EFFECT, not the call"
    )
    assert "rides someone else's claim" in before_push, (
        "refresh() must verify the committed claim names THIS branch"
    )


def test_a_failed_refresh_leaves_the_tree_clean_for_the_next_pr() -> None:
    """A dirty tree makes the NEXT branch's checkout fail — one bad PR must not
    take the rest of the sweep down with it. Observed live: after #11490 the tree
    held a staged session-board.json and #11543 died on `could not check the
    branch out`."""
    src = MODULE.read_text(encoding="utf-8")
    body = src.split("def refresh(")[1].split("\ndef ")[0]
    assert '"reset", "--hard"' in body, (
        "the commit-failure path must reset the working tree, or the next "
        "refresh in the same run cannot check its branch out"
    )
